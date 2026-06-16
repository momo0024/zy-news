"""
配置模块 - 爬虫系统全局配置
所有配置从 .env 文件加载，支持环境变量覆盖

优先级: 环境变量 > .env 文件 > 默认值
"""

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# ============================================================
# 加载 .env 文件 (优先项目根目录，兼容部署根目录)
# ============================================================
_PROJECT_ROOT = Path(__file__).resolve().parent
_ENV_PATH = _PROJECT_ROOT / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH, override=False)
else:
    # 尝试从部署根目录加载
    load_dotenv(override=False)

# ============================================================
# 辅助函数 (必须在环境变量配置之前定义)
# ============================================================

def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)

def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default

def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default

def _env_bool(key: str, default: bool = True) -> bool:
    val = os.getenv(key, str(default).lower()).lower()
    return val in ("true", "1", "yes", "on")

def _env_optional(key: str, default: Optional[str] = None) -> Optional[str]:
    val = os.getenv(key)
    return val if val else default

# ============================================================
# 搜索关键词 (创新平台相关)
# 支持环境变量 SEARCH_KEYWORDS 覆盖，逗号分隔，如：
# SEARCH_KEYWORDS=新型研发机构,实验室,中试平台
# ============================================================
_DEFAULT_KEYWORDS = "新型研发机构,实验室,中试平台,成果转化,孵化器,创新平台,科学装置,联合体,技术经理人,服务平台,研究院"
SEARCH_KEYWORDS = [
    kw.strip()
    for kw in _env("SEARCH_KEYWORDS", _DEFAULT_KEYWORDS).split(",")
    if kw.strip()
]

# ============================================================
# 随机 User-Agent 池 (支持环境变量覆盖，分号分隔)
# 环境变量: CRAWLER_USER_AGENTS
# ============================================================
_DEFAULT_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
]

_env_ua = _env("CRAWLER_USER_AGENTS", "").strip()
USER_AGENTS = [ua.strip() for ua in _env_ua.split(";") if ua.strip()] if _env_ua else _DEFAULT_USER_AGENTS

# ============================================================
# 随机视口尺寸池 (支持环境变量覆盖，逗号分隔，格式: 1920x1080)
# 环境变量: CRAWLER_VIEWPORTS
# ============================================================
_DEFAULT_VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 2560, "height": 1440},
]

def _parse_viewports(env_str: str) -> list:
    result = []
    for item in env_str.split(","):
        item = item.strip()
        if not item:
            continue
        if "x" in item:
            parts = item.lower().split("x")
            try:
                result.append({"width": int(parts[0]), "height": int(parts[1])})
            except (ValueError, IndexError):
                continue
    return result

_env_vp = _env("CRAWLER_VIEWPORTS", "").strip()
VIEWPORTS = _parse_viewports(_env_vp) if _env_vp else _DEFAULT_VIEWPORTS


# ============================================================
# LLM 模型配置 (ScrapeGraphAI 使用)
# ============================================================

class LLMConfig:
    """大语言模型配置 - 从 .env 读取，使用 vLLM 本地部署"""

    PROVIDER: str = _env("LLM_PROVIDER", "openai_compatible")

    # vLLM 配置 (OpenAI 兼容接口)
    OPENAI_API_KEY: str = _env("OPENAI_API_KEY", "")
    OPENAI_BASE_URL: Optional[str] = _env_optional("OPENAI_BASE_URL")
    OPENAI_MODEL_NAME: str = _env("OPENAI_MODEL_NAME", "qwen3.6:27b")
    OPENAI_TEMPERATURE: float = _env_float("OPENAI_TEMPERATURE", 0.0)
    OPENAI_MAX_TOKENS: int = _env_int("OPENAI_MAX_TOKENS", 16384)
    # 推理深度: low | medium | high (仅 Qwen3 等思考模型有效)
    REASONING_EFFORT: str = _env("REASONING_EFFORT", "high")

    # ScrapeGraphAI 图配置
    USE_DEEP_SCRAPER: bool = _env_bool("USE_DEEP_SCRAPER", True)

    # --- 结构化提取指令 (可自定义 prompt) ---
    # 使用时机：当搜索结果列表页只能获取标题、URL、来源、时间等基本信息时，
    # 访问新闻详情页提取完整的结构化数据（正文内容、作者、关键词、分类等）。
    # 不生成摘要(summary)，列表页已有 abstract。
    EXTRACTION_PROMPT: str = """
从以下新闻页面内容中提取结构化信息，返回 JSON 格式：

{
    "title": "新闻标题",
    "content": "新闻正文内容（提取核心正文，去除广告和导航）",
    "publish_time": "发布时间 (YYYY-MM-DD HH:mm:ss)",
    "source": "新闻来源/媒体名称",
    "author": "作者（如有）",
    "url": "原文链接",
    "keywords": ["关键词1", "关键词2"],
    "matched_keyword": "匹配到的搜索关键词",
    "category": "新闻分类 (政策/技术/产业/人才/资金/其他)",
    "related_entities": ["相关机构/企业/人物"]
}

注意：
- 如果某个字段无法从页面提取，设为 null
- keywords 提取3-5个核心关键词
- 分类根据新闻内容判断最匹配的类型
- content 提取正文核心内容，不是摘要
- 不需要生成 summary 字段
"""


# ============================================================
# 数据库配置 (PostgreSQL)
# ============================================================

class DBConfig:
    """PostgreSQL 数据库配置"""

    HOST: str = _env("DB_HOST", "localhost")
    PORT: int = _env_int("DB_PORT", 5432)
    NAME: str = _env("DB_NAME", "zy_news")
    USER: str = _env("DB_USER", "postgres")
    PASSWORD: str = _env("DB_PASSWORD", "")

    # 连接池参数
    POOL_MIN_SIZE: int = _env_int("DB_POOL_MIN_SIZE", 2)
    POOL_MAX_SIZE: int = _env_int("DB_POOL_MAX_SIZE", 10)
    POOL_MAX_IDLE: float = _env_float("DB_POOL_MAX_IDLE", 300)
    POOL_MAX_LIFETIME: float = _env_float("DB_POOL_MAX_LIFETIME", 3600)


# ============================================================
# 爬虫行为配置
# ============================================================

class CrawlerConfig:
    """爬虫行为配置 - 从 .env 读取"""

    # --- 浏览器 ---
    HEADLESS: bool = _env_bool("CRAWLER_HEADLESS", False)  # 默认显示浏览器
    STEALTH_MODE: bool = _env_bool("CRAWLER_STEALTH", True)
    PROXY_URL: Optional[str] = _env_optional("CRAWLER_PROXY")

    # --- 人类行为模拟 (模拟真实用户操作) ---
    HUMAN_DELAY_MIN: float = _env_float("HUMAN_DELAY_MIN", 1.0)
    HUMAN_DELAY_MAX: float = _env_float("HUMAN_DELAY_MAX", 3.0)
    KEYWORD_DELAY_MIN: float = _env_float("KEYWORD_DELAY_MIN", 5.0)
    KEYWORD_DELAY_MAX: float = _env_float("KEYWORD_DELAY_MAX", 10.0)
    HUMAN_MOUSE_MOVE: bool = _env_bool("HUMAN_MOUSE_MOVE", True)
    HUMAN_RANDOM_SCROLL: bool = _env_bool("HUMAN_RANDOM_SCROLL", True)

    # --- 频率/并发控制 ---
    MAX_CONCURRENT: int = _env_int("MAX_CONCURRENT", 2)
    MAX_PAGES_PER_KEYWORD: int = _env_int("MAX_PAGES_PER_KEYWORD", 2)
    TODAY_ONLY: bool = _env_bool("TODAY_ONLY", True)
    # 只保留最近N天的新闻（1=仅今天，2=今天和昨天，以此类推）
    KEEP_RECENT_DAYS: int = _env_int("KEEP_RECENT_DAYS", 1)
    # 同时爬取的网站并发数（每个网站内部关键词串行）
    MAX_CONCURRENT_SITES: int = _env_int("MAX_CONCURRENT_SITES", 3)

    # --- 定时任务 ---
    # 定时爬取时间（24小时制，多个时间用逗号分隔，如 "08:00,15:00"）
    CRAWL_SCHEDULE_TIMES: list[str] = [
        t.strip()
        for t in _env("CRAWL_SCHEDULE_TIMES", "08:00,15:00").split(",")
        if t.strip()
    ]


# ============================================================
# 日志配置
# ============================================================

class LogConfig:
    """日志配置"""

    LEVEL: str = _env("LOG_LEVEL", "DEBUG")
    ROTATION: str = _env("LOG_ROTATION", "10 MB")
    RETENTION: str = _env("LOG_RETENTION", "7 days")


# ============================================================
# 数据库表结构定义 (启动时自动建表，仅在表不存在时创建)
# ============================================================

# ---------- 爬取网站配置表: 管理需要爬取的新闻网站源 ----------
CRAWL_SITES_SCHEMA = """
CREATE TABLE IF NOT EXISTS crawl_sites (
    id                  SERIAL PRIMARY KEY,
    site_name           VARCHAR(200) NOT NULL UNIQUE,
    site_url            VARCHAR(1000),
    search_url_template VARCHAR(2000),
    search_url          VARCHAR(2000),
    category            VARCHAR(50),
    media_type          VARCHAR(50),
    supervisor          VARCHAR(500),
    is_active           BOOLEAN DEFAULT TRUE,
    sort_order          INTEGER DEFAULT 0,
    description         VARCHAR(500),
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE  crawl_sites IS '爬取网站配置表，管理需要爬取的新闻来源网站';
COMMENT ON COLUMN crawl_sites.id IS '主键自增ID';
COMMENT ON COLUMN crawl_sites.site_name IS '网站名称（如：百度新闻、新浪新闻）';
COMMENT ON COLUMN crawl_sites.site_url IS '网站首页地址';
COMMENT ON COLUMN crawl_sites.search_url_template IS '搜索URL模板（旧字段），{keyword}为占位符';
COMMENT ON COLUMN crawl_sites.search_url IS '新闻检索URL，{keyword}为关键词占位符，用于爬取新闻列表，例：https://apps.jmnews.cn/?app=search&controller=index&action=search&wd={keyword}';
COMMENT ON COLUMN crawl_sites.category IS '媒体类别：中央级/各部委级/省级/经济特区/财经科技/财经报纸/研究院/湖北省级/市级';
COMMENT ON COLUMN crawl_sites.media_type IS '媒体类型：报纸/网站/通讯社/电视台/期刊/智库/新媒体/融媒体平台/研究机构/财经杂志';
COMMENT ON COLUMN crawl_sites.supervisor IS '主管/主办单位';
COMMENT ON COLUMN crawl_sites.is_active IS '是否启用：TRUE=启用爬取，FALSE=暂停';
COMMENT ON COLUMN crawl_sites.sort_order IS '排序权重，越小越优先';
COMMENT ON COLUMN crawl_sites.description IS '备注说明';
COMMENT ON COLUMN crawl_sites.created_at IS '创建时间';
COMMENT ON COLUMN crawl_sites.updated_at IS '更新时间';
"""

# ---------- 爬取关键词配置表: 管理需要搜索的关键词 ----------
CRAWL_KEYWORDS_SCHEMA = """
CREATE TABLE IF NOT EXISTS crawl_keywords (
    id              SERIAL PRIMARY KEY,
    keyword         VARCHAR(200) NOT NULL UNIQUE,
    keyword_type    VARCHAR(100),
    is_active       BOOLEAN DEFAULT TRUE,
    priority        INTEGER DEFAULT 0,
    description     VARCHAR(500),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE  crawl_keywords IS '爬取关键词配置表，管理需要搜索的关键词列表';
COMMENT ON COLUMN crawl_keywords.id IS '主键自增ID';
COMMENT ON COLUMN crawl_keywords.keyword IS '关键词内容，唯一约束防止重复';
COMMENT ON COLUMN crawl_keywords.keyword_type IS '关键词分类（如：创新平台、实验室、成果转化）';
COMMENT ON COLUMN crawl_keywords.is_active IS '是否启用：TRUE=参与爬取，FALSE=暂停';
COMMENT ON COLUMN crawl_keywords.priority IS '优先级，越大越优先爬取';
COMMENT ON COLUMN crawl_keywords.description IS '备注说明';
COMMENT ON COLUMN crawl_keywords.created_at IS '创建时间';
COMMENT ON COLUMN crawl_keywords.updated_at IS '更新时间';
"""

# ---------- 新闻数据存储表: 存储爬取并解析后的新闻 ----------
NEWS_DATA_SCHEMA = """
CREATE TABLE IF NOT EXISTS news_data (
    id              SERIAL PRIMARY KEY,
    title           VARCHAR(500) NOT NULL,
    content         TEXT,
    summary         VARCHAR(300),
    publish_time    TIMESTAMPTZ,
    source          VARCHAR(200),
    author          VARCHAR(100),
    url             VARCHAR(2000) UNIQUE NOT NULL,
    keywords        JSONB DEFAULT '[]'::jsonb,
    matched_keyword VARCHAR(200),
    category        VARCHAR(50),
    related_entities JSONB DEFAULT '[]'::jsonb,
    crawl_site_id   INTEGER REFERENCES crawl_sites(id),
    fetch_time      TIMESTAMPTZ DEFAULT NOW(),
    content_hash    VARCHAR(64),
    raw_html        TEXT,
    is_processed    BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE  news_data IS '新闻数据存储表，存储爬取并解析后的新闻';
COMMENT ON COLUMN news_data.id IS '主键自增ID';
COMMENT ON COLUMN news_data.title IS '新闻标题';
COMMENT ON COLUMN news_data.content IS '新闻正文/摘要';
COMMENT ON COLUMN news_data.summary IS '一句话摘要（AI生成，50字以内）';
COMMENT ON COLUMN news_data.publish_time IS '新闻发布时间';
COMMENT ON COLUMN news_data.source IS '新闻来源/媒体名称';
COMMENT ON COLUMN news_data.author IS '作者';
COMMENT ON COLUMN news_data.url IS '原文链接，唯一索引用于去重';
COMMENT ON COLUMN news_data.keywords IS '提取的关键词，JSON数组格式，例：["创新平台","科技政策","成果转化"]';
COMMENT ON COLUMN news_data.matched_keyword IS '搜索时匹配到的关键词';
COMMENT ON COLUMN news_data.category IS '新闻分类：政策/技术/产业/人才/资金/其他';
COMMENT ON COLUMN news_data.related_entities IS '相关实体，JSON数组，例：[{"name":"XX实验室","type":"机构"}]';
COMMENT ON COLUMN news_data.crawl_site_id IS '关联爬取来源网站ID';
COMMENT ON COLUMN news_data.fetch_time IS '数据抓取入库时间';
COMMENT ON COLUMN news_data.content_hash IS '内容哈希值，辅助去重判断';
COMMENT ON COLUMN news_data.raw_html IS '原始HTML内容（可选，用于后期重新解析）';
COMMENT ON COLUMN news_data.is_processed IS '是否已完成AI结构化提取';
COMMENT ON COLUMN news_data.created_at IS '创建时间';
COMMENT ON COLUMN news_data.updated_at IS '更新时间';

-- 为常用查询字段创建索引，提升检索性能
CREATE INDEX IF NOT EXISTS idx_news_publish_time ON news_data(publish_time DESC);
CREATE INDEX IF NOT EXISTS idx_news_source ON news_data(source);
CREATE INDEX IF NOT EXISTS idx_news_category ON news_data(category);
CREATE INDEX IF NOT EXISTS idx_news_matched_keyword ON news_data(matched_keyword);
CREATE INDEX IF NOT EXISTS idx_news_fetch_time ON news_data(fetch_time DESC);
-- GIN 索引加速 JSONB 字段内数组的查询
CREATE INDEX IF NOT EXISTS idx_news_keywords_gin ON news_data USING GIN (keywords);
"""

# 所有建表语句合并，按依赖顺序执行 (先建配置表，再建数据表)
ALL_TABLE_SCHEMAS = [
    CRAWL_SITES_SCHEMA,
    CRAWL_KEYWORDS_SCHEMA,
    NEWS_DATA_SCHEMA,
]