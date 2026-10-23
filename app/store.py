"""SQLite 存储层。

并发要点：
- 每次操作独立连接（避免跨线程共享连接）。
- `timeout=10` + `PRAGMA busy_timeout=10000`：调度线程写日志与 Web 线程保存规则
  撞车时排队等待，而不是直接抛 "database is locked"。
- 规则更新是**单条 UPDATE**（rule_json 与 next_run_at 同一语句），天然原子，
  不会出现调度线程读到"规则已改、next 还是旧值"的中间态。
"""
from __future__ import annotations

import json
import os
import sqlite3

import clock
import notify

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data"))
DB_PATH = os.path.join(DATA_DIR, "reminder.db")

DEFAULT_SETTINGS = {
    "grace_seconds": 300,
    "email": {
        "enabled": False,
        "host": "",
        "port": 465,
        "ssl": True,
        "user": "",
        "password": "",
        "sender": "",
        "to": "",
    },
    # 两个钉钉群机器人，各自独立配置；规则里可以分别指定用哪个
    "dingtalk1": {"enabled": False, "webhook": "", "secret": "", "keyword": ""},
    "dingtalk2": {"enabled": False, "webhook": "", "secret": "", "keyword": ""},
    # 消息模板：留空 = 跟随内置默认（这样升级后默认样式能自动生效）
    "template": {"dingtalk": "", "text": ""},
}


def now_str() -> str:
    return clock.now_str()


def conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=10.0)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=10000")
    return c


def init() -> None:
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS rules(
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                title         TEXT NOT NULL,
                body          TEXT NOT NULL DEFAULT '',
                rule_json     TEXT NOT NULL,
                channels      TEXT NOT NULL DEFAULT '[]',
                enabled       INTEGER NOT NULL DEFAULT 1,
                next_run_at   TEXT,
                snooze_until  TEXT,
                last_run_at   TEXT,
                created_at    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS logs(
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts      TEXT NOT NULL,
                rule_id INTEGER,
                title   TEXT,
                channel TEXT,
                ok      INTEGER,
                status  TEXT DEFAULT '',
                detail  TEXT
            );
            CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT NOT NULL);
            """
        )
        # 老库升级：补 snooze_until 列
        cols = {r["name"] for r in c.execute("PRAGMA table_info(rules)")}
        if "snooze_until" not in cols:
            c.execute("ALTER TABLE rules ADD COLUMN snooze_until TEXT")
        # 老库升级：补 logs.status 列（sent / failed / skipped）
        lcols = {r["name"] for r in c.execute("PRAGMA table_info(logs)")}
        if "status" not in lcols:
            c.execute("ALTER TABLE logs ADD COLUMN status TEXT DEFAULT ''")
            c.execute("UPDATE logs SET status=CASE WHEN ok=1 THEN 'sent' ELSE 'failed' END "
                      "WHERE status IS NULL OR status=''")
        # 调度热路径索引：每 15 秒都会按 (enabled, next_run_at) 扫一次
        c.execute("CREATE INDEX IF NOT EXISTS idx_rules_next_run ON rules(enabled, next_run_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_logs_rule ON logs(rule_id, id DESC)")


# ---------------- 规则 ----------------

def _row_to_rule(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["rule"] = json.loads(d.pop("rule_json") or "{}")
    # 老规则的 "dingtalk" 会被规范化成 "dingtalk1"
    d["channels"] = notify.normalize_channels(json.loads(d.get("channels") or "[]"))
    d["enabled"] = bool(d["enabled"])
    d["state"] = rule_state(d)
    return d


def rule_state(d: dict) -> str:
    """active / paused / completed。"""
    if d.get("rule", {}).get("type") == "once" and d.get("last_run_at") and not d.get("next_run_at"):
        return "completed"
    if not d.get("enabled"):
        return "paused"
    return "active"


def list_rules() -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT * FROM rules ORDER BY id DESC").fetchall()
    return [_row_to_rule(r) for r in rows]


def get_rule(rid: int) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM rules WHERE id=?", (rid,)).fetchone()
    return _row_to_rule(r) if r else None


def due_rules(now: str) -> list[dict]:
    """到期规则：正常触发点 或 snooze 到点，两者取更早的。"""
    with conn() as c:
        rows = c.execute(
            """
            SELECT * FROM rules
            WHERE enabled=1 AND (
                (next_run_at IS NOT NULL AND next_run_at<=?)
                OR (snooze_until IS NOT NULL AND snooze_until<=?)
            )
            ORDER BY COALESCE(snooze_until, next_run_at)
            """,
            (now, now),
        ).fetchall()
    return [_row_to_rule(r) for r in rows]


def create_rule(title: str, body: str, rule: dict, channels: list[str],
                enabled: bool = True, next_run_at: str | None = None) -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO rules(title,body,rule_json,channels,enabled,next_run_at,created_at) VALUES(?,?,?,?,?,?,?)",
            (title, body, json.dumps(rule, ensure_ascii=False), json.dumps(channels),
             1 if enabled else 0, next_run_at, now_str()),
        )
        return int(cur.lastrowid)


def update_rule(rid: int, **fields) -> None:
    """单条 UPDATE，保证 rule_json 与 next_run_at 一起生效。"""
    if not fields:
        return
    if "rule" in fields:
        fields["rule_json"] = json.dumps(fields.pop("rule"), ensure_ascii=False)
    if "channels" in fields:
        fields["channels"] = json.dumps(fields["channels"])
    if "enabled" in fields:
        fields["enabled"] = 1 if fields["enabled"] else 0
    keys = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE rules SET {keys} WHERE id=?", (*fields.values(), rid))


def delete_rule(rid: int) -> None:
    with conn() as c:
        c.execute("DELETE FROM rules WHERE id=?", (rid,))


def set_next(rid: int, next_run_at: str | None, fired: bool = False, clear_snooze: bool = False) -> None:
    sets = ["next_run_at=?"]
    vals: list = [next_run_at]
    if fired:
        sets.append("last_run_at=?")
        vals.append(now_str())
    if clear_snooze:
        sets.append("snooze_until=NULL")
    vals.append(rid)
    with conn() as c:
        c.execute(f"UPDATE rules SET {', '.join(sets)} WHERE id=?", tuple(vals))


def set_snooze(rid: int, until: str | None) -> None:
    with conn() as c:
        c.execute("UPDATE rules SET snooze_until=? WHERE id=?", (until, rid))


# ---------------- 日志 ----------------

def add_log(rule_id: int | None, title: str, channel: str, ok: bool, detail: str,
            status: str = "") -> None:
    """status: sent / failed / skipped。省略时按 ok 推断，兼容旧调用点。"""
    status = status or ("sent" if ok else "failed")
    with conn() as c:
        c.execute(
            "INSERT INTO logs(ts,rule_id,title,channel,ok,status,detail) VALUES(?,?,?,?,?,?,?)",
            (now_str(), rule_id, title, channel, 1 if ok else 0, status, (detail or "")[:1000]),
        )
        c.execute("DELETE FROM logs WHERE id NOT IN (SELECT id FROM logs ORDER BY id DESC LIMIT 2000)")


def list_logs(limit: int = 100) -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["status"] = d.get("status") or ("sent" if d.get("ok") else "failed")
        d["ok"] = bool(d.get("ok"))
        out.append(d)
    return out


# ---------------- 设置 ----------------

def get_settings() -> dict:
    with conn() as c:
        row = c.execute("SELECT v FROM kv WHERE k='settings'").fetchone()
    cfg = json.loads(json.dumps(DEFAULT_SETTINGS))  # deep copy
    if row:
        try:
            stored = json.loads(row["v"])
        except Exception:
            stored = {}
        _migrate_legacy_dingtalk(stored)
        _deep_merge(cfg, stored)
    return cfg


def _migrate_legacy_dingtalk(stored: dict) -> None:
    """v1.1 及以前只有一个 "dingtalk" 配置块，升级后并入机器人 1。

    只在机器人 1 还是空的时候搬，避免覆盖用户已经填好的新配置。
    """
    legacy = stored.pop("dingtalk", None)
    if not isinstance(legacy, dict) or not (legacy.get("webhook") or "").strip():
        return
    slot = stored.setdefault("dingtalk1", {})
    if (slot.get("webhook") or "").strip():
        return
    slot.update({k: v for k, v in legacy.items() if v not in (None, "")})


def save_settings(patch: dict) -> dict:
    cfg = get_settings()
    _deep_merge(cfg, patch or {})
    with conn() as c:
        c.execute("INSERT INTO kv(k,v) VALUES('settings',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                  (json.dumps(cfg, ensure_ascii=False),))
    return cfg


def _deep_merge(dst: dict, src: dict) -> None:
    for k, v in (src or {}).items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v
