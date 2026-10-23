"""规则引擎单元测试：python -m unittest discover -s tests -v"""
import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

import rules  # noqa: E402


def seq(rule, start="2026-10-26 00:00:00", n=12):
    """从 start 起连续取 n 次触发，用于验证序列。"""
    out, cur = [], datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
    for _ in range(n):
        nxt = rules.next_after(rule, cur)
        if nxt is None:
            break
        out.append(nxt.strftime("%m-%d %H:%M"))
        cur = nxt
    return out


class TestDailyWindow(unittest.TestCase):
    def test_hourly_9_to_18(self):
        r = {"type": "daily_window", "start": "09:00", "end": "18:00", "interval_minutes": 60}
        self.assertEqual(seq(r, n=10), [
            "10-26 09:00", "10-26 10:00", "10-26 11:00", "10-26 12:00", "10-26 13:00",
            "10-26 14:00", "10-26 15:00", "10-26 16:00", "10-26 17:00", "10-26 18:00",
        ])

    def test_90min_last_point_is_end(self):
        r = {"type": "daily_window", "start": "09:00", "end": "18:00", "interval_minutes": 90}
        got = seq(r, n=7)
        self.assertEqual(got, ["10-26 09:00", "10-26 10:30", "10-26 12:00", "10-26 13:30",
                               "10-26 15:00", "10-26 16:30", "10-26 18:00"])
        # 第 8 次应跳到次日
        self.assertEqual(seq(r, n=8)[-1], "10-27 09:00")

    def test_cross_midnight(self):
        r = {"type": "daily_window", "start": "22:00", "end": "02:00", "interval_minutes": 60}
        # 从 21:00 开始，窗口跨到次日 02:00，之后回到当天 22:00
        self.assertEqual(seq(r, "2026-10-26 21:00:00", n=6),
                         ["10-26 22:00", "10-26 23:00", "10-27 00:00",
                          "10-27 01:00", "10-27 02:00", "10-27 22:00"])

    def test_weekday_filter(self):
        # 2026-10-26 是周一
        r = {"type": "daily_window", "start": "09:00", "end": "18:00",
             "interval_minutes": 60, "days": [1, 3, 5]}
        got = seq(r, "2026-10-26 08:00:00", n=12)
        dates = sorted({g[:5] for g in got})
        # 周一 -> 周二空 -> 周三
        self.assertEqual(dates, ["10-26", "10-28"])
        self.assertIn("10-28 09:00", got)

    def test_days_empty_means_every_day(self):
        r = {"type": "daily_window", "start": "09:00", "end": "09:00", "interval_minutes": 60, "days": []}
        self.assertEqual(seq(r, n=2), ["10-26 09:00", "10-27 09:00"])


class TestOtherTypes(unittest.TestCase):
    def test_once_future_and_past(self):
        self.assertEqual(seq({"type": "once", "at": "2026-12-01 09:30"}, n=1), ["12-01 09:30"])
        past = {"type": "once", "at": "2020-01-01 00:00"}
        self.assertIsNone(rules.next_after(past, datetime(2026, 1, 1)))

    def test_daily_multiple_times(self):
        r = {"type": "daily", "times": ["09:30", "17:30"]}
        self.assertEqual(seq(r, n=4), ["10-26 09:30", "10-26 17:30", "10-27 09:30", "10-27 17:30"])

    def test_weekly(self):
        r = {"type": "weekly", "days": [5], "times": ["17:30"]}
        # 2026-10-23 是周五
        got = seq(r, "2026-10-22 00:00:00", n=2)
        self.assertEqual(got, ["10-23 17:30", "10-30 17:30"])

    def test_interval_anchor(self):
        r = {"type": "interval", "start": "2026-10-26 09:00", "every_minutes": 90}
        self.assertEqual(seq(r, n=3), ["10-26 09:00", "10-26 10:30", "10-26 12:00"])

    def test_bad_input_returns_none(self):
        self.assertIsNone(rules.next_after({"type": "once", "at": "不是时间"}, datetime.now()))
        self.assertIsNone(rules.next_after({"type": "interval", "start": "2026-10-26 09:00", "every_minutes": 0}, datetime.now()))

    def test_no_infinite_loop_on_empty_times(self):
        self.assertIsNone(rules.next_after({"type": "daily", "times": []}, datetime(2026, 10, 26)))


class TestDescribe(unittest.TestCase):
    def test_human_readable(self):
        d = rules.describe({"type": "daily_window", "start": "09:00", "end": "18:00",
                            "interval_minutes": 60, "days": [1, 2, 3, 4, 5]})
        self.assertIn("09:00", d)
        self.assertIn("每 1 小时", d)
        self.assertIn("每周", d)


if __name__ == "__main__":
    unittest.main(verbosity=2)
