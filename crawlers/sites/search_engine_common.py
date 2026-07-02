"""搜索引擎（百度网页检索）共享工具"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from utils.timezone import APP_TZ, parse_app_date, recent_cutoff_date

_REL_MINUTES = re.compile(r"(\d+)\s*分钟前")
_REL_HOURS = re.compile(r"(\d+)\s*小时前")
_REL_DAYS = re.compile(r"(\d+)\s*天前")
_ABS_YMD = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
_ABS_DASH = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_ABS_SLASH = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})")


def parse_search_publish_time(raw: str) -> str:
    """解析搜索结果中的时间描述"""
    text = (raw or "").strip()
    if not text:
        return ""
    now = datetime.now(APP_TZ)

    m = _REL_MINUTES.search(text)
    if m:
        return (now - timedelta(minutes=int(m.group(1)))).strftime("%Y-%m-%d %H:%M:%S")
    m = _REL_HOURS.search(text)
    if m:
        return (now - timedelta(hours=int(m.group(1)))).strftime("%Y-%m-%d %H:%M:%S")
    if "昨天" in text:
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    m = _REL_DAYS.search(text)
    if m:
        return (now - timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d")
    for pat in (_ABS_YMD, _ABS_DASH, _ABS_SLASH):
        m = pat.search(text)
        if m:
            y, mo, d = m.groups()
            return f"{y}-{int(mo):02d}-{int(d):02d}"
    return ""


def baidu_recent_url_suffix(keep_days: int) -> str:
    """百度综合搜索：限定发布时间区间（秒级时间戳）"""
    cutoff = recent_cutoff_date(keep_days)
    start_dt = datetime(cutoff.year, cutoff.month, cutoff.day, tzinfo=APP_TZ)
    end_dt = datetime.now(APP_TZ)
    start_ts = int(start_dt.timestamp())
    end_ts = int(end_dt.timestamp())
    return f"&gpc=stf={start_ts},{end_ts}|stftype=1"


def item_publish_date(item: dict):
    from crawlers.sites.common import _item_publish_date
    return _item_publish_date(item)


def filter_web_search_results(
    items: list[dict],
    keep_days: int,
    *,
    require_date: bool = False,
) -> list[dict]:
    """
    网页搜索结果时间过滤。
    - 能解析出日期且早于 keep_days 的：丢弃
    - require_date=True：无有效日期也丢弃
    - 无日期：保留（配合搜索引擎 URL 时间筛选）
    """
    cutoff = recent_cutoff_date(keep_days)
    filtered = []
    for item in items:
        pub_date = item_publish_date(item)
        if pub_date is None:
            if not require_date:
                filtered.append(item)
            continue
        if pub_date >= cutoff:
            filtered.append(item)
    return filtered


def filter_search_engine_for_save(items: list[dict], keep_days: int) -> list[dict]:
    """搜索引擎入库前：无日期保留（URL 已限近 N 天），有日期且超期则丢弃"""
    cutoff = recent_cutoff_date(keep_days)
    kept = []
    for item in items:
        pub_date = item_publish_date(item)
        if pub_date is None or pub_date >= cutoff:
            kept.append(item)
    return kept


def page_all_items_too_old(items: list[dict], keep_days: int) -> bool:
    """本页是否全部可解析且均已超期（用于停止翻页）"""
    if not items:
        return False
    cutoff = recent_cutoff_date(keep_days)
    has_dated = False
    for item in items:
        pub_date = item_publish_date(item)
        if pub_date is None:
            return False
        has_dated = True
        if pub_date >= cutoff:
            return False
    return has_dated
