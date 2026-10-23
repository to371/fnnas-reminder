"""发送调度：队列 + 工作线程。

为什么不让调度线程直接发：
SMTP 握手/超时最长 20 秒，钉钉也可能卡 15 秒。若在调度 tick 里同步发送，
一次网络抖动就会让整个 tick 推迟，后续规则全部延后。所以：
  调度线程只做「判定到期 + 入队」，真正发送交给固定数量的工作线程。
"""
from __future__ import annotations

import queue
import threading

import clock
import notify
import rules as ruleset
import store

REASON_CN = {"schedule": "定时触发", "snooze": "稍后提醒", "manual": "手动试发"}


def build_context(rule: dict, reason: str = "schedule") -> dict:
    """组装模板变量。

    时间在入队时就固定下来：真正的发送可能排在工作线程里晚几秒，
    但「本次提醒是几点该响的」应该以调度判定那一刻为准。
    """
    now = clock.now()
    channels = [notify.channel_label(c) for c in rule.get("channels") or []]
    return {
        "title": rule.get("title") or "提醒",
        "content": (rule.get("body") or "").strip(),
        "time": clock.fmt(now),
        "date": now.strftime("%Y-%m-%d"),
        "weekday": "星期" + "一二三四五六日"[now.weekday()],
        "rule": ruleset.describe(rule.get("rule", {})),
        "reason": REASON_CN.get(reason, reason),
        "channels": "、".join(channels) or "未选通道",
        "next": rule.get("next_run_at") or "无",
    }


def deliver(rule: dict, reason: str = "schedule", retries: int = 2) -> list[dict]:
    """同步发送一条（试发、或工作线程内部调用）。结果逐通道落库并**返回本次结果**。

    返回值必须是本次发送自己的结果——以前「试发」接口返回的是全局最近 4 条日志，
    会把别的规则的历史失败（比如邮件密码错）当成这条提醒的失败显示给用户。
    """
    settings = store.get_settings()
    ctx = build_context(rule, reason)
    results = notify.dispatch(rule.get("channels", []), settings, ctx, retries=retries)
    for r in results:
        store.add_log(rule["id"], ctx["title"], r["channel"], r["ok"], r["detail"], status=r["status"])
    return results


class Dispatcher:
    def __init__(self, workers: int = 2, retries: int = 2, maxsize: int = 500):
        self.q: queue.Queue = queue.Queue(maxsize=maxsize)
        self.workers = workers
        self.retries = retries
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        for i in range(self.workers):
            threading.Thread(target=self._loop, name=f"sender-{i}", daemon=True).start()
        self._started = True

    def pending(self) -> int:
        return self.q.qsize()

    def submit(self, rule: dict, reason: str = "schedule") -> None:
        try:
            self.q.put_nowait((rule, reason))
        except queue.Full:
            store.add_log(rule["id"], rule["title"], "queue", False, "发送队列已满（500），本次提醒被丢弃")

    def _loop(self) -> None:
        while True:
            rule, reason = self.q.get()
            try:
                deliver(rule, reason, retries=self.retries)
            except Exception as e:  # noqa: BLE001
                store.add_log(rule.get("id"), rule.get("title", "?"), "dispatcher", False,
                              f"{type(e).__name__}: {e}")
            finally:
                self.q.task_done()
