"""提醒规则 -> 下一次触发时间。

所有规则都归一成一个纯函数 next_after(rule, after) -> datetime | None，
服务每次 tick 只问一句"现在该不该响"，因此重启、漏跑都不会把状态搞乱。

支持的规则类型：
  once          一次性         {"type":"once","at":"2026-10-24 09:00"}
  daily         每日固定时刻   {"type":"daily","times":["09:00","18:30"]}
  weekly        每周几         {"type":"weekly","days":[1,3,5],"times":["09:00"]}   # 1=周一
  daily_window  每日时段内每N分钟 {"type":"daily_window","start":"09:00","end":"18:00","interval_minutes":60,"days":[1,2,3,4,5]}
  interval      从某刻起每N分钟  {"type":"interval","start":"2026-10-23 09:00","every_minutes":90}
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

FMT = "%Y-%m-%d %H:%M:%S"


def parse_dt(s: str) -> datetime:
    s = (s or "").strip().replace("T", " ")
    for f in (FMT, "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    raise ValueError(f"无法识别的时间格式: {s!r}，应形如 2026-10-24 09:00")


def _minute_of_day(s: str) -> int:
    hh, mm = (s or "").strip().split(":")
    h, m = int(hh), int(mm)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"非法时刻: {s!r}")
    return h * 60 + m


def _offsets_in_window(start: str, end: str, interval_minutes: int) -> list[int]:
    """返回窗口内的分钟偏移列表（可能 >1440，表示跨零点到次日）。"""
    a = _minute_of_day(start)
    b = _minute_of_day(end)
    if b < a:  # 例如 22:00 - 02:00
        b += 1440
    step = max(1, int(interval_minutes))
    out, t = [], a
    while t <= b:
        out.append(t)
        t += step
    return out


def _at(d: date, minute_offset: int) -> datetime:
    return datetime.combine(d, time(0, 0)) + timedelta(minutes=minute_offset)


def _occurrences_on(rule: dict, d: date) -> list[datetime]:
    """某一天（00:00 起算）会触发的所有时刻，升序。"""
    t = rule.get("type")
    if t == "daily":
        return [_at(d, _minute_of_day(x)) for x in rule.get("times", [])]
    if t == "weekly":
        if d.isoweekday() not in _int_list(rule.get("days")):
            return []
        return [_at(d, _minute_of_day(x)) for x in rule.get("times", [])]
    if t == "daily_window":
        days = _int_list(rule.get("days"))
        if days and d.isoweekday() not in days:
            return []
        offs = _offsets_in_window(
            rule.get("start", "09:00"),
            rule.get("end", "18:00"),
            rule.get("interval_minutes", 60),
        )
        return [_at(d, o) for o in offs]
    return []


def _int_list(v) -> list[int]:
    if not v:
        return []
    if isinstance(v, str):
        v = [x for x in v.replace("，", ",").split(",") if x.strip()]
    return [int(x) for x in v]


def next_after(rule: dict, after: datetime) -> datetime | None:
    t = rule.get("type")
    if t == "once":
        try:
            dt = parse_dt(rule.get("at", ""))
        except ValueError:
            return None
        return dt if dt > after else None

    if t == "interval":
        every = int(rule.get("every_minutes") or 0)
        if every < 1:
            return None
        anchor = parse_dt(rule.get("start", ""))
        if anchor > after:
            return anchor
        passed = (after - anchor).total_seconds()
        n = int(passed // (every * 60)) + 1
        return anchor + timedelta(minutes=every * n)

    # 日历型规则：从前一天开始扫（跨零点窗口的余量落在次日），最多向后找 8 天
    for i in range(-1, 8):
        d = after.date() + timedelta(days=i)
        for dt in _occurrences_on(rule, d):
            if dt > after:
                return dt
    return None


def describe(rule: dict) -> str:
    """给 UI 用的人话描述。"""
    t = rule.get("type")
    wd = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "日"}
    days = _int_list(rule.get("days"))
    dstr = "每天" if not days or len(days) == 7 else "每周" + "、".join(wd.get(x, str(x)) for x in days)
    if t == "once":
        return f"一次性：{rule.get('at', '')}"
    if t == "daily":
        return "每天 " + "、".join(rule.get("times", []))
    if t == "weekly":
        return f"每周{'、'.join(wd.get(x, str(x)) for x in days)} " + "、".join(rule.get("times", []))
    if t == "daily_window":
        iv = int(rule.get("interval_minutes", 60))
        span = f"{rule.get('start')}–{rule.get('end')}"
        step = f"每 {iv} 分钟" if iv % 60 else f"每 {iv // 60} 小时"
        return f"{dstr} {span} 内{step}提醒"
    if t == "interval":
        return f"自 {rule.get('start')} 起每 {rule.get('every_minutes')} 分钟"
    return "未知规则"
