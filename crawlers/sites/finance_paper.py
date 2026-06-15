"""
财经报纸类网站爬虫
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from crawlers.sites.common import search_generic_with_pagination


async def search(browser, site: dict, keyword: str, keep_days: int, search_url: str) -> list[dict]:
    return await search_generic_with_pagination(
        browser, search_url, keyword,
        site["site_name"], site.get("site_url", ""), keep_days,
    )
