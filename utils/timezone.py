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
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=APP_TZ)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return to_app_tz(parsed)
    except ValueError:
        return None
