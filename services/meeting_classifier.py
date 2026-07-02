"""
会议/论坛/研讨会 判定（规则为主，后续可接 LLM）
"""

from __future__ import annotations

import re

# 活动类型词
_EVENT_PATTERN = re.compile(
    r"(?:"
    r"学术会议|主题论坛|高峰论坛|产业大会|产业论坛|"
    r"研讨会|座谈会|圆桌会|圆桌论坛|"
    r"博览会|峰会|年会|"
    r"论坛|会议"
    r")",
    re.IGNORECASE,
)

# 政策解读类，非活动通知
_EXCLUDE_PATTERN = re.compile(
    r"(?:"
    r"学习.*(?:精神|思想)|贯彻.*(?:精神|部署)|落实.*(?:部署|要求)|"
    r"(?:中央|全国).*工作会(?!议)|"
    r"解读|评论|述评|时评"
    r")",
    re.IGNORECASE,
)


def extract_event_type(title: str, abstract: str = "") -> str:
    """从标题/摘要提取活动类型词，供 event_type 字段使用"""
    title = (title or "").strip()
    abstract = (abstract or "").strip()
    combined = f"{title} {abstract}".strip()
    if not combined or _EXCLUDE_PATTERN.search(combined):
        return ""
    m = _EVENT_PATTERN.search(combined)
    return m.group(0) if m else ""


def classify_meeting_item(title: str, abstract: str = "") -> tuple[bool, str, float]:
    """
    判定是否为会议/论坛/研讨会相关报道。

    Returns:
        (is_event, event_type, confidence)
    """
    title = (title or "").strip()
    abstract = (abstract or "").strip()
    combined = f"{title} {abstract}".strip()
    if not combined:
        return False, "", 0.0

    if _EXCLUDE_PATTERN.search(combined):
        return False, "", 0.0

    event_type = extract_event_type(title, abstract)
    if not event_type:
        return False, "", 0.0

    # 标题命中置信度更高
    confidence = 0.9 if _EVENT_PATTERN.search(title) else 0.75
    return True, event_type, confidence
