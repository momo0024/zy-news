"""
中国日报（中国日报网）搜索爬虫 v2
- 搜索入口: https://newssearch.chinadaily.com.cn/cn/search?cond={JSON}&language=cn
- cond: publishedDateFrom/To + titleMust(标题) 或 fullMust(全文) + sort=dp + duplication=on
- 解析页面 .cs_result .lft_art .art_detail 条目，不调用任何内部 REST API
- 翻页: JS $SearchController.pagging(n)，n=page_no（从 0 开始，0→第1页本身，1→第2页）
"""

import asyncio
import json
import re
from urllib.parse import parse_qs, quote, urlparse

from loguru import logger

from crawlers.cloak_browser import CloakBrowser
from crawlers.sites.common import all_items_are_recent, filter_recent_news
from utils.timezone import recent_date_range_str

_SEARCH_BASE = "https://newssearch.chinadaily.com.cn/cn/search"
_MAX_PAGES = 10
_PAGE_SIZE = 10
_RESULT_APPEAR_TIMEOUT_MS = 25000  # 等第一批条目出现
_STABLE_POLL_MS = 250              # 稳定性轮询间隔
_STABLE_ROUNDS = 4                 # 连续 N 次相同算稳定（≈1s）
_STABLE_TIMEOUT_S = 12             # 稳定等待最长秒数

_TAG_RE = re.compile(r"<[^>]+>")
_SNIPPET_MAX = 400
# "(来源名称) YYYY-MM-DD HH:MM" 或 "(来源名称) YYYY-MM-DD"
_META_RE = re.compile(r"\((.+?)\)\s*(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?)")

_ART_DETAIL_SEL = ".cs_result .lft_art .art_detail"


def _strip_html(text: str) -> str:
    return _TAG_RE.sub("", text or "").strip()


def _scope_from_url(search_url: str) -> str:
    """从 search_url 的 scope 参数读取检索范围（body / title）"""
    params = parse_qs(urlparse(search_url or "").query)
    scope = (params.get("scope") or ["body"])[0].lower()
    return "title" if scope == "title" else "body"


def _scope_label(scope: str) -> str:
    return "标题" if scope == "title" else "全文"


def _build_search_url(keyword: str, scope: str, keep_days: int) -> str:
    """构造带 cond 参数的搜索页 URL"""
    date_from, date_to = recent_date_range_str(keep_days)
    cond: dict = {
        "publishedDateFrom": date_from,
        "publishedDateTo": date_to,
        "sort": "dp",
        "duplication": "on",
    }
    if scope == "title":
        cond["titleMust"] = keyword
    else:
        cond["fullMust"] = keyword
    cond_json = json.dumps(cond, separators=(",", ":"), ensure_ascii=False)
    return f"{_SEARCH_BASE}?cond={quote(cond_json)}&language=cn"


def _parse_meta(meta_text: str) -> tuple[str, str]:
    """从 '(来源) YYYY-MM-DD HH:MM' 中提取 (source, publish_time)"""
    m = _META_RE.search(meta_text or "")
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return "", ""


_PARSE_PAGE_JS = r"""
() => {
    const items = [];
    document.querySelectorAll('.cs_result .lft_art .art_detail').forEach(el => {
        const ha = el.querySelector('.intro h4 a');
        if (!ha) return;
        const href = ha.href || '';
        const title = (ha.innerText || '').trim().replace(/\s+/g, ' ');
        if (!href || !title) return;
        const bs = el.querySelectorAll('.intro b');
        const meta     = bs[0] ? (bs[0].innerText || '').trim() : '';
        const abstract = bs[1] ? (bs[1].innerText || '').trim().slice(0, 400) : '';
        items.push({ href, title, meta, abstract });
    });
    const totalEl = document.querySelector('.cs_result .results b');
    const total = totalEl ? parseInt(totalEl.innerText, 10) : 0;
    return { items, total };
}
"""

_FIRST_HREF_JS = """() => {
    const a = document.querySelector('.cs_result .lft_art .art_detail .intro h4 a');
    return a ? a.href : '';
}"""

_ART_COUNT_JS = f"() => document.querySelectorAll('{_ART_DETAIL_SEL}').length"


_LFT_ART_HAS_CONTENT_JS = (
    "() => { const d = document.querySelector('.cs_result .lft_art');"
    " return d && d.innerHTML.trim().length > 100; }"
)


async def _wait_stable(page, prev_first_url: str = "") -> int:
    """等待文章条目数量达到 ≥1 且连续稳定。

    若 prev_first_url 非空，先等第一篇 URL 切换（内容已翻页），再等稳定。
    返回稳定后的 .art_detail 条目数。
    """
    deadline = asyncio.get_event_loop().time() + _STABLE_TIMEOUT_S

    # 翻页后：先等第一篇 URL 发生变化
    if prev_first_url:
        while asyncio.get_event_loop().time() < deadline:
            first = await page.evaluate(_FIRST_HREF_JS)
            if first and first != prev_first_url:
                break
            await asyncio.sleep(_STABLE_POLL_MS / 1000)

    # 等 lft_art div 有内容（innerHTML 长度 > 100），已验证可靠
    try:
        await page.wait_for_function(
            _LFT_ART_HAS_CONTENT_JS,
            timeout=_RESULT_APPEAR_TIMEOUT_MS,
        )
    except Exception:
        return 0  # 无结果，返回 0

    # 等 .art_detail 数量稳定
    prev_n = -1
    stable = 0
    deadline2 = asyncio.get_event_loop().time() + _STABLE_TIMEOUT_S
    while asyncio.get_event_loop().time() < deadline2:
        n = await page.evaluate(_ART_COUNT_JS)
        if n >= 1 and n == prev_n:
            stable += 1
            if stable >= _STABLE_ROUNDS:
                return n
        else:
            stable = 0
        prev_n = n
        await asyncio.sleep(_STABLE_POLL_MS / 1000)
    return max(prev_n, 0)


async def _parse_page(page) -> tuple[list[dict], int]:
    result = await page.evaluate(_PARSE_PAGE_JS)
    return result.get("items", []), int(result.get("total") or 0)


async def search(
    browser: CloakBrowser,
    site: dict,
    keyword: str,
    keep_days: int,
    search_url: str,
) -> list[dict]:
    """中国日报搜索（标题或全文，由 search_url 中 scope 决定）"""
    site_name = site["site_name"]
    scope = _scope_from_url(search_url)
    scope_label = _scope_label(scope)
    nav_url = _build_search_url(keyword, scope, keep_days)

    all_items: list[dict] = []

    async with browser.session() as page:
        try:
            await page.goto(nav_url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            logger.warning(f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 搜索页加载失败: {e}")
            return []

        await _wait_stable(page)

        for page_no in range(_MAX_PAGES):
            try:
                raw_items, total = await _parse_page(page)
            except Exception as e:
                logger.warning(f"[{site_name}] 关键词 [{keyword}] 第{page_no + 1}页解析异常: {e}")
                break

            if not raw_items:
                logger.info(
                    f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 "
                    f"第{page_no + 1}页无结果，停止"
                )
                break

            page_items: list[dict] = []
            for it in raw_items:
                source, pub_time = _parse_meta(it.get("meta", ""))
                page_items.append(
                    {
                        "title": it["title"],
                        "url": it["href"],
                        "publish_time": pub_time,
                        "source": source or site_name,
                        "keyword": keyword,
                        "abstract": _strip_html(it.get("abstract", ""))[:_SNIPPET_MAX],
                    }
                )

            all_items.extend(page_items)
            total_pages = max(1, -(-total // _PAGE_SIZE))  # ceil division

            logger.info(
                f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 第{page_no + 1}页: "
                f"解析 {len(page_items)} 条，累计 {len(all_items)} / 共 {total} 条"
            )

            if not all_items_are_recent(page_items, keep_days):
                logger.info(
                    f"[{site_name}] 关键词 [{keyword}] {scope_label}检索 "
                    f"第{page_no + 1}页已出现超期条目，停止翻页"
                )
                break

            if page_no + 1 >= total_pages or page_no + 1 >= _MAX_PAGES:
                break

            # 翻到下一页：pagging(n) 跳到页号 n+1
            first_url = raw_items[0].get("href", "") if raw_items else ""
            try:
                await page.evaluate(f"$SearchController.pagging({page_no + 1})")
                await _wait_stable(page, prev_first_url=first_url)
            except Exception as e:
                logger.warning(f"[{site_name}] 翻页到第{page_no + 2}页失败: {e}")
                break

    filtered = filter_recent_news(all_items, keep_days)
    logger.info(
        f"[{site_name}] 关键词 [{keyword}] {scope_label}检索结束: "
        f"解析 {len(all_items)} 条，过滤后 {len(filtered)} 条（近 {keep_days} 天）"
    )
    return filtered
