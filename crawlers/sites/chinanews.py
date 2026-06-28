"""
中国新闻网搜索爬虫
- 搜索接口: https://sou.chinanews.com.cn/search/news
- searchField=title 标题检索 / content 正文检索（由 site_crawler 分别调用）
- sortType=time 时间倒序
- dateType=Nday（1/2/3）或 startDate/endDate
"""

import asyncio
import json
import re
from urllib.parse import parse_qs, urlencode, urlparse

from loguru import logger

from crawlers.cloak_browser import CloakBrowser
from crawlers.sites.common import all_items_are_recent, filter_recent_news
from utils.timezone import recent_date_range_str

_SEARCH_URL = "https://sou.chinanews.com.cn/search/news"
_MAX_SAFE_PAGES = 50
_PAGE_SIZE = 10
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return _TAG_RE.sub("", text or "").strip()


def _search_field_from_url(search_url: str) -> str:
    params = parse_qs(urlparse(search_url).query)
    field = (params.get("searchField") or ["all"])[0].lower()
    if field == "title":
        return "title"
    if field in ("content", "body"):
        return "content"
    return "all"


def _scope_label(field: str) -> str:
    if field == "title":
        return "标题"
    if field in ("content", "body"):
        return "正文"
    return "综合"


def _date_params(keep_days: int) -> dict[str, str]:
    if keep_days in (1, 2, 3):
        return {"dateType": f"{keep_days}day", "startDate": "", "endDate": ""}
    start, end = recent_date_range_str(keep_days)
    return {"dateType": "", "startDate": start, "endDate": end}


def _parse_doc_arr(text: str) -> list[dict]:
    m = re.search(r"var docArr\s*=\s*(\[.*?\]);", text, re.DOTALL)
    if not m:
        return []
    return json.loads(m.group(1))


def _safe_str(val) -> str:
    if isinstance(val, list):
        val = val[0] if val else ""
    return str(val or "").strip()


def _item_from_doc(doc: dict, keyword: str, site_name: str) -> dict:
    pub_time = _safe_str(doc.get("pubtime") or doc.get("createtime"))
    abstract = _strip_html(_safe_str(doc.get("content_without_tag")))
    return {
        "title": _safe_str(doc.get("title")),
        "url": _safe_str(doc.get("url")),
        "publish_time": pub_time,
        "source": site_name,
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
    """中国新闻网搜索（标题或正文，由 search_url 中 searchField 决定）"""
    site_name = site["site_name"]
    search_field = _search_field_from_url(search_url or "")
    scope_label = _scope_label(search_field)
    date_params = _date_params(keep_days)

    all_items: list[dict] = []
    page_no = 1
    total_fetched = 0

    async with browser.session() as page:
        while page_no <= _MAX_SAFE_PAGES:
            params = {
                "q": keyword,
                "searchField": search_field,
                "sortType": "time",
                **date_params,
                "channel": "all",
                "editor": "",
                "shouQiFlag": "show",
                "pageNum": str(page_no),
            }
            logger.debug(
                f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 第{page_no}页: "
                f"{_SEARCH_URL}?{urlencode(params)}"
            )

            try:
                response = await page.request.get(_SEARCH_URL, params=params)
                text = await response.text()
            except Exception as e:
                logger.warning(
                    f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 第{page_no}页请求失败: {e}"
                )
                break

            try:
                articles = _parse_doc_arr(text)
            except Exception as e:
                logger.warning(
                    f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 "
                    f"第{page_no}页 docArr 解析失败: {e}"
                )
                break

            if not articles:
                logger.info(
                    f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 "
                    f"第{page_no}页无结果，停止翻页"
                )
                break

            total_fetched += len(articles)
            page_items = [
                _item_from_doc(item, keyword, site_name)
                for item in articles
                if item.get("url")
            ]
            all_items.extend(page_items)

            logger.info(
                f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 第{page_no}页: "
                f"解析 {len(page_items)} 条，累计 {len(all_items)} 条"
            )

            if len(articles) < _PAGE_SIZE:
                break

            if not all_items_are_recent(page_items, keep_days):
                logger.info(
                    f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 "
                    f"第{page_no}页已出现超期条目，停止翻页"
                )
                break

            page_no += 1
            await asyncio.sleep(0.5)

    filtered = filter_recent_news(all_items, keep_days)
    logger.info(
        f"[{site_name}] 关键词 [{keyword}] {scope_label}检索结束: "
        f"翻{page_no}页, 接口返回 {total_fetched} 条, "
        f"时间过滤后 {len(filtered)} 条（保留近 {keep_days} 天）"
    )
    return filtered
