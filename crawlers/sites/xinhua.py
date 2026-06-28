"""
新华社（新华网）搜索爬虫
- 搜索入口: https://so.news.cn/#search/{searchFields}/{keyword}/{curPage}/{sortField}
- searchFields=1 标题 / 0 全文（由 site_crawler 分别调用 search_url_title/body）
- sortField=1 时间倒序
- API: GET /getNews（须先打开 so.news.cn 建立 Cookie，再用 page.request 带 Referer 请求；
  页面内 fetch 会被 WAF 返回 503 HTML）
"""

import asyncio
import json
import re
from urllib.parse import urlencode

from loguru import logger

from crawlers.cloak_browser import CloakBrowser
from crawlers.sites.common import (
    all_items_are_recent,
    check_and_retry_popup,
    filter_recent_news,
)

_API_BASE = "https://so.news.cn/getNews"
_SITE_ORIGIN = "https://so.news.cn/"
_MAX_PAGES = 50
_PAGE_SIZE = 10
_TAG_RE = re.compile(r"<[^>]+>")
_API_HEADERS = {
    "Referer": _SITE_ORIGIN,
    "Accept": "application/json, text/plain, */*",
}


def _strip_html(text: str) -> str:
    return _TAG_RE.sub("", text or "").strip()


def _parse_hash_params(search_url: str) -> tuple[int, int]:
    """从 hash 解析 searchFields、sortField"""
    if "#search/" not in search_url:
        return 0, 1
    parts = search_url.split("#search/", 1)[1].split("/")
    search_fields = int(parts[0]) if parts and parts[0].isdigit() else 0
    sort_field = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
    return search_fields, sort_field


def _scope_label(search_fields: int) -> str:
    return "标题" if search_fields == 1 else "全文"


def _item_from_record(rec: dict, keyword: str, site_name: str) -> dict | None:
    url = (rec.get("url") or "").strip()
    if not url:
        return None
    title = _strip_html(rec.get("title", ""))
    abstract = _strip_html(rec.get("des") or "")
    source = (rec.get("sitename") or site_name).strip()
    pub_time = (rec.get("pubtime") or "").strip()
    return {
        "title": title,
        "url": url,
        "publish_time": pub_time,
        "source": source,
        "keyword": keyword,
        "abstract": abstract,
    }


async def _api_fetch(
    page,
    keyword: str,
    cur_page: int,
    sort_field: int,
    search_fields: int,
) -> list[dict] | None:
    params = {
        "keyword": keyword,
        "curPage": cur_page,
        "sortField": sort_field,
        "searchFields": search_fields,
        "lang": "cn",
    }
    api_url = f"{_API_BASE}?{urlencode(params)}"
    text = ""
    try:
        response = await page.request.get(api_url, headers=_API_HEADERS)
        text = await response.text()
        if response.status != 200:
            logger.warning(
                f"[新华社] API 第{cur_page}页 HTTP {response.status}: {text[:120]!r}"
            )
            return None
        body = json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning(
            f"[新华社] API 第{cur_page}页 非 JSON 响应: {e}; head={text[:120]!r}"
        )
        return None
    except Exception as e:
        logger.warning(f"[新华社] API 第{cur_page}页 请求失败: {e}")
        return None

    code = body.get("code")
    content = body.get("content")
    if code is not None and code != 200:
        if isinstance(content, str) and "没有找到" in content:
            return []
        logger.warning(f"[新华社] API 第{cur_page}页 code={code} content={content!r:.80}")
        return []

    if isinstance(content, str):
        if "没有找到" in content:
            return []
        logger.warning(f"[新华社] API 第{cur_page}页异常 content={content!r:.80}")
        return []

    if not isinstance(content, dict):
        return []

    return content.get("results") or []


async def _open_search_session(page, search_url: str, site_name: str) -> bool:
    retry_delays = [10, 30]
    for attempt, delay in enumerate(retry_delays, start=1):
        try:
            await page.goto(search_url, wait_until="networkidle", timeout=60000)
            await page.wait_for_timeout(2000)
        except Exception as e:
            logger.warning(f"[{site_name}] 搜索页加载失败 (尝试{attempt}/{len(retry_delays)}): {e}")
            if attempt < len(retry_delays):
                await asyncio.sleep(delay)
                continue
            return False

        if await check_and_retry_popup(page, site_name):
            return True
        if attempt >= len(retry_delays):
            logger.error(f"[{site_name}] 多次重试后仍被拦截")
            return False
    return False


async def search(
    browser: CloakBrowser,
    site: dict,
    keyword: str,
    keep_days: int,
    search_url: str,
) -> list[dict]:
    """新华网搜索（标题或全文，由 search_url hash 中 searchFields 决定）"""
    site_name = site["site_name"]
    search_fields, sort_field = _parse_hash_params(search_url or "")
    scope_label = _scope_label(search_fields)

    all_items: list[dict] = []
    page_no = 0

    async with browser.session() as page:
        if not await _open_search_session(page, search_url, site_name):
            return []

        while page_no < _MAX_PAGES:
            page_no += 1
            logger.debug(
                f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 第{page_no}页 "
                f"searchFields={search_fields} sortField={sort_field}"
            )

            records = await _api_fetch(
                page, keyword, page_no, sort_field, search_fields,
            )
            if records is None:
                break
            if not records:
                logger.info(
                    f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 "
                    f"第{page_no}页无结果，停止翻页"
                )
                break

            page_items = [
                item for rec in records
                if (item := _item_from_record(rec, keyword, site_name))
            ]
            all_items.extend(page_items)

            logger.info(
                f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 第{page_no}页: "
                f"解析 {len(page_items)} 条，累计 {len(all_items)} 条"
            )

            if len(records) < _PAGE_SIZE:
                break

            if not all_items_are_recent(page_items, keep_days):
                logger.info(
                    f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 "
                    f"第{page_no}页已出现超期条目，停止翻页"
                )
                break

            await asyncio.sleep(0.5)

    filtered = filter_recent_news(all_items, keep_days)
    logger.info(
        f"[{site_name}] 关键词 [{keyword}] {scope_label}检索结束: "
        f"翻{page_no}页, 解析 {len(all_items)} 条, "
        f"时间过滤后 {len(filtered)} 条（保留近 {keep_days} 天）"
    )
    return filtered
