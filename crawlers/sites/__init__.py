"""
网站爬虫分类路由
按数据库 category 字段映射到对应分类脚本
"""

from crawlers.sites import (
    central,
    chinadaily,
    chinanews,
    ministry,
    province,
    special_zone,
    finance_tech,
    finance_paper,
    research,
    hubei,
    stdaily,
)

CATEGORY_HANDLERS = {
    "中央级": central,
    "中国日报": chinadaily,
    "中国新闻社": chinanews,
    "各部委级": ministry,
    "省级": province,
    "经济特区": special_zone,
    "财经科技": finance_tech,
    "科技日报": stdaily,
    "财经报纸": finance_paper,
    "研究院": research,
    "湖北省级": hubei,
    "武汉市": hubei,
    "黄石市": hubei,
    "十堰市": hubei,
    "宜昌市": hubei,
    "襄阳市": hubei,
    "鄂州市": hubei,
    "荆门市": hubei,
}


__all__ = ["get_search_handler", "CATEGORY_HANDLERS"]


def get_search_handler(category: str):
    """根据分类名称获取对应的爬虫模块"""
    handler = CATEGORY_HANDLERS.get(category)
    if not handler:
        # 未知分类默认使用通用省级逻辑
        return province
    return handler
