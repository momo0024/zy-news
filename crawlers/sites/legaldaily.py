"""
法治日报爬虫
- 搜索页: http://www.legaldaily.com.cn/founder/SearchServlet.do
- 数据接口: POST http://www.legaldaily.com.cn/xy/Search.do
- 仅高级搜索两种模式：按标题、按正文（无综合搜索第三种）
"""

from loguru import logger

from crawlers.cloak_browser import CloakBrowser
from crawlers.sites.common import all_items_are_recent, filter_recent_news
from utils.keyword_hit import (
    CRAWL_SCOPE_BODY,
    CRAWL_SCOPE_TITLE,
    MATCH_SOURCE_SITE_BODY,
    MATCH_SOURCE_SITE_TITLE,
)
from utils.timezone import recent_date_range_str

_SEARCH_PAGE = "http://www.legaldaily.com.cn/founder/SearchServlet.do"
_API_URL = "http://www.legaldaily.com.cn/xy/Search.do"
_DEFAULT_SITE_ID = "4"
_PAGE_SIZE = 20
_MAX_PAGES = 50


async def search(
    browser: CloakBrowser,
    site: dict,
    keyword: str,
    keep_days: int,
    search_url: str,
) -> list[dict]:
    """法治日报：依次执行标题高级检索、正文高级检索"""
    site_name = site["site_name"]
    all_items: list[dict] = []

    start_str, end_str = recent_date_range_str(keep_days)

    async with browser.session() as page:
        logger.debug(f"[{site_name}] 打开高级搜索页: {_SEARCH_PAGE}")
        await page.goto(_SEARCH_PAGE, wait_until="domcontentloaded", timeout=30000)
        await browser.human_delay(0.5, 1.0)

        # cookie xy_search_siteID 常为 1（全站默认），法治日报 API 需 siteID=4
        site_id = _DEFAULT_SITE_ID

        for crawl_scope, field_name, match_source in (
            (CRAWL_SCOPE_TITLE, "title", MATCH_SOURCE_SITE_TITLE),
            (CRAWL_SCOPE_BODY, "content", MATCH_SOURCE_SITE_BODY),
        ):
            mode_items = await _search_advanced(
                page,
                browser,
                site_name=site_name,
                keyword=keyword,
                keep_days=keep_days,
                crawl_scope=crawl_scope,
                field_name=field_name,
                match_source=match_source,
                site_id=site_id,
                start_str=start_str,
                end_str=end_str,
            )
            all_items.extend(mode_items)
            if crawl_scope == CRAWL_SCOPE_TITLE:
                await CloakBrowser.human_delay(1.0, 2.0)

    logger.info(
        f"[{site_name}] 关键词 [{keyword}] 标题+正文高级检索完成，"
        f"共 {len(all_items)} 条（含重复 URL）"
    )
    return filter_recent_news(all_items, keep_days)


async def _search_advanced(
    page,
    browser: CloakBrowser,
    *,
    site_name: str,
    keyword: str,
    keep_days: int,
    crawl_scope: str,
    field_name: str,
    match_source: str,
    site_id: str,
    start_str: str,
    end_str: str,
) -> list[dict]:
    mode_label = "标题" if crawl_scope == CRAWL_SCOPE_TITLE else "正文"
    logger.info(
        f"[{site_name}] 关键词 [{keyword}] 高级搜索-{mode_label}，"
        f"日期 {start_str} ~ {end_str}"
    )

    all_items: list[dict] = []
    page_no = 1

    while page_no <= _MAX_PAGES:
        form = {
            "pageNo": str(page_no),
            "pageSize": str(_PAGE_SIZE),
            "channel": "1",
            "sort": "date desc",
            "siteID": site_id,
            "nodeID": "",
            "q": "",
            "startDate": start_str,
            "endDate": end_str,
            field_name: keyword,
        }

        try:
            response = await page.request.post(_API_URL, form=form)
            if not response.ok:
                logger.warning(
                    f"[{site_name}] {mode_label}检索第{page_no}页 HTTP {response.status}"
                )
                break
            result = await response.json()
        except Exception as e:
            logger.warning(f"[{site_name}] {mode_label}检索第{page_no}页请求失败: {e}")
            break

        if result.get("errMsg"):
            logger.warning(f"[{site_name}] {mode_label}检索: {result['errMsg']}")
            break

        articles = result.get("article") or []
        if not articles:
            break

        page_items = []
        for article in articles:
            item = _parse_article(article, keyword, site_name, match_source)
            if item:
                page_items.append(item)

        all_items.extend(page_items)

        if not all_items_are_recent(page_items, keep_days):
            logger.info(
                f"[{site_name}] {mode_label}第{page_no}页已出现非近{keep_days}天新闻，停止翻页"
            )
            break

        found_num = int(result.get("foundNum") or 0)
        if page_no * _PAGE_SIZE >= found_num:
            break

        page_no += 1
        await CloakBrowser.human_delay(1.0, 2.0)

    logger.info(f"[{site_name}] {mode_label}检索完成，解析 {len(all_items)} 条")
    return all_items


def _parse_article(
    article: dict,
    keyword: str,
    site_name: str,
    match_source: str,
) -> dict | None:
    title = (article.get("title") or "").strip()
    url = (article.get("url") or "").strip()
    date_str = (article.get("date") or "").strip()
    source = (article.get("sourcename") or "").strip() or site_name

    if not title or not url:
        return None

    if not url.startswith("http"):
        url = "http://www.legaldaily.com.cn" + (url if url.startswith("/") else "/" + url)

    return {
        "title": title,
        "url": url,
        "publish_time": date_str,
        "source": source,
        "keyword": keyword,
        "match_source": match_source,
    }
