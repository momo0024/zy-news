"""
工人日报（中工网）搜索爬虫
- 搜索页: https://www.workercn.cn/search/result.shtml
- API: GET https://www.workercn.cn/cms/front/search/result
- query 前缀 title: 标题 / content: 正文（由 search_url 中 scope 决定）
- sort=publishDate 时间倒序、catalogID 空为全部栏目
- startDate/endDate 最近一天（今日至次日，与站点筛选一致）
"""

import asyncio
import re
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

from loguru import logger

from crawlers.cloak_browser import CloakBrowser
from crawlers.sites.common import all_items_are_recent, filter_recent_news
from utils.timezone import APP_TZ, recent_date_range_str

_API_URL = "https://www.workercn.cn/cms/front/search/result"
_SEARCH_PAGE = "https://www.workercn.cn/search/result.shtml?siteID=122"
_SITE_ID = "122"
_PAGE_SIZE = 10
_MAX_PAGES = 50
_TAG_RE = re.compile(r"<[^>]+>")
_SNIPPET_MAX = 500


def _strip_html(text: str) -> str:
    return _TAG_RE.sub("", text or "").strip()


def _scope_from_url(search_url: str) -> str:
    params = parse_qs(urlparse(search_url or "").query)
    scope = (params.get("scope") or ["body"])[0].lower()
    return "title" if scope == "title" else "body"


def _scope_label(scope: str) -> str:
    return "标题" if scope == "title" else "正文"


def _date_range(keep_days: int) -> tuple[str, str]:
    """keep_days=1 对齐站点：startDate=今日、endDate=次日"""
    now = datetime.now(APP_TZ)
    if keep_days <= 1:
        end = now + timedelta(days=1)
        return now.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    return recent_date_range_str(keep_days)


def _build_query(keyword: str, scope: str) -> str:
    prefix = "title:" if scope == "title" else "content:"
    return f"{prefix}{keyword}"


def _build_params(keyword: str, scope: str, keep_days: int, page_index: int) -> dict[str, str]:
    start_date, end_date = _date_range(keep_days)
    return {
        "query": _build_query(keyword, scope),
        "siteID": _SITE_ID,
        "type": "",
        "sort": "publishDate",
        "startDate": start_date,
        "endDate": end_date,
        "catalogID": "",
        "pageIndex": str(page_index),
        "pageSize": str(_PAGE_SIZE),
    }


def _item_from_record(rec: dict, keyword: str, site_name: str) -> dict | None:
    url = (rec.get("url") or rec.get("artUrl") or "").strip()
    if not url:
        return None
    title = _strip_html(rec.get("title", ""))
    if not title:
        return None
    source = (rec.get("source") or rec.get("catalogName") or site_name).strip()
    pub_time = (rec.get("publishDate") or rec.get("time") or rec.get("addTime") or "").strip()
    raw_snippet = _strip_html(rec.get("content") or rec.get("summary") or "")
    abstract = raw_snippet[:_SNIPPET_MAX] if raw_snippet else ""
    return {
        "title": title,
        "url": url,
        "publish_time": pub_time,
        "source": source,
        "keyword": keyword,
        "abstract": abstract,
    }


async def search(
    browser: CloakBrowser,
    site: dict,
    keyword: str,
    keep_days: int,
    search_url: str,
) -> list[dict]:
    """工人日报（中工网）搜索（标题或正文，由 search_url 中 scope 决定）"""
    site_name = site["site_name"]
    scope = _scope_from_url(search_url)
    scope_label = _scope_label(scope)

    all_items: list[dict] = []
    page_index = -1
    total = 0

    async with browser.session() as page:
        await page.goto(_SEARCH_PAGE, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(500)

        while page_index + 1 < _MAX_PAGES:
            page_index += 1
            params = _build_params(keyword, scope, keep_days, page_index)
            logger.debug(
                f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 第{page_index + 1}页 "
                f"date={params['startDate']}~{params['endDate']}"
            )

            try:
                response = await page.request.get(
                    _API_URL,
                    params=params,
                    headers={"referer": _SEARCH_PAGE},
                )
                result = await response.json()
            except Exception as e:
                logger.warning(
                    f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 "
                    f"第{page_index + 1}页请求失败: {e}"
                )
                break

            if result.get("status") != 1:
                logger.warning(
                    f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 "
                    f"第{page_index + 1}页 API 异常: {result.get('message')}"
                )
                break

            data = result.get("data") or {}
            records = data.get("data") or []
            total = int(data.get("total") or 0)

            if not records:
                logger.info(
                    f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 "
                    f"第{page_index + 1}页无结果，停止翻页"
                )
                break

            page_items = [
                item for rec in records
                if (item := _item_from_record(rec, keyword, site_name))
            ]
            all_items.extend(page_items)

            logger.info(
                f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 第{page_index + 1}页: "
                f"解析 {len(page_items)} 条，累计 {len(all_items)} 条"
            )

            if (page_index + 1) * _PAGE_SIZE >= total:
                break

            if not all_items_are_recent(page_items, keep_days):
                logger.info(
                    f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 "
                    f"第{page_index + 1}页已出现超期条目，停止翻页"
                )
                break

            await asyncio.sleep(0.5)

    filtered = filter_recent_news(all_items, keep_days)
    logger.info(
        f"[{site_name}] 关键词 [{keyword}] {scope_label}检索结束: "
        f"翻{page_index + 1}页, 解析 {len(all_items)} 条, "
        f"时间过滤后 {len(filtered)} 条（保留近 {keep_days} 天）"
    )
    return filtered
