"""
数据库初始化模块
- 启动时自动建表 (仅当表不存在时创建，已有表不会重复初始化)
- 插入默认配置数据 (仅当配置表为空时写入)
- 使用 schema_version 元数据表标记初始化状态
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from loguru import logger

from config import ALL_TABLE_SCHEMAS, SEARCH_KEYWORDS
from db.pool import get_engine

# 默认爬取网站配置
DEFAULT_CRAWL_SITES = []

# 全部新闻网站数据 (用户提供的87个网站)
NEWS_SITES = [
    # ===== 中央级 =====
    {"site_name": "人民日报（人民网）", "site_url": "https://www.people.com.cn", "search_url_template": "https://www.people.com.cn", "search_url": "http://search.people.cn/s?keyword={keyword}&st=0&_={timestamp}", "category": "中央级", "media_type": "报纸/网站", "supervisor": "中共中央", "sort_order": 10},
    {"site_name": "新华社（新华网）", "site_url": "https://www.xinhuanet.com", "search_url_template": "https://so.news.cn", "search_url": "https://so.news.cn/#search/0/{keyword}/1/0", "category": "中央级", "media_type": "通讯社/网站", "supervisor": "国务院", "sort_order": 11},
    {"site_name": "中央广播电视总台（央视网）", "site_url": "https://www.cctv.com", "search_url_template": "https://search.cctv.com", "search_url": "https://search.cctv.com/search.php?qtext={keyword}&page=1&type=web&sort=date&datepid=3&channel=&vtime=-1&is_search=1", "category": "中央级", "media_type": "电视台/网站", "supervisor": "中共中央", "sort_order": 12},
    {"site_name": "求是（求是网）", "site_url": "https://www.qstheory.cn", "search_url_template": "https://search.qstheory.cn/qiushi/", "search_url": "https://search.qstheory.cn/qiushi/?keyword={keyword}&channelid=269025", "category": "中央级", "media_type": "期刊/网站", "supervisor": "中共中央", "sort_order": 13},
    {"site_name": "光明日报（光明网）", "site_url": "https://www.gmw.cn", "search_url_template": "https://zhonghua.gmw.cn/news.htm", "search_url": "https://zhonghua.gmw.cn/news.htm?q={keyword}", "category": "中央级", "media_type": "报纸/网站", "supervisor": "中共中央", "sort_order": 14},
    {"site_name": "经济日报（中国经济网）", "site_url": "http://www.ce.cn", "search_url_template": "http://www.ce.cn", "category": "中央级", "media_type": "报纸/网站", "supervisor": "国务院", "sort_order": 15},
    {"site_name": "中国日报（中国日报网）", "site_url": "https://cn.chinadaily.com.cn", "search_url_template": "https://newssearch.chinadaily.com.cn", "search_url": "https://newssearch.chinadaily.com.cn/cn/search?query={keyword}", "category": "中央级", "media_type": "报纸/网站", "supervisor": "中共中央", "sort_order": 16},
    {"site_name": "科技日报", "site_url": "https://www.stdaily.com", "search_url_template": "https://search.stdaily.com:8888/founder/NewSearchServlet.do", "search_url": "https://search.stdaily.com:8888/founder/NewSearchServlet.do?siteID=1&content={keyword}", "category": "中央级", "media_type": "报纸", "supervisor": "科技部", "sort_order": 17},
    {"site_name": "工人日报（中工网）", "site_url": "http://www.workercn.cn", "search_url_template": "http://www.workercn.cn", "category": "中央级", "media_type": "报纸/网站", "supervisor": "中华全国总工会", "sort_order": 18},
    {"site_name": "中国新闻社（中国新闻网）", "site_url": "https://www.chinanews.com.cn", "search_url_template": "https://www.chinanews.com.cn", "category": "中央级", "media_type": "通讯社/网站", "supervisor": "国务院侨办", "sort_order": 19},
    {"site_name": "法治日报", "site_url": "http://www.legaldaily.com.cn", "search_url_template": "http://www.legaldaily.com.cn", "category": "中央级", "media_type": "报纸", "supervisor": "司法部", "sort_order": 20},
    {"site_name": "人民政协报（人民政协网）", "site_url": "http://www.rmzxb.com.cn", "search_url_template": "http://www.rmzxb.com.cn", "category": "中央级", "media_type": "报纸/网站", "supervisor": "全国政协办公厅", "sort_order": 21},
    {"site_name": "学习时报", "site_url": "http://www.studytimes.cn", "search_url_template": "http://www.studytimes.cn", "category": "中央级", "media_type": "报纸", "supervisor": "中共中央党校", "sort_order": 22},
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
    {"site_name": "荆门新闻网", "site_url": "https://www.jmnews.cn/", "search_url_template": "https://www.jmnews.cn/", "category": "荆门市", "media_type": "报纸/网站", "supervisor": "中共荆门市委", "sort_order": 260},
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


async def init_database(engine: AsyncEngine = None) -> None:
    """
    项目启动时初始化数据库
    - 创建元数据版本表 (schema_version)
    - 如果版本号未标记，依次建表 + 插入默认数据
    - 已初始化则跳过，不会修改已有数据
    - 支持版本迁移，新版本自动执行升级脚本
    """
    if engine is None:
        engine = await get_engine()

    async with engine.begin() as conn:
        # 1. 创建版本标记表
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS schema_version (
                id          SERIAL PRIMARY KEY,
                version     INTEGER NOT NULL,
                description VARCHAR(200),
                applied_at  TIMESTAMPTZ DEFAULT NOW()
            );
        """))

        # 2. 获取当前版本号
        current_version = (await conn.execute(text(
            "SELECT MAX(version) FROM schema_version"
        ))).scalar() or 0

        if current_version == 0:
            # ---- 首次初始化 ----
            logger.info("[DB Init] 开始数据库初始化...")

            for schema_sql in ALL_TABLE_SCHEMAS:
                try:
                    for stmt in _split_sql(schema_sql):
                        if stmt.strip():
                            await conn.execute(text(stmt))
                    logger.info(f"[DB Init] 建表完成: {_extract_table_name(schema_sql)}")
                except Exception as e:
                    logger.error(f"[DB Init] 建表失败: {e}")
                    raise

            # 插入默认爬取网站配置
            await _insert_default_sites(conn, DEFAULT_CRAWL_SITES, skip_empty_check=False)
            # 插入所有新闻网站数据
            await _insert_news_sites(conn, NEWS_SITES)
            # 插入默认关键词
            await _insert_default_keywords(conn)

            await conn.execute(text(
                "INSERT INTO schema_version (version, description) VALUES (1, '初始建表 + 默认数据')"
            ))

            # 顺序执行所有注册迁移
            for ver, desc, migrate_fn, extra_fn in MIGRATIONS:
                await migrate_fn(conn)
                if extra_fn:
                    await extra_fn(conn, NEWS_SITES)
                await conn.execute(text(
                    "INSERT INTO schema_version (version, description) VALUES (:ver, :desc)"
                ), dict(ver=ver, desc=desc))

            logger.info(f"[DB Init] 数据库初始化完成 (version={MIGRATIONS[-1][0]})")

        elif current_version < MIGRATIONS[-1][0]:
            # ---- 增量迁移 ----
            logger.info(f"[DB Init] 检测到 version={current_version}，开始迁移到最新版本...")

            for ver, desc, migrate_fn, extra_fn in MIGRATIONS:
                if ver <= current_version:
                    continue
                await migrate_fn(conn)
                if extra_fn:
                    await extra_fn(conn, NEWS_SITES)
                await conn.execute(text(
                    "INSERT INTO schema_version (version, description) VALUES (:ver, :desc)"
                ), dict(ver=ver, desc=desc))

            logger.info(f"[DB Init] 迁移到 version={MIGRATIONS[-1][0]} 完成")

        else:
            logger.info(f"[DB Init] 数据库已初始化 (version={current_version})，跳过")


async def _migrate_to_v2(conn):
    """迁移到版本 2：添加新字段"""
    try:
        await conn.execute(text(
            "ALTER TABLE crawl_sites ADD COLUMN IF NOT EXISTS category VARCHAR(50)"
        ))
        await conn.execute(text(
            "ALTER TABLE crawl_sites ADD COLUMN IF NOT EXISTS media_type VARCHAR(50)"
        ))
        await conn.execute(text(
            "ALTER TABLE crawl_sites ADD COLUMN IF NOT EXISTS supervisor VARCHAR(500)"
        ))
        # 添加 UNIQUE 约束（如果不存在）
        try:
            await conn.execute(text(
                "ALTER TABLE crawl_sites ADD CONSTRAINT crawl_sites_site_name_key UNIQUE (site_name)"
            ))
        except Exception:
            pass  # 约束已存在
        # 更新注释
        await conn.execute(text(
            "COMMENT ON COLUMN crawl_sites.category IS '媒体类别：中央级/各部委级/省级/经济特区/财经科技/财经报纸/研究院/湖北省级/市级'"
        ))
        await conn.execute(text(
            "COMMENT ON COLUMN crawl_sites.media_type IS '媒体类型：报纸/网站/通讯社/电视台/期刊/智库/新媒体/融媒体平台/研究机构/财经杂志'"
        ))
        await conn.execute(text(
            "COMMENT ON COLUMN crawl_sites.supervisor IS '主管/主办单位'"
        ))
        logger.info("[DB Init] 新字段 category, media_type, supervisor 已添加")
    except Exception as e:
        logger.warning(f"[DB Init] 添加新字段时出现警告（可能已存在）: {e}")


async def _migrate_to_v3(conn):
    """迁移到版本 3：添加 search_url 字段 + 更新荆门新闻网检索URL"""
    try:
        await conn.execute(text(
            "ALTER TABLE crawl_sites ADD COLUMN IF NOT EXISTS search_url VARCHAR(2000)"
        ))
        # 将 search_url_template 改为可空
        try:
            await conn.execute(text(
                "ALTER TABLE crawl_sites ALTER COLUMN search_url_template DROP NOT NULL"
            ))
        except Exception:
            pass  # 已经可空
        # 更新注释
        await conn.execute(text(
            "COMMENT ON COLUMN crawl_sites.search_url_template IS '搜索URL模板（旧字段），{keyword}为占位符'"
        ))
        await conn.execute(text(
            "COMMENT ON COLUMN crawl_sites.search_url IS '新闻检索URL，{keyword}为关键词占位符，用于爬取新闻列表，例：https://apps.jmnews.cn/?app=search&controller=index&action=search&wd={keyword}'"
        ))
        # 更新荆门新闻网的 search_url
        jmnews_search_url = "https://apps.jmnews.cn/?app=search&controller=index&action=search&wd={keyword}&advanced=1&type=article&order="
        await conn.execute(text(
            "UPDATE crawl_sites SET search_url = :url, updated_at = NOW() WHERE site_name = '荆门新闻网'"
        ), dict(url=jmnews_search_url))
        logger.info("[DB Init] 新字段 search_url 已添加，荆门新闻网检索URL已更新")
    except Exception as e:
        logger.warning(f"[DB Init] 添加 search_url 字段时出现警告: {e}")


async def _migrate_to_v4(conn):
    """迁移到版本 4：更新人民网检索URL"""
    try:
        people_search_url = "http://search.people.cn/s?keyword={keyword}&st=0&_={timestamp}"
        await conn.execute(text(
            "UPDATE crawl_sites SET search_url = :url, updated_at = NOW() WHERE site_name = '人民日报（人民网）'"
        ), dict(url=people_search_url))
        logger.info("[DB Init] 人民网检索URL已更新到 v4")
    except Exception as e:
        logger.warning(f"[DB Init] 更新人民网检索URL时出现警告: {e}")


async def _migrate_to_v5(conn):
    """迁移到版本 5：更新新华社搜索URL"""
    try:
        xinhua_search_url = "https://so.news.cn/#search/0/{keyword}/1/0"
        await conn.execute(text(
            "UPDATE crawl_sites SET search_url = :url, search_url_template = :template, updated_at = NOW() WHERE site_name = '新华社（新华网）'"
        ), dict(url=xinhua_search_url, template="https://so.news.cn"))
        logger.info("[DB Init] 新华社搜索URL已更新到 v5")
    except Exception as e:
        logger.warning(f"[DB Init] 更新新华社搜索URL时出现警告: {e}")


async def _migrate_to_v6(conn):
    """迁移到版本 6：更新央视网搜索URL"""
    try:
        cctv_search_url = "https://search.cctv.com/search.php?qtext={keyword}&page=1&type=web&sort=date&datepid=3&channel=&vtime=-1&is_search=1"
        await conn.execute(text(
            "UPDATE crawl_sites SET search_url = :url, search_url_template = :template, updated_at = NOW() WHERE site_name = '中央广播电视总台（央视网）'"
        ), dict(url=cctv_search_url, template="https://search.cctv.com"))
        logger.info("[DB Init] 央视网搜索URL已更新到 v6")
    except Exception as e:
        logger.warning(f"[DB Init] 更新央视网搜索URL时出现警告: {e}")


async def _migrate_to_v7(conn):
    """迁移到版本 7：更新光明日报搜索URL"""
    try:
        gmw_search_url = "https://zhonghua.gmw.cn/news.htm?q={keyword}"
        await conn.execute(text(
            "UPDATE crawl_sites SET search_url = :url, search_url_template = :template, updated_at = NOW() WHERE site_name = '光明日报（光明网）'"
        ), dict(url=gmw_search_url, template="https://zhonghua.gmw.cn/news.htm"))
        logger.info("[DB Init] 光明日报搜索URL已更新到 v7")
    except Exception as e:
        logger.warning(f"[DB Init] 更新光明日报搜索URL时出现警告: {e}")


async def _migrate_to_v8(conn):
    """迁移到版本 8：更新求是网搜索URL"""
    try:
        qiushi_search_url = "https://search.qstheory.cn/qiushi/?keyword={keyword}&channelid=269025"
        await conn.execute(text(
            "UPDATE crawl_sites SET search_url = :url, search_url_template = :template, site_url = 'https://www.qstheory.cn', updated_at = NOW() WHERE site_name = '求是（求是网）'"
        ), dict(url=qiushi_search_url, template="https://search.qstheory.cn/qiushi/"))
        logger.info("[DB Init] 求是网搜索URL已更新到 v8")
    except Exception as e:
        logger.warning(f"[DB Init] 更新求是网搜索URL时出现警告: {e}")


async def _migrate_to_v9(conn):
    """迁移到版本 9：更新科技日报搜索URL"""
    try:
        stdaily_search_url = "https://search.stdaily.com:8888/founder/NewSearchServlet.do?siteID=1&content={keyword}"
        await conn.execute(text(
            "UPDATE crawl_sites SET search_url = :url, search_url_template = :template, site_url = 'https://www.stdaily.com', updated_at = NOW() WHERE site_name = '科技日报'"
        ), dict(url=stdaily_search_url, template="https://search.stdaily.com:8888/founder/NewSearchServlet.do"))
        logger.info("[DB Init] 科技日报搜索URL已更新到 v9")
    except Exception as e:
        logger.warning(f"[DB Init] 更新科技日报搜索URL时出现警告: {e}")


async def _migrate_to_v10(conn):
    """迁移到版本 10：更新中国日报搜索URL"""
    try:
        chinadaily_search_url = "https://newssearch.chinadaily.com.cn/cn/search?query={keyword}"
        await conn.execute(text(
            "UPDATE crawl_sites SET search_url = :url, search_url_template = :template, site_url = 'https://cn.chinadaily.com.cn', updated_at = NOW() WHERE site_name = '中国日报（中国日报网）'"
        ), dict(url=chinadaily_search_url, template="https://newssearch.chinadaily.com.cn"))
        logger.info("[DB Init] 中国日报搜索URL已更新到 v10")
    except Exception as e:
        logger.warning(f"[DB Init] 更新中国日报搜索URL时出现警告: {e}")


async def _insert_default_sites(conn, sites, skip_empty_check: bool = True):
    """插入默认网站配置"""
    if skip_empty_check:
        count = (await conn.execute(text(
            "SELECT COUNT(*) FROM crawl_sites"
        ))).scalar()
        if count > 0:
            logger.info(f"[DB Init] crawl_sites 已有 {count} 条数据，跳过默认插入")
            return

    for site in sites:
        await conn.execute(
            text("""
                INSERT INTO crawl_sites (site_name, site_url, search_url_template, search_url, category, media_type, supervisor, sort_order, description)
                VALUES (:name, :url, :tmpl, :surl, :category, :media_type, :supervisor, :order, :desc)
                ON CONFLICT (site_name) DO NOTHING
            """),
            dict(
                name=site["site_name"],
                url=site["site_url"],
                tmpl=site["search_url_template"],
                surl=site.get("search_url"),
                category=site.get("category"),
                media_type=site.get("media_type"),
                supervisor=site.get("supervisor"),
                order=site.get("sort_order", 0),
                desc=site.get("description"),
            ),
        )
    logger.info(f"[DB Init] 已插入 {len(sites)} 条网站配置")


async def _insert_news_sites(conn, sites):
    """插入新闻网站数据（已存在的跳过）"""
    inserted = 0
    for site in sites:
        try:
            await conn.execute(
                text("""
                    INSERT INTO crawl_sites (site_name, site_url, search_url_template, search_url, category, media_type, supervisor, sort_order, is_active)
                    VALUES (:name, :url, :tmpl, :surl, :category, :media_type, :supervisor, :order, TRUE)
                    ON CONFLICT (site_name) DO NOTHING
                """),
                dict(
                    name=site["site_name"],
                    url=site["site_url"],
                    tmpl=site["search_url_template"],
                    surl=site.get("search_url"),
                    category=site["category"],
                    media_type=site["media_type"],
                    supervisor=site["supervisor"],
                    order=site["sort_order"],
                ),
            )
            inserted += 1
        except Exception as e:
            logger.warning(f"[DB Init] 插入网站 {site['site_name']} 失败: {e}")

    logger.info(f"[DB Init] 已插入 {inserted} 条新闻网站数据")


# 迁移注册表: [(version, description, migrate_fn, extra_fn)]
# extra_fn 为 None 或额外需要执行的函数（如 v2 需同时插入新闻网站）
# 注意：必须放在所有迁移函数定义之后
MIGRATIONS = [
    (2, "新增category/media_type/supervisor字段 + 新闻网站数据", _migrate_to_v2, _insert_news_sites),
    (3, "新增search_url字段 + 荆门新闻网检索URL", _migrate_to_v3, None),
    (4, "人民网检索URL配置", _migrate_to_v4, None),
    (5, "新华社搜索URL配置", _migrate_to_v5, None),
    (6, "央视网搜索URL配置", _migrate_to_v6, None),
    (7, "光明日报搜索URL配置", _migrate_to_v7, None),
    (8, "求是网搜索URL配置", _migrate_to_v8, None),
    (9, "科技日报搜索URL配置", _migrate_to_v9, None),
    (10, "中国日报搜索URL配置", _migrate_to_v10, None),
]


async def _insert_default_keywords(conn):
    """插入默认关键词"""
    kw_count = (await conn.execute(text(
        "SELECT COUNT(*) FROM crawl_keywords"
    ))).scalar()
    if kw_count == 0:
        for kw in DEFAULT_KEYWORDS:
            await conn.execute(
                text("""
                    INSERT INTO crawl_keywords (keyword, keyword_type, priority)
                    VALUES (:kw, '通用', 0)
                    ON CONFLICT (keyword) DO NOTHING
                """),
                dict(kw=kw),
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