#!/usr/bin/env python3
"""定时提醒 —— 零依赖 Web 服务。

http.server(ThreadingMixIn) + sqlite3 + 调度线程 + 发送队列。

运行： DATA_DIR=./data PORT=8388 python server.py
口令： ADMIN_TOKEN=xxx 时启用（默认不设置 = 内网免密）
"""
from __future__ import annotations

import hmac
import json
import mimetypes
import os
import posixpath
import re
import threading
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import clock
import dispatcher
import notify
import rules as ruleset
import store
import template

PORT = int(os.environ.get("PORT", "8388"))
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
TICK_SECONDS = int(os.environ.get("TICK_SECONDS", "15"))
ADMIN_TOKEN = (os.environ.get("ADMIN_TOKEN") or "").strip()
APP_VERSION = "1.3.2"

# 请求体上限：本服务的 JSON 都很小，超过这个量级一定是异常/恶意请求。
# 不设限的话，一个 Content-Length: 10GB 就能把进程内存吃干。
MAX_BODY_BYTES = 256 * 1024

# 各配置块里需要回传掩码的字段
_SECRET_FIELDS = {"email": ("password",), "dingtalk1": ("secret",), "dingtalk2": ("secret",)}

_stop = threading.Event()
_sender = dispatcher.Dispatcher(workers=2)


# ---------------------------------------------------------------- 调度

def schedule_next(rule: dict) -> str | None:
    return clock.fmt(ruleset.next_after(rule.get("rule", {}), clock.now()))


def scheduler_loop() -> None:
    while not _stop.is_set():
        try:
            now = clock.now()
            now_s = clock.fmt(now)
            grace = int(store.get_settings().get("grace_seconds", 300))
            for rule in store.due_rules(now_s):
                rid = rule["id"]
                try:
                    snooze_due = bool(rule.get("snooze_until")) and rule["snooze_until"] <= now_s
                    cron_due = bool(rule.get("next_run_at")) and rule["next_run_at"] <= now_s
                    sent = False

                    if snooze_due:
                        _sender.submit(rule, "snooze")
                        store.set_snooze(rid, None)
                        sent = True

                    if cron_due:
                        if not sent:
                            late = clock.seconds_late(now, rule["next_run_at"])
                            if late <= grace:
                                _sender.submit(rule, "schedule")
                            else:
                                store.add_log(rid, rule["title"], "scheduler", False,
                                              f"错过触发（迟到 {int(late)} 秒，已跳过）")
                        store.set_next(rid, schedule_next(rule), fired=True)
                except Exception as e:  # noqa: BLE001
                    store.add_log(rid, rule["title"], "scheduler", False, f"{type(e).__name__}: {e}")
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        _stop.wait(TICK_SECONDS)


# ---------------------------------------------------------------- HTTP

def _json(handler, obj, code=200):
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    try:
        handler.send_response(code)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(data)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(data)
    except (ConnectionError, OSError):
        # 浏览器刷新/关闭导致的断连，不值得打一屏堆栈
        pass


class Handler(BaseHTTPRequestHandler):
    server_version = f"FnnasReminder/{APP_VERSION}"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    # ---------- 基础 ----------
    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        if n > MAX_BODY_BYTES:
            raise ValueError(f"请求体过大（{n} 字节，上限 {MAX_BODY_BYTES}）")
        raw = self.rfile.read(n).decode("utf-8", "ignore")
        try:
            return json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            return {}

    def _authorized(self, query: dict) -> bool:
        if not ADMIN_TOKEN:
            return True
        provided = self.headers.get("X-Admin-Token") or ""
        # 恒定时间比较：避免用响应时间逐字节猜口令
        if provided and hmac.compare_digest(provided, ADMIN_TOKEN):
            return True
        # 兼容 ?token=xxx（方便 curl / 浏览器直接打开），但这种方式会留在访问日志和
        # 浏览器历史里，正式环境请用 X-Admin-Token 头。
        return any(hmac.compare_digest(t or "", ADMIN_TOKEN) for t in query.get("token", []))

    def _guard(self, path: str) -> bool:
        """只保护 /api/*（health 除外）；静态页面照常返回，由前端提示输入口令。"""
        if not ADMIN_TOKEN or path == "/api/health" or not path.startswith("/api/"):
            return True
        query = parse_qs(urlparse(self.path).query)
        if self._authorized(query):
            return True
        _json(self, {"error": "需要访问口令", "need_token": True}, 401)
        return False

    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        try:
            if not self._guard(path):
                return
            if path == "/api/health":
                return _json(self, {
                    "ok": True, "time": clock.now_str(), "tz": clock.TZ_NAME,
                    "version": APP_VERSION, "queue": _sender.pending(),
                    "auth": bool(ADMIN_TOKEN),
                })
            if path == "/api/rules":
                return _json(self, self._rules_with_meta())
            if path == "/api/logs":
                return _json(self, store.list_logs(120))
            if path == "/api/settings":
                return _json(self, _mask(store.get_settings()))
            if path == "/api/template":
                settings = store.get_settings()
                return _json(self, {
                    "dingtalk": template.current(settings, "dingtalk"),
                    "text": template.current(settings, "text"),
                    "defaults": {k: template.default_for(k) for k in template.FORMATS},
                    "labels": {k: template.label_for(k) for k in template.FORMATS},
                    "vars": [{"name": n, "desc": d} for n, d in template.VARS],
                })
            return self._static(path)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return _json(self, {"error": str(e)}, 500)

    def do_POST(self):  # noqa: N802
        return self._write("POST")

    def do_PUT(self):  # noqa: N802
        return self._write("PUT")

    def do_DELETE(self):  # noqa: N802
        path = urlparse(self.path).path
        if not self._guard(path):
            return
        m = re.fullmatch(r"/api/rules/(\d+)", path)
        try:
            if m:
                store.delete_rule(int(m.group(1)))
                return _json(self, {"ok": True})
            return _json(self, {"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            return _json(self, {"error": str(e)}, 500)

    # ---------- 业务 ----------
    def _write(self, method: str):
        path = urlparse(self.path).path
        if not self._guard(path):
            return
        # 只收 application/json：跨站表单发不出这个 Content-Type（浏览器会先发 preflight，
        # 而我们不返回 CORS 头，请求就停在预检），顺带挡掉最朴素的 CSRF。
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if (self.headers.get("Content-Length") or "0") != "0" and ctype != "application/json":
            self.close_connection = True   # body 没读完，别让 keep-alive 串到下一个请求
            return _json(self, {"error": "Content-Type 必须是 application/json"}, 415)
        try:
            payload = self._body()
        except ValueError as e:
            self.close_connection = True
            return _json(self, {"error": str(e)}, 413)
        try:
            if path == "/api/settings":
                payload = _unmask(payload)
                return _json(self, {"ok": True, "settings": _mask(store.save_settings(payload))})

            if path == "/api/settings/test":
                return self._test_channel(payload)

            if path == "/api/template/preview":
                return self._preview(payload)

            if re.fullmatch(r"/api/rules", path) and method == "POST":
                data = self._payload_rule(payload)
                rid = store.create_rule(data["title"], data["body"], data["rule"],
                                        data["channels"], data["enabled"], data["next_run_at"])
                return _json(self, store.get_rule(rid))

            m = re.fullmatch(r"/api/rules/(\d+)/(test|toggle|skip|snooze)", path)
            if m:
                rid = int(m.group(1))
                rule = store.get_rule(rid)
                if not rule:
                    return _json(self, {"error": "规则不存在"}, 404)
                return self._action(m.group(2), rid, rule, payload)

            m = re.fullmatch(r"/api/rules/(\d+)", path)
            if m and method == "PUT":
                rid = int(m.group(1))
                if not store.get_rule(rid):
                    return _json(self, {"error": "规则不存在"}, 404)
                return self._update(rid, payload)

            return _json(self, {"error": "not found"}, 404)
        except ValueError as e:
            return _json(self, {"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return _json(self, {"error": f"{type(e).__name__}: {e}"}, 500)

    def _action(self, action: str, rid: int, rule: dict, payload: dict):
        if action == "test":
            results = dispatcher.deliver(rule, "manual")
            return _json(self, {
                "ok": True,
                "results": results,
                "message": notify.summarize(results),
                "any_failed": any(r["status"] == "failed" for r in results),
            })
        elif action == "skip":
            nxt = schedule_next(rule)
            store.set_next(rid, nxt)
            store.add_log(rid, rule["title"], "manual", True, "手动跳过本次")
            return _json(self, {"ok": True, "next_run_at": nxt})
        elif action == "snooze":
            raw = payload.get("minutes", 15)
            try:
                minutes = int(raw)
            except (TypeError, ValueError):
                raise ValueError("稍后提醒的分钟数必须是整数")
            if not 1 <= minutes <= 1440:
                raise ValueError("稍后提醒的分钟数应在 1–1440 之间")
            until = clock.fmt(clock.after(minutes=minutes))
            store.set_snooze(rid, until)
            return _json(self, {"ok": True, "snooze_until": until})
        else:  # toggle
            enabled = not rule["enabled"]
            nxt = schedule_next(rule) if enabled else None
            store.update_rule(rid, enabled=enabled, next_run_at=nxt)
            return _json(self, {"ok": True, "enabled": enabled, "next_run_at": nxt})
        return _json(self, {"error": f"未知操作：{action}"}, 400)

    def _test_channel(self, payload: dict):
        name = notify.ALIASES.get(str(payload.get("channel") or ""), str(payload.get("channel") or ""))
        ch = notify.REGISTRY.get(name)
        if not ch:
            return _json(self, {"ok": False, "detail": "未知通道"})
        settings = store.get_settings()
        cfg = notify.cfg_for(settings, name)
        reason = ch.check(cfg)
        if reason:
            # 配置缺项直接说清楚，不去连网络、不重试
            return _json(self, {"ok": False, "detail": reason, "channel": name, "label": ch.label})
        content = ("这是一封测试邮件，收到即表示 SMTP 配置正确。" if name == "email"
                   else f"这是一条测试消息，收到即表示{ch.label}配置正确。")
        # 测试消息也走模板，用户在测试里看到的排版 = 以后真实提醒的排版
        ctx = template.sample_context("定时提醒 · 测试", content)
        ctx["channels"] = ch.label
        title, body = template.render(settings, ch.text_format, ctx)
        try:
            ok, detail = ch.send(cfg, title, body)
        except notify.ChannelConfigError as e:
            ok, detail = False, str(e)
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"{type(e).__name__}: {e}"
        return _json(self, {"ok": ok, "detail": detail, "channel": name, "label": ch.label})

    def _preview(self, payload: dict):
        """模板预览：不入库，直接把渲染结果和「近似钉钉效果」的 HTML 返回给界面。"""
        fmt = str(payload.get("format") or "dingtalk")
        if fmt not in template.FORMATS:
            fmt = "dingtalk"
        settings = store.get_settings()
        ctx = template.sample_context()
        for key in ("title", "content", "time", "date", "weekday",
                    "reason", "channels", "next"):
            val = payload.get(key)
            if val is not None:
                ctx[key] = str(val)
        # rule 既接受现成的描述文本，也接受规则对象（编辑器里还没保存时传的是对象）
        rule_obj = payload.get("rule")
        if isinstance(rule_obj, dict) and rule_obj.get("type"):
            try:
                ctx["rule"] = ruleset.describe(rule_obj)
            except Exception:  # noqa: BLE001
                pass
        elif isinstance(payload.get("rule"), str):
            ctx["rule"] = payload["rule"]
        title, text = template.render(settings, fmt, ctx, override=payload.get("template"))
        return _json(self, {
            "ok": True, "format": fmt, "title": title, "text": text,
            "html": template.md_to_html(text) if fmt == "dingtalk" else "",
            "chars": len(text),
        })

    def _payload_rule(self, payload: dict) -> dict:
        rule = payload.get("rule") or {"type": "daily", "times": ["09:00"]}
        if not isinstance(rule, dict) or not rule.get("type"):
            raise ValueError("rule 必须是包含 type 的对象")
        if rule["type"] not in ("once", "daily", "weekly", "daily_window", "interval"):
            raise ValueError(f"不支持的规则类型：{rule['type']}")
        title = (payload.get("title") or "").strip()
        if not title:
            raise ValueError("标题不能为空")
        channels = notify.normalize_channels(payload.get("channels") or ["dingtalk1"])
        nxt = ruleset.next_after(rule, clock.now())
        if nxt is None:
            if rule["type"] == "once":
                raise ValueError("触发时间需晚于当前时间")
            raise ValueError("按当前设置算不出下一次触发时间，请检查时刻/间隔")
        return {
            "title": title,
            "body": (payload.get("body") or "").strip(),
            "rule": rule,
            "channels": channels,
            "enabled": bool(payload.get("enabled", True)),
            "next_run_at": clock.fmt(nxt),
        }

    def _update(self, rid: int, payload: dict):
        cur = store.get_rule(rid)
        merged = {
            "title": payload.get("title", cur["title"]),
            "body": payload.get("body", cur["body"]),
            "rule": payload.get("rule", cur["rule"]),
            "channels": payload.get("channels", cur["channels"]),
            "enabled": payload.get("enabled", cur["enabled"]),
        }
        data = self._payload_rule(merged)
        store.update_rule(rid, title=data["title"], body=data["body"], rule=data["rule"],
                          channels=data["channels"], enabled=data["enabled"],
                          next_run_at=data["next_run_at"])
        return _json(self, store.get_rule(rid))

    def _rules_with_meta(self):
        out = []
        for r in store.list_rules():
            r["desc"] = ruleset.describe(r["rule"])
            out.append(r)
        return out

    # ---------- 静态文件 ----------
    def _static(self, path: str):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        rel = posixpath.normpath(rel).replace("\\", "/")
        base = os.path.realpath(STATIC_DIR)
        # 用 realpath 收口而不是只判 ".."：这样符号链接、Windows 下的怪异分隔符
        # 都逃不出 static/ 目录。
        full = os.path.realpath(os.path.join(base, *rel.split("/")))
        if full != base and not full.startswith(base + os.sep):
            return _json(self, {"error": "forbidden"}, 403)
        if not os.path.isfile(full):
            return _json(self, {"error": "not found"}, 404)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            data = f.read()
        try:
            self.send_response(200)
            self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") or ctype == "application/javascript" else ""))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)
        except (ConnectionError, OSError):
            pass


def _mask(cfg: dict) -> dict:
    """密钥字段回传掩码，避免明文回显到浏览器。"""
    out = json.loads(json.dumps(cfg))
    for block, fields in _SECRET_FIELDS.items():
        sec = out.get(block) or {}
        for f in fields:
            if sec.get(f):
                sec[f] = "******"
                sec[f"_has_{f}"] = True
    return out


def _unmask(patch: dict) -> dict:
    """前端把掩码原样提交回来时，视作「不修改」。"""
    out = json.loads(json.dumps(patch or {}))
    for block, fields in _SECRET_FIELDS.items():
        sec = out.get(block) or {}
        for f in fields:
            if sec.get(f) in (None, "", "******"):
                sec.pop(f, None)
            sec.pop(f"_has_{f}", None)
        if sec or block in out:
            out[block] = sec
    return out


def main() -> None:
    store.init()
    for r in store.list_rules():
        store.set_next(r["id"], schedule_next(r) if r["enabled"] else None)

    _sender.start()
    threading.Thread(target=scheduler_loop, daemon=True, name="scheduler").start()

    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    print(f"[fnnas-reminder] {APP_VERSION} listening on 0.0.0.0:{PORT}", flush=True)
    print(f"[fnnas-reminder] tz={clock.TZ_NAME} now={clock.now_str()} data={store.DB_PATH}", flush=True)
    print(f"[fnnas-reminder] auth={'token' if ADMIN_TOKEN else 'open(内网)'}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _stop.set()
        srv.server_close()


if __name__ == "__main__":
    main()
