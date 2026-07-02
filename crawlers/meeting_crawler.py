"""
会议/论坛监测爬虫
- 读取 enable_meeting_monitor 站点 + monitor_type=meeting 关键词
- 规则判定会议/论坛/研讨会
- 按 url 幂等写入 meeting_items
"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import quote

from loguru import logger
from sqlalchemy import text

from config import CrawlerConfig, MeetingConfig, MONITOR_TYPE_MEETING
from crawlers.cloak_browser import CloakBrowser
from crawlers.sites import get_search_handler
from crawlers.sites.common import deduplicate_by_url
from crawlers.sites.search_engine_common import filter_search_engine_for_save
from db.init_db import init_database
from db.pool import close_global_engine, get_engine
from services.meeting_classifier import classify_meeting_item, extract_event_type
from utils.keyword_hit import CRAWL_SCOPE_ALL, build_search_url, crawl_scope_to_match_source, resolve_crawl_modes
from utils.timezone import APP_TZ, parse_app_datetime

from datetime import datetime

# 会议监测站点分组
MEETING_GROUP_ALL = "all"
MEETING_GROUP_CENTRAL = "central"
MEETING_GROUP_SEARCH_ENGINE = "search_engine"


def _is_search_engine_site(site: dict) -> bool:
    category = (site.get("category") or "").strip()
    site_url = (site.get("site_url") or "").lower()
    site_name = site.get("site_name") or ""
    if category in ("百度搜索", "百度"):
        return True
    if "baidu.com" in site_url and "news.baidu.com" not in site_url:
        return True
    if site_name.startswith("百度") and "新闻" not in site_name:
        return True
    return False


def _resolve_meeting_search_keywords(site: dict, keywords: list[str]) -> tuple[list[str], list[str]]:
    """
    返回 (用于检索的 query 列表, 原始主题关键词列表)。
    百度网页搜索：主题词 + 会议类词空格合并为一次检索。
    中央媒体：每个主题词单独检索。
    """
    topic_keywords = [kw.strip() for kw in keywords if kw.strip()]
    if _is_search_engine_site(site) and MeetingConfig.SEARCH_ENGINE_COMBINED_KEYWORDS:
        parts = list(topic_keywords)
        for ev in MeetingConfig.SEARCH_ENGINE_EVENT_KEYWORDS:
            if ev not in parts:
                parts.append(ev)
        combined = " ".join(parts)
        return ([combined] if combined else topic_keywords), topic_keywords
    return topic_keywords, topic_keywords


def _filter_sites_by_group(sites: list[dict], group: str) -> list[dict]:
    if group == MEETING_GROUP_CENTRAL:
        return [s for s in sites if not _is_search_engine_site(s)]
    if group == MEETING_GROUP_SEARCH_ENGINE:
        return [s for s in sites if _is_search_engine_site(s)]
    return sites


def _item_hits_keywords(item: dict, keywords: list[str]) -> bool:
    """标题或摘要是否命中任一配置关键词（不区分大小写）"""
    text = f"{item.get('title', '')} {item.get('abstract', '')} {item.get('snippet', '')}"
    lower = text.lower()
    for kw in keywords:
        k = kw.strip()
        if k and k.lower() in lower:
            return True
    return False


def _first_event_keyword_in_text(text: str) -> str:
    lower = (text or "").lower()
    for kw in MeetingConfig.SEARCH_ENGINE_EVENT_KEYWORDS:
        if kw.lower() in lower:
            return kw
    return ""


def _matched_keywords_label(item: dict, keywords: list[str], fallback: str) -> str:
    text = f"{item.get('title', '')} {item.get('abstract', '')} {item.get('snippet', '')}"
    lower = text.lower()
    hits = [kw for kw in keywords if kw.strip() and kw.lower() in lower]
    return ",".join(hits) if hits else fallback


async def get_meeting_sites() -> list[dict]:
    engine = await get_engine()
    async with engine.connect() as conn:
        rows = (await conn.execute(text("""
            SELECT id, site_name, site_url, search_url, search_url_title, search_url_body,
                   search_scope_support, category
            FROM crawl_sites
            WHERE is_active = TRUE
              AND enable_meeting_monitor = TRUE
              AND search_url IS NOT NULL AND search_url != ''
            ORDER BY sort_order
        """))).mappings().fetchall()
    return [dict(r) for r in rows]


async def get_meeting_keywords() -> list[str]:
    engine = await get_engine()
    async with engine.connect() as conn:
        rows = (await conn.execute(
            text("""
                SELECT keyword FROM crawl_keywords
                WHERE is_active = TRUE AND monitor_type = :mt
                ORDER BY priority DESC
            """),
            {"mt": MONITOR_TYPE_MEETING},
        )).mappings().fetchall()
    return [r["keyword"] for r in rows]


async def save_meeting_items(items: list[dict], site_id: int | None = None) -> int:
    """
    按 url 幂等入库。已存在则更新元数据。
    返回本次新增条数。
    """
    if not items:
        return 0

    engine = await get_engine()
    inserted = 0
    now = datetime.now(APP_TZ)

    async with engine.begin() as conn:
        for item in items:
            url = (item.get("url") or "").strip()
            title = (item.get("title") or "").strip()
            if not url or not title:
                continue

            pub_time = parse_app_datetime(item.get("publish_time", ""))
            summary = (
                item.get("summary")
                or item.get("abstract")
                or item.get("snippet")
                or ""
            )[:500]

            existing = (await conn.execute(
                text("SELECT id FROM meeting_items WHERE url = :url"),
                {"url": url},
            )).mappings().fetchone()

            if existing:
                await conn.execute(
                    text("""
                        UPDATE meeting_items SET
                            title = :title,
                            summary = COALESCE(NULLIF(:summary, ''), summary),
                            source = COALESCE(NULLIF(:source, ''), source),
                            publish_time = COALESCE(:pub, publish_time),
                            event_type = COALESCE(NULLIF(:etype, ''), event_type),
                            matched_keyword = COALESCE(NULLIF(:mkw, ''), matched_keyword),
                            ai_confidence = COALESCE(:conf, ai_confidence),
                            crawl_site_id = COALESCE(:sid, crawl_site_id),
                            updated_at = :now
                        WHERE url = :url
                    """),
                    dict(
                        title=title,
                        summary=summary,
                        source=item.get("source", ""),
                        pub=pub_time,
                        etype=item.get("event_type", ""),
                        mkw=item.get("keyword", ""),
                        conf=item.get("ai_confidence"),
                        sid=site_id,
                        now=now,
                        url=url,
                    ),
                )
                continue

            await conn.execute(
                text("""
                    INSERT INTO meeting_items (
                        url, title, summary, source, publish_time,
                        event_type, matched_keyword, crawl_site_id,
                        ai_confidence, first_seen_at, created_at, updated_at
                    ) VALUES (
                        :url, :title, :summary, :source, :pub,
                        :etype, :mkw, :sid,
                        :conf, :now, :now, :now
                    )
                """),
                dict(
                    url=url,
                    title=title,
                    summary=summary,
                    source=item.get("source", ""),
                    pub=pub_time,
                    etype=item.get("event_type", ""),
                    mkw=item.get("keyword", ""),
                    sid=site_id,
                    conf=item.get("ai_confidence"),
                    now=now,
                ),
            )
            inserted += 1

    return inserted


async def _crawl_site_meeting(site: dict, keywords: list[str], browser: CloakBrowser) -> int:
    site_name = site["site_name"]
    site_id = site.get("id")
    site_url = site.get("site_url", "")
    category = site.get("category", "")
    keep_days = MeetingConfig.KEEP_RECENT_DAYS
    is_search = _is_search_engine_site(site)
    crawl_modes = resolve_crawl_modes(site)

    if "法治日报" in site_name or "legaldaily" in site_url.lower():
        crawl_modes = [CRAWL_SCOPE_ALL]
        from crawlers.sites import legaldaily
        handler = legaldaily
    else:
        handler = get_search_handler(category)

    search_queries, origin_keywords = _resolve_meeting_search_keywords(site, keywords)

    logger.info(
        f"[Meeting][{site_name}] 开始监测，主题词 {len(origin_keywords)} 个，"
        f"保留近 {keep_days} 天"
        + (f"，最多 {MeetingConfig.SEARCH_ENGINE_MAX_PAGES} 页" if is_search else "")
    )
    if is_search and search_queries:
        logger.info(
            f"[Meeting][{site_name}] 搜索引擎合并检索: [{search_queries[0]}]"
        )
    elif not is_search and len(search_queries) == 1 and len(origin_keywords) > 1:
        logger.info(
            f"[Meeting][{site_name}] 合并检索: [{search_queries[0]}]"
        )
    candidates: list[dict] = []

    for i, keyword in enumerate(search_queries):
        encoded_kw = quote(keyword)
        for crawl_scope in crawl_modes:
            search_url = build_search_url(site, encoded_kw, crawl_scope)
            if not search_url:
                continue
            try:
                raw_items = await handler.search(
                    browser, site, keyword, keep_days, search_url,
                )
                for item in raw_items:
                    if not item.get("keyword"):
                        item["keyword"] = keyword
                    if not item.get("match_source"):
                        item["match_source"] = crawl_scope_to_match_source(crawl_scope)
                candidates.extend(raw_items)
                logger.info(
                    f"[Meeting][{site_name}] 关键词 [{keyword}] "
                    f"检索 {len(raw_items)} 条"
                )
            except Exception as e:
                logger.error(f"[Meeting][{site_name}] 关键词 [{keyword}] 失败: {e}")

        if i < len(search_queries) - 1:
            if ("人民网" in site_name or "people.com.cn" in site_url.lower()) and "人民政协" not in site_name:
                await asyncio.sleep(CrawlerConfig.PEOPLE_KEYWORD_INTERVAL_SEC)
            else:
                await CloakBrowser.human_delay(2.0, 4.0)

    candidates = deduplicate_by_url(candidates)
    if is_search:
        before = len(candidates)
        candidates = [c for c in candidates if _item_hits_keywords(c, origin_keywords)]
        logger.info(
            f"[Meeting][{site_name}] 主题词本地校验: {before} → {len(candidates)} 条"
        )

    matched: list[dict] = []
    for item in candidates:
        abstract = item.get("abstract") or item.get("snippet") or ""
        title = item.get("title", "")
        if is_search:
            # 检索 query 已含会议类词，命中主题词 + 近 N 天即可入库
            event_type = extract_event_type(title, abstract) or _first_event_keyword_in_text(
                f"{title} {abstract}"
            )
            item["event_type"] = event_type or "综合检索"
            item["ai_confidence"] = 0.85 if event_type and event_type != "综合检索" else 0.6
        else:
            ok, event_type, confidence = classify_meeting_item(title, abstract)
            if not ok:
                continue
            item["event_type"] = event_type
            item["ai_confidence"] = confidence
        item["summary"] = abstract[:500] if abstract else title[:200]
        item["keyword"] = _matched_keywords_label(
            item, origin_keywords, item.get("keyword") or search_queries[0]
        )
        matched.append(item)

    if is_search and matched:
        before = len(matched)
        matched = filter_search_engine_for_save(matched, keep_days)
        logger.info(
            f"[Meeting][{site_name}] 入库前日期校验: {before} → {len(matched)} 条"
        )

    logger.info(
        f"[Meeting][{site_name}] 候选 {len(candidates)} 条，"
        f"会议/论坛相关 {len(matched)} 条"
    )
    if not matched:
        return 0

    return await save_meeting_items(matched, site_id)


async def crawl_meeting_sites(
    site_names: list[str] | None = None,
    site_group: str = MEETING_GROUP_ALL,
) -> None:
    """执行会议监测：爬取 → 入库"""
    await init_database()

    sites = await get_meeting_sites()
    if site_names:
        sites = [s for s in sites if s["site_name"] in site_names]
    else:
        sites = _filter_sites_by_group(sites, site_group)

    if not sites:
        label = site_group if not site_names else ",".join(site_names)
        logger.error(f"[Meeting] 没有启用的会议监测站点 ({label})")
        return

    keywords = await get_meeting_keywords()
    if not keywords:
        logger.error("[Meeting] 没有启用的会议监测关键词")
        return

    group_label = {
        MEETING_GROUP_CENTRAL: "中央媒体",
        MEETING_GROUP_SEARCH_ENGINE: "搜索引擎（百度）",
        MEETING_GROUP_ALL: "全部",
    }.get(site_group, site_group)

    total_start = time.time()
    logger.info("=" * 60)
    logger.info(f"会议/论坛监测启动 [{group_label}]")
    logger.info(f"站点: {', '.join(s['site_name'] for s in sites)}")
    logger.info(f"主题关键词: {', '.join(keywords)}")
    logger.info(f"时间窗口: 近 {MeetingConfig.KEEP_RECENT_DAYS} 天")
    has_search = any(_is_search_engine_site(s) for s in sites)
    if has_search:
        logger.info(
            f"搜索引擎检索词: {' '.join(keywords)} "
            f"{' '.join(MeetingConfig.SEARCH_ENGINE_EVENT_KEYWORDS)}"
        )
        logger.info(f"搜索引擎最多翻页: {MeetingConfig.SEARCH_ENGINE_MAX_PAGES} 页")
    logger.info("=" * 60)

    total_inserted = 0
    for i, site in enumerate(sites):
        browser = CloakBrowser(headless=CrawlerConfig.HEADLESS)
        try:
            n = await _crawl_site_meeting(site, keywords, browser)
            total_inserted += n
        except Exception as e:
            logger.error(f"[Meeting][{site['site_name']}] 异常: {e}")
        finally:
            await browser.close()
        if i < len(sites) - 1:
            await CloakBrowser.human_delay()

    elapsed = time.time() - total_start
    logger.info("=" * 60)
    logger.info(
        f"[Meeting] 完成 | 新增入库 {total_inserted} 条 | 耗时 {elapsed:.1f}s"
    )
    logger.info("=" * 60)

    await close_global_engine()


if __name__ == "__main__":
    asyncio.run(crawl_meeting_sites())
