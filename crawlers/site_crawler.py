"""
通用网站新闻爬虫
- 从数据库读取所有启用的网站（有 search_url 的）
- 从数据库读取所有启用的关键词
- 每个网站依次爬取每个关键词（单网站内串行）
- 多个网站可并发爬取（受 MAX_CONCURRENT_SITES 控制）
- 使用 CloakBrowser 有头浏览器
- 只保存标题、URL、来源、发布时间、匹配关键词
- 不保存 content、summary、raw_html
"""

import asyncio
import time
from urllib.parse import quote

from loguru import logger
from sqlalchemy import text

from config import CrawlerConfig
from db.pool import get_engine, close_global_engine
from db.init_db import init_database
from crawlers.cloak_browser import CloakBrowser
from crawlers.sites import get_search_handler
from crawlers.sites.common import save_news_to_db
from utils.keyword_hit import (
    CRAWL_SCOPE_ALL,
    build_search_url,
    crawl_scope_to_match_source,
    resolve_crawl_modes,
)

# 初始化日志文件输出（直接运行本文件时生效）
from pathlib import Path as _Path
_LOG_DIR = _Path(__file__).resolve().parent.parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)
logger.add(
    _LOG_DIR / "crawler_{time:YYYY-MM-DD}.log",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
    level="DEBUG",
    rotation="10 MB",
    retention="7 days",
    encoding="utf-8",
    enqueue=True,
)


# ============================================================
# 数据库读取
# ============================================================

async def get_active_sites() -> list[dict]:
    """从数据库获取所有有 search_url 的启用网站（含分类与搜索范围配置）"""
    engine = await get_engine()
    async with engine.connect() as conn:
        rows = (await conn.execute(text("""
            SELECT id, site_name, site_url, search_url, search_url_title, search_url_body,
                   search_scope_support, category
            FROM crawl_sites
            WHERE is_active = TRUE AND search_url IS NOT NULL AND search_url != ''
            ORDER BY sort_order
        """))).mappings().fetchall()
    return [dict(r) for r in rows]


async def get_all_keywords() -> list[str]:
    """从数据库获取所有启用的关键词"""
    engine = await get_engine()
    async with engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT keyword FROM crawl_keywords WHERE is_active = TRUE ORDER BY priority DESC"
        ))).mappings().fetchall()
    return [r["keyword"] for r in rows]


# ============================================================
# 单站点爬取（关键词串行）
# ============================================================

async def crawl_site(site: dict, keywords: list[str], browser: CloakBrowser) -> int:
    """
    爬取单个网站的所有关键词（串行）
    根据 site['category'] 路由到对应分类脚本
    返回实际新增条数
    """
    site_name = site["site_name"]
    site_id = site.get("id")
    site_url = site.get("site_url", "")
    category = site.get("category", "")
    keep_days = CrawlerConfig.KEEP_RECENT_DAYS
    crawl_modes = resolve_crawl_modes(site)
    if "法治日报" in site_name or "legaldaily" in site_url.lower():
        crawl_modes = [CRAWL_SCOPE_ALL]
        from crawlers.sites import legaldaily
        handler = legaldaily
    else:
        handler = get_search_handler(category)

    start_time = time.time()
    if "法治日报" in site_name or "legaldaily" in site_url.lower():
        scope_label = "title+body"
    else:
        scope_label = "+".join(crawl_modes)
    logger.info(
        f"[{site_name}] 开始爬取，共 {len(keywords)} 个关键词，"
        f"搜索模式 [{scope_label}]，保留近 {keep_days} 天"
    )
    total_saved = 0

    for i, keyword in enumerate(keywords):
        logger.info(f"[{site_name}] [{i+1}/{len(keywords)}] 关键词: {keyword}")

        encoded_kw = quote(keyword)
        keyword_saved = 0

        for crawl_scope in crawl_modes:
            search_url = build_search_url(site, encoded_kw, crawl_scope)
            if not search_url:
                logger.warning(f"[{site_name}] 关键词 [{keyword}] 模式 [{crawl_scope}] 无可用 URL，跳过")
                continue

            logger.debug(f"[{site_name}] 模式 [{crawl_scope}] 搜索URL: {search_url}")

            try:
                all_items = await handler.search(
                    browser, site, keyword, keep_days, search_url,
                )

                for item in all_items:
                    if not item.get("keyword"):
                        item["keyword"] = keyword
                    if not item.get("match_source"):
                        item["match_source"] = crawl_scope_to_match_source(crawl_scope)

                logger.info(
                    f"[{site_name}] 关键词 [{keyword}] 模式 [{crawl_scope}] "
                    f"解析到 {len(all_items)} 条（已按时间过滤）"
                )

                if not all_items:
                    continue

                saved = await save_news_to_db(all_items, site_id)
                keyword_saved += saved
                duplicated = len(all_items) - saved
                logger.info(
                    f"[{site_name}] 关键词 [{keyword}] 模式 [{crawl_scope}] 入库: "
                    f"新增 {saved} 条, 重复/合并跳过 {duplicated} 条"
                )

                if crawl_scope != crawl_modes[-1]:
                    await CloakBrowser.human_delay(1.0, 2.0)

            except Exception as e:
                logger.error(
                    f"[{site_name}] 关键词 [{keyword}] 模式 [{crawl_scope}] 爬取失败: {e}"
                )
                continue

        total_saved += keyword_saved

        # 关键词间延迟
        if i < len(keywords) - 1:
            if (
                ("人民网" in site_name or "people.com.cn" in site_url.lower())
                and "人民政协" not in site_name
            ):
                wait_sec = CrawlerConfig.PEOPLE_KEYWORD_INTERVAL_SEC
                logger.debug(f"[{site_name}] 遵守爬虫协议，等待 {wait_sec}s...")
                await asyncio.sleep(wait_sec)
            else:
                await CloakBrowser.human_delay(2.0, 4.0)

    elapsed = time.time() - start_time
    logger.info(f"[{site_name}] 爬取完成，实际新增 {total_saved} 条，耗时 {elapsed:.1f} 秒")
    return total_saved


# ============================================================
# 多站点并发爬取
# ============================================================

async def crawl_all_sites(site_names: list[str] | None = None):
    """
    爬取所有（或指定）网站
    - 每个网站内部关键词串行
    - 多个网站并发（受 MAX_CONCURRENT_SITES 控制）
    """
    await init_database()

    sites = await get_active_sites()
    if site_names:
        sites = [s for s in sites if s["site_name"] in site_names]

    if not sites:
        logger.error("没有可爬取的网站（需要有 search_url 配置）")
        return

    keywords = await get_all_keywords()
    if not keywords:
        logger.error("没有启用的关键词")
        return

    total_start = time.time()
    max_concurrent = CrawlerConfig.MAX_CONCURRENT_SITES
    logger.info(f"{'='*60}")
    logger.info(f"新闻爬虫启动")
    logger.info(f"网站数: {len(sites)} | 关键词数: {len(keywords)} | 并发数: {max_concurrent}")
    logger.info(f"保留最近 {CrawlerConfig.KEEP_RECENT_DAYS} 天新闻")
    logger.info(f"网站列表: {', '.join(s['site_name'] for s in sites)}")
    logger.info(f"关键词: {', '.join(keywords)}")
    logger.info(f"{'='*60}")

    # 用信号量控制并发
    semaphore = asyncio.Semaphore(max_concurrent)
    total_saved = 0

    async def _crawl_with_semaphore(site: dict) -> int:
        async with semaphore:
            # 每个站点创建独立的 CloakBrowser 实例
            browser = CloakBrowser(headless=CrawlerConfig.HEADLESS)
            try:
                return await crawl_site(site, keywords, browser)
            finally:
                await browser.close()

    # 并发执行所有站点
    tasks = [_crawl_with_semaphore(site) for site in sites]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for site, result in zip(sites, results):
        if isinstance(result, Exception):
            logger.error(f"[{site['site_name']}] 爬取异常: {result}")
        else:
            total_saved += result

    total_elapsed = time.time() - total_start
    logger.info(f"\n{'='*60}")
    logger.info(f"全部爬取完成 | 总计保存 {total_saved} 条新闻 | 总耗时 {total_elapsed:.1f} 秒")
    logger.info(f"{'='*60}")

    await close_global_engine()


# ============================================================
# 入口
# ============================================================

if __name__ == "__main__":
    asyncio.run(crawl_all_sites())
