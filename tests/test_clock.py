"""时间处理回归测试。

重点防守两件事：
1. 迟到秒数必须用 total_seconds()（timedelta.seconds 会截断整天，关机 25 小时会算成 1 小时）；
2. 全项目不能再出现裸 datetime.now() / .seconds 写法，必须走 clock。
"""
import glob
import os
import re
import sys
import unittest
from datetime import datetime, timedelta

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
sys.path.insert(0, APP)

import clock  # noqa: E402


class TestClock(unittest.TestCase):
    def test_late_over_24h_not_truncated(self):
        now = datetime(2026, 10, 24, 10, 0, 0)
        scheduled = now - timedelta(hours=25)  # 关机 25 小时后开机
        self.assertEqual(int(clock.seconds_late(now, scheduled)), 90000)
        # 反面教材：timedelta.seconds 只有 3600，会导致误判为"刚到点"
        self.assertEqual((now - scheduled).seconds, 3600)

    def test_parse_tolerates_t_and_missing_seconds(self):
        self.assertEqual(clock.parse("2026-10-24T09:30"), datetime(2026, 10, 24, 9, 30))
        self.assertEqual(clock.parse("2026-10-24 09:30:00"), datetime(2026, 10, 24, 9, 30, 0))

    def test_parse_rejects_garbage(self):
        with self.assertRaises(ValueError):
            clock.parse("明天早上")

    def test_now_is_naive_shanghai_wall_clock(self):
        n = clock.now()
        self.assertIsNone(n.tzinfo, "对外必须是 naive，避免与库里的字符串比较时抛异常")
        utc_naive = datetime.now(tz=__import__("datetime").timezone.utc).replace(tzinfo=None)
        self.assertLess(abs((n - utc_naive).total_seconds()), 15 * 3600)

    def test_fmt_none(self):
        self.assertIsNone(clock.fmt(None))
        self.assertEqual(clock.fmt(datetime(2026, 1, 2, 3, 4, 5)), "2026-01-02 03:04:05")


class TestNoRiskyTimeCode(unittest.TestCase):
    def _sources(self):
        return [p for p in glob.glob(os.path.join(APP, "*.py")) if not p.endswith("clock.py")]

    def test_no_timedelta_seconds(self):
        for path in self._sources():
            with open(path, encoding="utf-8") as f:
                src = f.read()
            self.assertIsNone(re.search(r"\.seconds\b", src),
                              f"{os.path.basename(path)} 使用了 .seconds，应改用 clock.seconds_late()")

    def test_no_bare_datetime_now(self):
        for path in self._sources():
            with open(path, encoding="utf-8") as f:
                src = f.read()
            self.assertIsNone(re.search(r"datetime\.now\(", src),
                              f"{os.path.basename(path)} 直接调用了 datetime.now()，应改用 clock.now()")


if __name__ == "__main__":
    unittest.main(verbosity=2)
