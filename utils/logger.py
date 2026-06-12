"""
日志模块 - 基于 loguru 的日志系统
支持控制台输出 + 文件滚动存储 + 按日期分割
"""

import sys
from pathlib import Path
from loguru import logger

# 日志目录
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

# 日志格式
CONSOLE_FORMAT = (
    "<green>{time:HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "<level>{message}</level>"
)

FILE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
    "{level: <8} | "
    "{name}:{function}:{line} | "
    "{message}"
)


def setup_logger(
    log_level: str = "DEBUG",
    rotation: str = "10 MB",
    retention: str = "7 days",
) -> logger:
    """
    初始化日志系统

    Args:
        log_level: 日志级别 (DEBUG/INFO/WARNING/ERROR)
        rotation: 日志文件滚动策略
        retention: 日志文件保留时间
    """
    # 移除默认 handler
    logger.remove()

    # 控制台输出 (彩色)
    logger.add(
        sys.stdout,
        format=CONSOLE_FORMAT,
        level=log_level,
        colorize=True,
        enqueue=True,
    )

    # 全量日志文件 (按大小滚动)
    logger.add(
        LOG_DIR / "zy-news_{time:YYYY-MM-DD}.log",
        format=FILE_FORMAT,
        level="DEBUG",
        rotation=rotation,
        retention=retention,
        encoding="utf-8",
        enqueue=True,
    )

    # 错误日志单独文件
    logger.add(
        LOG_DIR / "zy-news_error_{time:YYYY-MM-DD}.log",
        format=FILE_FORMAT,
        level="ERROR",
        rotation=rotation,
        retention=retention,
        encoding="utf-8",
        enqueue=True,
    )

    logger.info(f"日志系统初始化完成 | 级别: {log_level} | 目录: {LOG_DIR}")
    return logger


def get_logger(name: str = __name__):
    """获取带模块名的 logger 实例"""
    return logger.bind(name=name)