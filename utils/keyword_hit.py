"""
文章 × 关键词命中关系：in_title / in_body / match_source

法治日报等站点仅 site_title_search、site_body_search；
无高级搜索的站点用 site_combined_search / inferred / unknown。
"""

from __future__ import annotations

# 命中信息来源（入库 news_keyword_hits.match_source）
MATCH_SOURCE_SITE_TITLE = "site_title_search"
MATCH_SOURCE_SITE_BODY = "site_body_search"
MATCH_SOURCE_SITE_COMBINED = "site_combined_search"
MATCH_SOURCE_INFERRED = "inferred"
MATCH_SOURCE_UNKNOWN = "unknown"

VALID_MATCH_SOURCES = frozenset({
    MATCH_SOURCE_SITE_TITLE,
    MATCH_SOURCE_SITE_BODY,
    MATCH_SOURCE_SITE_COMBINED,
    MATCH_SOURCE_INFERRED,
    MATCH_SOURCE_UNKNOWN,
})

# API 筛选：按 in_title / in_body 组合
MATCH_SCOPE_TITLE = "title"
MATCH_SCOPE_BODY = "body"
MATCH_SCOPE_BOTH = "both"
MATCH_SCOPE_UNKNOWN = "unknown"

VALID_MATCH_SCOPES = frozenset({
    MATCH_SCOPE_TITLE,
    MATCH_SCOPE_BODY,
    MATCH_SCOPE_BOTH,
    MATCH_SCOPE_UNKNOWN,
})

CRAWL_SCOPE_TITLE = "title"
CRAWL_SCOPE_BODY = "body"
CRAWL_SCOPE_ALL = "all"

def _contains_keyword(text: str | None, keyword: str) -> bool:
    if not text or not keyword:
        return False
    return keyword.lower() in text.lower()


def _flags_from_list_text(
    title: str,
    snippet: str | None,
    keyword: str,
) -> tuple[bool | None, bool | None]:
    """综合检索：用列表页标题/摘要校验；均未出现则 (None, None)"""
    t_hit = _contains_keyword(title, keyword)
    s_hit = _contains_keyword(snippet, keyword)
    if t_hit and s_hit:
        return True, True
    if t_hit:
        return True, None
    if s_hit:
        return None, True
    return None, None


def _flags_from_body_search(
    title: str,
    snippet: str | None,
    keyword: str,
) -> tuple[bool, bool]:
    """
    正文检索：列表页能校验则写明确 True/False；否则信任站点正文检索结果。
    关键词仅在正文、标题/摘要都没有时 → in_title=False, in_body=True
    """
    t_hit = _contains_keyword(title, keyword)
    s_hit = _contains_keyword(snippet, keyword)
    if t_hit and s_hit:
        return True, True
    if t_hit:
        return True, False
    if s_hit:
        return False, True
    return False, True

def is_keyword_hit_verified(in_title: bool | None, in_body: bool | None) -> bool:
    """至少一侧（标题或摘要/正文侧）校验命中"""
    return in_title is True or in_body is True

def crawl_scope_to_match_source(crawl_scope: str) -> str:
    """爬虫外层搜索模式 → match_source（法治日报在爬虫内直接写 site_title/body，不走 all）"""
    scope = (crawl_scope or CRAWL_SCOPE_ALL).lower()
    if scope == CRAWL_SCOPE_TITLE:
        return MATCH_SOURCE_SITE_TITLE
    if scope == CRAWL_SCOPE_BODY:
        return MATCH_SOURCE_SITE_BODY
    return MATCH_SOURCE_SITE_COMBINED


def resolve_hit_flags(
    keyword: str,
    title: str,
    *,
    snippet: str | None = None,
    match_source: str = MATCH_SOURCE_SITE_COMBINED,
    in_title: bool | None = None,
    in_body: bool | None = None,
) -> tuple[bool | None, bool | None, str]:
    """
    解析单次爬取结果的 in_title / in_body / match_source。

    - 标题检索：仅标题含关键词才入库
    - 正文检索：标题/摘要能校验则写 True/False；否则信任站点正文检索（仅正文命中）
    - 综合检索：必须在标题或摘要中校验到，否则不入库
    """
    _ = in_title, in_body  # 统一走校验逻辑，不再信任爬虫侧预置布尔值
    source = match_source if match_source in VALID_MATCH_SOURCES else MATCH_SOURCE_UNKNOWN

    if source == MATCH_SOURCE_SITE_TITLE:
        if not _contains_keyword(title, keyword):
            return None, None, MATCH_SOURCE_UNKNOWN
        return True, None, source

    if source == MATCH_SOURCE_SITE_BODY:
        t_hit, b_hit = _flags_from_body_search(title, snippet, keyword)
        return t_hit, b_hit, source

    if source == MATCH_SOURCE_SITE_COMBINED:
        t_hit, b_hit = _flags_from_list_text(title, snippet, keyword)
        if not is_keyword_hit_verified(t_hit, b_hit):
            return None, None, MATCH_SOURCE_UNKNOWN
        if t_hit and b_hit:
            return True, True, MATCH_SOURCE_INFERRED
        if t_hit:
            return True, None, MATCH_SOURCE_INFERRED
        return None, True, MATCH_SOURCE_INFERRED

    return None, None, MATCH_SOURCE_UNKNOWN


def merge_hit_flags(
    cur_title: bool | None,
    cur_body: bool | None,
    cur_source: str,
    new_title: bool | None,
    new_body: bool | None,
    new_source: str,
) -> tuple[bool | None, bool | None, str]:
    """合并同一 (news_id, keyword) 的多次爬取结果，顺序无关"""

    def merge_bool(current: bool | None, incoming: bool | None) -> bool | None:
        if current is True or incoming is True:
            return True
        if current is False and incoming is False:
            return False
        if incoming is not None:
            return incoming if current is None else current
        return current

    merged_title = merge_bool(cur_title, new_title)
    merged_body = merge_bool(cur_body, new_body)

    site_sources = {
        MATCH_SOURCE_SITE_TITLE,
        MATCH_SOURCE_SITE_BODY,
    }
    if cur_source in site_sources:
        keep_source = cur_source
    elif new_source in site_sources:
        keep_source = new_source
    elif cur_source and cur_source != MATCH_SOURCE_UNKNOWN:
        keep_source = cur_source
    else:
        keep_source = new_source or MATCH_SOURCE_UNKNOWN

    return merged_title, merged_body, keep_source


def build_hit_scope_sql(scope: str, prefix: str = "h") -> str | None:
    """hit_scope 查询参数 → news_keyword_hits SQL 条件"""
    t, b = f"{prefix}.in_title", f"{prefix}.in_body"
    if scope == MATCH_SCOPE_TITLE:
        return f"({t} IS TRUE AND ({b} IS NOT TRUE))"
    if scope == MATCH_SCOPE_BODY:
        return f"({b} IS TRUE AND ({t} IS NOT TRUE))"
    if scope == MATCH_SCOPE_BOTH:
        return f"({t} IS TRUE AND {b} IS TRUE)"
    if scope == MATCH_SCOPE_UNKNOWN:
        return f"({t} IS NULL AND {b} IS NULL)"
    return None


def resolve_crawl_modes(site: dict) -> list[str]:
    """按站点 search_scope_support 决定 title/body/all 爬取模式"""
    support = (site.get("search_scope_support") or "none").lower()
    if support == "both":
        return [CRAWL_SCOPE_TITLE, CRAWL_SCOPE_BODY]
    return [CRAWL_SCOPE_ALL]


def build_search_url(site: dict, keyword_encoded: str, crawl_scope: str) -> str:
    """按爬取模式选择 search_url / search_url_title / search_url_body 模板"""
    import time

    if crawl_scope == CRAWL_SCOPE_TITLE and site.get("search_url_title"):
        template = site["search_url_title"]
    elif crawl_scope == CRAWL_SCOPE_BODY and site.get("search_url_body"):
        template = site["search_url_body"]
    else:
        template = site.get("search_url") or site.get("search_url_template") or ""
    url = template.replace("{keyword}", keyword_encoded)
    if "{timestamp}" in url:
        url = url.replace("{timestamp}", str(int(time.time() * 1000)))
    return url
