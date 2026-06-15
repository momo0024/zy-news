"""
中央级网站爬虫
包含：人民日报（人民网）、新华社（新华网）、央视网、光明网、经济日报等
"""

import asyncio
import sys
from pathlib import Path
from urllib.parse import urljoin, unquote, parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from loguru import logger

from crawlers.cloak_browser import CloakBrowser
from crawlers.sites.common import (
    all_items_are_recent,
    filter_recent_news,
    has_any_recent_item,
    parse_generic_search_results,
    save_news_to_db,
    search_generic_with_pagination,
)


# ============================================================
# 解析器
# ============================================================

def _parse_people(html: str, keyword: str, site_name: str, site_url: str) -> list[dict]:
    """解析人民网搜索（search.people.cn）结果"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    items = []

    for li in soup.select("ul.article li.clear"):
        try:
            content_div = li.select_one("div.content")
            if not content_div:
                continue

            title_el = content_div.select_one("div.ttl a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            url = title_el.get("href", "")

            abs_el = content_div.select_one("div.abs")
            abstract = abs_el.get_text(strip=True) if abs_el else ""

            time_el = content_div.select_one("span.tip-pubtime")
            pub_time = time_el.get_text(strip=True) if time_el else ""

            source_el = content_div.select_one("a.tip-source")
            source = source_el.get_text(strip=True) if source_el else site_name
            if source.startswith("来源："):
                source = source[3:]

            if title and url:
                items.append({
                    "title": title,
                    "url": url,
                    "publish_time": pub_time,
                    "source": source,
                    "matched_keyword": keyword,
                    "abstract": abstract,
                })
        except Exception as e:
            logger.warning(f"解析人民网条目失败: {e}")

    return items


def _parse_cctv(html: str, keyword: str, site_name: str, site_url: str) -> list[dict]:
    """解析央视网搜索（search.cctv.com）结果"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    items = []

    for li in soup.select(".tuwenjg ul li.image"):
        try:
            title_el = li.select_one("h3.tit a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")

            # 央视网使用 link_p.php?targetpage=... 跳转，提取真实 URL
            url = ""
            if href.startswith("link_p.php"):
                parsed = urlparse("https://search.cctv.com/" + href)
                qs = parse_qs(parsed.query)
                if "targetpage" in qs:
                    url = unquote(qs["targetpage"][0])
            elif href.startswith("http"):
                url = href
            else:
                url = urljoin("https://search.cctv.com/", href)

            # 摘要
            abs_el = li.select_one("p.bre")
            abstract = ""
            if abs_el:
                # 去掉摘要中的图片
                for img in abs_el.find_all("img"):
                    img.decompose()
                abstract = abs_el.get_text(strip=True)

            # 来源与时间
            src_tim = li.select_one(".src-tim")
            source = site_name
            pub_time = ""
            if src_tim:
                src_el = src_tim.select_one(".src")
                if src_el:
                    source_text = src_el.get_text(strip=True)
                    if source_text.startswith("来源："):
                        source = source_text[3:]
                    else:
                        source = source_text

                tim_el = src_tim.select_one(".tim")
                if tim_el:
                    tim_text = tim_el.get_text(strip=True)
                    if tim_text.startswith("发布时间："):
                        pub_time = tim_text[5:]
                    else:
                        pub_time = tim_text

            if title and url:
                items.append({
                    "title": title,
                    "url": url,
                    "publish_time": pub_time,
                    "source": source,
                    "matched_keyword": keyword,
                    "abstract": abstract,
                    "site_url": site_url,
                })
        except Exception as e:
            logger.warning(f"解析央视网条目失败: {e}")

    return items


# ============================================================
# 人民网：精确匹配 + 日期感知翻页
# ============================================================

async def _search_people(
    browser: CloakBrowser, search_url: str, keyword: str,
    site_name: str, site_url: str, keep_days: int,
) -> list[dict]:
    all_items = []
    async with browser.session() as page:
        logger.debug(f"[人民网] 导航到搜索页: {search_url[:120]}")
        await page.goto(search_url, wait_until="networkidle", timeout=30000)

        # 勾选精确匹配
        try:
            await page.wait_for_selector("label.el-checkbox", timeout=5000)
            checkbox = page.locator("label.el-checkbox input[type='checkbox']")
            if not await checkbox.is_checked():
                await page.click("label.el-checkbox")
                logger.debug("[人民网] 已勾选精确匹配，等待结果刷新")
                await page.wait_for_timeout(2000)
                await page.wait_for_selector("ul.article li.clear", timeout=10000)
        except Exception as e:
            logger.warning(f"[人民网] 精确匹配复选框操作失败: {e}")
            await page.wait_for_timeout(2000)

        page_num = 0
        max_pages = 50

        while page_num < max_pages:
            page_num += 1
            await browser.human_delay(0.5, 1.5)
            await browser.human_mouse_move(page)
            await browser.human_scroll(page)

            html = await page.content()
            page_items = _parse_people(html, keyword, site_name, site_url)
            logger.debug(f"[人民网] 第{page_num}页: {len(page_items)} 条")

            if not page_items:
                logger.debug(f"[人民网] 第{page_num}页无结果，停止翻页")
                break

            all_items.extend(page_items)

            if not all_items_are_recent(page_items, keep_days):
                logger.info(f"[人民网] 第{page_num}页已混入非近{keep_days}天新闻，停止翻页")
                break

            next_btn = page.locator("span.page-next")
            if await next_btn.count() > 0:
                cls = await next_btn.get_attribute("class") or ""
                if "disabled" in cls:
                    logger.debug(f"[人民网] 已到最后一页（第{page_num}页）")
                    break
                logger.debug(f"[人民网] 点击下一页...")
                await next_btn.click()
                await page.wait_for_timeout(2000)
                await page.wait_for_selector("ul.article li.clear", timeout=10000)
            else:
                logger.debug(f"[人民网] 无下一页按钮，共 {page_num} 页")
                break

    logger.info(f"[人民网] 翻页完成，共 {page_num} 页，解析 {len(all_items)} 条")
    return all_items


# ============================================================
# 新华社：SPA + Ant Design 翻页
# ============================================================

async def _search_xinhua(
    browser: CloakBrowser, search_url: str, keyword: str,
    site_name: str, site_url: str, keep_days: int,
) -> list[dict]:
    all_items = []
    async with browser.session() as page:
        logger.debug(f"[新华社] 导航到搜索页: {search_url[:120]}")
        await page.goto(search_url, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(3000)

        page_num = 0
        max_pages = 50

        while page_num < max_pages:
            page_num += 1
            await browser.human_delay(0.5, 1.5)
            await browser.human_mouse_move(page)
            await browser.human_scroll(page)

            links = await page.locator("a").all()
            page_items = []
            for link in links:
                href = await link.get_attribute("href") or ""
                title = (await link.inner_text()).strip()
                abs_url = urljoin("https://so.news.cn/", href)

                if "news.cn/" in abs_url and "/c.html" in abs_url and len(title) > 5:
                    pub_time = ""
                    parts = abs_url.split("/")
                    for part in parts:
                        if len(part) == 8 and part.isdigit():
                            pub_time = f"{part[:4]}-{part[4:6]}-{part[6:8]}"
                            break

                    page_items.append({
                        "title": title,
                        "url": abs_url,
                        "publish_time": pub_time,
                        "source": site_name,
                        "matched_keyword": keyword,
                        "site_url": site_url,
                    })

            seen = set()
            unique_items = []
            for item in page_items:
                if item["url"] not in seen:
                    seen.add(item["url"])
                    unique_items.append(item)

            logger.debug(f"[新华社] 第{page_num}页: {len(unique_items)} 条")

            if not unique_items:
                logger.debug(f"[新华社] 第{page_num}页无结果，停止翻页")
                break

            all_items.extend(unique_items)

            if not all_items_are_recent(unique_items, keep_days):
                logger.info(f"[新华社] 第{page_num}页已混入非近{keep_days}天新闻，停止翻页")
                break

            next_btn = page.locator("li.ant-pagination-next")
            if await next_btn.count() > 0:
                cls = await next_btn.get_attribute("class") or ""
                if "disabled" in cls:
                    logger.debug(f"[新华社] 已到最后一页（第{page_num}页）")
                    break
                logger.debug(f"[新华社] 点击下一页...")
                await next_btn.click()
                await page.wait_for_timeout(3000)
            else:
                logger.debug(f"[新华社] 无翻页按钮，共 {page_num} 页")
                break

    logger.info(f"[新华社] 翻页完成，共 {page_num} 页，解析 {len(all_items)} 条")
    return all_items


# ============================================================
# 央视网：服务端渲染 + URL 翻页
# ============================================================

async def _search_cctv(
    browser: CloakBrowser, search_url: str, keyword: str,
    site_name: str, site_url: str, keep_days: int,
) -> list[dict]:
    all_items = []
    async with browser.session() as page:
        logger.debug(f"[央视网] 导航到搜索页: {search_url[:120]}")
        await page.goto(search_url, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(3000)

        page_num = 0
        max_pages = 50

        while page_num < max_pages:
            page_num += 1
            await browser.human_delay(0.5, 1.5)
            await browser.human_mouse_move(page)
            await browser.human_scroll(page)

            html = await page.content()
            page_items = _parse_cctv(html, keyword, site_name, site_url)
            logger.debug(f"[央视网] 第{page_num}页: {len(page_items)} 条")

            if not page_items:
                logger.debug(f"[央视网] 第{page_num}页无结果，停止翻页")
                break

            all_items.extend(page_items)

            if not all_items_are_recent(page_items, keep_days):
                logger.info(f"[央视网] 第{page_num}页已混入非近{keep_days}天新闻，停止翻页")
                break

            # 央视网翻页：点击 .page-next
            next_btn = page.locator("a.page-next")
            if await next_btn.count() > 0:
                href = await next_btn.get_attribute("href") or ""
                if not href or href == "javascript:void(0);":
                    logger.debug(f"[央视网] 已到最后一页（第{page_num}页）")
                    break
                logger.debug(f"[央视网] 点击下一页...")
                await next_btn.click()
                await page.wait_for_timeout(3000)
                await page.wait_for_selector(".tuwenjg ul li.image", timeout=10000)
            else:
                logger.debug(f"[央视网] 无下一页按钮，共 {page_num} 页")
                break

    logger.info(f"[央视网] 翻页完成，共 {page_num} 页，解析 {len(all_items)} 条")
    return all_items


# ============================================================
# 入口
# ============================================================

async def search(
    browser: CloakBrowser, site: dict, keyword: str, keep_days: int, search_url: str,
) -> list[dict]:
    """中央级网站搜索入口"""
    site_name = site["site_name"]
    site_url = site.get("site_url", "")

    if "人民" in site_name or "people" in site_url.lower():
        return await _search_people(browser, search_url, keyword, site_name, site_url, keep_days)
    if "新华社" in site_name or "xinhuanet" in site_url.lower():
        return await _search_xinhua(browser, search_url, keyword, site_name, site_url, keep_days)
    if "央视" in site_name or "cctv" in site_url.lower():
        return await _search_cctv(browser, search_url, keyword, site_name, site_url, keep_days)

    # 其他中央级网站使用通用翻页
    return await search_generic_with_pagination(
        browser, search_url, keyword, site_name, site_url, keep_days,
    )
