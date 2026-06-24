"""
中国新闻社（中国新闻网）搜索爬虫
搜索入口: https://sou.chinanews.com.cn/search.do

实现说明：
- 该站搜索接口为 https://sou.chinanews.com.cn/search/news
- 通过 GET 请求带上 q/searchField/sortType/dateType/pageNum 等参数即可获取结果
- 服务端返回的是 HTML 页面，页面内嵌了 JavaScript 变量 var docArr = [...]
- 爬虫用正则提取 docArr 的 JSON 内容并解析，因此既不是纯接口调用，也不是 DOM 解析
"""

import asyncio
import json
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from loguru import logger

from crawlers.cloak_browser import CloakBrowser

_SEARCH_URL = "https://sou.chinanews.com.cn/search/news"
_MAX_SAFE_PAGES = 50
_PAGE_SIZE = 10


def _parse_date(ts_str: str | None) -> datetime:
    if not ts_str:
        return datetime.min
    # 时间字段可能是 "2026-06-24 16:59:48" 或 "2026-06-24"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue
    return datetime.min


def _all_items_are_recent(items: list[dict], keep_days: int) -> bool:
    """当整页结果都在保留窗口内时，认为后续页也无需继续翻页（按时间倒序）"""
    if not items or keep_days <= 0:
        return False
    cutoff = datetime.now() - timedelta(days=keep_days)
    return all(
        _parse_date(item.get("pubtime") or item.get("createtime")) >= cutoff
        for item in items
    )


async def search(
    browser: CloakBrowser,
    site: dict[str, Any],
    keyword: str,
    keep_days: int,
    search_url: str,
) -> list[dict[str, Any]]:
    """
    在中国新闻网搜索关键词。

    筛选条件固定为：
      - 搜索位置: 标题或正文 (searchField=all)
      - 排序方式: 按时间倒序 (sortType=time)
      - 时间范围: 近三日 (dateType=3day)
    """
    site_name = site["site_name"]
    search_url = search_url or site.get("search_url") or f"https://sou.chinanews.com.cn/search.do?q={keyword}"

    all_items: list[dict[str, Any]] = []
    page_no = 1
    total_fetched = 0

    async with browser.session() as page:
        while page_no <= _MAX_SAFE_PAGES:
            params = {
                "q": keyword,
                "searchField": "all",
                "sortType": "time",
                "dateType": "3day",
                "startDate": "",
                "endDate": "",
                "channel": "all",
                "editor": "",
                "shouQiFlag": "show",
                "pageNum": str(page_no),
            }
            logger.debug(
                f"[{site_name}] 关键词 [{keyword}] 请求第{page_no}页: "
                f"{_SEARCH_URL}?{urlencode(params)}"
            )

            try:
                response = await page.request.get(_SEARCH_URL, params=params)
                text = await response.text()
            except Exception as e:
                logger.warning(f"[{site_name}] 关键词 [{keyword}] 第{page_no}页请求失败: {e}")
                break

            m = re.search(r"var docArr\s*=\s*(\[.*?\]);", text, re.DOTALL)
            if not m:
                logger.warning(f"[{site_name}] 关键词 [{keyword}] 第{page_no}页未解析到 docArr")
                break

            try:
                articles = json.loads(m.group(1))
            except Exception as e:
                logger.warning(
                    f"[{site_name}] 关键词 [{keyword}] 第{page_no}页 docArr JSON 解析失败: {e}"
                )
                break

            total_fetched += len(articles)

            if not articles:
                logger.info(f"[{site_name}] 关键词 [{keyword}] 第{page_no}页无结果，停止翻页")
                break

            page_valid = 0
            for item in articles:
                pub_dt = _parse_date(item.get("pubtime") or item.get("createtime"))
                if pub_dt == datetime.min:
                    logger.debug(f"[{site_name}] 跳过无发布时间条目: {item.get('title', '')[:30]}")
                    continue

                if keep_days > 0 and pub_dt < datetime.now() - timedelta(days=keep_days):
                    logger.debug(
                        f"[{site_name}] 跳过超期条目: {item.get('title', '')[:30]} "
                        f"({pub_dt.strftime('%Y-%m-%d %H:%M')}, 保留{keep_days}天)"
                    )
                    continue

                all_items.append(
                    {
                        "title": item.get("title", "").strip(),
                        "url": item.get("url", "").strip(),
                        "publish_time": pub_dt,
                        "source": site_name,
                        "matched_keyword": keyword,
                    }
                )
                page_valid += 1

            logger.info(
                f"[{site_name}] 关键词 [{keyword}] 第{page_no}页: "
                f"接口返回 {len(articles)} 条, 经时间过滤后有效 {page_valid} 条"
            )

            if len(articles) < _PAGE_SIZE:
                logger.info(
                    f"[{site_name}] 关键词 [{keyword}] 第{page_no}页不足 {_PAGE_SIZE} 条，停止翻页"
                )
                break

            # 翻页前检查：若本页全部在保留窗口内，后续页也无需继续翻页
            if _all_items_are_recent(articles, keep_days):
                logger.info(
                    f"[{site_name}] 关键词 [{keyword}] 第{page_no}页全部在保留窗口内，停止翻页"
                )
                break

            page_no += 1
            await asyncio.sleep(0.5)

    logger.info(
        f"[{site_name}] 关键词 [{keyword}] 爬取结束: "
        f"共翻{page_no}页, 接口总计返回 {total_fetched} 条, 时间过滤后 {len(all_items)} 条"
    )
    return all_items
