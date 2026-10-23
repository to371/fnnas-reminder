"""通知通道：适配器模式。

新增通道（Bark / Server酱 / 飞书 / 企业微信）只需：
    class BarkChannel(BaseChannel):
        name = "bark"
        def send(self, cfg, title, body): ...
    REGISTRY["bark"] = BarkChannel()

核心调度代码完全不用改 —— 它只认 `channels` 里的名字和 `REGISTRY`。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import smtplib
import ssl
import time
import urllib.parse
import urllib.request
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

import template


class ChannelConfigError(Exception):
    """配置类错误：重试没有意义（比如没填 webhook / 收件人），直接失败。"""


class BaseChannel:
    """通道基类：子类只需实现 send()。

    `name` 是规则里存的通道键，`label` 是界面上显示的名字，
    `settings_key` 指到设置里的配置块（同一种通道可以有多个实例，
    比如两个钉钉群机器人各自一套 webhook）。
    """

    name = ""
    label = ""
    settings_key = ""
    timeout = 20
    # 用哪套模板：md = 钉钉 Markdown，text = 邮件纯文本
    text_format = "md"

    def __init__(self, name: str | None = None, label: str | None = None,
                 settings_key: str | None = None):
        if name:
            self.name = name
        if label:
            self.label = label
        self.settings_key = settings_key or self.name

    def send(self, cfg: dict, title: str, body: str) -> tuple[bool, str]:
        raise NotImplementedError

    def check(self, cfg: dict) -> str | None:
        """配置是否可用于发送。返回 None 表示就绪，返回字符串表示原因。

        这条判断的意义：**没配的通道直接跳过，不连网络、不重试、不报错**。
        否则用户在只要钉钉的提醒里，会因为邮件没配而看到一条红色失败。
        """
        return None

    def send_with_retry(self, cfg: dict, title: str, body: str,
                        retries: int = 2, backoff: tuple[int, ...] = (5, 15)) -> tuple[bool, str]:
        """失败重试，指数退避。只阻塞调用它的工作线程。

        配置类错误立即返回——否则「没填 webhook」也要干等 20 秒才写日志，
        用户在界面上会以为消息发出去了。
        """
        last = ""
        for i in range(retries + 1):
            try:
                ok, detail = self.send(cfg, title, body)
            except ChannelConfigError as e:
                return False, str(e)
            except Exception as e:  # noqa: BLE001
                ok, detail = False, f"{type(e).__name__}: {e}"
            if ok:
                return True, detail
            last = detail
            if i < retries:
                time.sleep(backoff[min(i, len(backoff) - 1)] if backoff else 0)
        return False, (f"{last}（已重试 {retries} 次）" if retries else last)


# ---------------- 邮件 ----------------

class EmailChannel(BaseChannel):
    name = "email"
    label = "邮件"
    text_format = "text"

    def check(self, cfg: dict) -> str | None:
        host = (cfg.get("host") or "").strip()
        to = str(cfg.get("to") or "").strip()
        user = (cfg.get("user") or "").strip()
        password = cfg.get("password") or ""
        if not host:
            return "邮件未配置（缺 SMTP 服务器）"
        if not to:
            return "邮件未配置（缺收件人）"
        if user and not password:
            return "邮件未配置（缺密码 / 授权码）"
        return None

    def send(self, cfg: dict, title: str, body: str) -> tuple[bool, str]:
        host = (cfg.get("host") or "").strip()
        user = (cfg.get("user") or "").strip()
        password = cfg.get("password") or ""
        sender = (cfg.get("sender") or user).strip()
        to = [x.strip() for x in str(cfg.get("to") or "").replace(";", ",").split(",") if x.strip()]
        port = int(cfg.get("port") or (465 if cfg.get("ssl", True) else 587))

        if not host or not to:
            raise ChannelConfigError("邮件未配置完整（SMTP 服务器 / 收件人）")

        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = Header(f"[提醒] {title}", "utf-8")
        msg["From"] = formataddr((str(Header("定时提醒", "utf-8")), sender or user))
        msg["To"] = ", ".join(to)

        try:
            if cfg.get("ssl", True):
                with smtplib.SMTP_SSL(host, port, timeout=self.timeout,
                                      context=ssl.create_default_context()) as s:
                    if user:
                        s.login(user, password)
                    s.sendmail(sender or user, to, msg.as_string())
            else:
                with smtplib.SMTP(host, port, timeout=self.timeout) as s:
                    s.ehlo()
                    s.starttls(context=ssl.create_default_context())
                    s.ehlo()
                    if user:
                        s.login(user, password)
                    s.sendmail(sender or user, to, msg.as_string())
            return True, f"已发送至 {', '.join(to)}"
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"


# ---------------- 钉钉 ----------------

class DingTalkChannel(BaseChannel):
    name = "dingtalk1"
    label = "钉钉机器人1"
    timeout = 15

    @staticmethod
    def _signed_url(webhook: str, secret: str) -> str:
        webhook = webhook.strip()
        if not secret:
            return webhook
        ts = str(round(time.time() * 1000))
        digest = hmac.new(secret.encode("utf-8"), f"{ts}\n{secret}".encode("utf-8"),
                          hashlib.sha256).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(digest))
        sep = "&" if "?" in webhook else "?"
        return f"{webhook}{sep}timestamp={ts}&sign={sign}"

    def check(self, cfg: dict) -> str | None:
        webhook = (cfg.get("webhook") or "").strip()
        if not webhook:
            return f"{self.label} 未配置（缺 Webhook）"
        if not webhook.startswith("https://"):
            return f"{self.label} 的 Webhook 不是 https 地址"
        return None

    def send(self, cfg: dict, title: str, body: str) -> tuple[bool, str]:
        webhook = (cfg.get("webhook") or "").strip()
        if not webhook.startswith("https://"):
            raise ChannelConfigError(f"{self.label} 的 Webhook 未配置或不是 https 地址")

        # body 已经是渲染好的 Markdown（标题、加粗、颜色、emoji 都在里面），
        # 这里不再拼 `###`，否则用户自定义模板会多出一层标题。
        text = body or f"### {title}"
        keyword = (cfg.get("keyword") or "").strip()
        if keyword and keyword not in text:
            text += f"\n\n{keyword}"

        payload = {
            "msgtype": "markdown",
            "markdown": {"title": title or "提醒", "text": text},
            "at": {"isAtAll": False},
        }
        req = urllib.request.Request(
            self._signed_url(webhook, (cfg.get("secret") or "").strip()),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "ignore")
            data = json.loads(raw) if raw.startswith("{") else {}
            if data.get("errcode", 0) == 0:
                return True, f"{self.label} 已推送"
            return False, f"{self.label} 返回错误: {raw[:300]}"
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"


# ---------------- 注册表 ----------------

REGISTRY: dict[str, BaseChannel] = {
    "dingtalk1": DingTalkChannel("dingtalk1", "钉钉机器人1", "dingtalk1"),
    "dingtalk2": DingTalkChannel("dingtalk2", "钉钉机器人2", "dingtalk2"),
    "email": EmailChannel("email", "邮件", "email"),
}

# 旧数据兼容：v1.1 之前规则里存的是 "dingtalk"（只有一个机器人），统一并到机器人 1
ALIASES = {"dingtalk": "dingtalk1"}


def normalize_channels(names: list[str] | None) -> list[str]:
    """去重、去未知项、把历史别名迁移成现名。"""
    out: list[str] = []
    for raw in names or []:
        name = ALIASES.get(str(raw), str(raw))
        if name in REGISTRY and name not in out:
            out.append(name)
    return out


def channel_label(name: str) -> str:
    ch = REGISTRY.get(name)
    return ch.label if ch else name


def cfg_for(settings: dict, name: str) -> dict:
    """取该通道对应的配置块；未知通道返回空 dict（send 里会报配置错误）。"""
    ch = REGISTRY.get(name)
    if not ch:
        return {}
    return settings.get(ch.settings_key) or {}


def dispatch(channels: list[str], settings: dict, ctx: dict,
             retries: int = 2) -> list[dict]:
    """按规则勾选的通道逐条发送。

    `ctx` 是模板变量字典（title / content / time / rule / reason …），
    每个通道用自己的模板格式渲染：钉钉走 Markdown，邮件走纯文本。

    三种结果：
      sent    真正发出去且对端确认成功
      skipped 这个通道没配好 —— 直接跳过，不连网、不重试（用户没在「通知设置」里配它，
              就不该因为一条只发钉钉的提醒看到邮件的红色失败）
      failed  配好了但发失败（密码错、网络断、钉钉限流等），这才是需要用户处理的问题
    """
    channels = normalize_channels(channels)
    out: list[dict] = []
    for name in channels:
        ch = REGISTRY[name]
        cfg = cfg_for(settings, name)
        reason = ch.check(cfg)
        if reason:
            out.append({"channel": name, "label": ch.label, "status": "skipped",
                        "ok": False, "detail": f"{reason}，本次已跳过"})
            continue
        title, body = template.render(settings, ch.text_format, ctx)
        ok, detail = ch.send_with_retry(cfg, title, body, retries=retries)
        out.append({"channel": name, "label": ch.label,
                    "status": "sent" if ok else "failed", "ok": ok, "detail": detail})
    if not channels:
        out.append({"channel": "none", "label": "未选通道", "status": "failed",
                    "ok": False, "detail": "这条提醒没有勾选任何通知通道"})
    return out


def summarize(results: list[dict]) -> str:
    """把 dispatch 结果压成一句人话，供「试发」提示用。"""
    sent = [r["label"] for r in results if r["status"] == "sent"]
    skipped = [r for r in results if r["status"] == "skipped"]
    failed = [r for r in results if r["status"] == "failed"]
    parts: list[str] = []
    if sent:
        parts.append("已发送：" + "、".join(sent))
    if skipped:
        parts.append("、".join(f"{r['label']}未配置已跳过" for r in skipped))
    if failed:
        parts.append("、".join(f"{r['label']}失败：{r['detail']}" for r in failed))
    return "；".join(parts) or "没有可发送的通道"
