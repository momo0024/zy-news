"""
数据库连接池模块 (基于 SQLAlchemy 异步引擎)
SQLAlchemy 是 Python 生态中最流行的数据库工具库，内置 QueuePool 连接池，支持连接复用、自动重连

用法:
    from db.engine import get_engine, get_pool

    engine = get_engine()

    async with engine.begin() as conn:
        rows = await conn.execute(text("SELECT ..."))
"""

from typing import Optional
from urllib.parse import quote

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncConnection,
    create_async_engine,
)

from loguru import logger

from config import DBConfig
from utils.timezone import APP_TZ


def _build_async_url(config: DBConfig) -> str:
    """构建 SQLAlchemy 异步连接 URL
    对用户名和密码进行 URL 编码，避免特殊字符（如 @ : / ? # [ ] ! $ & ' ( ) * + , ; =）
    破坏 URL 解析导致 getaddrinfo failed 等连接错误
    """
    return (
        f"postgresql+asyncpg://{quote(config.USER, safe='')}:{quote(config.PASSWORD, safe='')}"
        f"@{config.HOST}:{config.PORT}/{config.NAME}"
    )


def create_engine(config: Optional[DBConfig] = None) -> AsyncEngine:
    """创建 SQLAlchemy 异步引擎 (内置连接池)"""
    cfg = config or DBConfig()
    url = _build_async_url(cfg)

    engine = create_async_engine(
        url,
        pool_size=cfg.POOL_MAX_SIZE,
        max_overflow=5,                    # 池满时可额外创建的连接数
        pool_recycle=cfg.POOL_MAX_LIFETIME, # 连接回收时间
        pool_pre_ping=True,                 # 每次使用前 ping 检测有效性
        echo=False,
        connect_args={"server_settings": {"timezone": str(APP_TZ)}},
    )

    logger.info(
        f"SQLAlchemy 引擎已创建 | host={cfg.HOST}:{cfg.PORT} | "
        f"db={cfg.NAME} | pool={cfg.POOL_MIN_SIZE}-{cfg.POOL_MAX_SIZE}"
    )
    return engine


async def close_engine(engine: AsyncEngine):
    """关闭引擎 (释放所有连接)"""
    await engine.dispose()
    logger.info("SQLAlchemy 引擎已关闭")


# ============================================================
# 全局单例
# ============================================================
_global_engine: Optional[AsyncEngine] = None


async def get_engine() -> AsyncEngine:
    """获取全局引擎单例 (自动初始化)"""
    global _global_engine
    if _global_engine is None:
        _global_engine = create_engine()
    return _global_engine


async def close_global_engine():
    """关闭全局引擎"""
    global _global_engine
    if _global_engine:
        await close_engine(_global_engine)
        _global_engine = None


# 兼容旧接口别名 (PgPool -> engine)
async def get_pool() -> AsyncEngine:
    """兼容旧代码，返回全局引擎"""
    return await get_engine()


async def close_pool():
    """兼容旧代码"""
    await close_global_engine()