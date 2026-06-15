"""
湖北省内网站爬虫
包含：湖北省级、武汉市、黄石市、十堰市、宜昌市、襄阳市、鄂州市、荆门市等
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from loguru import logger

from crawlers.sites.common import (
    parse_generic_search_results,
    search_generic_with_pagination,
)


def _parse_jmnews(html: str, keyword: str, site_name: str, site_url: str) -> list[dict]:
    """解析荆门新闻网搜索结果"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    items = []

    for block in soup.select("dl.search-list dd"):
        try:
            title_el = block.select_one(".article.title a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            url = title_el.get("href", "")
            if url and not url.startswith("http"):
                url = "https://apps.jmnews.cn" + url if url.startswith("/") else "https://apps.jmnews.cn/" + url

            date_el = block.select_one("p.result var")
            date_str = date_el.get_text(strip=True) if date_el else ""

            if title and url:
                items.append({
                    "title": title,
                    "url": url,
                    "publish_time": date_str,
                    "source": site_name,
                    "matched_keyword": keyword,
                })
        except Exception as e:
            logger.warning(f"解析条目失败: {e}")
    return items


async def search(browser, site: dict, keyword: str, keep_days: int, search_url: str) -> list[dict]:
    site_name = site["site_name"]
    site_url = site.get("site_url", "")

    if "荆门" in site_name:
        # 荆门新闻使用特定解析器
        from crawlers.cloak_browser import CloakBrowser
        all_items = []
        page_num = 0
        max_pages = 50

        async with browser.session() as page:
            while page_num < max_pages:
                page_num += 1
                page_url = search_url if page_num == 1 else f"{search_url}&page={page_num}"
                await page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
                html = await page.content()
                page_items = _parse_jmnews(html, keyword, site_name, site_url)
                if not page_items:
                    break
                all_items.extend(page_items)
                await CloakBrowser.human_delay(1.0, 2.0)
        return all_items

    # 其他湖北网站使用通用翻页
    return await search_generic_with_pagination(
        browser, search_url, keyword, site_name, site_url, keep_days,
    )
