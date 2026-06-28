"""
学习时报爬虫
- 搜索入口: https://www.studytimes.cn/was5/web/search
- 标题检索: searchscope=doctitle
- 内容检索: searchscope=DOCCONTENT
- 时间: starttime / endtime 格式 YYYY.MM.DD（至少最近 2 天）
- 由 site_crawler 分别调用标题/内容两种 search_url，入库 match_source 区分
"""

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from loguru import logger

from crawlers.cloak_browser import CloakBrowser
from crawlers.sites.common import all_items_are_recent, filter_recent_news
from utils.timezone import recent_date_range_dots

_PAGE_SIZE = 10
_MAX_PAGES = 50
_NO_RESULT_TEXT = "对不起！没有发现你要找的内容"


def _normalize_pub_time(raw: str) -> str:
    """2026.06.27 11:25:00 → 2026-06-27 11:25:00"""
    text = (raw or "").strip()
    if not text:
        return ""
    parts = text.split(" ", 1)
    parts[0] = parts[0].replace(".", "-")
    return " ".join(parts)


def _scope_label(search_url: str) -> str:
    params = parse_qs(urlparse(search_url).query)
    scope = (params.get("searchscope") or [""])[0].lower()
    if scope == "doctitle":
        return "标题"
    if scope == "doccontent":
        return "内容"
    return scope or "未知"


def _parse_results(html: str, keyword: str, site_name: str) -> list[dict]:
    from bs4 import BeautifulSoup

    if _NO_RESULT_TEXT in html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []

    for block in soup.select(".result-list li"):
        try:
            title_el = block.select_one("h3 a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            url = (title_el.get("href") or "").strip()

            time_el = block.select_one("span.datetime")
            pub_time = _normalize_pub_time(time_el.get_text(strip=True) if time_el else "")

            abstract = ""
            for p in block.select("p"):
                if p.select_one("span.datetime"):
                    abstract = p.get_text(" ", strip=True)
                    dt_prefix = time_el.get_text(strip=True) if time_el else ""
                    if dt_prefix and abstract.startswith(dt_prefix):
                        abstract = abstract[len(dt_prefix):].strip()
                    break

            if title and url:
                items.append({
                    "title": title,
                    "url": url,
                    "publish_time": pub_time,
                    "source": site_name,
                    "keyword": keyword,
                    "abstract": abstract,
                })
        except Exception as e:
            logger.warning(f"[{site_name}] 解析条目失败: {e}")

    return items


def _build_page_url(search_url: str, keep_days: int, page: int = 1) -> str:
    """在 site_crawler 传入的 search_url 上追加日期与翻页"""
    start_str, end_str = recent_date_range_dots(keep_days)
    parsed = urlparse(search_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    flat = {k: v[0] if v else "" for k, v in params.items()}
    flat["starttime"] = start_str
    flat["endtime"] = end_str
    if page > 1:
        flat["page"] = str(page)
    else:
        flat.pop("page", None)
    query = urlencode(flat)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", query, ""))


def _parse_pagination(html: str) -> tuple[int, int]:
    count_m = re.search(r"var recordCount\s*=\s*(\d+)", html)
    size_m = re.search(r"var pageSize\s*=\s*(\d+)", html)
    record_count = int(count_m.group(1)) if count_m else 0
    page_size = int(size_m.group(1)) if size_m else _PAGE_SIZE
    return record_count, page_size


async def search(
    browser: CloakBrowser,
    site: dict,
    keyword: str,
    keep_days: int,
    search_url: str,
) -> list[dict]:
    """学习时报：单次搜索（标题或内容，由 search_url 中 searchscope 决定）"""
    site_name = site["site_name"]
    effective_days = max(keep_days, 2)
    mode = _scope_label(search_url)
    all_items: list[dict] = []
    page_no = 1
    record_count = None
    page_size = _PAGE_SIZE

    async with browser.session() as page:
        while page_no <= _MAX_PAGES:
            page_url = _build_page_url(search_url, effective_days, page_no)
            logger.debug(
                f"[{site_name}] [{mode}] 请求第{page_no}页: {page_url[:180]}"
            )

            try:
                response = await page.request.get(page_url)
                if not response.ok:
                    logger.warning(
                        f"[{site_name}] [{mode}] 第{page_no}页 HTTP {response.status}"
                    )
                    break
                html = await response.text()
            except Exception as e:
                logger.warning(f"[{site_name}] [{mode}] 第{page_no}页请求失败: {e}")
                break

            if page_no == 1:
                record_count, page_size = _parse_pagination(html)
                logger.info(
                    f"[{site_name}] 关键词 [{keyword}] [{mode}] "
                    f"时间 {recent_date_range_dots(effective_days)} "
                    f"共 {record_count} 条结果"
                )
                if record_count == 0:
                    break

            page_items = _parse_results(html, keyword, site_name)
            if not page_items:
                logger.info(f"[{site_name}] [{mode}] 第{page_no}页无结果，停止翻页")
                break

            all_items.extend(page_items)
            logger.info(
                f"[{site_name}] [{mode}] 第{page_no}页解析 {len(page_items)} 条，"
                f"累计 {len(all_items)} 条"
            )

            if not all_items_are_recent(page_items, effective_days):
                logger.info(
                    f"[{site_name}] [{mode}] 第{page_no}页已混入非近{effective_days}天新闻，停止翻页"
                )
                break

            if record_count is not None and page_no * page_size >= record_count:
                break

            page_no += 1
            await CloakBrowser.human_delay(0.8, 1.5)

    logger.info(
        f"[{site_name}] 关键词 [{keyword}] [{mode}] 翻页完成，"
        f"共 {page_no} 页，解析 {len(all_items)} 条"
    )
    return filter_recent_news(all_items, effective_days)
