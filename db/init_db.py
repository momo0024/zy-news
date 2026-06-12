"""
数据库初始化模块
- 启动时自动建表 (仅当表不存在时创建，已有表不会重复初始化)
- 插入默认配置数据 (仅当配置表为空时写入)
- 使用 schema_version 元数据表标记初始化状态
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from loguru import logger

from config import ALL_TABLE_SCHEMAS, SEARCH_KEYWORDS
from db.pool import get_engine

# 默认爬取网站配置
DEFAULT_CRAWL_SITES = [
    {
        "site_name": "百度新闻",
        "site_url": "https://news.baidu.com",
        "search_url_template": "https://www.baidu.com/s?tn=news&word={keyword}",
        "sort_order": 1,
        "description": "百度新闻搜索，覆盖面广，适合中文新闻采集",
    },
]

# 默认关键词列表 (来自 config.py 的 SEARCH_KEYWORDS)
DEFAULT_KEYWORDS = SEARCH_KEYWORDS


async def init_database(engine: AsyncEngine = None) -> None:
    """
    项目启动时初始化数据库
    - 创建元数据版本表 (schema_version)
    - 如果版本号未标记，依次建表 + 插入默认数据
    - 已初始化则跳过，不会修改已有数据
    """
    if engine is None:
        engine = await get_engine()

    async with engine.begin() as conn:
        # 1. 创建版本标记表
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS schema_version (
                id          SERIAL PRIMARY KEY,
                version     INTEGER NOT NULL,
                description VARCHAR(200),
                applied_at  TIMESTAMPTZ DEFAULT NOW()
            );
        """))

        # 2. 检查是否已初始化
        row = (await conn.execute(text(
            "SELECT version FROM schema_version WHERE version = 1"
        ))).fetchone()
        if row:
            logger.info("[DB Init] 数据库已初始化 (version=1)，跳过")
            return

        logger.info("[DB Init] 开始数据库初始化...")

        # 3. 依次执行建表语句 (每条 schema 可能包含多条 SQL，需拆分)
        for schema_sql in ALL_TABLE_SCHEMAS:
            try:
                for stmt in _split_sql(schema_sql):
                    if stmt.strip():
                        await conn.execute(text(stmt))
                logger.info(f"[DB Init] 建表完成: {_extract_table_name(schema_sql)}")
            except Exception as e:
                logger.error(f"[DB Init] 建表失败: {e}")
                raise

        # 4. 插入默认爬取网站配置 (仅当配置表为空时)
        site_count = (await conn.execute(text(
            "SELECT COUNT(*) FROM crawl_sites"
        ))).scalar()
        if site_count == 0:
            for site in DEFAULT_CRAWL_SITES:
                await conn.execute(
                    text("""
                        INSERT INTO crawl_sites (site_name, site_url, search_url_template, sort_order, description)
                        VALUES (:name, :url, :tmpl, :order, :desc)
                    """),
                    dict(name=site["site_name"], url=site["site_url"],
                         tmpl=site["search_url_template"], order=site["sort_order"],
                         desc=site["description"]),
                )
            logger.info(f"[DB Init] 已插入 {len(DEFAULT_CRAWL_SITES)} 条默认网站配置")
        else:
            logger.info(f"[DB Init] crawl_sites 已有 {site_count} 条数据，跳过默认插入")

        # 5. 插入默认关键词配置 (仅当配置表为空时)
        kw_count = (await conn.execute(text(
            "SELECT COUNT(*) FROM crawl_keywords"
        ))).scalar()
        if kw_count == 0:
            for kw in DEFAULT_KEYWORDS:
                await conn.execute(
                    text("""
                        INSERT INTO crawl_keywords (keyword, keyword_type, priority)
                        VALUES (:kw, '通用', 0)
                        ON CONFLICT (keyword) DO NOTHING
                    """),
                    dict(kw=kw),
                )
            logger.info(f"[DB Init] 已插入 {len(DEFAULT_KEYWORDS)} 条默认关键词")
        else:
            logger.info(f"[DB Init] crawl_keywords 已有 {kw_count} 条数据，跳过默认插入")

        # 6. 记录初始化版本
        await conn.execute(text(
            "INSERT INTO schema_version (version, description) VALUES (1, '初始建表 + 默认数据')"
        ))
        logger.info("[DB Init] 数据库初始化完成 (version=1)")


def _split_sql(sql: str) -> list[str]:
    """将包含多条语句的 SQL 字符串按分号拆分为独立语句"""
    raw = sql.strip()
    if not raw:
        return []
    # 去掉末尾多余的分号
    while raw.endswith(';'):
        raw = raw[:-1].strip()
    # 按分号拆分，过滤空语句
    return [s.strip() for s in raw.split(';') if s.strip()]


def _extract_table_name(sql: str) -> str:
    """从建表语句中提取表名"""
    import re
    m = re.search(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", sql, re.IGNORECASE)
    return m.group(1) if m else "unknown"