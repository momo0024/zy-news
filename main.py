"""
zy-news 新闻爬虫系统入口

用法:
    # 默认模式：同时启动 API 服务 + 定时爬取任务
    python main.py

    # 只启动 API 服务
    python main.py --api

    # 只启动定时任务
    python main.py --schedule

    # 立即执行一次爬取（自动入库）
    python main.py --crawl
    python main.py --crawl --crawl-sites "荆门新闻网"

    # 数据库初始化 (首次部署)
    python main.py --init-db
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from utils.logger import setup_logger, get_logger
from config import CrawlerConfig, LogConfig

logger = get_logger(__name__)


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="zy-news - 新闻爬虫系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 默认模式：API + 定时任务
  python main.py

  # 只启动 API 服务
  python main.py --api
  python main.py --api --api-port 8080

  # 只启动定时任务
  python main.py --schedule

  # 立即执行一次爬取（自动入库）
  python main.py --crawl
  python main.py --crawl --crawl-sites "荆门新闻网"

  # 数据库初始化 (首次部署)
  python main.py --init-db
        """,
    )

    parser.add_argument(
        "--init-db", action="store_true",
        help="仅执行数据库初始化（建表 + 插入默认配置数据）",
    )

    parser.add_argument(
        "--api", action="store_true",
        help="只启动 FastAPI 服务 (默认端口 9094)",
    )

    parser.add_argument(
        "--api-host", default="0.0.0.0",
        help="API 服务监听地址 (默认: 0.0.0.0)",
    )

    parser.add_argument(
        "--api-port", type=int, default=9094,
        help="API 服务端口 (默认: 9094)",
    )

    parser.add_argument(
        "--crawl", action="store_true",
        help="立即执行一次网站新闻爬取（自动保存到数据库）",
    )

    parser.add_argument(
        "--crawl-sites", nargs="+", default=None,
        help="指定爬取的网站名称（不指定则爬取所有有 search_url 的网站）",
    )

    parser.add_argument(
        "--schedule", action="store_true",
        help="只启动定时爬取任务",
    )

    parser.add_argument(
        "--log-level", default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help=f"日志级别 (默认: {LogConfig.LEVEL})",
    )

    return parser.parse_args()


async def _start_api_server(host: str, port: int, log_level: str):
    """启动 FastAPI 服务"""
    from api import create_app
    import uvicorn
    app = create_app()
    config = uvicorn.Config(
        app, host=host, port=port,
        log_level=log_level.lower(),
    )
    server = uvicorn.Server(config)
    await server.serve()


async def _start_scheduler():
    """启动定时爬取任务"""
    from crawlers.site_crawler import crawl_all_sites

    schedule_times = CrawlerConfig.CRAWL_SCHEDULE_TIMES
    logger.info(f"定时爬取模式启动，每天 {', '.join(schedule_times)} 执行")

    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.error("apscheduler 未安装，请执行: pip install apscheduler")
        raise

    scheduler = AsyncIOScheduler()
    for idx, time_str in enumerate(schedule_times):
        try:
            hour, minute = time_str.split(":")
            scheduler.add_job(
                crawl_all_sites,
                CronTrigger(hour=int(hour), minute=int(minute)),
                id=f"daily_crawl_{idx}",
                name=f"每日新闻爬取-{time_str}",
                misfire_grace_time=3600,
            )
            logger.info(f"定时任务已注册: 每天 {time_str} 执行爬取")
        except ValueError:
            logger.warning(f"定时时间格式错误，已跳过: {time_str}")
    scheduler.start()

    # 保持运行
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        logger.info("定时任务已停止")


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

    # ---------- 数据库初始化 ----------
    if args.init_db:
        logger.info("执行数据库初始化...")
        from db import init_database, close_pool
        await init_database()
        await close_pool()
        logger.info("数据库初始化完成")
        print("\n数据库初始化完成: 表结构已创建，默认数据已写入\n")
        return

    # ---------- 只启动 FastAPI 服务 ----------
    if args.api and not args.schedule:
        logger.info("启动 FastAPI 服务...")
        await _start_api_server(args.api_host, args.api_port, log_level)
        return

    # ---------- 只启动定时爬取模式 ----------
    if args.schedule and not args.api:
        logger.info("启动定时爬取任务...")
        from db.init_db import init_database
        await init_database()
        await _start_scheduler()
        return

    # ---------- 立即执行一次爬取 ----------
    if args.crawl:
        from crawlers.site_crawler import crawl_all_sites
        await crawl_all_sites(site_names=args.crawl_sites)
        return

    # ---------- 默认模式：同时启动 API + 定时任务 ----------
    logger.info("=" * 60)
    logger.info("zy-news 新闻爬虫系统")
    logger.info("默认模式：同时启动 API 服务 + 定时爬取任务")
    logger.info(f"API 地址: http://{args.api_host}:{args.api_port}")
    logger.info(f"定时任务: 每天 {', '.join(CrawlerConfig.CRAWL_SCHEDULE_TIMES)} 执行")
    logger.info("=" * 60)

    # 先初始化数据库
    from db.init_db import init_database
    await init_database()

    # 同时启动 API 和定时任务
    try:
        await asyncio.gather(
            _start_api_server(args.api_host, args.api_port, log_level),
            _start_scheduler(),
        )
    except asyncio.CancelledError:
        logger.info("服务已停止")
    except Exception as e:
        logger.error(f"服务异常: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())