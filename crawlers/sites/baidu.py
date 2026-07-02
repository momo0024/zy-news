"""
百度网页搜索（会议监测）
- 使用百度综合搜索，非新闻 tab
- 支持多关键词空格合并：wd=AI+智能制造
"""

from __future__ import annotations

from urllib.parse import quote, urljoin, urlparse

from loguru import logger

from config import MeetingConfig, CrawlerConfig
from crawlers.cloak_browser import CloakBrowser
from crawlers.sites.search_engine_common import (
    baidu_recent_url_suffix,
    filter_web_search_results,
    page_all_items_too_old,
    parse_search_publish_time,
)

_SEARCH_BASE = "https://www.baidu.com/s?ie=utf-8&wd={keyword}"
_PAGE_SIZE = 10

# 非网页结果：百科/视频/图片/广告/文库等
_SKIP_TPL = frozenset({
    "bk_polysemy", "bk_polysemy_list", "bk_polysemy_title", "bk_polysemy_san",
    "short_video", "vmp_player", "video", "video_recommend",
    "images_page", "image_grid", "img", "imgpage",
    "ai_answer", "recommend_list", "note_lead", "sp_wenku",
    "zhixin", "wenda", "map", "tieba",
})
_SKIP_HOSTS = (
    "baike.baidu.com",
    "wenku.baidu.com",
    "haokan.baidu.com",
    "v.baidu.com",
    "video.baidu.com",
    "image.baidu.com",
    "map.baidu.com",
    "zhidao.baidu.com",
    "tieba.baidu.com",
    "pan.baidu.com",
    "xueshu.baidu.com",
    "pic.baidu.com",
    "jingyan.baidu.com",
)
_SKIP_TITLE_KEYWORDS = ("百度百科", "百度图片", "百度视频", "百度文库", "百度知道")


def build_baidu_web_url(keyword: str, page_index: int = 0, keep_days: int = 3) -> str:
    pn = page_index * _PAGE_SIZE
    url = _SEARCH_BASE.format(keyword=quote(keyword))
    url += baidu_recent_url_suffix(keep_days)
    if pn > 0:
        url += f"&pn={pn}"
    return url


def _host_from_href(href: str) -> str:
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    try:
        return (urlparse(href).hostname or "").lower()
    except Exception:
        return ""


def _is_baidu_web_block(block) -> bool:
    """过滤百科/视频/图片/广告等非普通网页条目"""
    classes = block.get("class") or []
    if isinstance(classes, str):
        classes = classes.split()
    class_str = " ".join(classes)
    if "result-op" in classes:
        return False
    if block.get("data-tuiguang") is not None:
        return False
    if block.select_one("#ec_ad_results, .ec_ad_results, [data-landurl]"):
        return False
    if block.select_one("[data-module='video'], .video_list, .c-video, .video-x"):
        return False
    if block.select_one(".op-bk-pc, .op-bk-polysemy, .c-img-list, .imglist"):
        return False

    tpl = (block.get("tpl") or block.get("data-tpl") or "").strip()
    if tpl in _SKIP_TPL:
        return False
    if tpl.startswith(("bk_", "video", "img", "image")):
        return False

    head = block.get_text(strip=True)[:30]
    if "广告" in head or "推广" in head:
        return False
    if "video" in class_str or "image" in class_str:
        return False
    return True


def _is_baidu_web_link(href: str, title: str) -> bool:
    if not href or href.startswith("javascript:"):
        return False
    host = _host_from_href(href)
    if host and any(host == h or host.endswith("." + h) for h in _SKIP_HOSTS):
        return False
    if any(kw in title for kw in _SKIP_TITLE_KEYWORDS):
        return False
    return True


def _parse_baidu_web_html(html: str, keyword: str, site_name: str) -> list[dict]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []
    blocks = soup.select("#content_left div.result.c-container")
    if not blocks:
        blocks = soup.select("#content_left div.c-container")

    skipped = 0
    for block in blocks:
        try:
            if not _is_baidu_web_block(block):
                skipped += 1
                continue
            title_el = block.select_one("h3 a") or block.select_one("a[href]")
            if not title_el:
                skipped += 1
                continue
            title = title_el.get_text(strip=True)
            href = (title_el.get("href") or "").strip()
            if not title or not href:
                skipped += 1
                continue
            if href.startswith("/"):
                href = urljoin("https://www.baidu.com", href)
            if not _is_baidu_web_link(href, title):
                skipped += 1
                continue

            abstract = ""
            for sel in (".c-abstract", ".content-right_8Zs40", ".c-span-last", "div[class*='abstract']"):
                abs_el = block.select_one(sel)
                if abs_el:
                    abstract = abs_el.get_text(strip=True)
                    break

            pub_time = ""
            source = site_name
            for sel in (".c-color-gray2", ".c-gap-left-small"):
                meta_el = block.select_one(sel)
                if not meta_el:
                    continue
                meta = meta_el.get_text(strip=True)
                if meta:
                    pub_time = parse_search_publish_time(meta) or pub_time
                    if not pub_time and "前" not in meta:
                        source = meta.split()[0] if meta.split() else source

            if not pub_time and abstract:
                pub_time = parse_search_publish_time(abstract)

            items.append({
                "title": title,
                "url": href,
                "publish_time": pub_time,
                "source": source,
                "keyword": keyword,
                "abstract": abstract,
            })
        except Exception as e:
            logger.debug(f"[百度搜索] 解析条目失败: {e}")
    if skipped:
        logger.debug(f"[百度搜索] 跳过非网页结果 {skipped} 条")
    return items


async def _simulate_human_reading(page, browser: CloakBrowser) -> None:
    """加载后模拟阅读：随机等待、鼠标移动、自然滚动"""
    await CloakBrowser.human_delay()
    await CloakBrowser.human_mouse_move(page)
    await CloakBrowser.human_scroll(page, headless=browser.headless)


async def search(
    browser: CloakBrowser,
    site: dict,
    keyword: str,
    keep_days: int,
    search_url: str,
) -> list[dict]:
    site_name = site.get("site_name", "百度搜索")
    all_items: list[dict] = []
    page_index = 0

    max_pages = MeetingConfig.SEARCH_ENGINE_MAX_PAGES
    async with browser.session() as page:
        while page_index < max_pages:
            url = build_baidu_web_url(keyword, page_index, keep_days)
            logger.debug(f"[{site_name}] [{keyword}] 第{page_index + 1}页: {url}")

            if page_index > 0:
                await CloakBrowser.human_delay(
                    CrawlerConfig.KEYWORD_DELAY_MIN,
                    CrawlerConfig.KEYWORD_DELAY_MAX,
                )

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await _simulate_human_reading(page, browser)
                html = await page.content()
            except Exception as e:
                logger.warning(f"[{site_name}] [{keyword}] 第{page_index + 1}页失败: {e}")
                break

            page_items = _parse_baidu_web_html(html, keyword, site_name)
            if not page_items:
                logger.info(f"[{site_name}] [{keyword}] 第{page_index + 1}页无结果，停止")
                break

            all_items.extend(page_items)
            logger.info(
                f"[{site_name}] [{keyword}] 第{page_index + 1}页: "
                f"{len(page_items)} 条，累计 {len(all_items)} 条"
            )

            if page_all_items_too_old(page_items, keep_days):
                logger.info(f"[{site_name}] [{keyword}] 本页均已超期，停止翻页")
                break

            page_index += 1

    filtered = filter_web_search_results(all_items, keep_days)
    logger.info(
        f"[{site_name}] [{keyword}] 结束: 解析 {len(all_items)} 条，"
        f"过滤后 {len(filtered)} 条（近 {keep_days} 天，URL 已限时间，无日期保留）"
    )
    return filtered
