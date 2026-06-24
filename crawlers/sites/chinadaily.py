"""
中国日报爬虫
- 搜索接口: GET https://newssearch.chinadaily.com.cn/rest/cn/search
- 筛选条件: sort=dp (Newest 最新), curType=story (文章)
- 翻页: page 从 0 开始，通过 totalPages 判断总页数
- 日期过滤: 通过 filter_recent_news 保留最近 N 天
- 遵守爬虫协议: 请求间隔 1-2 秒，单关键词串行翻页
"""

from datetime import datetime, timedelta

from loguru import logger

from crawlers.cloak_browser import CloakBrowser
from crawlers.sites.common import filter_recent_news

_API_URL = "https://newssearch.chinadaily.com.cn/rest/cn/search"
_MAX_SAFE_PAGES = 20


async def search(
    browser: CloakBrowser,
    site: dict,
    keyword: str,
    keep_days: int,
    search_url: str,
) -> list[dict]:
    """中国日报搜索入口

    Args:
        browser: CloakBrowser 实例
        site: 网站配置字典
        keyword: 搜索关键词
        keep_days: 保留最近 N 天
        search_url: 数据库配置的搜索 URL（含替换后的关键词，当前未使用，直接调 API）
    """
    site_name = site["site_name"]
    all_items: list[dict] = []
    page_no = 0

    async with browser.session() as page:
        while page_no < _MAX_SAFE_PAGES:
            try:
                params = {
                    "keywords": keyword,
                    "sort": "dp",           # Newest 最新
                    "page": str(page_no),
                    "curType": "story",     # 文章类型
                }
                response = await page.request.get(_API_URL, params=params)
                result = await response.json()
            except Exception as e:
                logger.warning(f"[{site_name}] 第{page_no + 1}页请求失败: {e}")
                break

            articles = result.get("content", [])
            if not articles:
                break

            for article in articles:
                try:
                    title = article.get("title", "").strip()
                    url = article.get("url", "").strip()

                    # 优先使用 pubDateStr，否则从 publishTime 时间戳转换
                    pub_date_str = article.get("pubDateStr", "").strip()
                    if not pub_date_str and article.get("publishTime"):
                        try:
                            ts = article["publishTime"]
                            if isinstance(ts, (int, float)):
                                # publishTime 是毫秒时间戳
                                dt = datetime.fromtimestamp(ts / 1000)
                                pub_date_str = dt.strftime("%Y-%m-%d %H:%M")
                        except Exception:
                            pass

                    source = article.get("source", "") or site_name

                    if title and url:
                        all_items.append({
                            "title": title,
                            "url": url,
                            "publish_time": pub_date_str,
                            "source": source,
                            "matched_keyword": keyword,
                        })
                except Exception as e:
                    logger.warning(f"[{site_name}] 解析条目失败: {e}")

            # 检查是否还有更多页
            total_pages = result.get("totalPages", 0)
            if page_no + 1 >= total_pages:
                logger.debug(f"[{site_name}] 已到最后一页（{total_pages}页）")
                break

            # 日期感知：如果当前页已混入非近 N 天新闻，停止翻页
            page_items = all_items[-len(articles):]
            if not _all_items_are_recent(page_items, keep_days):
                logger.info(f"[{site_name}] 第{page_no + 1}页已混入非近{keep_days}天新闻，停止翻页")
                break

            page_no += 1
            await CloakBrowser.human_delay(1.0, 2.0)

    logger.info(
        f"[{site_name}] 翻页完成，共 {page_no + 1} 页，解析 {len(all_items)} 条"
    )
    return filter_recent_news(all_items, keep_days)


def _all_items_are_recent(items: list[dict], keep_days: int) -> bool:
    """检查列表中是否全部都是最近 N 天的新闻"""
    if not items:
        return False
    cutoff = datetime.now() - timedelta(days=keep_days)
    for item in items:
        dt = _parse_item_date(item)
        if dt is None or dt < cutoff:
            return False
    return True


def _parse_item_date(item: dict) -> datetime | None:
    """从 item 字典中解析发布时间为 datetime"""
    pub_time = item.get("publish_time", "")
    if not pub_time:
        return None
    for fmt in [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d",
    ]:
        try:
            return datetime.strptime(str(pub_time).strip(), fmt)
        except ValueError:
            continue
    return None
