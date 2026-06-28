"""
中国日报（中国日报网）高级搜索爬虫
- 高级搜索: https://newssearch.chinadaily.com.cn/cn/search/advanced
- API: GET https://newssearch.chinadaily.com.cn/rest/cn/search
- titleMust 标题 / fullMust 全文（由 site_crawler 分别调用）
- sort=dp 最新、duplication=on 去重、publishedDateFrom/To 限定日期
"""

import asyncio
import json
import re
from urllib.parse import parse_qs, urlencode, urlparse

from loguru import logger

from crawlers.cloak_browser import CloakBrowser
from crawlers.sites.common import all_items_are_recent, filter_recent_news
from utils.timezone import recent_date_range_str

_API_URL = "https://newssearch.chinadaily.com.cn/rest/cn/search"
_REFERER = "https://newssearch.chinadaily.com.cn/cn/search/advanced"
_MAX_PAGES = 50
_PAGE_SIZE = 10
_API_TIMEOUT_MS = 20000   # API 应快速响应，20s 超时足够
_MAX_RETRIES = 4           # 增加重试次数
_TAG_RE = re.compile(r"<[^>]+>")
_SNIPPET_MAX = 500
_API_HEADERS = {
    "referer": _REFERER,
    "x-requested-with": "XMLHttpRequest",
    "Accept": "application/json, text/plain, */*",
}


def _strip_html(text: str) -> str:
    return _TAG_RE.sub("", text or "").strip()


def _scope_from_url(search_url: str) -> str:
    params = parse_qs(urlparse(search_url or "").query)
    scope = (params.get("scope") or ["body"])[0].lower()
    return "title" if scope == "title" else "body"


def _scope_label(scope: str) -> str:
    return "标题" if scope == "title" else "全文"


def _date_range(keep_days: int) -> tuple[str, str]:
    start, end = recent_date_range_str(keep_days)
    return start, end


def _build_params(keyword: str, scope: str, keep_days: int, page: int) -> dict[str, str]:
    date_from, date_to = _date_range(keep_days)
    params: dict[str, str] = {
        "sort": "dp",
        "duplication": "on",
        "publishedDateFrom": date_from,
        "publishedDateTo": date_to,
        "page": str(page),
        "curType": "story",
    }
    if scope == "title":
        params["titleMust"] = keyword
    else:
        params["fullMust"] = keyword
    return params


def _item_from_article(article: dict, keyword: str, site_name: str) -> dict | None:
    title = _strip_html(article.get("title", ""))
    url = (article.get("url") or "").strip()
    if not title or not url:
        return None

    pub_time = (article.get("pubDateStr") or "").strip()
    source = (article.get("source") or site_name).strip()

    snippet = _strip_html(article.get("highlightContent") or "")
    if not snippet:
        plain = _strip_html(article.get("plainText") or "")
        snippet = plain[:_SNIPPET_MAX] if plain else ""

    return {
        "title": title,
        "url": url,
        "publish_time": pub_time,
        "source": source,
        "keyword": keyword,
        "abstract": snippet,
    }


async def _fetch_api_json(page, params: dict[str, str]) -> dict | None:
    """请求搜索 API：page.request 长超时 + 重试；失败时用页面内 fetch 兜底"""
    api_url = f"{_API_URL}?{urlencode(params)}"

    for attempt in range(1, _MAX_RETRIES + 1):
        text = ""
        try:
            response = await page.request.get(
                _API_URL,
                params=params,
                headers=_API_HEADERS,
                timeout=_API_TIMEOUT_MS,
            )
            text = await response.text()
            if response.status == 200:
                return json.loads(text)
            logger.debug(
                f"[中国日报] page.request 第{attempt}次 HTTP {response.status}: {text[:100]!r}"
            )
        except json.JSONDecodeError as e:
            logger.warning(f"[中国日报] API 非 JSON: {e}; head={text[:100]!r}")
        except Exception as e:
            logger.debug(f"[中国日报] page.request 第{attempt}次异常: {e}")

        try:
            ev = await page.evaluate(
                """async ({url, hdr, timeoutMs}) => {
                    const ctrl = new AbortController();
                    const tid = setTimeout(() => ctrl.abort(), timeoutMs);
                    try {
                        const r = await fetch(url, { headers: hdr, signal: ctrl.signal });
                        const text = await r.text();
                        return { ok: true, status: r.status, text };
                    } catch (e) {
                        return { ok: false, error: String(e) };
                    } finally {
                        clearTimeout(tid);
                    }
                }""",
                {"url": api_url, "hdr": _API_HEADERS, "timeoutMs": _API_TIMEOUT_MS},
            )
            if ev.get("ok") and ev.get("status") == 200:
                return json.loads(ev["text"])
            logger.debug(
                f"[中国日报] fetch 第{attempt}次: status={ev.get('status')} err={ev.get('error')}"
            )
        except json.JSONDecodeError as e:
            logger.warning(f"[中国日报] API 非 JSON (fetch): {e}")
        except Exception as e:
            logger.debug(f"[中国日报] fetch 第{attempt}次异常: {e}")

        if attempt < _MAX_RETRIES:
            await page.goto(_REFERER, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(1500)

    return None


async def _open_session(page, site_name: str) -> bool:
    try:
        await page.goto(_REFERER, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(1500)
        return True
    except Exception as e:
        logger.warning(f"[{site_name}] 高级搜索页加载失败: {e}")
        return False


async def search(
        browser: CloakBrowser,
        site: dict,
        keyword: str,
        keep_days: int,
        search_url: str,
) -> list[dict]:
    """中国日报高级搜索（标题或全文，由 search_url 中 scope 决定）"""
    site_name = site["site_name"]
    scope = _scope_from_url(search_url)
    scope_label = _scope_label(scope)

    all_items: list[dict] = []
    page_no = -1
    total_pages = _MAX_PAGES

    async with browser.session() as page:
        if not await _open_session(page, site_name):
            return []

        while page_no + 1 < total_pages and page_no + 1 < _MAX_PAGES:
            page_no += 1
            params = _build_params(keyword, scope, keep_days, page_no)
            logger.debug(
                f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 第{page_no + 1}页 "
                f"date={params['publishedDateFrom']}~{params['publishedDateTo']}"
            )

            result = await _fetch_api_json(page, params)
            if result is None:
                logger.warning(
                    f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 "
                    f"第{page_no + 1}页请求失败（已重试 {_MAX_RETRIES} 次）"
                )
                break

            articles = result.get("content") or []
            if not articles:
                logger.info(
                    f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 "
                    f"第{page_no + 1}页无结果，停止翻页"
                )
                break

            page_items = [
                item for art in articles
                if (item := _item_from_article(art, keyword, site_name))
            ]
            all_items.extend(page_items)

            total_pages = min(int(result.get("totalPages") or 1), _MAX_PAGES)

            logger.info(
                f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 第{page_no + 1}页: "
                f"解析 {len(page_items)} 条，累计 {len(all_items)} 条"
            )

            if len(articles) < _PAGE_SIZE:
                break

            if not all_items_are_recent(page_items, keep_days):
                logger.info(
                    f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 "
                    f"第{page_no + 1}页已出现超期条目，停止翻页"
                )
                break

            if page_no + 1 >= total_pages:
                break

            await asyncio.sleep(0.5)

    filtered = filter_recent_news(all_items, keep_days)
    logger.info(
        f"[{site_name}] 关键词 [{keyword}] {scope_label}检索结束: "
        f"翻{page_no + 1}页, 解析 {len(all_items)} 条, "
        f"时间过滤后 {len(filtered)} 条（保留近 {keep_days} 天）"
    )
    return filtered
