"""
数据库初始化模块
- 首次启动：自动创建 PostgreSQL 库（若不存在）+ 建表 + 写入默认站点/关键词（schema_version=1）
- 后续变更：在 MIGRATIONS 中追加版本，仅对已有库做增量迁移
"""

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from loguru import logger

from config import ALL_TABLE_SCHEMAS, DBConfig, SEARCH_KEYWORDS, MEETING_ITEMS_SCHEMA, MONITOR_TYPE_NEWS, MeetingConfig
from db.pool import get_engine

# 基线版本（重构后首次初始化记为 v1；后续增量从 v2 起追加）
BASELINE_VERSION = 1

_DB_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,62}$")

# 全部新闻网站数据（87 个站点，中央级站点含最新 search_scope 配置）
NEWS_SITES = [
    # ===== 中央级 =====
    {
        "site_name": "人民日报（人民网）",
        "site_url": "https://www.people.com.cn",
        "search_url_template": "https://www.people.com.cn",
        "search_url": "http://search.people.cn/s?keyword={keyword}&st=0&_={timestamp}",
        "search_scope_support": "all",
        "category": "中央级", "media_type": "报纸/网站", "supervisor": "中共中央", "sort_order": 10,
    },
    {
        "site_name": "新华社（新华网）",
        "site_url": "https://www.xinhuanet.com",
        "search_url_template": "https://so.news.cn",
        "search_url": "https://so.news.cn/#search/0/{keyword}/1/1",
        "search_url_title": "https://so.news.cn/#search/1/{keyword}/1/1",
        "search_url_body": "https://so.news.cn/#search/0/{keyword}/1/1",
        "search_scope_support": "both",
        "category": "中央级", "media_type": "通讯社/网站", "supervisor": "国务院", "sort_order": 11,
    },
    {
        "site_name": "中央广播电视总台（央视网）",
        "site_url": "https://www.cctv.com",
        "search_url_template": "https://search.cctv.com",
        "search_url": "https://search.cctv.com/search.php?qtext={keyword}&page=1&type=web&sort=date&datepid=3&channel=&vtime=-1&is_search=1",
        "search_scope_support": "none",
        "is_active": False,
        "category": "中央级", "media_type": "电视台/网站", "supervisor": "中共中央", "sort_order": 12,
    },
    {
        "site_name": "求是（求是网）",
        "site_url": "https://www.qstheory.cn",
        "search_url_template": "https://search.qstheory.cn/qiushi/",
        "search_url": "https://search.qstheory.cn/qiushi/?keyword={keyword}&channelid=269025",
        "search_scope_support": "none",
        "is_active": False,
        "category": "中央级", "media_type": "期刊/网站", "supervisor": "中共中央", "sort_order": 13,
    },
    {
        "site_name": "光明日报（光明网）",
        "site_url": "https://www.gmw.cn",
        "search_url_template": "https://zhonghua.gmw.cn/search_advanced.htm?source=gmrb",
        "search_url": "https://zhonghua.gmw.cn/gmrb.htm?q={keyword}&c=n&adv=true&cp=1&tt=false&fm=false&siteflag=",
        "search_url_title": "https://zhonghua.gmw.cn/gmrb.htm?q={keyword}&c=n&adv=true&cp=1&tt=true&fm=false&siteflag=",
        "search_url_body": "https://zhonghua.gmw.cn/gmrb.htm?q={keyword}&c=n&adv=true&cp=1&tt=false&fm=false&siteflag=",
        "search_scope_support": "both",
        "category": "中央级", "media_type": "报纸/网站", "supervisor": "中共中央", "sort_order": 14,
    },
    {"site_name": "经济日报（中国经济网）", "site_url": "http://www.ce.cn", "search_url_template": "http://www.ce.cn", "is_active": False, "category": "中央级", "media_type": "报纸/网站", "supervisor": "国务院", "sort_order": 15},
    {
        "site_name": "中国日报（中国日报网）",
        "site_url": "https://cn.chinadaily.com.cn",
        "search_url_template": "https://newssearch.chinadaily.com.cn/cn/search/advanced",
        "search_url": "https://newssearch.chinadaily.com.cn/cn/search/advanced?scope=body",
        "search_url_title": "https://newssearch.chinadaily.com.cn/cn/search/advanced?scope=title",
        "search_url_body": "https://newssearch.chinadaily.com.cn/cn/search/advanced?scope=body",
        "search_scope_support": "both",
        "category": "中央级", "media_type": "报纸/网站", "supervisor": "中共中央", "sort_order": 16,
    },
    {
        "site_name": "科技日报",
        "site_url": "https://www.stdaily.com",
        "search_url_template": "https://search.stdaily.com:8888/founder/NewSearchServlet.do",
        "search_url": "https://search.stdaily.com:8888/founder/NewSearchServlet.do?siteID=1&scope=body",
        "search_url_title": "https://search.stdaily.com:8888/founder/NewSearchServlet.do?siteID=1&scope=title",
        "search_url_body": "https://search.stdaily.com:8888/founder/NewSearchServlet.do?siteID=1&scope=body",
        "search_scope_support": "both",
        "category": "中央级", "media_type": "报纸", "supervisor": "科技部", "sort_order": 17,
    },
    {
        "site_name": "工人日报（中工网）",
        "site_url": "https://www.workercn.cn",
        "search_url_template": "https://www.workercn.cn/search/result.shtml",
        "search_url": "https://www.workercn.cn/search/result.shtml?siteID=122&sort=publishDate&scope=body",
        "search_url_title": "https://www.workercn.cn/search/result.shtml?siteID=122&sort=publishDate&scope=title",
        "search_url_body": "https://www.workercn.cn/search/result.shtml?siteID=122&sort=publishDate&scope=body",
        "search_scope_support": "both",
        "category": "中央级", "media_type": "报纸/网站", "supervisor": "中华全国总工会", "sort_order": 18,
    },
    {
        "site_name": "中国新闻社（中国新闻网）",
        "site_url": "https://www.chinanews.com.cn",
        "search_url_template": "https://sou.chinanews.com.cn/search.do",
        "search_url": "https://sou.chinanews.com.cn/search.do?q={keyword}&searchField=content",
        "search_url_title": "https://sou.chinanews.com.cn/search.do?q={keyword}&searchField=title",
        "search_url_body": "https://sou.chinanews.com.cn/search.do?q={keyword}&searchField=content",
        "search_scope_support": "both",
        "category": "中央级", "media_type": "通讯社/网站", "supervisor": "国务院侨办", "sort_order": 19,
    },
    {
        "site_name": "法治日报",
        "site_url": "http://www.legaldaily.com.cn",
        "search_url_template": "http://www.legaldaily.com.cn/founder/SearchServlet.do",
        "search_url": "http://www.legaldaily.com.cn/founder/SearchServlet.do",
        "search_scope_support": "both",
        "category": "中央级", "media_type": "报纸", "supervisor": "司法部", "sort_order": 20,
    },
    {
        "site_name": "人民政协报（人民政协网）",
        "site_url": "http://www.rmzxb.com.cn",
        "search_url_template": "http://apply.rmzxb.com/unicms/search/result",
        "search_url": "http://apply.rmzxb.com/unicms/search/result?SiteID=14&Sort=PublishDate&Query={keyword}&PageIndex=1&TitleOnly=N&usingSynonym=N",
        "search_url_title": "http://apply.rmzxb.com/unicms/search/result?SiteID=14&Sort=PublishDate&Query={keyword}&PageIndex=1&TitleOnly=Y&usingSynonym=N",
        "search_url_body": "http://apply.rmzxb.com/unicms/search/result?SiteID=14&Sort=PublishDate&Query={keyword}&PageIndex=1&TitleOnly=N&usingSynonym=N",
        "search_scope_support": "both",
        "category": "中央级", "media_type": "报纸/网站", "supervisor": "全国政协办公厅", "sort_order": 21,
    },
    {
        "site_name": "学习时报",
        "site_url": "https://www.studytimes.cn",
        "search_url_template": "https://www.studytimes.cn/was5/web/search",
        "search_url": "https://www.studytimes.cn/was5/web/search?channelid=266446&searchword={keyword}&searchscope=DOCCONTENT",
        "search_url_title": "https://www.studytimes.cn/was5/web/search?channelid=266446&searchword={keyword}&searchscope=doctitle",
        "search_url_body": "https://www.studytimes.cn/was5/web/search?channelid=266446&searchword={keyword}&searchscope=DOCCONTENT",
        "search_scope_support": "both",
        "category": "中央级", "media_type": "报纸", "supervisor": "中共中央党校", "sort_order": 22,
    },
    # ===== 各部委级 =====
    {"site_name": "中国科学报（科学网）", "site_url": "http://news.sciencenet.cn", "search_url_template": "http://news.sciencenet.cn", "category": "各部委级", "media_type": "报纸", "supervisor": "中国科学院/中国工程院/国家自然科学基金委", "sort_order": 30},
    {"site_name": "中国教育报（中国教育新闻网）", "site_url": "http://www.jyb.cn", "search_url_template": "http://www.jyb.cn", "category": "各部委级", "media_type": "报纸", "supervisor": "教育部", "sort_order": 31},
    {"site_name": "中国工业新闻网", "site_url": "http://www.cinn.cn", "search_url_template": "http://www.cinn.cn", "category": "各部委级", "media_type": "报纸", "supervisor": "中国工业经济联合会", "sort_order": 32},
    {"site_name": "中国财经报", "site_url": "http://www.cfen.com.cn", "search_url_template": "http://www.cfen.com.cn", "category": "各部委级", "media_type": "报纸", "supervisor": "财政部", "sort_order": 33},
    {"site_name": "中国证券报", "site_url": "https://www.cs.com.cn", "search_url_template": "https://www.cs.com.cn", "category": "各部委级", "media_type": "报纸", "supervisor": "新华社", "sort_order": 34},
    {"site_name": "中国市场监管报", "site_url": "http://www.cmrnn.com.cn", "search_url_template": "http://www.cmrnn.com.cn", "category": "各部委级", "media_type": "报纸", "supervisor": "国家市场监督管理总局", "sort_order": 35},
    {"site_name": "中国商务新闻网", "site_url": "https://www.comnews.cn", "search_url_template": "https://www.comnews.cn", "category": "各部委级", "media_type": "报纸", "supervisor": "商务部", "sort_order": 36},
    {"site_name": "中国建设新闻网", "site_url": "http://www.chinajsb.cn", "search_url_template": "http://www.chinajsb.cn", "category": "各部委级", "media_type": "报纸", "supervisor": "住房和城乡建设部", "sort_order": 37},
    {"site_name": "中国交通新闻网", "site_url": "http://www.zgjtb.com", "search_url_template": "http://www.zgjtb.com", "category": "各部委级", "media_type": "报纸", "supervisor": "交通运输部", "sort_order": 38},
    # ===== 省级 =====
    {"site_name": "北京日报（京报网）", "site_url": "https://bjrb.bjd.com.cn", "search_url_template": "https://bjrb.bjd.com.cn", "category": "省级", "media_type": "报纸/网站", "supervisor": "中共北京市委", "sort_order": 50},
    {"site_name": "解放日报（上观新闻）", "site_url": "https://www.jfdaily.com", "search_url_template": "https://www.jfdaily.com", "category": "省级", "media_type": "报纸/网站", "supervisor": "中共上海市委", "sort_order": 51},
    {"site_name": "天津日报（津云）", "site_url": "http://www.tjyun.com", "search_url_template": "http://www.tjyun.com", "category": "省级", "media_type": "报纸/网站", "supervisor": "中共天津市委", "sort_order": 52},
    {"site_name": "重庆日报（华龙网）", "site_url": "https://www.cqrb.cn", "search_url_template": "https://www.cqrb.cn", "category": "省级", "media_type": "报纸/网站", "supervisor": "中共重庆市委", "sort_order": 53},
    {"site_name": "南方日报（南方+）", "site_url": "http://www.southcn.com", "search_url_template": "http://www.southcn.com", "category": "省级", "media_type": "报纸/网站", "supervisor": "中共广东省委", "sort_order": 54},
    {"site_name": "浙江日报（浙江新闻）", "site_url": "https://zjol.com.cn", "search_url_template": "https://zjol.com.cn", "category": "省级", "media_type": "报纸/网站", "supervisor": "中共浙江省委", "sort_order": 55},
    {"site_name": "新华日报（交汇点）", "site_url": "http://www.xhby.net", "search_url_template": "http://www.xhby.net", "category": "省级", "media_type": "报纸/网站", "supervisor": "中共江苏省委", "sort_order": 56},
    {"site_name": "大众日报（海报新闻）", "site_url": "https://www.dzwww.com", "search_url_template": "https://www.dzwww.com", "category": "省级", "media_type": "报纸/网站", "supervisor": "中共山东省委", "sort_order": 57},
    {"site_name": "四川日报（川观新闻）", "site_url": "https://www.scdaily.cn", "search_url_template": "https://www.scdaily.cn", "category": "省级", "media_type": "报纸/网站", "supervisor": "中共四川省委", "sort_order": 58},
    {"site_name": "湖南日报（新湖南）", "site_url": "https://www.voc.com.cn", "search_url_template": "https://www.voc.com.cn", "category": "省级", "media_type": "报纸/网站", "supervisor": "中共湖南省委", "sort_order": 59},
    {"site_name": "河南日报（顶端新闻）", "site_url": "https://www.dahe.cn", "search_url_template": "https://www.dahe.cn", "category": "省级", "media_type": "报纸/网站", "supervisor": "中共河南省委", "sort_order": 60},
    {"site_name": "福建日报（东南网）", "site_url": "https://www.fjsen.com", "search_url_template": "https://www.fjsen.com", "category": "省级", "media_type": "报纸/网站", "supervisor": "中共福建省委", "sort_order": 61},
    {"site_name": "安徽日报（中安在线）", "site_url": "http://www.anhuinews.com", "search_url_template": "http://www.anhuinews.com", "category": "省级", "media_type": "报纸/网站", "supervisor": "中共安徽省委", "sort_order": 62},
    {"site_name": "河北日报（河北新闻网）", "site_url": "http://www.hebnews.cn", "search_url_template": "http://www.hebnews.cn", "category": "省级", "media_type": "报纸/网站", "supervisor": "中共河北省委", "sort_order": 63},
    {"site_name": "辽宁日报（北国网）", "site_url": "https://www.lnd.com.cn", "search_url_template": "https://www.lnd.com.cn", "category": "省级", "media_type": "报纸/网站", "supervisor": "中共辽宁省委", "sort_order": 64},
    {"site_name": "陕西日报（群众新闻网）", "site_url": "http://www.sxdaily.com.cn", "search_url_template": "http://www.sxdaily.com.cn", "category": "省级", "media_type": "报纸/网站", "supervisor": "中共陕西省委", "sort_order": 65},
    {"site_name": "江西日报（大江网）", "site_url": "https://www.jxnews.com.cn", "search_url_template": "https://www.jxnews.com.cn", "category": "省级", "media_type": "报纸/网站", "supervisor": "中共江西省委", "sort_order": 66},
    {"site_name": "山西日报（山西新闻网）", "site_url": "http://www.sxrb.com", "search_url_template": "http://www.sxrb.com", "category": "省级", "media_type": "报纸/网站", "supervisor": "中共山西省委", "sort_order": 67},
    {"site_name": "黑龙江日报（黑龙江新闻网）", "site_url": "http://www.hljnews.cn", "search_url_template": "http://www.hljnews.cn", "category": "省级", "media_type": "报纸/网站", "supervisor": "中共黑龙江省委", "sort_order": 68},
    {"site_name": "吉林日报（中国吉林网）", "site_url": "http://www.cnjiwang.com", "search_url_template": "http://www.cnjiwang.com", "category": "省级", "media_type": "报纸/网站", "supervisor": "中共吉林省委", "sort_order": 69},
    {"site_name": "云南日报（云新闻）", "site_url": "https://yndaily.yunnan.cn", "search_url_template": "https://yndaily.yunnan.cn", "category": "省级", "media_type": "报纸/网站", "supervisor": "中共云南省委", "sort_order": 70},
    {"site_name": "广西日报（广西云）", "site_url": "http://www.gxnews.com.cn", "search_url_template": "http://www.gxnews.com.cn", "category": "省级", "media_type": "报纸/网站", "supervisor": "中共广西壮族自治区党委", "sort_order": 71},
    {"site_name": "内蒙古日报（正北方网）", "site_url": "http://www.northnews.cn", "search_url_template": "http://www.northnews.cn", "category": "省级", "media_type": "报纸/网站", "supervisor": "中共内蒙古自治区党委", "sort_order": 72},
    {"site_name": "宁夏日报（宁夏新闻网）", "site_url": "http://www.nxnews.net", "search_url_template": "http://www.nxnews.net", "category": "省级", "media_type": "报纸/网站", "supervisor": "中共宁夏回族自治区党委", "sort_order": 73},
    {"site_name": "海南日报（南海网）", "site_url": "http://www.hinews.cn", "search_url_template": "http://www.hinews.cn", "category": "省级", "media_type": "报纸/网站", "supervisor": "中共海南省委", "sort_order": 74},
    {"site_name": "甘肃日报（每日甘肃网）", "site_url": "http://www.gansudaily.com.cn", "search_url_template": "http://www.gansudaily.com.cn", "category": "省级", "media_type": "报纸/网站", "supervisor": "中共甘肃省委", "sort_order": 75},
    {"site_name": "青海日报（青海新闻网）", "site_url": "http://www.qhnews.com", "search_url_template": "http://www.qhnews.com", "category": "省级", "media_type": "报纸/网站", "supervisor": "中共青海省委", "sort_order": 76},
    {"site_name": "西藏日报（中国西藏新闻网）", "site_url": "http://www.chinatibetnews.com", "search_url_template": "http://www.chinatibetnews.com", "category": "省级", "media_type": "报纸/网站", "supervisor": "中共西藏自治区党委", "sort_order": 77},
    # ===== 经济特区 =====
    {"site_name": "深圳特区报（读特）", "site_url": "http://www.sznews.com", "search_url_template": "http://www.sznews.com", "category": "经济特区", "media_type": "报纸/网站", "supervisor": "中共深圳市委", "sort_order": 90},
    {"site_name": "珠海特区报（观海融媒）", "site_url": "http://www.hizh.cn", "search_url_template": "http://www.hizh.cn", "category": "经济特区", "media_type": "报纸/网站", "supervisor": "中共珠海市委", "sort_order": 91},
    {"site_name": "厦门日报（潮前智媒）", "site_url": "https://www.xmnn.cn", "search_url_template": "https://www.xmnn.cn", "category": "经济特区", "media_type": "报纸/网站", "supervisor": "中共厦门市委", "sort_order": 92},
    # ===== 财经科技 =====
    {"site_name": "第一财经（Yicai）", "site_url": "https://www.yicai.com", "search_url_template": "https://www.yicai.com", "category": "财经科技", "media_type": "网站/电视", "supervisor": "上海广播电视台/上海文化广播影视集团", "sort_order": 110},
    {"site_name": "财新传媒（财新网）", "site_url": "https://www.caixin.com", "search_url_template": "https://www.caixin.com", "category": "财经科技", "media_type": "网站", "supervisor": "财新传媒", "sort_order": 111},
    {"site_name": "21世纪经济报道（21财经）", "site_url": "https://www.21jingji.com", "search_url_template": "https://www.21jingji.com", "category": "财经科技", "media_type": "报纸/网站", "supervisor": "南方财经全媒体集团", "sort_order": 112},
    {"site_name": "经济观察报（经济观察网）", "site_url": "https://www.eeo.com.cn", "search_url_template": "https://www.eeo.com.cn", "category": "财经科技", "media_type": "报纸/网站", "supervisor": "经济观察报社", "sort_order": 113},
    {"site_name": "每日经济新闻（每经网）", "site_url": "https://www.nbd.com.cn", "search_url_template": "https://www.nbd.com.cn", "category": "财经科技", "media_type": "报纸/网站", "supervisor": "成都传媒集团", "sort_order": 114},
    {"site_name": "界面新闻", "site_url": "https://www.jiemian.com", "search_url_template": "https://www.jiemian.com", "category": "财经科技", "media_type": "网站", "supervisor": "上海报业集团", "sort_order": 115},
    {"site_name": "澎湃新闻", "site_url": "https://www.thepaper.cn", "search_url_template": "https://www.thepaper.cn", "category": "财经科技", "media_type": "网站", "supervisor": "上海报业集团", "sort_order": 116},
    {"site_name": "财经网", "site_url": "http://www.caijing.com.cn", "search_url_template": "http://www.caijing.com.cn", "category": "财经科技", "media_type": "网站", "supervisor": "中国证券市场研究设计中心", "sort_order": 117},
    # ===== 财经报纸 =====
    {"site_name": "金融时报", "site_url": "https://www.financialnews.com.cn", "search_url_template": "https://www.financialnews.com.cn", "category": "财经报纸", "media_type": "报纸", "supervisor": "中国人民银行", "sort_order": 130},
    {"site_name": "证券日报", "site_url": "http://www.zqrb.cn", "search_url_template": "http://www.zqrb.cn", "category": "财经报纸", "media_type": "报纸", "supervisor": "经济日报社", "sort_order": 131},
    {"site_name": "中国经营报", "site_url": "http://www.cb.com.cn", "search_url_template": "http://www.cb.com.cn", "category": "财经报纸", "media_type": "报纸", "supervisor": "中国经营报社", "sort_order": 132},
    {"site_name": "中国改革报", "site_url": "http://www.cfgw.net.cn", "search_url_template": "http://www.cfgw.net.cn", "category": "财经报纸", "media_type": "报纸", "supervisor": "国家发展改革委", "sort_order": 133},
    # ===== 研究院 =====
    {"site_name": "中国工程院", "site_url": "https://www.cae.cn", "search_url_template": "https://www.cae.cn", "category": "研究院", "media_type": "研究机构", "supervisor": "国务院", "sort_order": 150},
    {"site_name": "中国社会科学院", "site_url": "http://www.cass.cn", "search_url_template": "http://www.cass.cn", "category": "研究院", "media_type": "研究机构", "supervisor": "国务院", "sort_order": 151},
    {"site_name": "国务院发展研究中心", "site_url": "https://www.drc.gov.cn", "search_url_template": "https://www.drc.gov.cn", "category": "研究院", "media_type": "智库", "supervisor": "国务院", "sort_order": 152},
    {"site_name": "中国科学技术发展战略研究院", "site_url": "http://www.casted.org.cn", "search_url_template": "http://www.casted.org.cn", "category": "研究院", "media_type": "智库", "supervisor": "科技部", "sort_order": 153},
    # ===== 湖北省级 =====
    {"site_name": "极目新闻（楚天都市报）", "site_url": "https://www.ctdsb.net", "search_url_template": "https://www.ctdsb.net", "category": "湖北省级", "media_type": "新媒体/报纸", "supervisor": "湖北日报传媒集团", "sort_order": 170},
    {"site_name": "支点财经", "site_url": "https://ipivot.hubeidaily.net", "search_url_template": "https://ipivot.hubeidaily.net", "category": "湖北省级", "media_type": "财经杂志/网站", "supervisor": "湖北日报传媒集团", "sort_order": 171},
    {"site_name": "荆楚网（湖北日报网）", "site_url": "http://www.cnhubei.com", "search_url_template": "http://www.cnhubei.com", "category": "湖北省级", "media_type": "网站", "supervisor": "湖北日报传媒集团", "sort_order": 172},
    {"site_name": "长江云", "site_url": "http://news.hbtv.com.cn", "search_url_template": "http://news.hbtv.com.cn", "category": "湖北省级", "media_type": "融媒体平台", "supervisor": "湖北广播电视台", "sort_order": 173},
    # ===== 武汉市 =====
    {"site_name": "长江网", "site_url": "https://www.cjn.cn", "search_url_template": "https://www.cjn.cn", "category": "武汉市", "media_type": "报纸/网站", "supervisor": "中共武汉市委", "sort_order": 200},
    # ===== 黄石市 =====
    {"site_name": "东楚新闻", "site_url": "http://www.dongchu.cn", "search_url_template": "http://www.dongchu.cn", "category": "黄石市", "media_type": "报纸/网站", "supervisor": "中共黄石市委", "sort_order": 210},
    # ===== 十堰市 =====
    {"site_name": "秦楚网", "site_url": "http://www.10yan.com", "search_url_template": "http://www.10yan.com", "category": "十堰市", "media_type": "报纸/网站", "supervisor": "中共十堰市委", "sort_order": 220},
    # ===== 宜昌市 =====
    {"site_name": "三峡宜昌网", "site_url": "https://www.cn3x.com.cn/index.html", "search_url_template": "https://www.cn3x.com.cn/index.html", "category": "宜昌市", "media_type": "报纸/网站", "supervisor": "中共宜昌市委", "sort_order": 230},
    # ===== 襄阳市 =====
    {"site_name": "汉江网", "site_url": "http://www.hj.cn", "search_url_template": "http://www.hj.cn", "category": "襄阳市", "media_type": "报纸/网站", "supervisor": "中共襄阳市委", "sort_order": 240},
    # ===== 鄂州市 =====
    {"site_name": "鄂州新闻网", "site_url": "http://www.eznews.cn", "search_url_template": "http://www.eznews.cn", "category": "鄂州市", "media_type": "报纸/网站", "supervisor": "中共鄂州市委", "sort_order": 250},
    # ===== 荆门市 =====
    {
        "site_name": "荆门新闻网",
        "site_url": "https://www.jmnews.cn/",
        "search_url_template": "https://www.jmnews.cn/",
        "search_url": "https://apps.jmnews.cn/?app=search&controller=index&action=search&wd={keyword}&advanced=1&type=article&order=",
        "search_url_title": "https://apps.jmnews.cn/?app=search&controller=index&action=search&wd={keyword}&advanced=1&type=article&order=&field=title",
        "search_url_body": "https://apps.jmnews.cn/?app=search&controller=index&action=search&wd={keyword}&advanced=1&type=article&order=&field=content",
        "search_scope_support": "both",
        "category": "荆门市", "media_type": "报纸/网站", "supervisor": "中共荆门市委", "sort_order": 260,
    },
    # ===== 孝感市 =====
    {"site_name": "孝感新闻网", "site_url": "http://www.xgrb.cn", "search_url_template": "http://www.xgrb.cn", "category": "孝感市", "media_type": "报纸/网站", "supervisor": "中共孝感市委", "sort_order": 270},
    # ===== 荆州市 =====
    {"site_name": "荆州新闻网", "site_url": "http://www.jznews.com.cn/", "search_url_template": "http://www.jznews.com.cn/", "category": "荆州市", "media_type": "报纸/网站", "supervisor": "中共荆州市委", "sort_order": 280},
    # ===== 黄冈市 =====
    {"site_name": "黄冈新闻网", "site_url": "https://www.hgdaily.com.cn/", "search_url_template": "https://www.hgdaily.com.cn/", "category": "黄冈市", "media_type": "报纸/网站", "supervisor": "中共黄冈市委", "sort_order": 290},
    # ===== 咸宁市 =====
    {"site_name": "咸宁新闻网", "site_url": "http://www.xnnews.com.cn", "search_url_template": "http://www.xnnews.com.cn", "category": "咸宁市", "media_type": "报纸/网站", "supervisor": "中共咸宁市委", "sort_order": 300},
    # ===== 随州市 =====
    {"site_name": "随州新闻网", "site_url": "http://www.suiw.cn/", "search_url_template": "http://www.suiw.cn/", "category": "随州市", "media_type": "报纸/网站", "supervisor": "中共随州市委", "sort_order": 310},
    # ===== 恩施州 =====
    {"site_name": "恩施新闻网", "site_url": "http://www.enshi.cn", "search_url_template": "http://www.enshi.cn", "category": "恩施州", "media_type": "报纸/网站", "supervisor": "中共恩施州委", "sort_order": 320},
    # ===== 仙桃市 =====
    {"site_name": "仙桃新闻网", "site_url": "http://www.cnxiantao.com", "search_url_template": "http://www.cnxiantao.com", "category": "仙桃市", "media_type": "报纸/网站", "supervisor": "中共仙桃市委", "sort_order": 330},
]

# 默认关键词列表 (来自 config.py 的 SEARCH_KEYWORDS)
DEFAULT_KEYWORDS = SEARCH_KEYWORDS


_db_ready = False


async def ensure_database_exists(config: DBConfig | None = None) -> None:
    """若 PostgreSQL 实例可连但目标库不存在，则自动 CREATE DATABASE"""
    cfg = config or DBConfig()
    if not _DB_NAME_RE.match(cfg.NAME):
        raise ValueError(f"非法数据库名: {cfg.NAME}")

    import asyncpg

    admin_db = "postgres"
    try:
        conn = await asyncpg.connect(
            host=cfg.HOST,
            port=cfg.PORT,
            user=cfg.USER,
            password=cfg.PASSWORD or None,
            database=admin_db,
        )
    except Exception as e:
        logger.error(
            f"[DB Init] 无法连接 PostgreSQL ({cfg.HOST}:{cfg.PORT}，库={admin_db})，"
            f"请确认服务已启动且 .env 中 DB_HOST/DB_USER/DB_PASSWORD 正确: {e}"
        )
        raise

    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", cfg.NAME,
        )
        if exists:
            return
        await conn.execute(
            f'CREATE DATABASE "{cfg.NAME}" ENCODING \'UTF8\' TEMPLATE template0'
        )
        logger.info(f"[DB Init] 已自动创建数据库 {cfg.NAME}")
    finally:
        await conn.close()


async def init_database(engine: AsyncEngine = None) -> None:
    """
    项目启动时初始化数据库
    - 自动创建 PostgreSQL 库（若不存在）
    - version=0：建表 + 插入默认数据，记为 BASELINE_VERSION
    - version<最新迁移版本：仅执行 MIGRATIONS 中未应用的增量脚本
    - 同一进程内重复调用直接跳过（无日志）
    """
    global _db_ready
    if _db_ready:
        return

    await ensure_database_exists()

    if engine is None:
        engine = await get_engine()

    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS schema_version (
                id          SERIAL PRIMARY KEY,
                version     INTEGER NOT NULL,
                description VARCHAR(200),
                applied_at  TIMESTAMPTZ DEFAULT NOW()
            );
        """))

        current_version = (await conn.execute(text(
            "SELECT MAX(version) FROM schema_version"
        ))).scalar() or 0
        target_version = _target_schema_version()

        initialized_now = False
        if current_version == 0:
            logger.info("[DB Init] 开始首次初始化...")
            await _create_all_tables(conn)
            await _insert_news_sites(conn, NEWS_SITES)
            await _insert_default_keywords(conn)
            await conn.execute(text(
                "INSERT INTO schema_version (version, description) VALUES (:ver, :desc)"
            ), dict(ver=BASELINE_VERSION, desc="重构后初始建表 + 默认数据"))
            current_version = BASELINE_VERSION
            initialized_now = True
            logger.info(f"[DB Init] 首次初始化完成 (version={BASELINE_VERSION})")

        if current_version < target_version:
            logger.info(
                f"[DB Init] 检测到 version={current_version}，迁移到 version={target_version}..."
            )
            for ver, desc, migrate_fn, extra_fn in MIGRATIONS:
                if ver <= current_version:
                    continue
                await migrate_fn(conn)
                if extra_fn:
                    await extra_fn(conn, NEWS_SITES)
                await conn.execute(text(
                    "INSERT INTO schema_version (version, description) VALUES (:ver, :desc)"
                ), dict(ver=ver, desc=desc))
                logger.info(f"[DB Init] 已应用迁移 v{ver}: {desc}")
            logger.info(f"[DB Init] 迁移完成 (version={target_version})")
        elif not initialized_now:
            logger.info(f"[DB Init] 数据库已就绪 (version={current_version})，跳过")

    _db_ready = True


async def _create_all_tables(conn) -> None:
    for schema_sql in ALL_TABLE_SCHEMAS:
        try:
            for stmt in _split_sql(schema_sql):
                if stmt.strip():
                    await conn.execute(text(stmt))
            logger.info(f"[DB Init] 建表完成: {_extract_table_name(schema_sql)}")
        except Exception as e:
            logger.error(f"[DB Init] 建表失败: {e}")
            raise


async def _insert_news_sites(conn, sites):
    """插入新闻网站配置（site_name 冲突则跳过）"""
    inserted = 0
    for site in sites:
        try:
            await conn.execute(
                text("""
                    INSERT INTO crawl_sites (
                        site_name, site_url, search_url_template, search_url,
                        search_url_title, search_url_body, search_scope_support,
                        category, media_type, supervisor, sort_order, is_active,
                        enable_news_crawl, enable_meeting_monitor
                    ) VALUES (
                        :name, :url, :tmpl, :surl,
                        :title_url, :body_url, :scope,
                        :category, :media_type, :supervisor, :order, :active,
                        :enable_news, :enable_meeting
                    )
                    ON CONFLICT (site_name) DO NOTHING
                """),
                dict(
                    name=site["site_name"],
                    url=site["site_url"],
                    tmpl=site["search_url_template"],
                    surl=site.get("search_url"),
                    title_url=site.get("search_url_title"),
                    body_url=site.get("search_url_body"),
                    scope=site.get("search_scope_support", "none"),
                    category=site["category"],
                    media_type=site["media_type"],
                    supervisor=site["supervisor"],
                    order=site["sort_order"],
                    active=site.get("is_active", True),
                    enable_news=site.get("enable_news_crawl", True),
                    enable_meeting=site.get("enable_meeting_monitor", False),
                ),
            )
            inserted += 1
        except Exception as e:
            logger.warning(f"[DB Init] 插入网站 {site['site_name']} 失败: {e}")

    logger.info(f"[DB Init] 已写入 {inserted} 条网站配置")


# 增量迁移注册表: [(version, description, migrate_fn, extra_fn)]
# 重构后基线为 v1；后续 schema/配置变更从 v2 起在此追加

_SITES_DISABLED_BY_DEFAULT = (
    "中央广播电视总台（央视网）",
    "求是（求是网）",
    "经济日报（中国经济网）",
)


async def _migrate_v2_disable_default_sites(conn) -> None:
    for name in _SITES_DISABLED_BY_DEFAULT:
        await conn.execute(
            text("UPDATE crawl_sites SET is_active = FALSE WHERE site_name = :name"),
            {"name": name},
        )


async def _migrate_v3_meeting_monitor_schema(conn) -> None:
    """新增站点/关键词监测类型字段，以及 meeting_items 表"""
    await conn.execute(text("""
        ALTER TABLE crawl_sites
            ADD COLUMN IF NOT EXISTS enable_news_crawl BOOLEAN NOT NULL DEFAULT TRUE,
            ADD COLUMN IF NOT EXISTS enable_meeting_monitor BOOLEAN NOT NULL DEFAULT FALSE
    """))
    await conn.execute(text("""
        UPDATE crawl_sites
        SET enable_news_crawl = TRUE
        WHERE enable_news_crawl IS NULL
    """))

    await conn.execute(text("""
        ALTER TABLE crawl_keywords
            ADD COLUMN IF NOT EXISTS monitor_type VARCHAR(20) NOT NULL DEFAULT 'news'
    """))
    await conn.execute(text("""
        UPDATE crawl_keywords SET monitor_type = 'news' WHERE monitor_type IS NULL
    """))

    await conn.execute(text(
        "ALTER TABLE crawl_keywords DROP CONSTRAINT IF EXISTS crawl_keywords_keyword_key"
    ))
    await conn.execute(text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'crawl_keywords_keyword_monitor_type_key'
            ) THEN
                ALTER TABLE crawl_keywords
                    ADD CONSTRAINT crawl_keywords_keyword_monitor_type_key
                    UNIQUE (keyword, monitor_type);
            END IF;
        END $$
    """))

    for stmt in _split_sql(MEETING_ITEMS_SCHEMA):
        if stmt.strip():
            await conn.execute(text(stmt))


_MEETING_SITE_NAME = "人民日报（人民网）"
_BAIDU_NEWS_SITE_NAME = "百度新闻"
_BAIDU_WEB_SITE_NAME = "百度搜索"
_MEETING_SEED_KEYWORDS = ("AI", "智能制造")


async def _migrate_v4_meeting_peopledaily_seed(conn) -> None:
    """启用人民日报会议监测，写入会议关键词"""
    await conn.execute(
        text("""
            UPDATE crawl_sites
            SET enable_meeting_monitor = TRUE
            WHERE site_name = :name
        """),
        {"name": _MEETING_SITE_NAME},
    )
    for kw in _MEETING_SEED_KEYWORDS:
        await conn.execute(
            text("""
                INSERT INTO crawl_keywords (keyword, keyword_type, monitor_type, priority, is_active)
                VALUES (:kw, '会议监测', 'meeting', 10, TRUE)
                ON CONFLICT (keyword, monitor_type) DO NOTHING
            """),
            {"kw": kw},
        )


async def _migrate_v5_meeting_notify_tables(conn) -> None:
    """历史版本占位（邮件通知已移除，v8 清理表结构）"""
    pass


async def _migrate_v6_baidu_meeting_site(conn) -> None:
    await conn.execute(
        text("""
            INSERT INTO crawl_sites (
                site_name, site_url, search_url_template, search_url,
                search_scope_support, category, media_type, supervisor,
                sort_order, is_active, enable_news_crawl, enable_meeting_monitor,
                description
            ) VALUES (
                :name, :url, :tmpl, :surl,
                'none', '百度', '聚合搜索', '百度',
                5, TRUE, FALSE, TRUE,
                '会议监测：多关键词空格合并一次检索'
            )
            ON CONFLICT (site_name) DO UPDATE SET
                enable_meeting_monitor = TRUE,
                category = EXCLUDED.category,
                search_url = EXCLUDED.search_url,
                description = EXCLUDED.description,
                updated_at = NOW()
        """),
        dict(
            name=_BAIDU_NEWS_SITE_NAME,
            url="https://news.baidu.com",
            tmpl="https://www.baidu.com/s?tn=news",
            surl="https://www.baidu.com/s?ie=utf-8&medium=0&rtt=1&bsst=1&cl=2&tn=news&word={keyword}",
        ),
    )


async def _migrate_v7_web_search_engines(conn) -> None:
    """停用百度新闻 tab，启用百度网页搜索"""
    await conn.execute(
        text("""
            UPDATE crawl_sites
            SET enable_meeting_monitor = FALSE, updated_at = NOW()
            WHERE site_name = :old_name
        """),
        {"old_name": _BAIDU_NEWS_SITE_NAME},
    )

    await conn.execute(
        text("""
            INSERT INTO crawl_sites (
                site_name, site_url, search_url_template, search_url,
                search_scope_support, category, media_type, supervisor,
                sort_order, is_active, enable_news_crawl, enable_meeting_monitor,
                description
            ) VALUES (
                :name, :url, :tmpl, :surl,
                'none', :cat, '搜索引擎', :supervisor,
                :ord, TRUE, FALSE, TRUE,
                '会议监测：多关键词空格合并一次网页检索'
            )
            ON CONFLICT (site_name) DO UPDATE SET
                enable_meeting_monitor = TRUE,
                enable_news_crawl = FALSE,
                category = EXCLUDED.category,
                site_url = EXCLUDED.site_url,
                search_url = EXCLUDED.search_url,
                description = EXCLUDED.description,
                updated_at = NOW()
        """),
        dict(
            name=_BAIDU_WEB_SITE_NAME,
            url="https://www.baidu.com",
            tmpl="https://www.baidu.com/s?wd={keyword}",
            surl="https://www.baidu.com/s?ie=utf-8&wd={keyword}",
            cat="百度搜索",
            supervisor="百度搜索",
            ord=4,
        ),
    )


async def _migrate_v8_drop_meeting_notify(conn) -> None:
    """移除邮件通知相关表与字段"""
    await conn.execute(text("DROP TABLE IF EXISTS meeting_notify_logs"))
    await conn.execute(text("DROP TABLE IF EXISTS meeting_notify_recipients"))
    await conn.execute(text("DROP INDEX IF EXISTS idx_meeting_items_notified"))
    await conn.execute(text("""
        ALTER TABLE meeting_items DROP COLUMN IF EXISTS notified_at
    """))


MIGRATIONS: list = [
    (2, "默认停用央视网、求是网、经济日报爬取", _migrate_v2_disable_default_sites, None),
    (3, "站点/关键词监测类型字段 + meeting_items 表", _migrate_v3_meeting_monitor_schema, None),
    (4, "人民日报会议监测种子：站点开关 + AI/智能制造关键词", _migrate_v4_meeting_peopledaily_seed, None),
    (5, "会议通知收件人表 + 发送记录表", _migrate_v5_meeting_notify_tables, None),
    (6, "百度新闻会议监测站点", _migrate_v6_baidu_meeting_site, None),
    (7, "改为百度网页搜索监测", _migrate_v7_web_search_engines, None),
    (8, "移除会议监测邮件通知表与字段", _migrate_v8_drop_meeting_notify, None),
]


def _target_schema_version() -> int:
    return MIGRATIONS[-1][0] if MIGRATIONS else BASELINE_VERSION


async def _insert_default_keywords(conn):
    """插入默认关键词"""
    kw_count = (await conn.execute(text(
        "SELECT COUNT(*) FROM crawl_keywords"
    ))).scalar()
    if kw_count == 0:
        for kw in DEFAULT_KEYWORDS:
            await conn.execute(
                text("""
                    INSERT INTO crawl_keywords (keyword, keyword_type, monitor_type, priority)
                    VALUES (:kw, '通用', :mt, 0)
                    ON CONFLICT (keyword, monitor_type) DO NOTHING
                """),
                dict(kw=kw, mt=MONITOR_TYPE_NEWS),
            )
        logger.info(f"[DB Init] 已插入 {len(DEFAULT_KEYWORDS)} 条默认关键词")
    else:
        logger.info(f"[DB Init] crawl_keywords 已有 {kw_count} 条数据，跳过默认插入")
def _split_sql(sql: str) -> list[str]:
    """将包含多条语句的 SQL 字符串按分号拆分为独立语句"""
    raw = sql.strip()
    if not raw:
        return []
    # 去掉末尾多余的分号
    while raw.endswith(';'):
        raw = raw[:-1].strip()
    # 按分号拆分，过滤空语句
    return [s.strip() for s in raw.split(';') if s.strip()]


def _extract_table_name(sql: str) -> str:
    """从建表语句中提取表名"""
    import re
    m = re.search(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", sql, re.IGNORECASE)
    return m.group(1) if m else "unknown"