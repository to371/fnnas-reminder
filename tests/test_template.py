"""消息模板测试：Markdown 直通、占位符、颜色标签、纯文本降级、预览 HTML。"""
import os
import sys
import unittest

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
sys.path.insert(0, APP)

import dispatcher  # noqa: E402
import notify  # noqa: E402
import template  # noqa: E402

CTX = {
    "title": "起来喝水",
    "content": "记得接水，顺便看看窗外。",
    "time": "2026-10-23 17:00:00",
    "date": "2026-10-23",
    "weekday": "星期五",
    "rule": "每天 09:00–18:00 内每 1 小时提醒",
    "reason": "定时触发",
    "channels": "钉钉机器人1",
    "next": "2026-10-23 18:00:00",
}


class TestRender(unittest.TestCase):

    def test_default_md_has_emoji_and_all_fields(self):
        title, text = template.render({}, "dingtalk", CTX)
        self.assertEqual(title, "起来喝水")
        for token in ("### 🔔 起来喝水", "🗓", "⏰", "📮",
                      CTX["rule"], CTX["time"], CTX["reason"]):
            self.assertIn(token, text)
        self.assertNotIn("{", text.replace("<font", ""), "不应残留占位符")

    def test_content_markdown_passthrough(self):
        """用户在正文里写的 Markdown / 颜色标签必须原样进钉钉，不能被转义。"""
        ctx = dict(CTX, content="**重点**：<font color=#e11d48>马上做</font> 💧")
        _, text = template.render({}, "dingtalk", ctx)
        self.assertIn("**重点**", text)
        self.assertIn("<font color=#e11d48>马上做</font>", text)

    def test_unknown_placeholder_kept(self):
        _, text = template.render({}, "dingtalk", CTX, override="标题 {title} / 未知 {nope}")
        self.assertIn("标题 起来喝水", text)
        self.assertIn("{nope}", text, "拼错的占位符应原样显示，方便用户发现")

    def test_empty_content_collapses_blank_lines(self):
        _, text = template.render({}, "dingtalk", dict(CTX, content=""))
        self.assertNotIn("\n\n\n", text)

    def test_backslash_n_escape(self):
        _, text = template.render({}, "text", CTX, override="第一行\\n第二行")
        self.assertEqual(text, "第一行\n第二行")

    def test_custom_template_overrides_default(self):
        settings = {"template": {"dingtalk": "自定义 {title}\n{content}"}}
        _, text = template.render(settings, "dingtalk", CTX)
        self.assertTrue(text.startswith("自定义 起来喝水"))
        self.assertNotIn("🗓", text)

    def test_blank_template_falls_back_to_default(self):
        settings = {"template": {"dingtalk": "   "}}
        self.assertEqual(template.current(settings, "dingtalk"), template.DEFAULT_MD)

    def test_clip_long_text(self):
        long_ctx = dict(CTX, content="x" * (template.MAX_MD_CHARS + 500))
        _, text = template.render({}, "dingtalk", long_ctx)
        self.assertLessEqual(len(text), template.MAX_MD_CHARS + 40)
        self.assertIn("已截断", text)


class TestHardenBreaks(unittest.TestCase):
    """钉钉只认空行分段：单 \n 会被吞掉，手机上看就是「没有换行」。"""

    def test_single_newline_becomes_blank_line(self):
        self.assertEqual(template.harden_breaks("a\nb"), "a\n\nb")

    def test_existing_blank_line_not_doubled(self):
        self.assertEqual(template.harden_breaks("a\n\nb"), "a\n\nb")

    def test_list_items_stay_tight(self):
        self.assertEqual(template.harden_breaks("- a\n- b\n1. c"), "- a\n- b\n1. c")

    def test_paragraph_then_list_gets_separator(self):
        self.assertEqual(template.harden_breaks("说明\n- a"), "说明\n\n- a")

    def test_code_fence_untouched(self):
        src = "```\nline1\nline2\n```"
        self.assertEqual(template.harden_breaks(src), src)

    def test_default_md_meta_lines_are_separate_paragraphs(self):
        """这正是用户反馈的那条：三行灰色元信息挤成了一坨。"""
        _, text = template.render({}, "dingtalk", CTX)
        self.assertIn("</font>\n\n<font color=#8f959e>⏰", text)
        self.assertIn("</font>\n\n<font color=#8f959e>📮", text)
        self.assertNotIn("\n\n\n", text)

    def test_user_content_line_breaks_survive(self):
        ctx = dict(CTX, content="第一行\n第二行\n\n第三段")
        _, text = template.render({}, "dingtalk", ctx)
        self.assertIn("第一行\n\n第二行\n\n第三段", text)

    def test_email_keeps_single_newlines(self):
        """邮件是纯文本，单换行本来就生效，不该被塞进空行。"""
        _, text = template.render({}, "text", CTX)
        self.assertIn("触发规则：", text)
        self.assertIn("：", text)
        self.assertNotIn("\n\n\n", text)
        lines = [ln for ln in text.split("\n") if ln.strip()]
        self.assertEqual(lines[0], "起来喝水")
        self.assertTrue(lines[-1].startswith("方式："))


class TestPlain(unittest.TestCase):

    def test_to_plain_strips_markdown(self):
        md = "### 标题\n\n**加粗** 与 *斜体*\n> 引用行\n- 列表项\n[链接](https://a.com)"
        out = template.to_plain(md)
        for bad in ("###", "**", "> ", "[链接]"):
            self.assertNotIn(bad, out)
        self.assertIn("标题", out)
        self.assertIn("加粗", out)
        self.assertIn("引用行", out)
        self.assertIn("· 列表项", out)
        self.assertIn("https://a.com", out)

    def test_to_plain_strips_font_tags_keeps_text(self):
        out = template.to_plain('<font color=#e11d48>红色</font>正常')
        self.assertEqual(out, "红色正常")
        self.assertNotIn("color=", out)

    def test_email_gets_plain_text(self):
        _, text = template.render({}, "text", CTX)
        self.assertNotIn("###", text)
        self.assertNotIn("<font", text)
        self.assertIn("起来喝水", text)
        self.assertIn("触发规则：", text)
        self.assertIn("方式：定时触发", text)

    def test_email_default_has_no_emoji(self):
        """邮件默认模板刻意不带 emoji：部分老邮箱客户端里 emoji 会变成方块。"""
        self.assertNotIn("🔔", template.DEFAULT_TEXT)


class TestPreviewHtml(unittest.TestCase):

    def test_headings_quote_list_bold(self):
        html = template.md_to_html("### 标题\n\n**粗**\n> 引用\n- 项")
        self.assertIn('class="h h3"', html)
        self.assertIn("<b>粗</b>", html)
        self.assertIn('class="quote"', html)
        self.assertIn("• 项", html)

    def test_font_color_becomes_span(self):
        html = template.md_to_html('<font color=#e11d48>红</font>')
        self.assertIn('<span style="color:#e11d48">红</span>', html)

    def test_font_color_with_quotes_also_works(self):
        html = template.md_to_html('<font color="#1677ff">蓝</font>')
        self.assertIn('<span style="color:#1677ff">蓝</span>', html)

    def test_html_is_escaped(self):
        """预览是把内容塞进 innerHTML，必须先把用户文本转义掉。"""
        html = template.md_to_html("<script>alert(1)</script>")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)


class _Capture(notify.BaseChannel):
    def __init__(self, name, fmt, store):
        super().__init__(name, name, name)
        self.text_format = fmt
        self.store = store

    def check(self, cfg):
        return None

    def send(self, cfg, title, body):
        self.store.append((self.name, title, body))
        return True, "ok"


class TestDispatchFormat(unittest.TestCase):

    def test_each_channel_uses_its_own_format(self):
        got = []
        old = dict(notify.REGISTRY)
        notify.REGISTRY["dt"] = _Capture("dt", "md", got)
        notify.REGISTRY["mail"] = _Capture("mail", "text", got)
        try:
            res = notify.dispatch(["dt", "mail"], {}, CTX, retries=0)
        finally:
            notify.REGISTRY.clear()
            notify.REGISTRY.update(old)
        self.assertTrue(all(r["ok"] for r in res), res)
        bodies = dict((n, b) for n, _t, b in got)
        self.assertIn("### 🔔 起来喝水", bodies["dt"])
        self.assertNotIn("###", bodies["mail"])


class TestBuildContext(unittest.TestCase):

    def test_fields(self):
        rule = {"title": "喝水", "body": " 接水 ", "channels": ["dingtalk1", "email"],
                "rule": {"type": "daily", "times": ["09:00"]},
                "next_run_at": "2026-10-24 09:00:00"}
        ctx = dispatcher.build_context(rule, "snooze")
        self.assertEqual(ctx["title"], "喝水")
        self.assertEqual(ctx["content"], "接水")
        self.assertEqual(ctx["reason"], "稍后提醒")
        self.assertEqual(ctx["channels"], "钉钉机器人1、邮件")
        self.assertIn("每天 09:00", ctx["rule"])
        self.assertRegex(ctx["weekday"], r"^星期[一二三四五六日]$")
        self.assertRegex(ctx["time"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    def test_empty_channels(self):
        ctx = dispatcher.build_context({"title": "x", "channels": []})
        self.assertEqual(ctx["channels"], "未选通道")


if __name__ == "__main__":
    unittest.main()
