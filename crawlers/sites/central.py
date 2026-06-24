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


def _parse_gmw(html: str, keyword: str, site_name: str, site_url: str) -> list[dict]:
    """解析光明网搜索（zhonghua.gmw.cn）结果"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    items = []

    for box in soup.select(".m-news-box"):
        try:
            title_el = box.select_one("h3 a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")

            # 优先使用 u-links 中的真实URL
            links_el = box.select_one("p.u-links")
            if links_el:
                url = links_el.get_text(strip=True)
            else:
                url = href if href.startswith("http") else urljoin("https://zhonghua.gmw.cn", href)

            source_time_el = box.select_one("p.u-source")
            source = site_name
            pub_time = ""
            if source_time_el:
                source_text = source_time_el.get_text(strip=True)
                time_el = source_time_el.select_one("span.u-time")
                if time_el:
                    pub_time = time_el.get_text(strip=True)
                    source = source_text.replace(pub_time, "").replace("来源：", "").strip()
                else:
                    source = source_text

            if title and url:
                items.append({
                    "title": title,
                    "url": url,
                    "publish_time": pub_time,
                    "source": source or site_name,
                    "matched_keyword": keyword,
                    "site_url": site_url,
                })
        except Exception as e:
            logger.warning(f"解析光明网条目失败: {e}")

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
                    "matched_keyword": keyword,
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
                    "matched_keyword": keyword,
                    "site_url": site_url,
                })
        except Exception as e:
            logger.warning(f"解析求是网条目失败: {e}")

    return items


# ============================================================
# 人民网：精确匹配 + 日期感知翻页
# ============================================================

async def _search_people(
    browser: CloakBrowser, search_url: str, keyword: str,
    site_name: str, site_url: str, keep_days: int,
) -> list[dict]:
    async with browser.session() as page:
        logger.debug(f"[人民网] 导航到搜索页: {search_url[:120]}")
        await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)

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

        # 解析函数
        async def parse_page(page):
            html = await page.content()
            return _parse_people(html, keyword, site_name, site_url)

        # 翻页函数
        async def click_next(page, page_num):
            next_btn = page.locator("span.page-next")
            if await next_btn.count() > 0:
                cls = await next_btn.get_attribute("class") or ""
                if "disabled" in cls:
                    logger.debug(f"[人民网] 已到最后一页（第{page_num}页）")
                    return False
                await next_btn.click()
                await page.wait_for_timeout(2000)
                await page.wait_for_selector("ul.article li.clear", timeout=10000)
                return True
            return False

        return await pagination_loop(page, browser, site_name, keep_days, parse_page, click_next)


# ============================================================
# 新华社：SPA + Ant Design 翻页 + 弹窗重试
# ============================================================

async def _search_xinhua(
    browser: CloakBrowser, search_url: str, keyword: str,
    site_name: str, site_url: str, keep_days: int,
) -> list[dict]:
    async with browser.session() as page:
        logger.debug(f"[新华社] 导航到搜索页: {search_url[:120]}")

        # 初始加载 + 弹窗检测重试
        retry_delays = [10, 30]
        for attempt, delay in enumerate(retry_delays, start=1):
            try:
                await page.goto(search_url, wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(3000)
            except Exception as e:
                logger.warning(f"[新华社] 页面加载失败 (尝试{attempt}/{len(retry_delays)}): {e}")
                if attempt < len(retry_delays):
                    await asyncio.sleep(delay)
                    continue
                else:
                    logger.error(f"[新华社] 搜索页加载多次失败，跳过")
                    return []

            if await check_and_retry_popup(page, site_name):
                break
            if attempt >= len(retry_delays):
                logger.error(f"[新华社] 多次重试后仍被拦截，跳过")
                return []
        else:
            logger.error(f"[新华社] 所有重试均失败，跳过")
            return []

        # 解析函数
        async def parse_page(page):
            # 弹窗检测
            if not await check_and_retry_popup(page, site_name):
                return []

            links = await page.locator("a").all()
            items = []
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

                    items.append({
                        "title": title,
                        "url": abs_url,
                        "publish_time": pub_time,
                        "source": site_name,
                        "matched_keyword": keyword,
                        "site_url": site_url,
                    })

            return deduplicate_by_url(items)

        # 翻页函数
        async def click_next(page, page_num):
            next_btn = page.locator("li.ant-pagination-next")
            if await next_btn.count() > 0:
                cls = await next_btn.get_attribute("class") or ""
                if "disabled" in cls:
                    logger.debug(f"[新华社] 已到最后一页（第{page_num}页）")
                    return False
                await next_btn.click()
                await page.wait_for_timeout(3000)
                return True
            return False

        return await pagination_loop(page, browser, site_name, keep_days, parse_page, click_next)


# ============================================================
# 央视网：服务端渲染 + 点击翻页
# ============================================================

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


# ============================================================
# 光明网：layui 分页 + JS 动态加载
# ============================================================

async def _search_gmw(
    browser: CloakBrowser, search_url: str, keyword: str,
    site_name: str, site_url: str, keep_days: int,
) -> list[dict]:
    async with browser.session() as page:
        logger.debug(f"[光明网] 导航到搜索页: {search_url[:120]}")
        await page.goto(search_url, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(3000)

        async def parse_page(page):
            html = await page.content()
            return _parse_gmw(html, keyword, site_name, site_url)

        async def click_next(page, page_num):
            next_btn = page.locator(".layui-laypage-next")
            if await next_btn.count() > 0:
                cls = await next_btn.get_attribute("class") or ""
                if "disabled" in cls or "layui-disabled" in cls:
                    logger.debug(f"[光明网] 已到最后一页（第{page_num}页）")
                    return False
                await next_btn.click()
                await page.wait_for_timeout(4000)
                return True
            return False

        return await pagination_loop(page, browser, site_name, keep_days, parse_page, click_next)


# ============================================================
# 求是网：固定搜索URL + JS动态筛选 + AJAX翻页
# ============================================================

async def _search_qiushi(
    browser: CloakBrowser, search_url: str, keyword: str,
    site_name: str, site_url: str, keep_days: int,
) -> list[dict]:
    async with browser.session() as page:
        logger.debug(f"[求是网] 导航到搜索页: {search_url[:120]}")

        # 1. 打开固定搜索页
        await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(1500)

        # 2. 通过 JS 点击设置筛选条件
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

        # 全部来源 ly=1（默认已选中）
        # 一周内 sj=3
        await _apply_filter("3", "3", "一周内")
        # 时间顺序 orderby=1
        await _apply_filter("4", "1", "时间顺序")

        # 3. 等待 AJAX 结果刷新
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

            # 尝试点击当前页码后的下一页数字
            next_num = page_num + 1
            next_link = pagination.locator(f"a:has-text('{next_num}')")
            if await next_link.count() > 0:
                cls = await next_link.get_attribute("class") or ""
                if "current" in cls or "disabled" in cls:
                    return False
                await next_link.click()
                await page.wait_for_timeout(3000)
                return True

            # 没有具体页码时尝试"下一页"类元素
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
    if "光明" in site_name or "gmw" in site_url.lower():
        return await _search_gmw(browser, search_url, keyword, site_name, site_url, keep_days)
    if "求是" in site_name or "qstheory" in site_url.lower():
        return await _search_qiushi(browser, search_url, keyword, site_name, site_url, keep_days)
    if "科技日报" in site_name or "stdaily" in site_url.lower():
        from crawlers.sites import stdaily
        return await stdaily.search(browser, site, keyword, keep_days, search_url)
    if "中国日报" in site_name or "chinadaily" in site_url.lower():
        from crawlers.sites import chinadaily
        return await chinadaily.search(browser, site, keyword, keep_days, search_url)
    if "中国新闻社" in site_name or "中国新闻网" in site_name or "chinanews" in site_url.lower():
        from crawlers.sites import chinanews
        return await chinanews.search(browser, site, keyword, keep_days, search_url)

    return await search_generic_with_pagination(
        browser, search_url, keyword, site_name, site_url, keep_days,
    )