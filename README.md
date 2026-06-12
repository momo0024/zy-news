# zy-news

高隐匿智能新闻爬虫系统，基于 **CloakBrowser**（Playwright 隐匿浏览器）+ **ScrapeGraphAI**（LLM 结构化解析），支持 PostgreSQL 持久化与 FastAPI 对外接口。

## 功能特性

- 百度新闻关键词搜索，模拟真人浏览行为降低封禁风险
- ScrapeGraphAI + vLLM / OpenAI 兼容接口，自动提取新闻结构化字段
- PostgreSQL 连接池存储，支持去重、分类、关键词索引
- FastAPI REST 接口，供前端或其他服务调用
- 命令行模式与 API 服务模式可独立运行

## 技术栈

| 组件 | 说明 |
|------|------|
| Playwright + playwright-stealth | 高隐匿浏览器爬取 |
| ScrapeGraphAI | AI 页面结构化解析 |
| SQLAlchemy + asyncpg | 异步 PostgreSQL 连接池 |
| FastAPI + Uvicorn | HTTP API 服务 |
| Loguru | 日志管理 |

## 项目结构

```
zy-news/
├── main.py              # 入口（爬虫 / API / 数据库初始化）
├── config.py            # 全局配置（从 .env 加载）
├── requirements.txt     # Python 依赖
├── .env.example         # 环境变量模板
├── api/                 # FastAPI 路由与应用
├── crawlers/            # 新闻爬虫（CloakBrowser）
├── scrapers/            # AI 解析（ScrapeGraphAI）
├── db/                  # 数据库连接池与初始化
├── models/              # 数据模型
└── utils/               # 工具（日志等）
```

## 快速开始

### 1. 环境要求

- Python 3.10+
- PostgreSQL 14+
- vLLM 或其他 OpenAI 兼容 LLM 服务（用于 ScrapeGraphAI）

### 2. 安装依赖

```bash
# 克隆仓库
git clone https://github.com/momo0024/zy-news.git
cd zy-news

# 创建虚拟环境（推荐）
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 安装 Playwright 浏览器
playwright install chromium
```

### 3. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入数据库、LLM 等实际配置
```

主要配置项：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OPENAI_BASE_URL` | vLLM 服务地址 | `http://localhost:8000/v1` |
| `OPENAI_MODEL_NAME` | 模型名称 | - |
| `DB_HOST` / `DB_PORT` / `DB_NAME` | PostgreSQL 连接 | `localhost:5432/zy_news` |
| `CRAWLER_HEADLESS` | 无头模式 | `false` |
| `TODAY_ONLY` | 仅抓取当天新闻 | `true` |

### 4. 初始化数据库

```bash
# 需先在 PostgreSQL 中创建数据库 zy_news
python main.py --init-db
```

### 5. 运行爬虫

```bash
# 默认配置运行（显示浏览器窗口）
python main.py

# 指定关键词
python main.py -k "新型研发机构" -k "实验室"

# 无头模式 + 写入数据库
python main.py --headless --save-db

# 指定 LLM 模型
python main.py --provider openai_compatible --model qwen2.5:7b
```

### 6. 启动 API 服务

```bash
python main.py --api
# 默认监听 http://0.0.0.0:8000

# 指定端口
python main.py --api --api-port 8080
```

API 文档：启动后访问 `http://localhost:8000/docs`

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/news/list` | 新闻列表（分页、筛选） |
| GET | `/api/news/search?q=` | 关键词搜索 |
| GET | `/api/news/{id}` | 新闻详情 |
| GET | `/api/sources` | 新闻来源列表 |
| GET | `/api/keywords/list` | 已匹配关键词 |
| GET | `/api/crawl-keywords` | 爬取关键词配置 |
| POST | `/api/crawl-keywords` | 新增爬取关键词 |
| GET | `/api/sites` | 爬取站点配置 |
| GET | `/api/stats` | 数据统计概览 |

## 命令行参数

```bash
python main.py --help

# 常用参数
-k, --keywords        搜索关键词（可多个）
--headless            无头模式
--save-db             结果写入 PostgreSQL
--init-db             仅执行数据库初始化
--api                 启动 FastAPI 服务
--provider            LLM 提供商 (openai / ollama / openai_compatible)
--model               模型名称
--no-today-only       不过滤当天新闻
--list-providers      列出支持的 LLM 提供商
```

## 默认搜索关键词

系统内置创新平台相关关键词：新型研发机构、实验室、中试平台、成果转化、孵化器、创新平台、科学装置、联合体、技术经理人、服务平台、研究院。

可在 `config.py` 或数据库 `crawl_keywords` 表中管理。

## 注意事项

- `.env` 含敏感信息，请勿提交到 Git
- 爬虫默认显示浏览器窗口，便于调试；生产部署建议 `--headless`
- 请合理设置爬取频率，遵守目标网站 robots 协议
- LLM 服务需提前启动并确保 `/v1` 接口可访问

## License

MIT
