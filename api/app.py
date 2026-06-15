"""
FastAPI 应用工厂
创建并配置 FastAPI 实例，注册路由和生命周期事件
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from db.pool import get_pool, close_pool
from db.init_db import init_database
from api.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理
    - 启动时: 初始化数据库连接池 + 建表
    - 关闭时: 释放连接池
    """
    logger.info("[FastAPI] 服务启动中...")

    pool = await get_pool()
    await init_database(pool)

    logger.info("[FastAPI] 服务已就绪")

    yield

    logger.info("[FastAPI] 服务关闭中...")
    await close_pool()
    logger.info("[FastAPI] 服务已关闭")


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例"""
    app = FastAPI(
        title="zy-news API",
        description="新闻爬虫系统 API - 提供新闻搜索、列表、统计等接口",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)

    return app
