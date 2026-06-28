"""
应用时区工具 — 新闻业务时间统一按北京时间（Asia/Shanghai）处理
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

APP_TZ = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Shanghai"))


def to_app_tz(dt: datetime) -> datetime:
    """将 datetime 转为应用时区（默认北京时间）"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=APP_TZ)
    return dt.astimezone(APP_TZ)


def format_app_datetime(dt: datetime | None) -> str | None:
    """序列化为北京时间字符串，与数据库客户端显示一致：YYYY-MM-DD HH:mm:ss"""
    if dt is None:
        return None
    return to_app_tz(dt).strftime("%Y-%m-%d %H:%M:%S")


def parse_app_datetime(value: str) -> datetime | None:
    """解析爬虫/接口中的时间字符串，按北京时间理解"""
    if not value or not str(value).strip():
        return None
    text = str(value).strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y.%m.%d %H:%M:%S",
        "%Y.%m.%d %H:%M",
        "%Y.%m.%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=APP_TZ)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return to_app_tz(parsed)
    except ValueError:
        return None


def parse_app_date(value: str):
    """从发布 time 字符串提取日期（列表页仅有 YYYY-MM-DD 时使用）"""
    from datetime import date
    dt = parse_app_datetime(value)
    return dt.date() if dt else None


def recent_cutoff_date(keep_days: int):
    """
    最近 N 个自然日（含今天）的起始日期。
    keep_days=1 → 仅今天；2 → 今天+昨天。
    """
    from datetime import date, timedelta
    today = datetime.now(APP_TZ).date()
    return today - timedelta(days=max(0, keep_days - 1))


def recent_date_range_str(keep_days: int) -> tuple[str, str]:
    """站点搜索 API 用的 startDate / endDate（YYYY-MM-DD）"""
    today = datetime.now(APP_TZ).date()
    start = recent_cutoff_date(keep_days)
    return start.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


def recent_date_range_dots(keep_days: int) -> tuple[str, str]:
    """学习时报等站点用的 starttime / endtime（YYYY.MM.DD）"""
    start, end = recent_date_range_str(keep_days)
    return start.replace("-", "."), end.replace("-", ".")
