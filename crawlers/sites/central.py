"""
中央级网站爬虫
包含：人民日报（人民网）、新华社（新华网）、央视网、光明网、经济日报等
"""

import asyncio
from urllib.parse import urljoin, unquote, parse_qs, urlparse

from loguru import logger

from crawlers.cloak_browser import CloakBrowser
from crawlers.sites.common import (
    all_items_are_recent,
    deduplicate_by_url,
    check_and_retry_popup,
    pagination_loop,
    search_generic_with_pagination,
)


# ============================================================
# 解析器
# ============================================================

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

            abs_el = li.select_one("p.bre")
            abstract = ""
            if abs_el:
                for img in abs_el.find_all("img"):
                    img.decompose()
                abstract = abs_el.get_text(strip=True)

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
                    "keyword": keyword,
                    "abstract": abstract,
                    "site_url": site_url,
                })
        except Exception as e:
            logger.warning(f"解析央视网条目失败: {e}")

    return items


def _parse_qiushi(html: str, keyword: str, site_name: str, site_url: str) -> list[dict]:
    """解析求是网搜索（search.qstheory.cn/qiushi/）结果"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    items = []

    for item in soup.select(".search-content-list .search-content-item"):
        try:
            title_el = item.select_one("p.search-title a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            url = title_el.get("href", "")
            if url and not url.startswith("http"):
                url = urljoin("http://www.qstheory.cn", url)

            info_spans = item.select(".search-content-info span")
            source = site_name
            pub_time = ""
            for span in info_spans:
                text = span.get_text(strip=True)
                if text.startswith("来源："):
                    source = text[3:].strip() or site_name
                elif text.startswith("时间："):
                    pub_time = text[3:].strip()

            if title and url:
                items.append({
                    "title": title,
                    "url": url,
                    "publish_time": pub_time,
                    "source": source,
                    "keyword": keyword,
                    "site_url": site_url,
                })
        except Exception as e:
            logger.warning(f"解析求是网条目失败: {e}")

    return items


async def _search_cctv(
    browser: CloakBrowser, search_url: str, keyword: str,
    site_name: str, site_url: str, keep_days: int,
) -> list[dict]:
    async with browser.session() as page:
        logger.debug(f"[央视网] 导航到搜索页: {search_url[:120]}")
        await page.goto(search_url, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(3000)

        async def parse_page(page):
            html = await page.content()
            return _parse_cctv(html, keyword, site_name, site_url)

        async def click_next(page, page_num):
            next_btn = page.locator("a.page-next")
            if await next_btn.count() > 0:
                href = await next_btn.get_attribute("href") or ""
                if not href or href == "javascript:void(0);":
                    logger.debug(f"[央视网] 已到最后一页（第{page_num}页）")
                    return False
                await next_btn.click()
                await page.wait_for_timeout(3000)
                await page.wait_for_selector(".tuwenjg ul li.image", timeout=10000)
                return True
            return False

        return await pagination_loop(page, browser, site_name, keep_days, parse_page, click_next)


async def _search_qiushi(
    browser: CloakBrowser, search_url: str, keyword: str,
    site_name: str, site_url: str, keep_days: int,
) -> list[dict]:
    async with browser.session() as page:
        logger.debug(f"[求是网] 导航到搜索页: {search_url[:120]}")

        await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(1500)

        async def _apply_filter(data_class: str, data_value: str, name: str):
            selector = f".search-condition[data-class='{data_class}'] li[data-value='{data_value}']"
            btn = page.locator(selector).first
            if await btn.count() == 0:
                logger.warning(f"[求是网] 未找到{name}筛选按钮")
                return False
            cls = await btn.get_attribute("class") or ""
            if "active" in cls:
                logger.debug(f"[求是网] {name}已是目标值")
                return True
            await btn.click()
            logger.debug(f"[求是网] 已点击{name}")
            return True

        await _apply_filter("3", "3", "一周内")
        await _apply_filter("4", "1", "时间顺序")

        try:
            await page.wait_for_selector(".search-content-list .search-content-item", timeout=30000)
        except Exception:
            logger.warning("[求是网] 等待搜索结果超时，继续尝试解析当前内容")
        await page.wait_for_timeout(1500)

        async def parse_page(page):
            html = await page.content()
            return _parse_qiushi(html, keyword, site_name, site_url)

        async def click_next(page, page_num):
            pagination = page.locator("#Pagination")
            if await pagination.count() == 0:
                return False

            next_num = page_num + 1
            next_link = pagination.locator(f"a:has-text('{next_num}')")
            if await next_link.count() > 0:
                cls = await next_link.get_attribute("class") or ""
                if "current" in cls or "disabled" in cls:
                    return False
                await next_link.click()
                await page.wait_for_timeout(3000)
                return True

            next_btn = pagination.locator("a.next, a.jp-next, span.next")
            if await next_btn.count() > 0:
                cls = await next_btn.first.get_attribute("class") or ""
                if "disabled" in cls or "jp-disabled" in cls:
                    logger.debug(f"[求是网] 已到最后一页（第{page_num}页）")
                    return False
                await next_btn.first.click()
                await page.wait_for_timeout(3000)
                return True

            logger.debug(f"[求是网] 未找到第{page_num + 1}页入口，停止翻页")
            return False

        return await pagination_loop(page, browser, site_name, keep_days, parse_page, click_next)


async def search(
    browser: CloakBrowser, site: dict, keyword: str, keep_days: int, search_url: str,
) -> list[dict]:
    """中央级网站搜索入口"""
    site_name = site["site_name"]
    site_url = site.get("site_url", "")

    if "人民政协" in site_name or "rmzxb" in site_url.lower():
        from crawlers.sites import rmzxb
        return await rmzxb.search(browser, site, keyword, keep_days, search_url)
    if (
        ("人民日报" in site_name and "人民网" in site_name)
        or ("people.com.cn" in site_url.lower() and "人民政协" not in site_name)
    ):
        from crawlers.sites import people
        return await people.search(browser, site, keyword, keep_days, search_url)
    if (
        (site_name.startswith("新华社") or "xinhuanet" in site_url.lower())
        and "新华日报" not in site_name
    ):
        from crawlers.sites import xinhua
        return await xinhua.search(browser, site, keyword, keep_days, search_url)
    if "央视" in site_name or "cctv" in site_url.lower():
        return await _search_cctv(browser, search_url, keyword, site_name, site_url, keep_days)
    if "光明" in site_name or "gmw" in site_url.lower():
        from crawlers.sites import gmw
        return await gmw.search(browser, site, keyword, keep_days, search_url)
    if "求是" in site_name or "qstheory" in site_url.lower():
        return await _search_qiushi(browser, search_url, keyword, site_name, site_url, keep_days)
    if "学习时报" in site_name or "studytimes" in site_url.lower():
        from crawlers.sites import studytimes
        return await studytimes.search(browser, site, keyword, keep_days, search_url)
    if "科技日报" in site_name or "stdaily" in site_url.lower():
        from crawlers.sites import stdaily
        return await stdaily.search(browser, site, keyword, keep_days, search_url)
    if "中国日报" in site_name or "chinadaily" in site_url.lower():
        from crawlers.sites import chinadaily
        return await chinadaily.search(browser, site, keyword, keep_days, search_url)
    if "工人日报" in site_name or "workercn" in site_url.lower():
        from crawlers.sites import workercn
        return await workercn.search(browser, site, keyword, keep_days, search_url)
    if "中国新闻" in site_name or "chinanews" in site_url.lower():
        from crawlers.sites import chinanews
        return await chinanews.search(browser, site, keyword, keep_days, search_url)
    if "法治日报" in site_name or "legaldaily" in site_url.lower():
        from crawlers.sites import legaldaily
        return await legaldaily.search(browser, site, keyword, keep_days, search_url)

    return await search_generic_with_pagination(
        browser, search_url, keyword, site_name, site_url, keep_days,
    )
