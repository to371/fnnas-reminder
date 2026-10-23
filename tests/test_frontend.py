"""前端静态检查：守住「被 iframe 嵌入时原生弹窗被拦」这类坑。

背景：飞牛应用中心 / 预览面板都用 iframe 嵌页面，浏览器会拦截原生
`confirm()`（恒返回 false）和 `prompt()`（恒返回 null）。
表现就是「点删除没反应」「点稍后没反应」——功能全对，就是静默失败。
所以页面里必须用自绘弹层（uiConfirm / uiPrompt）。
"""
import os
import re
import unittest

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
INDEX = os.path.join(APP, "static", "index.html")

# 原生同步弹窗（前面不能紧跟标识符，避免误伤 uiConfirm 之类）
NATIVE_DIALOG = re.compile(r"(?<![A-Za-z0-9_$])(confirm|prompt|alert)\s*\(")


def read_html() -> str:
    with open(INDEX, encoding="utf-8") as f:
        return f.read()


def inline_script(html: str) -> str:
    blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
    return blocks[-1] if blocks else ""


def strip_js_comments(js: str) -> str:
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    js = re.sub(r"(?m)^\s*//.*$", "", js)
    return js


class TestNoNativeDialogs(unittest.TestCase):

    def test_no_confirm_prompt_alert(self):
        js = strip_js_comments(inline_script(read_html()))
        hits = NATIVE_DIALOG.findall(js)
        self.assertEqual(hits, [], f"页面里还有原生弹窗调用：{hits}（iframe 内会被拦截，改用 uiConfirm/uiPrompt）")

    def test_has_inpage_dialog(self):
        html = read_html()
        self.assertIn('id="dlg"', html)
        js = inline_script(html)
        for fn in ("function dlgClose", "const uiConfirm", "const uiPrompt"):
            self.assertIn(fn, js, f"缺少 {fn}——页内弹层被删了？")


class TestBranding(unittest.TestCase):

    def test_title_is_plain(self):
        html = read_html()
        self.assertRegex(html, r"<title>定时提醒</title>")
        self.assertIn("<h1>定时提醒</h1>", html)
        self.assertNotIn("飞牛 · 定时提醒", html)
        self.assertNotIn("飞牛·定时提醒", html)

    def test_gradient_background(self):
        html = read_html()
        self.assertIn("linear-gradient(160deg", html)
        self.assertIn("background-attachment:fixed", html)


class TestChannelUi(unittest.TestCase):

    def test_two_robots_selectable(self):
        html = read_html()
        for elem in ('id="f-dt1"', 'id="f-dt2"', 'id="f-email"',
                     'id="s-dt1-webhook"', 'id="s-dt2-webhook"'):
            self.assertIn(elem, html, f"缺少元素 {elem}")
        self.assertNotIn('id="f-dingtalk"', html, "旧的单机器人勾选框不该还在")

    def test_channel_labels(self):
        js = inline_script(read_html())
        self.assertIn("dingtalk1:'钉钉机器人1'", js)
        self.assertIn("dingtalk2:'钉钉机器人2'", js)

    def test_unconfigured_channel_marked(self):
        html = read_html()
        self.assertIn('id="ch-hint"', html, "编辑器缺少通道提示行")
        js = inline_script(html)
        self.assertIn("function paintChannelHints", js)
        self.assertIn("未配置", js)


class TestTestSendResult(unittest.TestCase):
    """试发提示只能取「本条规则本次」的结果。

    曾经踩过：接口返回全局最近 4 条日志，别的规则的历史邮件失败会串到
    一条只发钉钉的提醒上，显示成「发送失败：SMTPServerDisconnected」。
    """

    def test_uses_scoped_results(self):
        js = strip_js_comments(inline_script(read_html()))
        self.assertIn("r.results", js, "试发应读取本条规则的 results")
        self.assertNotIn("r.logs", js, "试发不该再读全局日志")

    def test_log_table_has_skipped_state(self):
        html = read_html()
        self.assertIn("l.status==='skipped'", html, "日志表应能显示「跳过」状态")


class TestEscaping(unittest.TestCase):
    """页面大量用 innerHTML 拼字符串，用户输入（规则标题、正文、错误详情）必须先 esc()。

    漏一处就是自 XSS —— 本地单人应用危害有限，但一条 `</div><img onerror=...>`
    的提醒标题被自己打开也会中。
    """

    def test_esc_defined(self):
        self.assertIn("function esc(s)", read_html())

    def test_innerhtml_interpolations_escaped(self):
        js = strip_js_comments(inline_script(read_html()))
        # 规则列表 / 日志表里出现的字段名，必须都套了 esc()
        for field in ("r.title", "r.desc", "l.title", "l.detail", "l.ts"):
            self.assertRegex(js, r"esc\(\s*" + re.escape(field),
                             f"{field} 拼进 innerHTML 前没有 esc()")

    def test_attribute_values_escaped(self):
        """value="${...}" 是属性上下文，引号能直接闭合标签，必须转义。"""
        html = read_html()
        bad = [m for m in re.findall(r'value="\$\{([^}]*)', html) if not m.startswith("esc(")]
        self.assertEqual(bad, [], f"这些属性插值没转义：{bad}")

    def test_preview_html_is_escaped(self):
        """预览气泡的 html 来自服务端 md_to_html()，那边已整体转义；这里只确认没改回手拼。"""
        js = inline_script(read_html())
        self.assertIn("pv-body').innerHTML = r.html", js)


if __name__ == "__main__":
    unittest.main()
