"""
新闻数据模型 - 结构化数据，可直接映射到数据库

基于 Pydantic v2，提供数据验证、序列化和数据库映射能力
"""

import hashlib
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class NewsItem(BaseModel):
    """单条新闻的结构化数据"""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="唯一标识")
    title: str = Field(description="新闻标题")
    content: Optional[str] = Field(default=None, description="新闻正文摘要 (200字以内)")
    publish_time: Optional[str] = Field(default=None, description="发布时间 (YYYY-MM-DD HH:mm:ss)")
    source: Optional[str] = Field(default=None, description="新闻来源/媒体名称")
    author: Optional[str] = Field(default=None, description="作者")
    url: str = Field(description="原文链接")
    keywords: list[str] = Field(default_factory=list, description="关键词列表 (3-5个)")
    matched_keyword: str = Field(description="匹配到的搜索关键词")
    category: Optional[str] = Field(default=None, description="新闻分类 (政策/技术/产业/人才/资金/其他)")
    summary: Optional[str] = Field(default=None, description="一句话摘要 (50字以内)")
    related_entities: list[str] = Field(default_factory=list, description="相关机构/企业/人物")
    fetch_time: str = Field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        description="抓取时间",
    )
    hash: str = Field(default="", description="内容去重哈希")

    def model_post_init(self, __context) -> None:
        """自动生成内容去重哈希"""
        if not self.hash:
            raw = f"{self.title}|{self.url}|{self.publish_time or ''}"
            self.hash = hashlib.md5(raw.encode("utf-8")).hexdigest()

    def to_db_dict(self) -> dict:
        """转换为数据库可存储的字典格式"""
        data = self.model_dump()
        # JSON 字段序列化
        data["keywords"] = self.keywords
        data["related_entities"] = self.related_entities
        return data

    def to_json(self, **kwargs) -> str:
        """序列化为 JSON 字符串"""
        return self.model_dump_json(**kwargs)

    @classmethod
    def from_scrape_result(cls, result: dict, matched_keyword: str, url: str) -> "NewsItem":
        """从 ScrapeGraphAI 提取结果构造 NewsItem"""
        return cls(
            title=result.get("title") or "无标题",
            content=result.get("content"),
            publish_time=result.get("publish_time"),
            source=result.get("source"),
            author=result.get("author"),
            url=url,
            keywords=result.get("keywords") or [],
            matched_keyword=matched_keyword,
            category=result.get("category"),
            summary=result.get("summary"),
            related_entities=result.get("related_entities") or [],
        )


class NewsItemList(BaseModel):
    """新闻列表容器"""

    total: int = Field(default=0, description="总条数")
    keyword: str = Field(description="搜索关键词")
    fetch_time: str = Field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        description="抓取时间",
    )
    items: list[NewsItem] = Field(default_factory=list, description="新闻条目列表")

    def to_json(self, **kwargs) -> str:
        return self.model_dump_json(**kwargs)