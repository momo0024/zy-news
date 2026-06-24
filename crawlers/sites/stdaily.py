"""
科技日报爬虫
- 搜索接口: POST https://search.stdaily.com:8888/xy/Search.do
- 筛选条件: 全文检索(channel=1)、一周内(startDate/endDate)、按时间排序(sort=date desc)
- 响应格式: JSON
- robots.txt: 允许所有爬虫访问
"""

from datetime import datetime, timedelta

from loguru import logger

from crawlers.cloak_browser import CloakBrowser
from crawlers.sites.common import filter_recent_news


_API_URL = "https://search.stdaily.com:8888/xy/Search.do"
_PAGE_SIZE = 20
_MAX_PAGES = 50


async def search(
    browser: CloakBrowser,
    site: dict,
    keyword: str,
    keep_days: int,
    search_url: str,
) -> list[dict]:
    """科技日报搜索入口

    Args:
        browser: CloakBrowser 实例
        site: 网站配置字典
        keyword: 搜索关键词
        keep_days: 保留最近 N 天
        search_url: 数据库配置的搜索 URL（含替换后的关键词）
    """
    site_name = site["site_name"]
    site_url = site.get("site_url", "https://www.stdaily.com")

    # 计算日期范围（一周内）
    end_date = datetime.now()
    start_date = end_date - timedelta(days=keep_days)
    end_date_str = end_date.strftime("%Y-%m-%d")
    start_date_str = start_date.strftime("%Y-%m-%d")

    all_items: list[dict] = []
    page_no = 0

    async with browser.session() as page:
        while page_no < _MAX_PAGES:
            try:
                response = await page.request.post(
                    _API_URL,
                    form={
                        "pageNo": str(page_no),
                        "pageSize": str(_PAGE_SIZE),
                        "channel": "1",           # 全文检索
                        "sort": "date desc",      # 按时间排序
                        "siteID": "1",
                        "nodeID": "",
                        "q": keyword,
                        "startDate": start_date_str,
                        "endDate": end_date_str,
                    },
                )
                result = await response.json()
            except Exception as e:
                logger.warning(f"[{site_name}] 第{page_no + 1}页请求失败: {e}")
                break

            articles = result.get("article", [])
            if not articles:
                break

            for article in articles:
                try:
                    title = article.get("title", "").strip()
                    url = article.get("url", "").strip()
                    date_str = article.get("date", "").strip()
                    source = article.get("sourcename", "") or site_name

                    if title and url:
                        all_items.append(
                            {
                                "title": title,
                                "url": url,
                                "publish_time": date_str,
                                "source": source,
                                "matched_keyword": keyword,
                            }
                        )
                except Exception as e:
                    logger.warning(f"[{site_name}] 解析条目失败: {e}")

            # 检查是否还有更多页
            found_num = result.get("foundNum", 0)
            if (page_no + 1) * _PAGE_SIZE >= found_num:
                break

            page_no += 1
            await CloakBrowser.human_delay(1.0, 2.0)

    logger.info(
        f"[{site_name}] 翻页完成，共 {page_no + 1} 页，解析 {len(all_items)} 条"
    )
    return filter_recent_news(all_items, keep_days)
