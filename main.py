"""
zy-news 新闻爬虫系统入口
基于 CloakBrowser (高隐匿) + ScrapeGraphAI (智能解析)
所有环境变量配置在 .env 文件中

用法:
    # 使用 .env 默认配置运行 (默认显示浏览器窗口)
    python main.py

    # 指定关键词
    python main.py -k "新型研发机构" -k "实验室"

    # 无头模式运行 (不显示浏览器)
    python main.py --headless

    # 同时写入数据库
    python main.py --save-db

    # 指定 LLM 提供商和模型 (覆盖 .env)
    python main.py --provider ollama --model qwen2.5:7b

    # 不过滤当天
    python main.py --no-today-only

    # 查看支持信息
    python main.py --list-providers
"""

import argparse
import asyncio
import sys
from pathlib import Path

# 部署根目录
sys.path.insert(0, str(Path(__file__).resolve().parent))

from utils.logger import setup_logger, get_logger
from config import SEARCH_KEYWORDS, LLMConfig, CrawlerConfig, LogConfig
from crawlers.news_crawler import NewsCrawler
from scrapers.ai_scraper import AIScraper

logger = get_logger(__name__)


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="zy-news - 高隐匿智能新闻爬虫系统 (默认显示浏览器窗口)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
配置:
  所有环境变量在项目根目录 .env 文件中配置
  命令行参数可覆盖 .env 中的部分设置

示例:
  # 数据库初始化 (首次部署)
  python main.py --init-db

  # 启动 API 服务
  python main.py --api
  python main.py --api --api-port 8080

  # 爬虫模式
  python main.py
  python main.py -k "新型研发机构" -k "实验室"
  python main.py --headless --save-db
  python main.py --provider ollama --model qwen2.5:7b
        """,
    )

    parser.add_argument(
        "-k", "--keywords",
        nargs="+",
        help="搜索关键词 (空格分隔)，默认使用 .env 配置列表",
    )

    parser.add_argument(
        "--provider",
        default=LLMConfig.PROVIDER,
        choices=["openai", "ollama", "openai_compatible"],
        help=f"LLM 提供商 (默认: {LLMConfig.PROVIDER})",
    )

    parser.add_argument(
        "--model", default=None,
        help="模型名称 (默认取决于 provider)",
    )

    parser.add_argument(
        "--api-key", default=None,
        help="API Key (覆盖 .env 中的 OPENAI_API_KEY)",
    )

    parser.add_argument(
        "--base-url", default=None,
        help="API Base URL (覆盖 .env 中的 OPENAI_BASE_URL)",
    )

    parser.add_argument(
        "--temperature", type=float, default=None,
        help="LLM 温度参数 (覆盖 .env)",
    )

    parser.add_argument(
        "--max-pages", type=int, default=None,
        help="每个关键词最大搜索页数 (覆盖 .env)",
    )

    parser.add_argument(
        "--no-today-only", action="store_true",
        help="不过滤当天，获取所有时间新闻",
    )

    parser.add_argument(
        "--headless", action="store_true",
        help="无头模式 (不显示浏览器窗口)，默认显示浏览器",
    )

    parser.add_argument(
        "--proxy", default=None,
        help="代理地址，如 http://127.0.0.1:7890",
    )

    parser.add_argument(
        "--output-dir", default=None,
        help="结果输出目录",
    )

    parser.add_argument(
        "--save-db", action="store_true",
        help="同时保存结果到 PostgreSQL 数据库",
    )

    parser.add_argument(
        "--list-providers", action="store_true",
        help="列出支持的 LLM 提供商",
    )

    parser.add_argument(
        "--log-level", default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help=f"日志级别 (默认: {LogConfig.LEVEL})",
    )

    parser.add_argument(
        "--init-db", action="store_true",
        help="仅执行数据库初始化（建表 + 插入默认配置数据）",
    )

    parser.add_argument(
        "--api", action="store_true",
        help="启动 FastAPI 服务提供新闻搜索接口 (默认端口 8000)",
    )

    parser.add_argument(
        "--api-host", default="0.0.0.0",
        help="API 服务监听地址 (默认: 0.0.0.0)",
    )

    parser.add_argument(
        "--api-port", type=int, default=8000,
        help="API 服务端口 (默认: 8000)",
    )

    return parser.parse_args()


async def main():
    """主函数"""
    args = parse_args()

    # 初始化日志
    log_level = args.log_level or LogConfig.LEVEL
    setup_logger(
        log_level=log_level,
        rotation=LogConfig.ROTATION,
        retention=LogConfig.RETENTION,
    )

    # 列出提供商
    if args.list_providers:
        providers = AIScraper.list_available_providers()
        print("\n支持的 LLM 提供商:\n")
        for name, info in providers.items():
            print(f"  [{name}]")
            print(f"    描述: {info['description']}")
            print(f"    环境变量: {', '.join(info['env_vars'])}")
            print()
        return

    # ---------- 数据库初始化 ----------
    if args.init_db:
        logger.info("执行数据库初始化...")
        from db import init_database, close_pool
        await init_database()
        await close_pool()
        logger.info("数据库初始化完成")
        print("\n数据库初始化完成: 表结构已创建，默认数据已写入\n")
        return

    # ---------- 启动 FastAPI 服务 ----------
    if args.api:
        logger.info("启动 FastAPI 服务...")
        from api import create_app
        import uvicorn
        app = create_app()
        config = uvicorn.Config(
            app, host=args.api_host, port=args.api_port,
            log_level=log_level.lower(),
        )
        server = uvicorn.Server(config)
        await server.serve()
        return

    # ---------- 爬虫模式 ----------
    # 爬虫需要数据库时，先初始化
    if args.save_db:
        from db import init_database
        await init_database()

    # 命令行参数覆盖全局配置
    if args.proxy:
        CrawlerConfig.PROXY_URL = args.proxy
    if args.headless:
        CrawlerConfig.HEADLESS = True

    logger.info("=" * 60)
    logger.info("zy-news 新闻爬虫系统")
    logger.info(f"LLM: {args.provider}/{args.model or LLMConfig.OPENAI_MODEL_NAME}")
    logger.info(f"浏览器: {'无头' if CrawlerConfig.HEADLESS else '可见窗口'}")
    logger.info(f"当天模式: {not args.no_today_only} | 写库: {args.save_db}")
    logger.info(f"延迟: {CrawlerConfig.HUMAN_DELAY_MIN}-{CrawlerConfig.HUMAN_DELAY_MAX}s/条")
    logger.info("=" * 60)

    # 创建爬虫
    max_pages = args.max_pages or CrawlerConfig.MAX_PAGES_PER_KEYWORD
    crawler = NewsCrawler(
        keywords=args.keywords,
        today_only=not args.no_today_only,
        max_pages_per_keyword=max_pages,
        output_dir=args.output_dir,
    )

    # 使用命令行指定的 scraper 配置
    crawler.scraper = AIScraper(
        provider=args.provider,
        model_name=args.model,
        api_key=args.api_key,
        base_url=args.base_url,
        temperature=args.temperature,
    )

    # 执行爬取
    try:
        results = await crawler.run(save_to_db=args.save_db)
    except KeyboardInterrupt:
        logger.warning("用户中断")
        return
    except Exception as e:
        logger.error(f"爬虫异常: {e}")
        raise

    # 保存汇总结果
    if results:
        crawler.save_all_results()

        print("\n" + "=" * 60)
        print("爬取结果摘要:")
        print("-" * 60)
        for r in results:
            print(f"  [{r.keyword}] 共 {r.total} 条新闻")
        print("-" * 60)
        total = sum(r.total for r in results)
        print(f"  总计: {total} 条新闻")
        print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())