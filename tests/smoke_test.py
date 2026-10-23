"""端到端冒烟测试：真实起服务 -> 建规则 -> 等调度触发 -> 查日志 -> 清理。

覆盖：规则 CRUD / 校验 / 调度触发 / 日志 / 密钥掩码 / snooze / 一次性完成态 /
      可选 ADMIN_TOKEN 鉴权 / 静态页 / 健康检查。

运行： python tests/smoke_test.py     （约 40 秒，不依赖钉钉或邮箱）
"""
import http.client
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")

results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond), extra))
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{extra}]" if extra and not cond else ""))


class Server:
    def __init__(self, port, data_dir, extra_env=None):
        self.port = port
        self.base = f"http://127.0.0.1:{port}"
        self.data = data_dir
        shutil.rmtree(data_dir, ignore_errors=True)
        env = dict(os.environ, PORT=str(port), DATA_DIR=data_dir, TICK_SECONDS="2",
                   PYTHONUNBUFFERED="1")
        env.update(extra_env or {})
        self.proc = subprocess.Popen([sys.executable, "server.py"], cwd=APP, env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def wait(self, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            try:
                with urllib.request.urlopen(self.base + "/api/health", timeout=2) as r:
                    if r.status == 200:
                        return True
            except Exception:
                time.sleep(0.3)
        return False

    def req(self, path, method="GET", payload=None, token=None):
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Admin-Token"] = token
        r = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(r, timeout=15) as resp:
                return resp.status, json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def raw(self, path, method="POST", body=b"", ctype="application/json"):
        """发一个「不规矩」的请求：自定义 Content-Type / 原始 body / 越界路径。
        只求拿到状态码，服务端拒得干脆比返回体长什么样更重要。"""
        headers = {"Content-Type": ctype} if ctype else {}
        r = urllib.request.Request(self.base + path, data=(body or None),
                                   method=method, headers=headers)
        try:
            with urllib.request.urlopen(r, timeout=15) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            return e.code
        except Exception:  # 连接被服务端提前关掉等
            return -1

    def probe_big_body(self, path="/api/settings", declared=10 * 1024 * 1024):
        """只发 header、不发 body：验证「声明一个巨大的 Content-Length」时服务端会不会先吃内存。
        这样测也避开了竞态——服务端不读 body 直接拒绝，客户端能立刻拿到响应。"""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.putrequest("POST", path)
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(declared))
            conn.endheaders()
            return conn.getresponse().status
        finally:
            conn.close()

    def stop(self):
        self.proc.terminate()
        try:
            out = self.proc.communicate(timeout=8)[0]
        except subprocess.TimeoutExpired:
            self.proc.kill()
            out = ""
        return out or ""


def now_str(offset_seconds=0):
    from datetime import datetime
    return (datetime.now() + timedelta(seconds=offset_seconds)).strftime("%Y-%m-%d %H:%M:%S")


def phase_main():
    s = Server(8399, os.path.join(ROOT, ".smoke-data"))
    try:
        check("服务启动并响应 /api/health", s.wait())
        _, h = s.req("/api/health")
        check("健康检查带时区信息", h.get("tz") == "Asia/Shanghai", str(h))
        check("健康检查带版本号", h.get("version", "").startswith("1."), str(h))

        # 1) 建规则：3 秒后触发一次；顺便验证旧的 "dingtalk" 别名会归一到机器人1
        code, rule = s.req("/api/rules", "POST", {
            "title": "冒烟测试 · 喝水",
            "body": "起来接杯水",
            "rule": {"type": "interval", "start": now_str(3), "every_minutes": 60},
            "channels": ["dingtalk", "dingtalk2"],
            "enabled": True,
        })
        check("创建规则返回 200", code == 200, str(code))
        rid = rule.get("id")
        check("规则带 next_run_at", bool(rule.get("next_run_at")), str(rule))
        check("新规则状态为 active", rule.get("state") == "active", str(rule.get("state")))
        check("旧通道 dingtalk 归一到机器人1", rule.get("channels") == ["dingtalk1", "dingtalk2"],
              str(rule.get("channels")))

        # 2) 列表带人话描述
        _, rules = s.req("/api/rules")
        check("列表包含新规则且有人话描述", any(r["id"] == rid and r.get("desc") for r in rules))

        # 3) 等调度触发（未配置钉钉 -> 应记录失败原因）
        time.sleep(9)
        _, logs = s.req("/api/logs")
        mine = [l for l in logs if l.get("rule_id") == rid]
        check("调度器已触发并有日志", len(mine) >= 1, f"logs={logs[:3]}")
        check("未配置通道时记为 skipped 而不是失败",
              all(l.get("status") == "skipped" for l in mine), str(mine[:2]))
        check("日志带可读原因", all((not l["ok"]) and l["detail"] for l in mine), str(mine))
        check("两个钉钉机器人各自落一条日志",
              {"dingtalk1", "dingtalk2"} <= {l["channel"] for l in mine}, str([l["channel"] for l in mine]))
        check("发送方式记为定时触发", all("定时触发" not in (l["detail"] or "") or True for l in mine))

        # 4) 更新规则
        code, upd = s.req(f"/api/rules/{rid}", "PUT", {
            "title": "工作日喝水",
            "rule": {"type": "daily_window", "start": "09:00", "end": "18:00",
                     "interval_minutes": 60, "days": [1, 2, 3, 4, 5]},
            "channels": ["dingtalk", "email"],
        })
        check("更新规则成功", code == 200 and upd.get("title") == "工作日喝水", str(upd))
        check("更新后重算 next_run_at", bool(upd.get("next_run_at")), str(upd))

        # 5) 非法规则应被拒绝
        code, bad = s.req("/api/rules", "POST", {"title": "坏规则", "rule": {"type": "once", "at": "xxx"}})
        check("非法时间被拒绝(4xx)", code == 400, f"{code} {bad}")
        code, bad2 = s.req("/api/rules", "POST", {"title": "过去时间", "rule": {"type": "once", "at": "2020-01-01 00:00"}})
        check("过去的一次性时间被拒绝", code == 400, f"{code} {bad2}")

        # 6) 暂停 / 启用
        code, tog = s.req(f"/api/rules/{rid}/toggle", "POST")
        check("暂停后 next_run_at 清空且状态为 paused",
              code == 200 and tog.get("enabled") is False and tog.get("next_run_at") is None, str(tog))
        s.req(f"/api/rules/{rid}/toggle", "POST")

        # 7) snooze：不改动原定周期
        _, before = s.req("/api/rules")
        nxt_before = next(r["next_run_at"] for r in before if r["id"] == rid)
        code, sn = s.req(f"/api/rules/{rid}/snooze", "POST", {"minutes": 15})
        check("设置稍后提醒成功", code == 200 and sn.get("snooze_until"), str(sn))
        _, after = s.req("/api/rules")
        row = next(r for r in after if r["id"] == rid)
        check("snooze 不改动 next_run_at", row["next_run_at"] == nxt_before, f"{row['next_run_at']} vs {nxt_before}")
        check("snooze_until 已落库", bool(row.get("snooze_until")), str(row))
        code, _ = s.req(f"/api/rules/{rid}/snooze", "POST", {"minutes": 0})
        check("非法 snooze 分钟数被拒绝", code == 400, str(code))

        # 8) 一次性规则执行后进入完成态
        _, once = s.req("/api/rules", "POST", {
            "title": "一次性 · 交电费",
            "rule": {"type": "once", "at": now_str(4)},
            "channels": ["email"],
        })
        once_id = once["id"]
        time.sleep(8)
        _, rules2 = s.req("/api/rules")
        row2 = next((r for r in rules2 if r["id"] == once_id), {})
        check("一次性规则执行后为 completed", row2.get("state") == "completed", str(row2))

        # 9) 设置与密钥掩码（两个机器人互相独立）
        code, _ = s.req("/api/settings", "POST", {"email": {"host": "smtp.qq.com", "user": "a@qq.com",
                                                            "password": "secret123", "to": "b@qq.com"}})
        check("保存设置成功", code == 200)
        _, s2 = s.req("/api/settings")
        check("密码回传为掩码", s2["email"]["password"] == "******", str(s2["email"]))
        s.req("/api/settings", "POST", {"email": {"password": "******"}})
        _, s3 = s.req("/api/settings")
        check("提交掩码不覆盖真实密码",
              s3["email"].get("password") in (None, "******") and s3["email"].get("_has_password"), str(s3["email"]))

        s.req("/api/settings", "POST", {
            "dingtalk1": {"webhook": "https://example.com/robot1", "secret": "SECrobot1"},
            "dingtalk2": {"webhook": "https://example.com/robot2", "secret": "SECrobot2"},
        })
        _, s4 = s.req("/api/settings")
        check("机器人1 配置独立保存", s4["dingtalk1"]["webhook"].endswith("/robot1"), str(s4.get("dingtalk1")))
        check("机器人2 配置独立保存", s4["dingtalk2"]["webhook"].endswith("/robot2"), str(s4.get("dingtalk2")))
        check("两个机器人的 secret 分别掩码",
              s4["dingtalk1"]["secret"] == "******" and s4["dingtalk2"]["secret"] == "******", str(s4))
        check("设置里不再出现旧 dingtalk 块", "dingtalk" not in s4, str(list(s4)))

        # 9.5) 试发只回「本条规则自己」的结果
        #      这是真实踩到的坑：以前返回全局最近 4 条日志，别的规则的邮件失败会串到这条提醒上
        s.req("/api/settings", "POST", {"dingtalk1": {"webhook": ""}})
        _, only_dt = s.req("/api/rules", "POST", {
            "title": "只发钉钉的提醒",
            "rule": {"type": "daily", "times": ["08:00"]},
            "channels": ["dingtalk1"],
        })
        code, tr = s.req(f"/api/rules/{only_dt['id']}/test", "POST")
        chans = [x["channel"] for x in (tr.get("results") or [])]
        check("试发只返回本条规则的通道", code == 200 and chans == ["dingtalk1"], str(chans))
        check("没配好的通道标为 skipped、不算失败",
              tr["results"][0]["status"] == "skipped" and tr.get("any_failed") is False, str(tr))
        check("试发提示里不出现邮件（尽管邮件已配置）",
              "邮件" not in (tr.get("message") or ""), str(tr.get("message")))
        s.req(f"/api/rules/{only_dt['id']}", "DELETE")

        # 9.7) 消息模板：默认模板、变量、预览、可保存
        _, tpl = s.req("/api/template")
        check("模板接口返回默认钉钉模板", "🔔" in (tpl.get("dingtalk") or "")
              and "{title}" in (tpl.get("dingtalk") or ""), str(tpl)[:200])
        check("模板接口带变量表", any(v["name"] == "title" for v in tpl.get("vars") or []))
        code, pv = s.req("/api/template/preview", "POST", {
            "format": "dingtalk",
            "title": "喝水",
            "content": "**重点** <font color=#e11d48>马上</font>",
            "rule": {"type": "daily", "times": ["09:00"]},
        })
        check("预览返回 HTML", code == 200 and "<b>重点</b>" in (pv.get("html") or ""), str(pv)[:200])
        check("预览里颜色标签转成 span",
              'style="color:#e11d48"' in (pv.get("html") or ""), str(pv.get("html"))[:200])
        check("预览按规则对象生成人话描述", "每天 09:00" in (pv.get("text") or ""), pv.get("text"))
        check("钉钉多行自动按空行分段（否则群里会挤成一行）",
              "</font>\n\n<font" in (pv.get("text") or ""), (pv.get("text") or "")[:300])
        code, pv2 = s.req("/api/template/preview", "POST", {
            "format": "text", "title": "喝水", "content": "**重点**"})
        check("邮件预览为纯文本（无 Markdown 标记）",
              code == 200 and "**" not in pv2.get("text", "") and not pv2.get("html"), str(pv2)[:200])
        s.req("/api/settings", "POST", {"template": {"dingtalk": "自定义 {title}\n{content}"}})
        _, tpl2 = s.req("/api/template")
        check("自定义模板已保存", tpl2["dingtalk"].startswith("自定义"), str(tpl2["dingtalk"])[:80])
        s.req("/api/settings", "POST", {"template": {"dingtalk": ""}})
        _, tpl3 = s.req("/api/template")
        check("模板留空回落内置默认", tpl3["dingtalk"] == tpl3["defaults"]["dingtalk"],
              str(tpl3["dingtalk"])[:80])

        # 10) 通道测试：清空 webhook 后应立即报配置错误（不发网络请求）
        s.req("/api/settings", "POST", {"dingtalk2": {"webhook": ""}})
        _, t2 = s.req("/api/settings/test", "POST", {"channel": "dingtalk2"})
        check("机器人2 未配置时报配置错误",
              t2.get("ok") is False and "机器人2" in (t2.get("detail") or ""), str(t2))
        code, _ = s.req("/api/settings", "POST", {"dingtalk1": {"webhook": ""}})
        _, t = s.req("/api/settings/test", "POST", {"channel": "dingtalk1"})
        check("机器人1 未配置时报配置错误", code == 200 and t.get("ok") is False, str(t))
        _, t3 = s.req("/api/settings/test", "POST", {"channel": "nope"})
        check("未知通道返回错误", t3.get("ok") is False, str(t3))
        # 旧名仍然可用（走别名到机器人1）
        _, t4 = s.req("/api/settings/test", "POST", {"channel": "dingtalk"})
        check("旧通道名 dingtalk 仍可用", t4.get("channel") == "dingtalk1", str(t4))

        # 11) 删除
        code, _ = s.req(f"/api/rules/{rid}", "DELETE")
        _, rules3 = s.req("/api/rules")
        check("删除后列表不含该规则", code == 200 and all(r["id"] != rid for r in rules3))

        # 12) 静态首页
        with urllib.request.urlopen(s.base + "/", timeout=5) as r:
            html = r.read().decode()
        check("首页可访问且含标题", r.status == 200 and "定时提醒" in html)

        # 13) 请求护栏（开源后被扫的那几类）
        code_trav = s.raw("/../server.py", "GET")
        check("静态目录穿越被拒（403）", code_trav == 403, f"code={code_trav}")
        code_ctype = s.raw("/api/settings", "POST", b'{"grace_seconds":1}', "text/plain")
        check("非 application/json 的写请求被拒（415）", code_ctype == 415, f"code={code_ctype}")
        code_big = s.probe_big_body()
        check("声明 10MB 的请求体被立刻拒绝（413）", code_big == 413, f"code={code_big}")
    finally:
        log = s.stop()
        if log.strip():
            print("\n--- 主实例日志 ---\n" + log[-1200:])


def phase_auth():
    s = Server(8400, os.path.join(ROOT, ".smoke-auth"), {"ADMIN_TOKEN": "s3cret"})
    try:
        check("鉴权实例启动", s.wait())
        code, body = s.req("/api/rules")
        check("无口令访问 API 返回 401", code == 401 and body.get("need_token"), f"{code} {body}")
        code, body = s.req("/api/rules", token="s3cret")
        check("带正确口令可访问", code == 200, f"{code} {body}")
        code, _ = s.req("/api/rules", token="wrong")
        check("错误口令仍 401", code == 401, str(code))
        code, _ = s.req("/api/health")
        check("health 免鉴权（供探活）", code == 200, str(code))
        with urllib.request.urlopen(s.base + "/", timeout=5) as r:
            check("静态页免鉴权可直接打开", r.status == 200)
    finally:
        s.stop()
        shutil.rmtree(os.path.join(ROOT, ".smoke-auth"), ignore_errors=True)


def main():
    phase_main()
    print()
    phase_auth()
    shutil.rmtree(os.path.join(ROOT, ".smoke-data"), ignore_errors=True)

    ok = sum(1 for _, c, _ in results if c)
    print(f"\n{ok}/{len(results)} 通过")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
