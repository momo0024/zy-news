"""
CloakBrowser - 高隐匿浏览器封装
基于 Playwright + playwright-stealth，模拟真实用户行为

特性:
  - 随机 User-Agent + 浏览器指纹
  - 隐匿模式 (stealth)
  - 人类行为模拟 (随机延迟、鼠标移动、自然滚动)
  - 代理支持
  - 默认非无头模式 (便于观察和调试)
"""

import asyncio
import ctypes
import random
from contextlib import asynccontextmanager
from typing import Optional, AsyncIterator

from loguru import logger

from config import CrawlerConfig, USER_AGENTS, VIEWPORTS


class CloakBrowser:
    """
    高隐匿浏览器 (默认非无头模式)

    使用方式:
        async with CloakBrowser() as browser:
            html = await browser.get_page_content("https://example.com")
    """

    def __init__(
        self,
        headless: Optional[bool] = None,
        stealth_mode: Optional[bool] = None,
        proxy_url: Optional[str] = None,
        viewport: Optional[dict] = None,
        user_agent: Optional[str] = None,
        timeout: int = 30000,
    ):
        self.headless = headless if headless is not None else CrawlerConfig.HEADLESS
        self.stealth_mode = stealth_mode if stealth_mode is not None else CrawlerConfig.STEALTH_MODE
        self.proxy_url = proxy_url or CrawlerConfig.PROXY_URL
        self.timeout = timeout
        self.user_agent = user_agent or self._random_ua()
        self.viewport = viewport or self._random_viewport()

        self._playwright = None
        self._browser = None
        self._context = None

    # ============================================================
    # 人类行为模拟
    # ============================================================

    @staticmethod
    async def human_delay(
        min_seconds: Optional[float] = None,
        max_seconds: Optional[float] = None,
    ):
        """
        模拟人类操作间隔延迟

        Args:
            min_seconds: 最小延迟 (秒), 默认从配置读取
            max_seconds: 最大延迟 (秒), 默认从配置读取
        """
        min_s = min_seconds if min_seconds is not None else CrawlerConfig.HUMAN_DELAY_MIN
        max_s = max_seconds if max_seconds is not None else CrawlerConfig.HUMAN_DELAY_MAX
        delay = random.uniform(min_s, max_s)
        logger.debug(f"模拟等待 {delay:.2f}s...")
        await asyncio.sleep(delay)

    @staticmethod
    async def human_mouse_move(page, steps: int = 3):
        """
        模拟鼠标在页面上随机移动

        Args:
            page: Playwright Page 对象
            steps: 移动步数
        """
        if not CrawlerConfig.HUMAN_MOUSE_MOVE:
            return

        try:
            for _ in range(steps):
                x = random.randint(100, 800)
                y = random.randint(100, 600)
                await page.mouse.move(x, y, steps=random.randint(5, 15))
                await asyncio.sleep(random.uniform(0.1, 0.3))
        except Exception:
            pass  # 非关键，静默忽略

    @staticmethod
    async def human_scroll(page):
        """
        模拟人类自然滚动行为:
        - 分多次缓慢滚动到底部
        - 中途随机暂停 (模拟阅读)
        - 不再滚回顶部（留在底部确保内容完整加载）
        """
        if not CrawlerConfig.HUMAN_RANDOM_SCROLL:
            return

        try:
            viewport_height = await page.evaluate("window.innerHeight")
            scroll_height = await page.evaluate("document.body.scrollHeight")

            if scroll_height <= viewport_height * 1.2:
                return  # 页面太短不需要滚动

            # 分 3-5 段滚动到底部
            segments = random.randint(3, 5)
            for i in range(segments):
                target = int(scroll_height * (i + 1) / segments)
                target += random.randint(-100, 100)
                target = max(0, min(target, scroll_height))

                await page.evaluate(f"window.scrollTo({{top: {target}, behavior: 'smooth'}})")
                await asyncio.sleep(random.uniform(0.5, 1.5))

                # 50% 概率在中间暂停 (模拟阅读)
                if random.random() < 0.5 and i < segments - 1:
                    await asyncio.sleep(random.uniform(1.0, 3.0))

            # 确保最终停在底部
            await page.evaluate("window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'})")
            await asyncio.sleep(0.5)

        except Exception:
            pass

    # ============================================================
    # 浏览器核心
    # ============================================================

    @staticmethod
    def _random_ua() -> str:
        return random.choice(USER_AGENTS)

    @staticmethod
    def _random_viewport() -> dict:
        vp = random.choice(VIEWPORTS).copy()
        vp["width"] += random.randint(-50, 50)
        vp["height"] += random.randint(-30, 30)
        return vp

    async def _init_playwright(self):
        """初始化 Playwright"""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.error("playwright 未安装，请执行: pip install playwright && playwright install chromium")
            raise

        self._playwright = await async_playwright().start()

        # 浏览器边框+工具栏的估算开销（Windows Chrome）
        # 左右边框约 16px，标题栏+地址栏+标签栏约 130px
        chrome_border_w = 16
        chrome_border_h = 130
        window_width = self.viewport["width"] + chrome_border_w
        window_height = self.viewport["height"] + chrome_border_h

        # 窗口在屏幕居中
        try:
            screen_w = ctypes.windll.user32.GetSystemMetrics(0)
            screen_h = ctypes.windll.user32.GetSystemMetrics(1)
            window_x = max(0, (screen_w - window_width) // 2)
            window_y = max(0, (screen_h - window_height) // 2)
        except Exception:
            window_x = 100
            window_y = 100

        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-infobars",
            "--disable-setuid-sandbox",
            "--disable-accelerated-2d-canvas",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-webrtc",
            "--disable-sync",
            "--disable-default-apps",
            "--disable-translate",
            "--mute-audio",
            "--no-first-run",
            "--no-default-browser-check",
            f"--window-size={window_width},{window_height}",
            f"--window-position={window_x},{window_y}",
        ]

        # 非无头模式下去掉一些可能影响体验的参数
        if not self.headless:
            launch_args = [a for a in launch_args if a not in (
                "--disable-gpu", "--hide-scrollbars"
            )]

        if self.proxy_url:
            launch_args.append(f"--proxy-server={self.proxy_url}")

        try:
            self._browser = await self._playwright.chromium.launch(
                headless=self.headless,
                args=launch_args,
                slow_mo=random.randint(30, 80) if not self.headless else 0,  # 操作间微量延迟
            )
            logger.info(
                f"CloakBrowser 启动 | headless={self.headless} | "
                f"viewport={self.viewport['width']}x{self.viewport['height']}"
            )
        except Exception as e:
            logger.error(f"浏览器启动失败: {e}")
            raise

    async def _create_context(self):
        """创建隐匿浏览器上下文"""
        context_options = {
            "user_agent": self.user_agent,
            "viewport": self.viewport,
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
            "geolocation": {"longitude": 114.3055, "latitude": 30.5928},
            "permissions": [],
            "extra_http_headers": {
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "DNT": "1",
                "Upgrade-Insecure-Requests": "1",
            },
        }

        if self.proxy_url:
            context_options["proxy"] = {"server": self.proxy_url}

        self._context = await self._browser.new_context(**context_options)

        if self.stealth_mode:
            try:
                from playwright_stealth import Stealth
                await Stealth().apply_stealth_async(self._context)
            except ImportError:
                logger.warning("playwright-stealth 未安装，将使用内置隐匿")
                await self._inject_stealth_scripts()

        return self._context

    async def _inject_stealth_scripts(self):
        """内置隐匿脚本"""
        stealth_js = """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
        window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
        const oq = window.navigator.permissions.query;
        window.navigator.permissions.query = (p) => (
            p.name === 'notifications' ? Promise.resolve({state: Notification.permission}) : oq(p)
        );
        """
        self._browser.on("page", lambda page: page.add_init_script(stealth_js))

    @asynccontextmanager
    async def session(self) -> AsyncIterator:
        """创建浏览器会话"""
        if not self._browser:
            await self._init_playwright()
        if not self._context:
            await self._create_context()

        page = await self._context.new_page()

        if self.stealth_mode:
            try:
                from playwright_stealth import Stealth
                # context 已在 _create_context 中应用 stealth
                # 如果页面是后来创建的，检查是否已应用，未应用则补充
                if not getattr(page, '_stealth_applied', False):
                    await Stealth().apply_stealth_async(page)
            except ImportError:
                pass

        try:
            yield page
        finally:
            await page.close()

    # ============================================================
    # 页面获取 (带人类行为)
    # ============================================================

    async def get_page_content(
        self,
        url: str,
        wait_until: str = "domcontentloaded",
        wait_selector: Optional[str] = None,
        simulate_human: bool = True,
    ) -> str:
        """
        获取页面 HTML 内容 (带人类行为模拟)

        Args:
            url: 目标 URL
            wait_until: 等待策略
            wait_selector: 等待特定选择器
            simulate_human: 是否模拟人类行为

        Returns:
            页面 HTML 内容
        """
        async with self.session() as page:
            logger.debug(f"导航到: {url[:100]}")

            try:
                # 导航 (模拟从地址栏输入)
                await page.goto(url, wait_until=wait_until, timeout=self.timeout)

                # 等待特定元素
                if wait_selector:
                    await page.wait_for_selector(wait_selector, timeout=self.timeout)

                if simulate_human:
                    # 模拟人类行为: 随机延迟 + 鼠标移动 + 自然滚动
                    await self.human_delay(0.5, 1.5)
                    await self.human_mouse_move(page)
                    await self.human_scroll(page)

                html = await page.content()
                logger.debug(f"获取内容完成 | 长度: {len(html)} 字符")
                return html

            except Exception as e:
                logger.error(f"页面加载失败: {url[:100]} | 错误: {e}")
                raise

    # ============================================================
    # 搜索新闻列表
    # ============================================================

    async def get_news_list_from_search(
        self,
        keyword: str,
        max_pages: int = 2,
    ) -> list[dict]:
        """
        从百度新闻搜索获取新闻列表 (带人类行为模拟)

        Args:
            keyword: 搜索关键词
            max_pages: 最大页数

        Returns:
            [{"title": "...", "url": "...", "source": "...", "time": "..."}, ...]
        """
        news_items = []
        base_url = f"https://www.baidu.com/s?tn=news&word={keyword}"

        async with self.session() as page:
            for page_num in range(max_pages):
                url = base_url if page_num == 0 else f"{base_url}&pn={page_num * 10}"

                try:
                    logger.info(f"百度新闻搜索 | 关键词: {keyword} | 第{page_num + 1}页")
                    await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)

                    # 模拟人类: 浏览搜索结果前先停顿
                    await self.human_delay(1.0, 2.0)
                    await self.human_mouse_move(page)

                    # 等待新闻列表加载
                    await page.wait_for_selector(".result-op, .news-list-item, h3 a", timeout=10000)

                    # 模拟人类滚动浏览搜索结果
                    await self.human_scroll(page)

                    # 提取新闻条目
                    items = await page.evaluate("""
                        () => {
                            const results = [];
                            const containers = document.querySelectorAll('.result-op, .result');
                            containers.forEach(c => {
                                const titleEl = c.querySelector('h3 a');
                                const sourceEl = c.querySelector('.c-author, .c-color-gray2');
                                const timeEl = c.querySelector('.c-color-gray2');
                                const abstractEl = c.querySelector('.c-abstract, .c-summary');

                                if (titleEl && titleEl.href) {
                                    results.push({
                                        title: titleEl.innerText.trim(),
                                        url: titleEl.href,
                                        source: sourceEl ? sourceEl.innerText.trim() : '',
                                        time: timeEl ? timeEl.innerText.trim() : '',
                                        abstract: abstractEl ? abstractEl.innerText.trim() : '',
                                    });
                                }
                            });
                            return results;
                        }
                    """)

                    if items:
                        news_items.extend(items)
                        logger.info(f"找到 {len(items)} 条新闻")
                    else:
                        logger.warning("未找到新闻条目，可能需要调整选择器")
                        break

                    # 翻页间隔 (模拟人类看完一页再翻)
                    if page_num < max_pages - 1:
                        await self.human_delay(2.0, 4.0)

                except Exception as e:
                    logger.error(f"搜索页面加载失败: {e}")
                    break

            # 去重 (按 URL)
            seen_urls = set()
            unique_items = []
            for item in news_items:
                if item["url"] not in seen_urls:
                    seen_urls.add(item["url"])
                    unique_items.append(item)

            logger.info(f"搜索完成 | 关键词: {keyword} | 去重后: {len(unique_items)} 条")
            return unique_items

    async def close(self):
        """关闭浏览器"""
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("CloakBrowser 已关闭")