"""通道注册表测试：两个钉钉机器人、别名归一、配置分块、旧配置迁移。"""
import json
import os
import shutil
import sys
import tempfile
import unittest

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
sys.path.insert(0, APP)

import notify  # noqa: E402

CTX = {"title": "标题", "content": "正文"}
import store  # noqa: E402


class TestRegistry(unittest.TestCase):

    def test_registry_keys(self):
        self.assertEqual(sorted(notify.REGISTRY), ["dingtalk1", "dingtalk2", "email"])

    def test_labels(self):
        self.assertEqual(notify.channel_label("dingtalk1"), "钉钉机器人1")
        self.assertEqual(notify.channel_label("dingtalk2"), "钉钉机器人2")
        self.assertEqual(notify.channel_label("email"), "邮件")

    def test_legacy_alias_normalized(self):
        self.assertEqual(notify.normalize_channels(["dingtalk"]), ["dingtalk1"])
        self.assertEqual(notify.normalize_channels(["dingtalk", "dingtalk1"]), ["dingtalk1"])

    def test_unknown_and_dupes(self):
        self.assertEqual(notify.normalize_channels(["bogus", "email", "email"]), ["email"])
        self.assertEqual(notify.normalize_channels(None), [])

    def test_cfg_for_keeps_robots_separate(self):
        settings = {
            "dingtalk1": {"webhook": "https://a/1"},
            "dingtalk2": {"webhook": "https://a/2"},
            "email": {"host": "smtp"},
        }
        self.assertEqual(notify.cfg_for(settings, "dingtalk1")["webhook"], "https://a/1")
        self.assertEqual(notify.cfg_for(settings, "dingtalk2")["webhook"], "https://a/2")
        self.assertEqual(notify.cfg_for(settings, "nope"), {})

    def test_unconfigured_robot_skipped_not_failed(self):
        """两个机器人都没填 webhook 时，dispatch 应各返回一条 skipped，且不重试、不连网络。"""
        res = notify.dispatch(["dingtalk1", "dingtalk2"], {}, CTX, retries=2)
        self.assertEqual([r["channel"] for r in res], ["dingtalk1", "dingtalk2"])
        self.assertTrue(all(r["status"] == "skipped" for r in res), res)
        self.assertIn("机器人1", res[0]["detail"])
        self.assertIn("机器人2", res[1]["detail"])
        self.assertNotIn("重试", res[0]["detail"])

    def test_email_missing_config_is_skipped(self):
        res = notify.dispatch(["email"], {}, CTX, retries=0)
        self.assertEqual(res[0]["status"], "skipped")
        self.assertIn("邮件未配置", res[0]["detail"])

    def test_rule_without_email_never_touches_email(self):
        """只勾钉钉的提醒，即使邮件配了错的东西，也不该出现邮件的结果。"""
        settings = {
            "dingtalk1": {"webhook": ""},                      # 也没配
            "email": {"host": "smtp.qq.com", "to": "a@b.c", "user": "a@b.c", "password": "x"},
        }
        res = notify.dispatch(["dingtalk1"], settings, CTX, retries=0)
        self.assertEqual([r["channel"] for r in res], ["dingtalk1"])
        self.assertNotIn("email", [r["channel"] for r in res])

    def test_summarize_mentions_sent_skipped_failed(self):
        results = [
            {"channel": "dingtalk1", "label": "钉钉机器人1", "status": "sent", "ok": True, "detail": ""},
            {"channel": "email", "label": "邮件", "status": "skipped", "ok": False, "detail": "邮件未配置"},
        ]
        msg = notify.summarize(results)
        self.assertIn("已发送：钉钉机器人1", msg)
        self.assertIn("邮件未配置已跳过", msg)

    def test_empty_channels_reports_failed(self):
        res = notify.dispatch([], {}, CTX, retries=0)
        self.assertEqual(res[0]["status"], "failed")
        self.assertIn("没有勾选", res[0]["detail"])


class TestSettingsMigration(unittest.TestCase):
    """老库只有一个 dingtalk 配置块，升级后应并入机器人1。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="reminder-mig-")
        self._old = store.DB_PATH
        store.DB_PATH = os.path.join(self.tmp, "reminder.db")
        store.init()

    def tearDown(self):
        store.DB_PATH = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _put(self, cfg: dict) -> None:
        with store.conn() as c:
            c.execute("INSERT INTO kv(k,v) VALUES('settings',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                      (json.dumps(cfg, ensure_ascii=False),))

    def test_legacy_block_moves_to_robot1(self):
        self._put({"grace_seconds": 120,
                   "dingtalk": {"enabled": True, "webhook": "https://oapi.dingtalk.com/robot/send?access_token=old"}})
        cfg = store.get_settings()
        self.assertEqual(cfg["dingtalk1"]["webhook"], "https://oapi.dingtalk.com/robot/send?access_token=old")
        self.assertEqual(cfg["grace_seconds"], 120)
        self.assertNotIn("dingtalk", cfg)
        self.assertTrue(cfg["dingtalk2"]["webhook"] == "", "机器人2 不应被旧配置污染")

    def test_existing_robot1_not_overwritten(self):
        self._put({"dingtalk": {"webhook": "https://old"},
                   "dingtalk1": {"webhook": "https://new"}})
        self.assertEqual(store.get_settings()["dingtalk1"]["webhook"], "https://new")

    def test_fresh_settings_have_two_robots(self):
        cfg = store.get_settings()
        self.assertEqual(sorted(k for k in cfg if k.startswith("dingtalk")), ["dingtalk1", "dingtalk2"])


if __name__ == "__main__":
    unittest.main()
