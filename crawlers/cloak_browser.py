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
import os
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
    async def human_scroll(page, headless: bool = False):
        """
        模拟人类自然滚动行为:
        - 分多次缓慢滚动到底部
        - 中途随机暂停 (模拟阅读)
        - 不再滚回顶部（留在底部确保内容完整加载）
        - 无头模式下简化为单次快速滚动，大幅节省耗时
        """
        if not CrawlerConfig.HUMAN_RANDOM_SCROLL:
            return

        try:
            viewport_height = await page.evaluate("window.innerHeight")
            scroll_height = await page.evaluate("document.body.scrollHeight")

            if scroll_height <= viewport_height * 1.2:
                return  # 页面太短不需要滚动

            if headless:
                # 无头模式：单次快速滚动到底部，无停顿
                await page.evaluate("window.scrollTo({top: document.body.scrollHeight, behavior: 'auto'})")
                await asyncio.sleep(0.3)
                return

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

        # 清除系统代理环境变量，避免 Playwright Chromium 继承全局代理
        # （如 mihomo/clash 等）导致国内网站 ERR_EMPTY_RESPONSE
        _proxy_vars = [
            "http_proxy", "https_proxy", "all_proxy",
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
            "ftp_proxy", "FTP_PROXY", "no_proxy", "NO_PROXY",
        ]
        _cleared = []
        for _var in _proxy_vars:
            val = os.environ.pop(_var, None)
            if val is not None:
                _cleared.append((_var, val))
        if _cleared:
            logger.debug(f"已临时清除 {len(_cleared)} 个代理环境变量")

        self._playwright = await async_playwright().start()

        # 恢复代理环境变量（不影响 Playwright 子进程，因为已经启动）
        for _var, _val in _cleared:
            os.environ[_var] = _val
        if _cleared:
            logger.debug(f"已恢复 {len(_cleared)} 个代理环境变量")

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
            "--disable-background-networking",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
            "--disable-breakpad",
            "--disable-component-update",
            "--disable-ipc-flooding-protection",
            f"--window-size={window_width},{window_height}",
            f"--window-position={window_x},{window_y}",
        ]

        # 无头模式下追加反检测参数
        if self.headless:
            launch_args.extend([
                "--hide-scrollbars",
                "--disable-gpu",
            ])

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
                slow_mo=random.randint(30, 80) if not self.headless else 0,
            )
            logger.info(
                f"CloakBrowser 启动 | headless={self.headless} | "
                f"viewport={self.viewport['width']}x{self.viewport['height']}"
            )
        except Exception as e:
            logger.warning(f"内置 Chromium 启动失败: {e}，尝试本机 Chrome (channel=chrome)")
            try:
                self._browser = await self._playwright.chromium.launch(
                    channel="chrome",
                    headless=self.headless,
                    args=launch_args,
                    slow_mo=random.randint(30, 80) if not self.headless else 0,
                )
                logger.info(
                    f"CloakBrowser 启动(本机 Chrome) | headless={self.headless} | "
                    f"viewport={self.viewport['width']}x{self.viewport['height']}"
                )
            except Exception as e2:
                logger.error(f"浏览器启动失败: {e2}")
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

        # 无头模式下补充 outerWidth/Height 和 screen 模拟
        if self.headless:
            await self._context.add_init_script("""
                const vw = window.innerWidth;
                const vh = window.innerHeight;
                const chromeH = 130;  // 标题栏+地址栏+标签栏估算
                const chromeW = 16;   // 左右边框
                Object.defineProperty(window, 'outerWidth', { get: () => vw + chromeW });
                Object.defineProperty(window, 'outerHeight', { get: () => vh + chromeH });
                Object.defineProperty(window.screen, 'availWidth', { get: () => vw + chromeW });
                Object.defineProperty(window.screen, 'availHeight', { get: () => vh + chromeH + 40 });
                Object.defineProperty(window.screen, 'width', { get: () => vw + chromeW + 20 });
                Object.defineProperty(window.screen, 'height', { get: () => vh + chromeH + 60 });
                Object.defineProperty(window.screen, 'availLeft', { get: () => 0 });
                Object.defineProperty(window.screen, 'availTop', { get: () => 0 });
                Object.defineProperty(window.screen, 'colorDepth', { get: () => 24 });
                Object.defineProperty(window.screen, 'pixelDepth', { get: () => 24 });
            """)

        return self._context

    async def _inject_stealth_scripts(self):
        """内置隐匿脚本 - 覆盖无头浏览器常见检测点"""
        stealth_js = """
        // 1. 删除 webdriver 标志
        delete Object.getPrototypeOf(navigator).webdriver;
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

        // 2. 模拟 chrome 对象
        window.chrome = window.chrome || {};
        window.chrome.runtime = {
            OnInstalledReason: {CHROME_UPDATE: "chrome_update", EXTENSION_UPDATE: "extension_update", INSTALL: "install", SHARED_MODULE_UPDATE: "shared_module_update", UPDATE: "update"},
            OnRestartRequiredReason: {APP_UPDATE: "app_update", OS_UPDATE: "os_update", PERIODIC: "periodic"},
            PlatformArch: {ARM: "arm", ARM64: "arm64", MIPS: "mips", MIPS64: "mips64", MIPS64EL: "mips64el", MIPSEL: "mipsel", X86_32: "x86-32", X86_64: "x86-64"},
            PlatformNaclArch: {ARM: "arm", MIPS: "mips", MIPS64: "mips64", MIPS64EL: "mips64el", MIPSEL: "mipsel", MIPS_EL: "mipsel", X86_32: "x86-32", X86_64: "x86-64"},
            PlatformOs: {ANDROID: "android", CROS: "cros", LINUX: "linux", MAC: "mac", OPENBSD: "openbsd", WIN: "win"},
            RequestUpdateCheckStatus: {NO_UPDATE: "no_update", THROTTLED: "throttled", UPDATE_AVAILABLE: "update_available"},
            connect: function() { return {postMessage: function() {}, disconnect: function() {}, onMessage: {addListener: function() {}, removeListener: function() {}}, onDisconnect: {addListener: function() {}, removeListener: function() {}}} },
            sendMessage: function() {},
            onMessage: {addListener: function() {}, removeListener: function() {}, hasListener: function() { return false; }},
            onConnect: {addListener: function() {}, removeListener: function() {}, hasListener: function() { return false; }},
            loadTimes: function() { return {} },
            csi: function() { return {} },
            app: {isInstalled: false, InstallState: {DISABLED: "disabled", INSTALLED: "installed", NOT_INSTALLED: "not_installed"}, RunningState: {CANNOT_RUN: "cannot_run", READY_TO_RUN: "ready_to_run", RUNNING: "running"}}
        };

        // 3. 模拟 plugins 和 mimeTypes
        const makeFakePlugin = (name, filename, description, version) => ({
            name, filename, description, version,
            length: 1,
            item: function() { return this[0]; },
            namedItem: function() { return this[0]; },
            [0]: {description: description, suffixes: "pdf", type: "application/pdf", enabledPlugin: this}
        });
        const fakePlugins = [
            makeFakePlugin("Chrome PDF Plugin", "internal-pdf-viewer", "Portable Document Format", "undefined"),
            makeFakePlugin("Chrome PDF Viewer", "mhjfbmdgcfjbbpaeojofohoefgiehjai", "Portable Document Format", "undefined"),
            makeFakePlugin("Native Client", "internal-nacl-plugin", "Native Client module"),
        ];
        Object.setPrototypeOf(fakePlugins, PluginArray.prototype);
        fakePlugins.length = fakePlugins.length;
        fakePlugins.item = function(idx) { return this[idx] || null; };
        fakePlugins.namedItem = function(name) { return this.find(p => p.name === name) || null; };
        fakePlugins.refresh = function() {};
        Object.defineProperty(navigator, 'plugins', { get: () => fakePlugins });

        const fakeMimeTypes = [
            {type: "application/pdf", suffixes: "pdf", description: "Portable Document Format", enabledPlugin: fakePlugins[1]},
            {type: "application/x-google-chrome-pdf", suffixes: "pdf", description: "Portable Document Format", enabledPlugin: fakePlugins[1]},
            {type: "application/x-nacl", suffixes: "", description: "Native Client executable", enabledPlugin: fakePlugins[2]},
            {type: "application/x-pnacl", suffixes: "", description: "Portable Native Client executable", enabledPlugin: fakePlugins[2]},
        ];
        Object.setPrototypeOf(fakeMimeTypes, MimeTypeArray.prototype);
        fakeMimeTypes.length = fakeMimeTypes.length;
        fakeMimeTypes.item = function(idx) { return this[idx] || null; };
        fakeMimeTypes.namedItem = function(name) { return this.find(m => m.type === name) || null; };
        fakeMimeTypes.refresh = function() {};
        Object.defineProperty(navigator, 'mimeTypes', { get: () => fakeMimeTypes });

        // 4. 模拟 languages
        Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });

        // 5. 模拟硬件信息
        Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
        Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
        Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 });

        // 6. 覆盖 Permissions.query
        const oq = window.navigator.permissions.query;
        window.navigator.permissions.query = (p) => {
            if (p.name === 'notifications') {
                return Promise.resolve({state: Notification.permission, onchange: null});
            }
            return oq(p);
        };

        // 7. 模拟 Notification.permission
        if (Notification.permission === 'default') {
            Object.defineProperty(Notification, 'permission', { get: () => 'default' });
        }

        // 8. 覆盖 canvas fingerprint (基础)
        const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = function(type) {
            if (type === 'image/png' && this.width > 100 && this.height > 100) {
                // 添加微小噪声
                const ctx = this.getContext('2d');
                if (ctx) {
                    const imageData = ctx.getImageData(0, 0, this.width, this.height);
                    const data = imageData.data;
                    data[0] = data[0] ^ 1;
                    ctx.putImageData(imageData, 0, 0);
                }
            }
            return origToDataURL.apply(this, arguments);
        };

        // 9. 覆盖 WebGL vendor / renderer
        const getParameterProxyHandler = {
            apply: function(target, thisArg, args) {
                const param = args[0];
                if (param === 37445) return 'Intel Inc.';      // UNMASKED_VENDOR_WEBGL
                if (param === 37446) return 'Intel Iris OpenGL Engine'; // UNMASKED_RENDERER_WEBGL
                return target.apply(thisArg, args);
            }
        };
        if (window.WebGLRenderingContext) {
            const origGetParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = new Proxy(origGetParameter, getParameterProxyHandler);
        }
        if (window.WebGL2RenderingContext) {
            const origGetParameter2 = WebGL2RenderingContext.prototype.getParameter;
            WebGL2RenderingContext.prototype.getParameter = new Proxy(origGetParameter2, getParameterProxyHandler);
        }
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

    async def close(self):
        """关闭浏览器"""
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("CloakBrowser 已关闭")