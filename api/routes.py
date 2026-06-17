"""
FastAPI 路由 - 新闻搜索/列表接口
对外提供 RESTful API，供前端或其他服务调用
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query
from sqlalchemy import text
from loguru import logger

from db.pool import get_engine
from utils.timezone import APP_TZ, format_app_datetime

router = APIRouter(tags=["news"])

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


@router.get("/news/list")
async def list_news(
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="每页条数"),
    keyword: Optional[str] = Query(None, description="关键词搜索 (匹配标题和内容)"),
    matched_keywords: Optional[str] = Query(None, description="关键词标签筛选 (匹配 matched_keyword，空格分隔)"),
    site_id: Optional[int] = Query(None, description="新闻网站ID筛选"),
    category: Optional[str] = Query(None, description="分类筛选: 政策/技术/产业/人才/资金/其他"),
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    sort_by: str = Query("publish_time", description="排序字段: publish_time / fetch_time"),
    sort_order: str = Query("desc", description="排序方向: asc / desc"),
    include_summary: bool = Query(False, description="是否附带概览统计（今日更新、覆盖来源）"),
):
    """新闻列表接口 (分页 + 多条件筛选，可选附带概览统计)"""
    engine = await get_engine()

    conditions = []
    params = {}

    if keyword:
        conditions.append("(title ILIKE :kw OR content ILIKE :kw)")
        params["kw"] = f"%{keyword}%"

    if matched_keywords:
        kw_list = [k.strip() for k in matched_keywords.split() if k.strip()]
        if kw_list:
            placeholders = ", ".join(f":mkw{i}" for i in range(len(kw_list)))
            for i, k in enumerate(kw_list):
                params[f"mkw{i}"] = k
            conditions.append(f"matched_keyword IN ({placeholders})")

    if site_id:
        conditions.append("crawl_site_id = :sid")
        params["sid"] = site_id

    if category:
        conditions.append("category = :cat")
        params["cat"] = category

    if start_date:
        conditions.append("publish_time >= :sd")
        params["sd"] = datetime.strptime(start_date, "%Y-%m-%d").date()

    if end_date:
        # 计算 end_date 的下一天，避免 SQL 中 :: 类型转换与 asyncpg 参数冲突
        from datetime import timedelta
        ed = datetime.strptime(end_date, "%Y-%m-%d")
        ed_next = (ed + timedelta(days=1)).date()
        conditions.append("publish_time < :ed_next")
        params["ed_next"] = ed_next

    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

    allowed_sort = {"publish_time", "fetch_time", "created_at", "title"}
    if sort_by not in allowed_sort:
        sort_by = "publish_time"
    sort_dir = "DESC" if sort_order.lower() == "desc" else "ASC"

    async with engine.connect() as conn:
        # 总数
        total = (await conn.execute(
            text(f"SELECT COUNT(*) FROM news_data{where_clause}"), params
        )).scalar()
        total_pages = (total + page_size - 1) // page_size if total > 0 else 0

        # 分页数据
        offset = (page - 1) * page_size
        rows = (await conn.execute(
            text(f"""
                SELECT id, title, content, summary, publish_time,
                       source, author, url, keywords, matched_keyword,
                       category, related_entities, crawl_site_id,
                       fetch_time, created_at
                FROM news_data
                {where_clause}
                ORDER BY {sort_by} {sort_dir}
                LIMIT :limit OFFSET :offset
            """),
            {**params, "limit": page_size, "offset": offset},
        )).mappings().fetchall()

        items = [dict(r) for r in rows]
        _serialize(items)

        summary = None
        if include_summary:
            today_count = (await conn.execute(
                text("""
                    SELECT COUNT(*) FROM news_data
                    WHERE (publish_time AT TIME ZONE :tz)::date = (NOW() AT TIME ZONE :tz)::date
                """),
                {"tz": str(APP_TZ)},
            )).scalar()
            total_sources = (await conn.execute(
                text("SELECT COUNT(DISTINCT source) FROM news_data WHERE source IS NOT NULL")
            )).scalar()
            summary = {
                "today_news": today_count,
                "total_sources": total_sources,
            }

        logger.info(f"[API] 新闻列表: page={page}, total={total}")
        payload: dict = {
            "items": items,
            "pagination": {
                "page": page, "page_size": page_size,
                "total": total, "total_pages": total_pages,
            },
        }

        # 关键词列表：从新闻表中聚合去重
        keywords_rows = (await conn.execute(
            text("SELECT DISTINCT matched_keyword FROM news_data WHERE matched_keyword IS NOT NULL ORDER BY matched_keyword")
        )).fetchall()
        payload["keywords"] = [r[0] for r in keywords_rows]

        if summary is not None:
            payload["summary"] = summary
        return {"code": 0, "message": "ok", "data": payload}


@router.get("/news/search")
async def search_news(
    q: str = Query(..., min_length=1, description="搜索关键词"),
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
):
    """快捷搜索新闻接口，在标题、内容、匹配关键词中全文搜索"""
    engine = await get_engine()
    pattern = f"%{q}%"
    base_where = """
        WHERE title ILIKE :q
           OR content ILIKE :q
           OR matched_keyword ILIKE :q
           OR keywords::text ILIKE :q
    """

    async with engine.connect() as conn:
        total = (await conn.execute(
            text(f"SELECT COUNT(*) FROM news_data {base_where}"),
            {"q": pattern},
        )).scalar()

        offset = (page - 1) * page_size
        total_pages = (total + page_size - 1) // page_size if total > 0 else 0

        rows = (await conn.execute(
            text(f"""
                SELECT id, title, content, summary, publish_time,
                       source, author, url, keywords, matched_keyword,
                       category, related_entities, crawl_site_id,
                       fetch_time, created_at
                FROM news_data {base_where}
                ORDER BY publish_time DESC
                LIMIT :limit OFFSET :offset
            """),
            {"q": pattern, "limit": page_size, "offset": offset},
        )).mappings().fetchall()

        items = [dict(r) for r in rows]
        _serialize(items)

        return {
            "code": 0, "message": "ok",
            "data": {
                "items": items,
                "pagination": {
                    "page": page, "page_size": page_size,
                    "total": total, "total_pages": total_pages,
                },
            },
        }


@router.get("/news/{news_id}")
async def get_news_detail(news_id: int):
    """获取单条新闻详情"""
    engine = await get_engine()

    async with engine.connect() as conn:
        row = (await conn.execute(
            text("""
                SELECT id, title, content, summary, publish_time,
                       source, author, url, keywords, matched_keyword,
                       category, related_entities, crawl_site_id,
                       fetch_time, raw_html, is_processed, created_at
                FROM news_data WHERE id = :id
            """),
            {"id": news_id},
        )).mappings().fetchone()

        if not row:
            return {"code": 404, "message": "新闻不存在", "data": None}

        item = dict(row)
        _serialize([item])
        return {"code": 0, "message": "ok", "data": item}


@router.get("/sources")
async def list_sources():
    """获取所有新闻来源 (从爬取站点配置中读取，返回 id 和 site_name)"""
    engine = await get_engine()
    async with engine.connect() as conn:
        rows = (await conn.execute(
            text("SELECT id, site_name FROM crawl_sites WHERE is_active = TRUE ORDER BY sort_order")
        )).mappings().fetchall()
        sources = [{"id": r["id"], "site_name": r["site_name"]} for r in rows]
    return {"code": 0, "message": "ok", "data": sources}


@router.get("/keywords/list")
async def list_keywords():
    """获取所有可用的匹配关键词列表"""
    engine = await get_engine()
    async with engine.connect() as conn:
        rows = (await conn.execute(text("""
            SELECT DISTINCT matched_keyword FROM news_data
            WHERE matched_keyword IS NOT NULL ORDER BY matched_keyword
        """))).fetchall()
        keywords = [r[0] for r in rows]
    return {"code": 0, "message": "ok", "data": keywords}


@router.get("/crawl-keywords")
async def list_crawl_keywords():
    """获取爬取关键词配置列表"""
    engine = await get_engine()
    async with engine.connect() as conn:
        rows = (await conn.execute(text("""
            SELECT id, keyword, keyword_type, is_active, priority, description
            FROM crawl_keywords ORDER BY priority DESC, id ASC
        """))).mappings().fetchall()
        items = [dict(r) for r in rows]
    return {"code": 0, "message": "ok", "data": items}


@router.post("/crawl-keywords")
async def create_crawl_keyword(
    keyword: str = Query(..., min_length=1, description="关键词内容"),
    keyword_type: str = Query("通用", description="关键词分类"),
    priority: int = Query(0, description="优先级"),
    description: str = Query("", description="备注说明"),
):
    """新增爬取关键词"""
    engine = await get_engine()
    async with engine.begin() as conn:
        # 检查是否已存在
        existing = (await conn.execute(
            text("SELECT id FROM crawl_keywords WHERE keyword = :kw"),
            {"kw": keyword},
        )).fetchone()
        if existing:
            return {"code": 409, "message": "关键词已存在", "data": None}

        result = await conn.execute(
            text("""
                INSERT INTO crawl_keywords (keyword, keyword_type, priority, description)
                VALUES (:kw, :kt, :pri, :desc)
                RETURNING id
            """),
            {"kw": keyword, "kt": keyword_type, "pri": priority, "desc": description},
        )
        new_id = result.scalar()
        logger.info(f"[API] 新增关键词: id={new_id}, keyword={keyword}")
    return {"code": 0, "message": "ok", "data": {"id": new_id, "keyword": keyword}}


@router.get("/sites")
async def list_crawl_sites():
    """获取爬取站点配置列表"""
    engine = await get_engine()
    async with engine.connect() as conn:
        rows = (await conn.execute(text("""
            SELECT id, site_name, site_url, search_url_template, search_url,
                   category, media_type, supervisor,
                   is_active, sort_order, description
            FROM crawl_sites ORDER BY sort_order
        """))).mappings().fetchall()
        items = [dict(r) for r in rows]
    return {"code": 0, "message": "ok", "data": items}


@router.get("/stats")
async def get_stats():
    """获取新闻数据概览统计"""
    engine = await get_engine()
    async with engine.connect() as conn:
        total = (await conn.execute(
            text("SELECT COUNT(*) FROM news_data")
        )).scalar()
        today_count = (await conn.execute(
            text("SELECT COUNT(*) FROM news_data WHERE fetch_time >= CURRENT_DATE")
        )).scalar()
        sources = (await conn.execute(
            text("SELECT COUNT(DISTINCT source) FROM news_data WHERE source IS NOT NULL")
        )).scalar()

        cat_rows = (await conn.execute(text("""
            SELECT category, COUNT(*) as cnt FROM news_data
            WHERE category IS NOT NULL GROUP BY category ORDER BY cnt DESC
        """))).fetchall()
        categories = {r[0]: r[1] for r in cat_rows}

    return {
        "code": 0, "message": "ok",
        "data": {
            "total_news": total,
            "today_news": today_count,
            "total_sources": sources,
            "categories": categories,
        },
    }


def _serialize(items: list[dict]):
    """时间字段序列化为北京时间字符串（与库内显示一致）"""
    for item in items:
        for field in ("publish_time", "fetch_time", "created_at"):
            if isinstance(item.get(field), datetime):
                item[field] = format_app_datetime(item[field])