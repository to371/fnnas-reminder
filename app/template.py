"""消息模板：把「规则 + 触发信息」渲染成钉钉 Markdown / 邮件纯文本。

为什么要单独一层：
- 钉钉群机器人（msgtype=markdown）能渲染标题、加粗、引用、列表、链接，
  以及 `<font color=#RRGGBB>文字</font>` 颜色；emoji 直接就是普通字符。
- 邮件是纯文本，同一份内容必须去掉 `###`、`**`、`<font>` 这些标记，
  否则收件人看到的是满屏符号。

所以模板分两套（settings.template.dingtalk / .text），由通道自己声明用哪套
（BaseChannel.text_format），一处渲染、各端得体。

模板里的占位符写 `{name}`，见 VARS；写错的占位符**原样保留**，
用户在预览里一眼就能看出拼错了，而不是被静默吞掉。
"""
from __future__ import annotations

import html
import re

# 钉钉单条消息正文上限约 20000 字节，留足余量截断，避免整条被拒
MAX_MD_CHARS = 8000
MAX_TEXT_CHARS = 8000

# 默认模板：emoji 打头 + 灰色小字元信息，接近一张「信息卡」的样子。
# <font color=#…> 必须是**六位色值、成对闭合、不加引号**，否则部分客户端会显示裸标签。
DEFAULT_MD = """### 🔔 {title}

{content}

<font color=#8f959e>🗓 触发规则：{rule}</font>
<font color=#8f959e>⏰ 本次时间：{time}</font>
<font color=#8f959e>📮 发送方式：{reason}</font>"""

DEFAULT_TEXT = """{title}

{content}

触发规则：{rule}
时间：{time}
方式：{reason}"""

FORMATS = {
    "dingtalk": ("钉钉（Markdown）", DEFAULT_MD),
    "text": ("邮件（纯文本）", DEFAULT_TEXT),
}

# 界面上的变量说明表：名字 -> 说明
VARS = [
    ("title", "提醒标题"),
    ("content", "提醒内容（原样直通，可写 Markdown / emoji）"),
    ("time", "本次时间，如 2026-10-23 09:00:00"),
    ("date", "日期，如 2026-10-23"),
    ("weekday", "星期，如 星期五"),
    ("rule", "触发规则的中文描述"),
    ("reason", "触发方式：定时触发 / 稍后提醒 / 手动试发"),
    ("channels", "本次发送的通道，如 钉钉机器人1"),
    ("next", "下一次触发时间"),
]

_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


def default_for(fmt: str) -> str:
    return FORMATS.get(fmt, FORMATS["dingtalk"])[1]


def label_for(fmt: str) -> str:
    return FORMATS.get(fmt, FORMATS["dingtalk"])[0]


def current(settings: dict, fmt: str) -> str:
    """取用户配置的模板；留空表示「跟随内置默认」——这样以后升级默认模板，
    没动过手的老用户也能拿到新样式。"""
    block = (settings or {}).get("template") or {}
    tpl = str(block.get(fmt) or "").strip()
    return tpl or default_for(fmt)


def render_text(tpl: str, ctx: dict) -> str:
    """占位符替换。只替换一层，替换进去的内容里即使有 `{}` 也不会被二次展开。"""
    tpl = (tpl or "").replace("\\n", "\n")  # 允许在单行输入框里写 \n 表示换行

    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key in ctx:
            v = ctx.get(key)
            return "" if v is None else str(v)
        return m.group(0)

    out = _PLACEHOLDER.sub(sub, tpl)
    out = out.replace("\r\n", "\n")
    out = re.sub(r"[ \t]+\n", "\n", out)      # 行尾空白
    out = re.sub(r"\n{3,}", "\n\n", out)      # 内容为空时别留一大堆空行
    return out.strip()


_LIST_RE = re.compile(r"^\s{0,3}(?:[-*+]|\d{1,3}[.)])\s+")


def _is_fence(line: str) -> bool:
    return line.strip().startswith("```")


def harden_breaks(text: str) -> str:
    """把「单换行」升级成「空行分段」——钉钉的 markdown 渲染器只认空行。

    钉钉群里 `a\\nb` 会渲染成同一段（手机上就是挤成一行，看起来像没换行），
    必须写成 `a\\n\\nb` 才是两段。所以这里逐行扫一遍，给相邻的两行普通文字
    之间补一个空行；**列表项之间不补**（`- a` / `- b` 本来就各占一行），
    代码块内原样保留。

    一句话：用户/模板里按人眼习惯敲的回车，在这里都变成真正生效的换行。
    """
    lines = (text or "").replace("\r\n", "\n").split("\n")
    out: list[str] = []
    in_code = False
    for i, line in enumerate(lines):
        if _is_fence(line):
            in_code = not in_code
            out.append(line)
            continue
        out.append(line)
        if in_code:
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        if nxt is None:
            continue
        if not line.strip() or not nxt.strip():
            continue                      # 已经有空行，别叠加
        if _LIST_RE.match(line) and _LIST_RE.match(nxt):
            continue                      # 连续列表项：各自成行，保持紧凑
        out.append("")
    return "\n".join(out)


def clip(text: str, limit: int = MAX_MD_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n\n…（内容过长已截断）"


def to_plain(text: str) -> str:
    """Markdown -> 纯文本（给邮件用）。"""
    out = re.sub(r"</?\s*font[^>]*>", "", text or "", flags=re.I)
    out = re.sub(r"^\s{0,3}#{1,6}\s*", "", out, flags=re.M)
    out = re.sub(r"^\s{0,3}>\s?", "", out, flags=re.M)
    out = re.sub(r"^\s{0,3}[-*+]\s+", "· ", out, flags=re.M)
    out = re.sub(r"\*\*(.+?)\*\*", r"\1", out, flags=re.S)
    out = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"\1", out, flags=re.S)
    out = re.sub(r"~~(.+?)~~", r"\1", out, flags=re.S)
    out = re.sub(r"`([^`]+)`", r"\1", out)
    out = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", r"[图片] \1 \2", out)
    out = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r"\1（\2）", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def render(settings: dict, fmt: str, ctx: dict, override: str | None = None) -> tuple[str, str]:
    """返回 (标题, 正文)。override 用于「预览」时不落库地试一个模板。"""
    tpl = override if (override is not None and str(override).strip()) else current(settings, fmt)
    text = render_text(tpl, ctx)
    title = str(ctx.get("title") or "提醒")
    if fmt == "text":
        # 邮件是纯文本，单换行本来就生效，不用补空行
        text = to_plain(text)
        return to_plain(title) or "提醒", clip(text, MAX_TEXT_CHARS)
    return title, clip(harden_breaks(text), MAX_MD_CHARS)


def sample_context(title: str = "定时提醒 · 测试", content: str = "") -> dict:
    import clock

    now = clock.now()
    return {
        "title": title,
        "content": content or "这是一条测试消息，收到即表示配置正确。",
        "time": clock.fmt(now),
        "date": now.strftime("%Y-%m-%d"),
        "weekday": "星期" + "一二三四五六日"[now.weekday()],
        "rule": "每天 09:00–18:00 内每 1 小时提醒",
        "reason": "手动试发",
        "channels": "钉钉机器人1",
        "next": "—",
    }


# ---------------- 预览用的极简 Markdown -> HTML ----------------
# 只服务界面上的气泡预览：把「钉钉大概长什么样」还原出来。
# 先整体转义，再放行 <font color=#hex>，因此模板里写 <script> 也不会被渲染。

_ESC_FONT_OPEN = re.compile(
    r"&lt;\s*font\s+color\s*=\s*(?:&quot;|&#x27;|\"|')?(#[0-9a-fA-F]{3,8})(?:&quot;|&#x27;|\"|')?\s*&gt;",
    re.I,
)
_ESC_FONT_CLOSE = re.compile(r"&lt;\s*/\s*font\s*&gt;", re.I)


def _inline(escaped: str) -> str:
    s = _ESC_FONT_OPEN.sub(r'<span style="color:\1">', escaped)
    s = _ESC_FONT_CLOSE.sub("</span>", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", s)
    s = re.sub(r"~~(.+?)~~", r"<s>\1</s>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', s)
    return s


def md_to_html(text: str) -> str:
    lines = (text or "").replace("\r\n", "\n").split("\n")
    out: list[str] = []
    for raw in lines:
        line = html.escape(raw, quote=False)
        stripped = line.strip()
        if not stripped:
            out.append('<div class="sp"></div>')
            continue
        m = re.match(r"^#{1,6}\s*(.*)$", stripped)
        if m:
            lvl = min(len(stripped) - len(stripped.lstrip("#")), 3)
            out.append(f'<div class="h h{lvl}">{_inline(m.group(1))}</div>')
            continue
        if stripped.startswith("&gt;"):
            body = re.sub(r"^(&gt;\s?)+", "", stripped)
            out.append(f'<div class="quote">{_inline(body)}</div>')
            continue
        if re.match(r"^[-*+]\s+", stripped):
            body = re.sub(r"^[-*+]\s+", "", stripped)
            out.append(f'<div class="li">• {_inline(body)}</div>')
            continue
        out.append(f'<div class="p">{_inline(stripped)}</div>')
    return "".join(out)
