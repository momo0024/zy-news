"""
人民日报（人民网）搜索爬虫
- 站点不支持标题/正文分 scope，仅综合检索
- API: POST http://search.people.cn/search-platform/front/search
- 命中判断：列表标题 + 摘要（content 字段），入库时由 resolve_hit_flags(site_combined_search) 校验
- 精确匹配：isFuzzy=False
- 排序：sortType=2 时间倒序
- 时间：startTime/endTime 毫秒（keep_days）
"""

import asyncio
import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from loguru import logger

from crawlers.cloak_browser import CloakBrowser
from crawlers.sites.common import all_items_are_recent, filter_recent_news
from utils.timezone import APP_TZ, recent_cutoff_date

_API_URL = "http://search.people.cn/search-platform/front/search"
_PAGE_SIZE = 10
_MAX_PAGES = 50
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return _TAG_RE.sub("", text or "").strip()


def _time_range_ms(keep_days: int) -> tuple[int, int]:
    cutoff = recent_cutoff_date(keep_days)
    start_dt = datetime(cutoff.year, cutoff.month, cutoff.day, tzinfo=APP_TZ)
    now_dt = datetime.now(APP_TZ)
    return int(start_dt.timestamp() * 1000), int(now_dt.timestamp() * 1000)


def _format_display_time(ms: int | str | None) -> str:
    if not ms:
        return ""
    try:
        ts = int(ms) / 1000
        return datetime.fromtimestamp(ts, tz=APP_TZ).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return ""


def _item_from_record(rec: dict, keyword: str, site_name: str) -> dict | None:
    url = (rec.get("url") or "").strip()
    if not url:
        return None
    title = _strip_html(rec.get("title", ""))
    abstract = _strip_html(rec.get("content", ""))
    source = (rec.get("belongsName") or rec.get("source") or site_name).strip()
    pub_time = _format_display_time(rec.get("displayTime"))
    return {
        "title": title,
        "url": url,
        "publish_time": pub_time,
        "source": source,
        "keyword": keyword,
        "abstract": abstract,
    }


def _build_payload(keyword: str, keep_days: int, page: int) -> dict[str, Any]:
    start_ms, end_ms = _time_range_ms(keep_days)
    return {
        "key": keyword,
        "page": page,
        "limit": _PAGE_SIZE,
        "sortType": 2,
        "type": 0,
        "hasTitle": True,
        "hasContent": True,
        "isFuzzy": False,
        "startTime": start_ms,
        "endTime": end_ms,
    }


def _build_referer(keyword: str) -> str:
    return f"http://search.people.cn/s?{urlencode({'keyword': keyword, 'st': '0'})}"


async def search(
    browser: CloakBrowser,
    site: dict,
    keyword: str,
    keep_days: int,
    search_url: str,
) -> list[dict]:
    """人民网综合检索（标题+摘要校验命中，不支持分 scope）"""
    site_name = site["site_name"]
    all_items: list[dict] = []
    page_no = 0
    api_pages = _MAX_PAGES

    async with browser.session() as page:
        while page_no < api_pages and page_no < _MAX_PAGES:
            page_no += 1
            payload = _build_payload(keyword, keep_days, page_no)
            logger.debug(
                f"[{site_name}] 关键词 [{keyword}] 综合检索 第{page_no}页 API: "
                f"startTime={payload['startTime']} endTime={payload['endTime']}"
            )

            try:
                response = await page.request.post(
                    _API_URL,
                    data=json.dumps(payload),
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json;charset=UTF-8",
                        "Origin": "http://search.people.cn",
                        "Referer": _build_referer(keyword),
                    },
                )
                body = json.loads(await response.text())
            except Exception as e:
                logger.warning(
                    f"[{site_name}] 关键词 [{keyword}] 第{page_no}页 API 请求失败: {e}"
                )
                break

            code = body.get("code")
            if code not in (None, 0, "0", 200):
                logger.warning(
                    f"[{site_name}] 关键词 [{keyword}] 第{page_no}页 API 返回异常 code={code}"
                )
                break

            data = body.get("data") or {}
            records = data.get("records") or []
            api_pages = min(int(data.get("pages") or 1), _MAX_PAGES)

            if not records:
                logger.info(f"[{site_name}] 关键词 [{keyword}] 第{page_no}页无结果，停止翻页")
                break

            page_items = [
                item for rec in records
                if (item := _item_from_record(rec, keyword, site_name))
            ]
            all_items.extend(page_items)

            logger.info(
                f"[{site_name}] 关键词 [{keyword}] 第{page_no}页: "
                f"解析 {len(page_items)} 条，累计 {len(all_items)} 条"
            )

            if len(records) < _PAGE_SIZE:
                break

            if not all_items_are_recent(page_items, keep_days):
                logger.info(
                    f"[{site_name}] 关键词 [{keyword}] 第{page_no}页已出现超期条目，停止翻页"
                )
                break

            if page_no >= api_pages:
                break

            await asyncio.sleep(0.5)

    filtered = filter_recent_news(all_items, keep_days)
    logger.info(
        f"[{site_name}] 关键词 [{keyword}] 综合检索结束: "
        f"翻{page_no}页, 解析 {len(all_items)} 条, "
        f"时间过滤后 {len(filtered)} 条（保留近 {keep_days} 天）"
    )
    return filtered
