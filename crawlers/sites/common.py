"""
爬虫共享工具模块
- 通用翻页搜索
- 日期过滤 / 解析
- 新闻保存到数据库
- 通用 HTML 解析
- 弹窗检测与重试
"""

import asyncio
import json
import re
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import text

from config import CrawlerConfig
from db.pool import get_engine
from utils.timezone import APP_TZ, parse_app_datetime
from crawlers.cloak_browser import CloakBrowser


# ============================================================
# 通用 HTML 解析
# ============================================================

def parse_generic_search_results(html: str, keyword: str, site_name: str, site_url: str) -> list[dict]:
    """通用搜索结果解析器（基础版，后续按站点扩展）"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    items = []

    selectors = [
        "dl.search-list dd",
        ".search-item",
        ".list-item",
        ".news-item",
        ".article-item",
        "ul.list li",
    ]
    result_blocks = []
    for sel in selectors:
        result_blocks = soup.select(sel)
        if result_blocks:
            break

    for block in result_blocks:
        try:
            title_el = (
                block.select_one("h3 a")
                or block.select_one("h2 a")
                or block.select_one(".title a")
                or block.select_one("a.title")
                or block.find("a", href=True)
            )
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            url = title_el.get("href", "")
            if url and not url.startswith("http"):
                base = site_url.rstrip("/")
                url = base + url if url.startswith("/") else base + "/" + url

            date_el = (
                block.select_one(".date")
                or block.select_one(".time")
                or block.select_one(".pub-time")
                or block.select_one("var")
                or block.select_one("span.time")
            )
            date_str = date_el.get_text(strip=True) if date_el else ""

            if title and url:
                items.append({
                    "title": title,
                    "url": url,
                    "publish_time": date_str,
                    "source": site_name,
                    "matched_keyword": keyword,
                })
        except Exception as e:
            logger.warning(f"解析条目失败: {e}")
    return items


# ============================================================
# 列表去重
# ============================================================

def deduplicate_by_url(items: list[dict]) -> list[dict]:
    """按 URL 去重，保留首次出现的条目"""
    seen = set()
    result = []
    for item in items:
        url = item.get("url", "")
        if url and url not in seen:
            seen.add(url)
            result.append(item)
    return result


# ============================================================
# 日期过滤
# ============================================================

def filter_recent_news(items: list[dict], keep_days: int = 1) -> list[dict]:
    """只保留最近 N 天的新闻"""
    now = datetime.now()
    cutoff = now - timedelta(days=keep_days)
    filtered = []
    for item in items:
        dt = _parse_item_date(item)
        if dt and dt >= cutoff:
            filtered.append(item)
    return filtered


def _parse_item_date(item: dict) -> datetime | None:
    """从 item 字典中解析发布时间为 datetime"""
    pub_time = item.get("publish_time", "")
    if not pub_time:
        return None
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"]:
        try:
            return datetime.strptime(str(pub_time).strip(), fmt)
        except ValueError:
            continue
    return None


def all_items_are_recent(items: list[dict], keep_days: int) -> bool:
    """检查列表中是否**全部**都是最近 N 天的新闻（有一页没显示全才需要翻页）"""
    if not items:
        return False
    cutoff = datetime.now() - timedelta(days=keep_days)
    for item in items:
        dt = _parse_item_date(item)
        if dt is None or dt < cutoff:
            return False
    return True


# ============================================================
# 数据库保存
# ============================================================

async def save_news_to_db(items: list[dict], site_id: int | None = None) -> int:
    """保存新闻到数据库，返回实际新增条数"""
    if not items:
        return 0

    engine = await get_engine()
    saved = 0

    async with engine.begin() as conn:
        for item in items:
            try:
                pub_time = parse_app_datetime(item.get("publish_time", ""))

                result = await conn.execute(
                    text("""
                        INSERT INTO news_data (
                            title, publish_time, source, url,
                            keywords, matched_keyword, category,
                            related_entities, fetch_time, content_hash,
                            crawl_site_id
                        ) VALUES (
                            :title, :publish_time, :source, :url,
                            CAST(:keywords AS jsonb), :matched_keyword, :category,
                            CAST(:related_entities AS jsonb), :fetch_time, :content_hash,
                            :crawl_site_id
                        )
                        ON CONFLICT (url) DO NOTHING
                    """),
                    dict(
                        title=item["title"],
                        publish_time=pub_time,
                        source=item.get("source", ""),
                        url=item["url"],
                        keywords=json.dumps([item["matched_keyword"]], ensure_ascii=False),
                        matched_keyword=item["matched_keyword"],
                        category="",
                        related_entities=json.dumps([], ensure_ascii=False),
                        fetch_time=datetime.now(APP_TZ),
                        content_hash=str(hash(item["title"] + item["url"])),
                        crawl_site_id=site_id,
                    ),
                )
                if result.rowcount and result.rowcount > 0:
                    saved += 1
            except Exception as e:
                logger.error(f"保存失败 [{item.get('title', '')[:40]}]: {e}")

    return saved


# ============================================================
# 通用翻页循环（核心抽象）
# ============================================================

async def pagination_loop(
    page,
    browser: CloakBrowser,
    site_name: str,
    keep_days: int,
    parse_page_func,
    click_next_func,
    *,
    max_pages: int = 50,
) -> list[dict]:
    """
    通用翻页循环 - 封装"解析 → 日期检查 → 翻页"的公共逻辑

    Args:
        page: Playwright page 对象
        browser: CloakBrowser 实例
        site_name: 网站名称（用于日志）
        keep_days: 保留天数
        parse_page_func: async func(page) -> list[dict]，解析当前页返回条目列表
        click_next_func: async func(page, page_num) -> bool，翻页，返回 True 表示继续
        max_pages: 最大翻页数
    """
    all_items = []
    page_num = 0

    while page_num < max_pages:
        page_num += 1
        await browser.human_delay(0.5, 1.5)
        await browser.human_mouse_move(page)
        await browser.human_scroll(page)

        page_items = await parse_page_func(page)
        if not page_items:
            logger.debug(f"[{site_name}] 第{page_num}页无结果，停止翻页")
            break

        all_items.extend(page_items)

        if not all_items_are_recent(page_items, keep_days):
            logger.info(f"[{site_name}] 第{page_num}页已混入非近{keep_days}天新闻，停止翻页")
            break

        if not await click_next_func(page, page_num):
            break

    logger.info(f"[{site_name}] 翻页完成，共 {page_num} 页，解析 {len(all_items)} 条")
    return all_items


# ============================================================
# 弹窗检测与重试
# ============================================================

async def check_and_retry_popup(
    page,
    site_name: str,
    popup_keywords: list[str] = None,
    retry_delays: list[float] = None,
) -> bool:
    """
    检测页面弹窗（如"当前用户较多，请稍后重试"），自动重试

    Args:
        page: Playwright page 对象
        site_name: 网站名称
        popup_keywords: 弹窗提示关键词列表
        retry_delays: 重试等待秒数列表

    Returns:
        True 表示页面正常，False 表示所有重试均失败
    """
    if popup_keywords is None:
        popup_keywords = ["当前用户较多", "请稍后重试"]
    if retry_delays is None:
        retry_delays = [10, 30]

    body_text = ""
    try:
        body_text = await page.locator("body").inner_text()
    except Exception:
        return True

    if not any(kw in body_text for kw in popup_keywords):
        return True

    for attempt, delay in enumerate(retry_delays, start=1):
        logger.warning(f"[{site_name}] 检测到弹窗，等待 {delay} 秒后重试 (第{attempt}次)...")
        await asyncio.sleep(delay)
        try:
            await page.reload(wait_until="networkidle", timeout=30000)
            await asyncio.sleep(3)
        except Exception as e:
            logger.warning(f"[{site_name}] 刷新失败: {e}")
            if attempt >= len(retry_delays):
                return False
            continue
        body_text = ""
        try:
            body_text = await page.locator("body").inner_text()
        except Exception:
            pass
        if not any(kw in body_text for kw in popup_keywords):
            logger.info(f"[{site_name}] 重试成功")
            return True

    logger.error(f"[{site_name}] 所有重试均失败，放弃")
    return False


# ============================================================
# 通用翻页搜索（URL 参数 page）
# ============================================================

async def search_generic_with_pagination(
    browser: CloakBrowser, search_url: str, keyword: str,
    site_name: str, site_url: str, keep_days: int,
) -> list[dict]:
    """
    通用搜索翻页：URL 参数 page 翻页 + 日期感知停止
    """
    all_items = []
    page_num = 0
    max_pages = 50

    async with browser.session() as page:
        while page_num < max_pages:
            page_num += 1

            if page_num == 1:
                page_url = search_url
            else:
                if "?" in search_url:
                    base = re.sub(r'[&?]page=\d+', '', search_url)
                    page_url = f"{base}&page={page_num}"
                else:
                    page_url = f"{search_url}?page={page_num}"

            logger.debug(f"[{site_name}] 获取第{page_num}页: {page_url[:120]}")

            try:
                await page.goto(page_url, wait_until="domcontentloaded", timeout=30000)

                if page_num == 1:
                    await browser.human_delay(0.5, 1.5)
                    await browser.human_mouse_move(page)
                    await browser.human_scroll(page)

                html = await page.content()
            except Exception as e:
                logger.warning(f"[{site_name}] 第{page_num}页获取失败: {e}")
                break

            page_items = parse_generic_search_results(html, keyword, site_name, site_url)
            logger.debug(f"[{site_name}] 第{page_num}页: {len(page_items)} 条")

            if not page_items:
                break

            all_items.extend(page_items)

            if not all_items_are_recent(page_items, keep_days):
                logger.info(f"[{site_name}] 第{page_num}页已混入非近{keep_days}天新闻，停止翻页")
                break

            await CloakBrowser.human_delay(1.0, 2.0)

    logger.info(f"[{site_name}] 翻页完成，共 {page_num} 页，解析 {len(all_items)} 条")
    return all_items