import requests
from bs4 import BeautifulSoup

URL = "https://search.cctv.com/search.php?qtext=%E5%AE%9E%E9%AA%8C%E5%AE%A4&page=1&type=web&sort=date&datepid=1&channel=&vtime=-1&is_search=1"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

resp = requests.get(URL, headers=headers, timeout=30)
resp.encoding = "utf-8"
html = resp.text

with open("_cctv_html.html", "w", encoding="utf-8") as f:
    f.write(html)

soup = BeautifulSoup(html, "html.parser")

# 打印页面标题
print("页面标题:", soup.title.string if soup.title else "无")

# 尝试多种选择器提取新闻
selectors = [
    ".result_list .result_item",
    ".result_item",
    ".result-list .item",
    ".search-result .item",
    "ul.result li",
    ".box .text",
    ".title a",
    "h3 a",
    "h2 a",
    "a[href*='cctv.com']",
]

for sel in selectors:
    els = soup.select(sel)
    if els:
        print(f"\n=== {sel} ({len(els)}个) ===")
        for el in els[:5]:
            text = el.get_text(strip=True)
            href = el.get("href", "")
            print(f"  [{text[:50]}] {href[:100]}")

# 翻页
for sel in [".page-next", ".pagination a", ".page a", "[class*='page'] a"]:
    els = soup.select(sel)
    if els:
        print(f"\n=== 翻页: {sel} ({len(els)}个) ===")
        for el in els[:5]:
            print(f"  {el.get_text(strip=True)} {el.get('href', '')}")

# 日期相关元素
for sel in ["[class*='time']", "[class*='date']", ".pubtime", ".pub-time"]:
    els = soup.select(sel)
    if els:
        print(f"\n=== 日期: {sel} ({len(els)}个) ===")
        for el in els[:5]:
            print(f"  {el.get_text(strip=True)}")
