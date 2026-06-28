"""
光明日报（光明网）高级搜索爬虫
- 高级搜索入口: https://zhonghua.gmw.cn/search_advanced.htm?source=gmrb
- 结果页: gmrb.htm（category=g，默认光明日报 tab）
- tt=true 标题 / tt=false 全文（由 site_crawler 分别调用）
- siteflag= 全站、fm=false 精确检索、limitTime=2 一天内
"""

import asyncio
import re
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from loguru import logger

from crawlers.cloak_browser import CloakBrowser
from crawlers.sites.common import all_items_are_recent, filter_recent_news
from utils.timezone import APP_TZ

_BASE = "https://zhonghua.gmw.cn/gmrb.htm"
_MAX_PAGES = 50
_PAGE_SIZE = 10
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return _TAG_RE.sub("", text or "").strip()


def _scope_label(tt: str) -> str:
    return "标题" if tt == "true" else "全文"


def _time_params(keep_days: int) -> tuple[str, str, str]:
    """limitTime + beginTime/endTime（与高级搜索页 1天内 逻辑一致）"""
    now = datetime.now(APP_TZ)
    if keep_days <= 1:
        start = now - timedelta(days=1)
        return (
            "2",
            start.strftime("%Y-%m-%d %H:%M:%S"),
            now.strftime("%Y-%m-%d %H:%M:%S"),
        )
    if keep_days <= 7:
        start = now - timedelta(days=7)
        return (
            "3",
            start.strftime("%Y-%m-%d %H:%M:%S"),
            now.strftime("%Y-%m-%d %H:%M:%S"),
        )
    from utils.timezone import recent_date_range_str
    start_d, end_d = recent_date_range_str(keep_days)
    return "0", f"{start_d} 00:00:00", f"{end_d} 23:59:59"


def _build_page_url(
    search_url: str,
    keyword: str,
    keep_days: int,
    page: int,
) -> str:
    """根据 search_url 中的 tt/fm/siteflag 等拼结果页 URL"""
    parsed = urlparse(search_url or _BASE)
    params = parse_qs(parsed.query, keep_blank_values=True)
    flat = {k: (v[0] if v else "") for k, v in params.items()}

    limit_time, begin, end = _time_params(keep_days)
    flat.update({
        "q": keyword,
        "c": "n",
        "adv": "true",
        "cp": str(page),
        "limitTime": limit_time,
        "beginTime": begin,
        "endTime": end,
        "fm": flat.get("fm") or "false",
        "siteflag": flat.get("siteflag") or "",
        "editor": flat.get("editor") or "",
        "sourceName": flat.get("sourceName") or "",
    })
    if "tt" not in flat:
        flat["tt"] = "false"

    path = parsed.path or "/gmrb.htm"
    if not path.endswith(".htm"):
        path = "/gmrb.htm"
    query = urlencode(flat)
    return urlunparse(("https", "zhonghua.gmw.cn", path, "", query, ""))


def _parse_results(html: str, keyword: str, site_name: str) -> list[dict]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []

    for box in soup.select(".m-news-box"):
        try:
            title_el = box.select_one("h3 a")
            if not title_el:
                continue
            title = _strip_html(title_el.get_text(strip=True))
            href = (title_el.get("href") or "").strip()

            source = site_name
            pub_time = ""

            h3_el = box.select_one("h3")
            if h3_el:
                meta_span = h3_el.select_one("span")
                if meta_span:
                    m = re.search(
                        r"(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}:\d{2})?)",
                        meta_span.get_text(strip=True),
                    )
                    if m:
                        pub_time = m.group(1)

            links_el = box.select_one("p.u-links")
            url = links_el.get_text(strip=True) if links_el else href
            if url and not url.startswith("http"):
                url = f"https://zhonghua.gmw.cn/{url.lstrip('/')}"

            abstract = ""
            des_el = box.select_one("p.u-des")
            if des_el:
                abstract = _strip_html(des_el.get_text(strip=True))

            source_time_el = box.select_one("p.u-source")
            if source_time_el:
                source_text = source_time_el.get_text(strip=True)
                time_el = source_time_el.select_one("span.u-time")
                if time_el:
                    if not pub_time:
                        pub_time = time_el.get_text(strip=True)
                    source = source_text.replace(time_el.get_text(strip=True), "").replace("来源：", "").strip()
                else:
                    source = source_text.replace("来源：", "").strip()

            if title and url:
                items.append({
                    "title": title,
                    "url": url,
                    "publish_time": pub_time,
                    "source": source or site_name,
                    "keyword": keyword,
                    "abstract": abstract,
                })
        except Exception as e:
            logger.warning(f"[{site_name}] 解析条目失败: {e}")

    return items


async def _ensure_gmrb_tab(page, site_name: str) -> None:
    """结果页确保选中光明日报 tab（id=g）"""
    tab = page.locator(".m-nav-box li#g")
    if await tab.count() == 0:
        return
    cls = await tab.get_attribute("class") or ""
    if "active" not in cls:
        await tab.click()
        await page.wait_for_timeout(2000)
        logger.debug(f"[{site_name}] 已切换到光明日报 tab")


async def search(
    browser: CloakBrowser,
    site: dict,
    keyword: str,
    keep_days: int,
    search_url: str,
) -> list[dict]:
    """光明日报高级搜索（标题或全文，结果限定光明日报 tab）"""
    site_name = site["site_name"]
    parsed = urlparse(search_url or "")
    tt = (parse_qs(parsed.query).get("tt") or ["false"])[0]
    scope_label = _scope_label(tt)

    all_items: list[dict] = []
    page_no = 0

    async with browser.session() as page:
        while page_no < _MAX_PAGES:
            page_no += 1
            page_url = _build_page_url(search_url, keyword, keep_days, page_no)
            logger.debug(
                f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 第{page_no}页: "
                f"{page_url[:160]}"
            )

            try:
                await page.goto(page_url, wait_until="networkidle", timeout=60000)
                await page.wait_for_timeout(2000)
            except Exception as e:
                logger.warning(
                    f"[{site_name}] 关键词 [{keyword}] 第{page_no}页加载失败: {e}"
                )
                break

            await _ensure_gmrb_tab(page, site_name)

            try:
                await page.wait_for_selector(".m-news-box", timeout=30000)
            except Exception:
                if page_no == 1:
                    logger.info(
                        f"[{site_name}] 关键词 [{keyword}] {scope_label}检索无结果"
                    )
                break

            html = await page.content()
            page_items = _parse_results(html, keyword, site_name)
            if not page_items:
                logger.info(
                    f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 "
                    f"第{page_no}页无结果，停止翻页"
                )
                break

            all_items.extend(page_items)
            logger.info(
                f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 第{page_no}页: "
                f"解析 {len(page_items)} 条，累计 {len(all_items)} 条"
            )

            if len(page_items) < _PAGE_SIZE:
                break

            if not all_items_are_recent(page_items, keep_days):
                logger.info(
                    f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 "
                    f"第{page_no}页已出现超期条目，停止翻页"
                )
                break

            await asyncio.sleep(0.8)

    filtered = filter_recent_news(all_items, keep_days)
    logger.info(
        f"[{site_name}] 关键词 [{keyword}] {scope_label}检索结束: "
        f"翻{page_no}页, 解析 {len(all_items)} 条, "
        f"时间过滤后 {len(filtered)} 条（保留近 {keep_days} 天）"
    )
    return filtered
