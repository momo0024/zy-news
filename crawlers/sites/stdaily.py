"""
科技日报爬虫
- 搜索页: https://search.stdaily.com:8888/founder/NewSearchServlet.do
- API: POST https://search.stdaily.com:8888/xy/Search.do
- title 标题 / content 正文（由 site_crawler 分别调用，q 置空）
- sort=date desc 时间排序、一天内日期范围
"""

import asyncio
import re
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

from loguru import logger

from crawlers.cloak_browser import CloakBrowser
from crawlers.sites.common import all_items_are_recent, filter_recent_news
from utils.timezone import APP_TZ, recent_date_range_str

_API_URL = "https://search.stdaily.com:8888/xy/Search.do"
_SEARCH_PAGE = "https://search.stdaily.com:8888/founder/NewSearchServlet.do?siteID=1"
_PAGE_SIZE = 20
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
    """keep_days=1 对齐站点「一天内」：昨天 0 点至今天"""
    now = datetime.now(APP_TZ)
    if keep_days <= 1:
        start = (now - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        end = now.replace(hour=23, minute=59, second=59, microsecond=0)
        return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    return recent_date_range_str(keep_days)


def _build_form(keyword: str, scope: str, keep_days: int, page_no: int) -> dict[str, str]:
    start_date, end_date = _date_range(keep_days)
    form: dict[str, str] = {
        "pageNo": str(page_no),
        "pageSize": str(_PAGE_SIZE),
        "channel": "1",
        "sort": "date desc",
        "siteID": "1",
        "nodeID": "",
        "q": "",
        "startDate": start_date,
        "endDate": end_date,
    }
    if scope == "title":
        form["title"] = keyword
    else:
        form["content"] = keyword
    return form


def _item_from_article(article: dict, keyword: str, site_name: str) -> dict | None:
    title = _strip_html(article.get("title", ""))
    url = (article.get("url") or "").strip()
    if not title or not url:
        return None

    date_str = (article.get("date") or "").strip()
    source = (article.get("sourcename") or site_name).strip()
    raw_snippet = _strip_html(article.get("enpcontent") or "")
    abstract = raw_snippet[:_SNIPPET_MAX] if raw_snippet else ""

    return {
        "title": title,
        "url": url,
        "publish_time": date_str,
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
    """科技日报搜索（标题或正文，由 search_url 中 scope 决定）"""
    site_name = site["site_name"]
    scope = _scope_from_url(search_url)
    scope_label = _scope_label(scope)

    all_items: list[dict] = []
    page_no = -1
    found_num = 0

    async with browser.session() as page:
        await page.goto(_SEARCH_PAGE, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(1000)

        while page_no + 1 < _MAX_PAGES:
            page_no += 1
            form = _build_form(keyword, scope, keep_days, page_no)
            logger.debug(
                f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 第{page_no + 1}页 "
                f"date={form['startDate']}~{form['endDate']}"
            )

            try:
                response = await page.request.post(
                    _API_URL,
                    form=form,
                    headers={"referer": _SEARCH_PAGE},
                )
                result = await response.json()
            except Exception as e:
                logger.warning(
                    f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 "
                    f"第{page_no + 1}页请求失败: {e}"
                )
                break

            articles = result.get("article") or []
            found_num = int(result.get("foundNum") or 0)

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

            logger.info(
                f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 第{page_no + 1}页: "
                f"解析 {len(page_items)} 条，累计 {len(all_items)} 条"
            )

            if (page_no + 1) * _PAGE_SIZE >= found_num:
                break

            if not all_items_are_recent(page_items, keep_days):
                logger.info(
                    f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 "
                    f"第{page_no + 1}页已出现超期条目，停止翻页"
                )
                break

            await asyncio.sleep(0.5)

    filtered = filter_recent_news(all_items, keep_days)
    logger.info(
        f"[{site_name}] 关键词 [{keyword}] {scope_label}检索结束: "
        f"翻{page_no + 1}页, 解析 {len(all_items)} 条, "
        f"时间过滤后 {len(filtered)} 条（保留近 {keep_days} 天）"
    )
    return filtered
