"""
人民政协报（人民政协网）爬虫
- 搜索入口: http://apply.rmzxb.com/unicms/search/result
- 参数: SiteID=14, Query, PageIndex, Sort=PublishDate
- TitleOnly=Y 标题检索 / TitleOnly=N 全文检索
- usingSynonym=N 不启用同义词
"""

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from loguru import logger

from crawlers.cloak_browser import CloakBrowser
from crawlers.sites.common import all_items_are_recent, filter_recent_news

_BASE_PARAMS = {
    "SiteID": "14",
    "Sort": "PublishDate",
    "usingSynonym": "N",
}
_MAX_PAGES = 50


def _parse_results(html: str, keyword: str, site_name: str) -> list[dict]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []

    for block in soup.select(".searchResults"):
        try:
            title_el = block.select_one("p.fz16.line24 a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            url = (title_el.get("href") or "").strip()

            paragraphs = block.select("p")
            abstract = paragraphs[1].get_text(strip=True) if len(paragraphs) >= 2 else ""

            pub_time = ""
            meta_text = block.get_text("\n", strip=True)
            m = re.search(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", meta_text)
            if m:
                pub_time = m.group(1)

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


def _build_page_url(search_url: str, page_index: int) -> str:
    parsed = urlparse(search_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    flat = {k: v[0] if v else "" for k, v in params.items()}
    flat["PageIndex"] = str(page_index)
    query = urlencode(flat)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", query, ""))


async def search(
    browser: CloakBrowser,
    site: dict,
    keyword: str,
    keep_days: int,
    search_url: str,
) -> list[dict]:
    """人民政协网搜索（标题或全文，由 search_url 中 TitleOnly 决定）"""
    site_name = site["site_name"]
    all_items: list[dict] = []
    page_index = 1

    async with browser.session() as page:
        while page_index <= _MAX_PAGES:
            page_url = _build_page_url(search_url, page_index)
            logger.debug(f"[{site_name}] 请求第{page_index}页: {page_url[:160]}")

            try:
                response = await page.request.get(page_url)
                if not response.ok:
                    logger.warning(
                        f"[{site_name}] 第{page_index}页 HTTP {response.status}"
                    )
                    break
                html = await response.text()
            except Exception as e:
                logger.warning(f"[{site_name}] 第{page_index}页请求失败: {e}")
                break

            page_items = _parse_results(html, keyword, site_name)
            if not page_items:
                logger.info(f"[{site_name}] 第{page_index}页无结果，停止翻页")
                break

            all_items.extend(page_items)
            logger.info(
                f"[{site_name}] 第{page_index}页解析 {len(page_items)} 条，"
                f"累计 {len(all_items)} 条"
            )

            if not all_items_are_recent(page_items, keep_days):
                logger.info(
                    f"[{site_name}] 第{page_index}页已混入非近{keep_days}天新闻，停止翻页"
                )
                break

            if not re.search(rf"PageIndex={page_index + 1}", html):
                break

            page_index += 1
            await CloakBrowser.human_delay(0.8, 1.5)

    logger.info(
        f"[{site_name}] 关键词 [{keyword}] 翻页完成，"
        f"共 {page_index} 页，解析 {len(all_items)} 条"
    )
    return filter_recent_news(all_items, keep_days)
