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
                    "keyword": keyword,
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
# 日期过滤（按北京时间自然日，不依赖时分秒；未进详情页）
# ============================================================

def _item_publish_date(item: dict):
    """从列表项解析发布日期；法治日报等 API 通常只有 YYYY-MM-DD"""
    from utils.timezone import parse_app_date
    pub_time = item.get("publish_time", "")
    if not pub_time:
        return None
    d = parse_app_date(str(pub_time))
    if d:
        return d
    dt = _parse_item_date(item)
    return dt.date() if dt else None


def filter_recent_news(items: list[dict], keep_days: int = 1) -> list[dict]:
    """
    只保留最近 N 个自然日内发布的新闻（含今天）。
    列表页只有年月日时，按日期比较，不按 0 点与当前时刻比。
    """
    from utils.timezone import recent_cutoff_date
    cutoff = recent_cutoff_date(keep_days)
    filtered = []
    for item in items:
        pub_date = _item_publish_date(item)
        if pub_date is None:
            continue
        if pub_date >= cutoff:
            filtered.append(item)
    return filtered


def _parse_item_date(item: dict) -> datetime | None:
    """从 item 解析发布时间（兼容带时分秒的字符串）"""
    pub_time = item.get("publish_time", "")
    if not pub_time:
        return None
    from utils.timezone import parse_app_datetime
    dt = parse_app_datetime(str(pub_time).strip())
    if dt:
        return dt.replace(tzinfo=None)
    for fmt in [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d",
    ]:
        try:
            return datetime.strptime(str(pub_time).strip(), fmt)
        except ValueError:
            continue
    return None


def all_items_are_recent(items: list[dict], keep_days: int) -> bool:
    """本页结果是否都在最近 keep_days 个自然日内（用于翻页停止）"""
    from utils.timezone import recent_cutoff_date
    if not items:
        return False
    cutoff = recent_cutoff_date(keep_days)
    for item in items:
        pub_date = _item_publish_date(item)
        if pub_date is None or pub_date < cutoff:
            return False
    return True


# ============================================================
# 数据库保存
# ============================================================

async def save_news_to_db(items: list[dict], site_id: int | None = None) -> int:
    """保存新闻：url 去重文章表，(news_id, keyword) 合并命中关系表"""
    if not items:
        return 0

    from utils.keyword_hit import (
        MATCH_SOURCE_UNKNOWN,
        is_keyword_hit_verified,
        merge_hit_flags,
        resolve_hit_flags,
    )

    engine = await get_engine()
    saved = 0
    now = datetime.now(APP_TZ)

    async with engine.begin() as conn:
        for item in items:
            try:
                keyword = (item.get("keyword") or "").strip()
                if not keyword:
                    continue

                pub_time = parse_app_datetime(item.get("publish_time", ""))
                snippet = item.get("abstract") or item.get("snippet") or item.get("summary")
                match_source = item.get("match_source") or MATCH_SOURCE_UNKNOWN

                in_title, in_body, match_source = resolve_hit_flags(
                    keyword,
                    item.get("title", ""),
                    snippet=snippet,
                    match_source=match_source,
                )
                if not is_keyword_hit_verified(in_title, in_body):
                    logger.debug(
                        f"跳过未校验命中 [{keyword}] "
                        f"{item.get('title', '')[:40]}"
                    )
                    continue

                existing_news = (await conn.execute(
                    text("SELECT id FROM news_data WHERE url = :url"),
                    {"url": item["url"]},
                )).mappings().fetchone()

                if existing_news:
                    news_id = existing_news["id"]
                    await conn.execute(
                        text("UPDATE news_data SET updated_at = NOW() WHERE id = :id"),
                        {"id": news_id},
                    )
                else:
                    result = await conn.execute(
                        text("""
                            INSERT INTO news_data (
                                title, publish_time, source, url,
                                category, related_entities, fetch_time, content_hash,
                                crawl_site_id
                            ) VALUES (
                                :title, :publish_time, :source, :url,
                                :category, CAST(:related_entities AS jsonb), :fetch_time, :content_hash,
                                :crawl_site_id
                            )
                            RETURNING id
                        """),
                        dict(
                            title=item["title"],
                            publish_time=pub_time,
                            source=item.get("source", ""),
                            url=item["url"],
                            category="",
                            related_entities=json.dumps([], ensure_ascii=False),
                            fetch_time=now,
                            content_hash=str(hash(item["title"] + item["url"])),
                            crawl_site_id=site_id,
                        ),
                    )
                    news_id = result.scalar()
                    saved += 1

                existing_hit = (await conn.execute(
                    text("""
                        SELECT id, in_title, in_body, match_source
                        FROM news_keyword_hits
                        WHERE news_id = :nid AND keyword = :kw
                    """),
                    {"nid": news_id, "kw": keyword},
                )).mappings().fetchone()

                if existing_hit:
                    m_title, m_body, m_source = merge_hit_flags(
                        existing_hit["in_title"],
                        existing_hit["in_body"],
                        existing_hit["match_source"],
                        in_title,
                        in_body,
                        match_source,
                    )
                    await conn.execute(
                        text("""
                            UPDATE news_keyword_hits SET
                                in_title = :it, in_body = :ib, match_source = :ms,
                                crawl_site_id = COALESCE(:sid, crawl_site_id),
                                last_seen_at = :now
                            WHERE id = :id
                        """),
                        dict(
                            it=m_title, ib=m_body, ms=m_source,
                            sid=site_id, now=now, id=existing_hit["id"],
                        ),
                    )
                else:
                    await conn.execute(
                        text("""
                            INSERT INTO news_keyword_hits (
                                news_id, keyword, in_title, in_body, match_source,
                                crawl_site_id, first_seen_at, last_seen_at
                            ) VALUES (
                                :nid, :kw, :it, :ib, :ms, :sid, :now, :now
                            )
                        """),
                        dict(
                            nid=news_id, kw=keyword, it=in_title, ib=in_body,
                            ms=match_source, sid=site_id, now=now,
                        ),
                    )

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

        # 无头模式下简化人工操作（反爬主要靠浏览器指纹而非行为）
        if browser.headless:
            await browser.human_delay(0.3, 0.8)
            await browser.human_scroll(page, headless=True)
        else:
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