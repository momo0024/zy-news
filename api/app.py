"""
FastAPI 应用工厂
创建并配置 FastAPI 实例，注册路由和生命周期事件
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from loguru import logger
import httpx

from db.pool import get_pool, close_pool
from db.init_db import init_database
from api.routes import router

REMOTE_BASE_URL = "http://119.96.30.33:8096"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理
    - 启动时: 初始化数据库连接池 + 建表
    - 关闭时: 释放连接池
    """
    logger.info("[FastAPI] 服务启动中...")

    # 初始化数据库 (连接池 + 建表)
    pool = await get_pool()
    await init_database(pool)

    logger.info("[FastAPI] 服务已就绪")

    yield

    # 关闭时释放资源
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

    # CORS 配置 (允许前端跨域访问)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # 生产环境应限制为具体域名
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 注册路由
    app.include_router(router)

    # Catch-all: 未匹配的本地路由转发到远程服务器
    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
    async def proxy_fallback(request: Request, path: str):
        """本地无匹配路由时，代理到远程服务器"""
        client = httpx.AsyncClient(base_url=REMOTE_BASE_URL, timeout=30.0)
        try:
            target_path = path if path.startswith("/") else f"/{path}"
            url = httpx.URL(path=target_path, query=request.url.query.encode("utf-8"))
            headers = dict(request.headers)
            headers.pop("host", None)

            body = await request.body()
            method = request.method

            logger.debug(f"[Proxy] {method} {target_path} -> {REMOTE_BASE_URL}{target_path}")
            rp = await client.request(method, url, headers=headers, content=body)

            return Response(
                content=await rp.aread(),
                status_code=rp.status_code,
                headers=dict(rp.headers),
            )
        except Exception as e:
            logger.error(f"[Proxy] 转发失败 {path}: {e}")
            return Response(content=f"Proxy error: {e}".encode(), status_code=502)
        finally:
            await client.aclose()

    return app