"""统一时间入口。

设计要点：
1. 时区**显式绑定**，不依赖宿主机系统时区。fnOS 若把系统时区设成 UTC，
   `datetime.now()` 会直接错 8 小时，而提醒服务对时间极其敏感。
2. 对外一律返回 **naive（不带 tzinfo）的东八区墙上时间**，
   与数据库里 'YYYY-MM-DD HH:MM:SS' 的字符串比较语义一致，避免 naive/aware 混比抛异常。
3. 全程只走 clock.now()，不散落 datetime.now()，方便测试注入。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

FMT = "%Y-%m-%d %H:%M:%S"
TZ_NAME = os.environ.get("TZ_NAME", "Asia/Shanghai")

try:
    from zoneinfo import ZoneInfo

    TZ = ZoneInfo(TZ_NAME)
except Exception:  # pragma: no cover - 极简环境下没有 tzdata
    TZ = None


def now() -> datetime:
    """东八区当前墙上时间（naive）。"""
    if TZ is not None:
        return datetime.now(TZ).replace(tzinfo=None)
    return datetime.now()


def now_str() -> str:
    return now().strftime(FMT)


def parse(s: str) -> datetime:
    """解析库里的时间串，容忍 'T' 分隔与缺秒。"""
    s = (s or "").strip().replace("T", " ")
    for f in (FMT, "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    raise ValueError(f"无法识别的时间格式: {s!r}，应形如 2026-10-24 09:00")


def fmt(dt: datetime | None) -> str | None:
    return dt.strftime(FMT) if dt else None


def seconds_late(now_dt: datetime, scheduled) -> float:
    """迟到秒数。必须是 total_seconds()：

    timedelta.seconds 只返回「去掉整天后的余数」（0..86399），
    关机 25 小时后 .seconds 会显示 3600，grace 判定会完全失真。
    """
    if isinstance(scheduled, str):
        scheduled = parse(scheduled)
    return (now_dt - scheduled).total_seconds()


def after(minutes: int = 0, seconds: int = 0) -> datetime:
    return now() + timedelta(minutes=minutes, seconds=seconds)
