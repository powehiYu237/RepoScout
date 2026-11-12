"""时间与通用工具。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def utc_now() -> datetime:
    """当前 UTC 时间（带时区）。"""
    return datetime.now(timezone.utc)


def iso_z(dt: datetime) -> str:
    """格式化为 `YYYY-MM-DDTHH:MM:SSZ`，统一以 UTC 存储。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def floor_to_bucket(dt: datetime, hours: int) -> datetime:
    """把时间向下取整到 N 小时桶（N 必须整除 24）。"""
    if hours <= 0 or 24 % hours != 0:
        raise ValueError("bucket hours must be a positive divisor of 24")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.replace(hour=dt.hour - dt.hour % hours, minute=0, second=0, microsecond=0)


def hours_ago(dt: datetime, hours: int) -> datetime:
    return dt - timedelta(hours=hours)
