"""
新闻爬虫主逻辑
工作流程:
  1. 对每个关键词，使用 CloakBrowser 搜索百度新闻 (人类行为模拟)
  2. 获取新闻列表 (标题、URL、来源、时间)
  3. 逐条访问新闻详情页，用 CloakBrowser + 人类延迟获取 HTML
  4. 使用 ScrapeGraphAI (AIScraper) 提取结构化数据
  5. 构造 NewsItem 对象，可选存入 PostgreSQL
"""

import asyncio
import json
import random
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from config import SEARCH_KEYWORDS, CrawlerConfig
from models.news_item import NewsItem, NewsItemList
from scrapers.ai_scraper import AIScraper
from crawlers.cloak_browser import CloakBrowser


class NewsCrawler:
    """
    新闻爬虫 - 组合 CloakBrowser + ScrapeGraphAI

    使用方式:
        crawler = NewsCrawler()
        results = await crawler.run()

        # 带数据库写入
        results = await crawler.run(save_to_db=True)
    """

    def __init__(
        self,
        keywords: Optional[list[str]] = None,
        today_only: Optional[bool] = None,
        max_pages_per_keyword: Optional[int] = None,
        output_dir: Optional[str] = None,
    ):
        self.keywords = keywords or SEARCH_KEYWORDS
        self.today_only = today_only if today_only is not None else CrawlerConfig.TODAY_ONLY
        self.max_pages_per_keyword = max_pages_per_keyword or CrawlerConfig.MAX_PAGES_PER_KEYWORD
        self.output_dir = Path(output_dir) if output_dir else Path(__file__).parent.parent / "output"
        self.output_dir.mkdir(exist_ok=True)

        self.browser: Optional[CloakBrowser] = None
        self.scraper: Optional[AIScraper] = None
        self.all_results: list[NewsItemList] = []
        self._total_processed = 0

        logger.info(
            f"NewsCrawler 初始化 | 关键词数: {len(self.keywords)} | "
            f"当天模式: {self.today_only} | 每词页数: {self.max_pages_per_keyword}"
        )

    def _filter_today(self, items: list[dict]) -> list[dict]:
        """过滤当天新闻"""
        if not self.today_only:
            return items

        today_prefixes = [
            datetime.now().strftime("%Y-%m-%d"),
            datetime.now().strftime("%m月%d日"),
            datetime.now().strftime("%Y年%m月%d日"),
            "小时前", "分钟前", "刚刚", "今天", "今日",
        ]

        filtered = []
        for item in items:
            time_str = item.get("time", "")
            if not time_str:
                filtered.append(item)
                continue
            if any(prefix in time_str for prefix in today_prefixes):
                filtered.append(item)

        logger.debug(f"当天过滤: {len(items)} -> {len(filtered)}")
        return filtered

    async def _process_single_news(
        self,
        news_data: dict,
        keyword: str,
        index: int,
        total: int,
    ) -> Optional[NewsItem]:
        """
        处理单条新闻: 页面间延迟 -> 访问详情页 -> 提取结构化数据

        Args:
            news_data: {"title", "url", "source", "time", "abstract"}
            keyword: 搜索关键词
            index: 当前序号
            total: 总数

        Returns:
            NewsItem 或 None (失败时)
        """
        title = news_data.get("title", "")
        url = news_data.get("url", "")

        if not url:
            logger.warning(f"新闻缺少 URL，跳过: {title}")
            return None

        # ============================================================
        # 人类行为: 新闻间随机延迟 (模拟逐条阅读新闻)
        # ============================================================
        delay = random.uniform(CrawlerConfig.HUMAN_DELAY_MIN, CrawlerConfig.HUMAN_DELAY_MAX)
        logger.info(f"[{index}/{total}] 等待 {delay:.1f}s 后抓取: {title[:40]}...")
        await asyncio.sleep(delay)

        try:
            # 第1步: 使用 CloakBrowser 获取页面 HTML
            html = await self.browser.get_page_content(url)

            # 第2步: 使用 ScrapeGraphAI 提取结构化数据
            extracted = self.scraper.extract_from_html(
                html_content=html,
                url=url,
            )

            # 第3步: 合并搜索结果基本信息
            if not extracted.get("title"):
                extracted["title"] = title
            if not extracted.get("source"):
                extracted["source"] = news_data.get("source", "")
            if not extracted.get("publish_time"):
                extracted["publish_time"] = news_data.get("time", "")

            # 第4步: 构造 NewsItem
            news_item = NewsItem.from_scrape_result(
                result=extracted,
                matched_keyword=keyword,
                url=url,
            )

            logger.success(f"[{index}/{total}] 解析成功: {news_item.title[:50]}")
            return news_item

        except Exception as e:
            logger.error(f"[{index}/{total}] 解析失败: {title[:50]} | 错误: {e}")

            # 降级方案
            fallback = NewsItem(
                title=title,
                content=news_data.get("abstract", ""),
                publish_time=news_data.get("time", ""),
                source=news_data.get("source", ""),
                url=url,
                matched_keyword=keyword,
                summary=news_data.get("abstract", ""),
            )
            logger.info(f"[{index}/{total}] 降级处理: {fallback.title[:50]}")
            return fallback

    async def _crawl_keyword(self, keyword: str) -> NewsItemList:
        """
        爬取单个关键词的新闻

        Args:
            keyword: 搜索关键词

        Returns:
            NewsItemList 包含该关键词的所有新闻条目
        """
        logger.info(f"{'='*40}")
        logger.info(f"开始爬取关键词: 【{keyword}】")
        logger.info(f"{'='*40}")

        # 搜索获取新闻列表
        news_list = await self.browser.get_news_list_from_search(
            keyword=keyword,
            max_pages=self.max_pages_per_keyword,
        )

        if not news_list:
            logger.warning(f"关键词 [{keyword}] 未找到新闻")
            return NewsItemList(total=0, keyword=keyword)

        # 过滤当天新闻
        news_list = self._filter_today(news_list)

        if not news_list:
            logger.warning(f"关键词 [{keyword}] 无当天新闻")
            return NewsItemList(total=0, keyword=keyword)

        total = len(news_list)
        logger.info(f"关键词 [{keyword}] 共 {total} 条新闻待解析")

        # 逐条顺序抓取并解析 (限制并发)
        items = []
        semaphore = asyncio.Semaphore(CrawlerConfig.MAX_CONCURRENT)

        async def process_with_limit(idx, news_data):
            async with semaphore:
                return await self._process_single_news(news_data, keyword, idx, total)

        tasks = [process_with_limit(i + 1, news) for i, news in enumerate(news_list)]
        results = await asyncio.gather(*tasks)

        items = [r for r in results if r is not None]
        self._total_processed += len(items)

        logger.info(f"关键词 [{keyword}] 完成: {len(items)}/{total} 条 | 累计: {self._total_processed}")

        return NewsItemList(
            total=len(items),
            keyword=keyword,
            items=items,
        )

    async def run(self, save_to_db: bool = False) -> list[NewsItemList]:
        """
        执行爬虫主流程

        Args:
            save_to_db: 是否保存到数据库

        Returns:
            [NewsItemList, ...] 每个关键词一个列表
        """
        logger.info("=" * 60)
        logger.info("zy-news 新闻爬虫启动")
        logger.info(f"关键词 ({len(self.keywords)}): {', '.join(self.keywords[:3])}...")
        logger.info(f"当天模式: {self.today_only} | 无头模式: {CrawlerConfig.HEADLESS}")
        logger.info(f"延迟范围: {CrawlerConfig.HUMAN_DELAY_MIN}-{CrawlerConfig.HUMAN_DELAY_MAX}s")
        logger.info("=" * 60)

        # 初始化组件
        self.browser = CloakBrowser()
        self.scraper = AIScraper()

        # 数据库连接池 (可选)
        db_engine = None
        if save_to_db:
            try:
                from db.pool import get_engine
                db_engine = await get_engine()
            except Exception as e:
                logger.error(f"数据库初始化失败: {e}，将继续抓取但不写库")

        try:
            for i, keyword in enumerate(self.keywords):
                logger.info(f"\n>>> 关键词进度: {i + 1}/{len(self.keywords)} <<<")

                try:
                    result = await self._crawl_keyword(keyword)
                    self.all_results.append(result)

                    # 实时保存到 JSON
                    self._save_result(result)

                    # 保存到数据库
                    if db_engine and result.items:
                        async with db_engine.begin() as conn:
                            await self._save_to_db(conn, result.items)

                except Exception as e:
                    logger.error(f"关键词 [{keyword}] 爬取异常: {e}")
                    continue

                # ============================================================
                # 人类行为: 关键词间较长延迟 (模拟换主题搜索)
                # ============================================================
                if i < len(self.keywords) - 1:
                    kw_delay = random.uniform(
                        CrawlerConfig.KEYWORD_DELAY_MIN,
                        CrawlerConfig.KEYWORD_DELAY_MAX,
                    )
                    logger.info(f"关键词间休息 {kw_delay:.0f}s ...")
                    await asyncio.sleep(kw_delay)

        finally:
            await self.browser.close()

        # 汇总统计
        total_news = sum(r.total for r in self.all_results)
        logger.info("=" * 60)
        logger.info(f"爬虫完成 | {len(self.all_results)} 个关键词 | {total_news} 条新闻")
        logger.info(f"输出目录: {self.output_dir}")
        logger.info("=" * 60)

        return self.all_results

    # ============================================================
    # 持久化
    # ============================================================

    def _save_result(self, result: NewsItemList):
        """保存单个关键词结果到 JSON"""
        filename = f"{result.keyword}_{datetime.now().strftime('%Y%m%d')}.json"
        filepath = self.output_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(result.to_json(indent=2, ensure_ascii=False))
        logger.debug(f"已保存: {filepath}")

    async def _save_to_db(self, conn, items: list[NewsItem]):
        """
        保存新闻到 PostgreSQL

        Args:
            conn: SQLAlchemy AsyncConnection
            items: NewsItem 列表
        """
        from sqlalchemy import text

        insert_sql = text("""
            INSERT INTO news_data (
                title, content, publish_time, source, author, url,
                keywords, matched_keyword, category, summary,
                related_entities, fetch_time, content_hash
            ) VALUES (
                :title, :content, :publish_time, :source, :author, :url,
                CAST(:keywords AS jsonb), :matched_keyword, :category, :summary,
                CAST(:related_entities AS jsonb), :fetch_time, :content_hash
            )
            ON CONFLICT (url) DO UPDATE SET
                title = EXCLUDED.title,
                content = EXCLUDED.content,
                fetch_time = EXCLUDED.fetch_time
        """)

        saved = 0
        for item in items:
            try:
                await conn.execute(
                    insert_sql,
                    dict(
                        title=item.title,
                        content=item.content,
                        publish_time=item.publish_time,
                        source=item.source,
                        author=item.author,
                        url=item.url,
                        keywords=json.dumps(item.keywords, ensure_ascii=False),
                        matched_keyword=item.matched_keyword,
                        category=item.category,
                        summary=item.summary,
                        related_entities=json.dumps(item.related_entities, ensure_ascii=False),
                        fetch_time=item.fetch_time,
                        content_hash=item.hash,
                    ),
                )
                saved += 1
            except Exception as e:
                logger.error(f"数据写入失败 [{item.title[:30]}]: {e}")

        logger.info(f"数据库写入: {saved}/{len(items)} 条")

    def save_all_results(self):
        """保存全部结果到汇总 JSON"""
        all_items = []
        for result_list in self.all_results:
            all_items.extend(result_list.items)

        summary = {
            "fetch_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_news": len(all_items),
            "keywords": self.keywords,
            "items": [item.model_dump() for item in all_items],
        }

        filepath = self.output_dir / f"all_news_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        logger.info(f"汇总结果已保存: {filepath} ({len(all_items)} 条)")
        return filepath