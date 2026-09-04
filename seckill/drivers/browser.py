"""浏览器驱动层。

用 Playwright 的持久化上下文（真实 Chrome profile）替代原来的
``webdriver.Chrome()`` + 手工 chromedriver.exe：
  * 不再需要手动下载与 Chrome 版本严格对应的驱动
  * 登录态天然留存，扫一次码能用很久
  * 隐去 webdriver 特征，降低被识别的概率
"""
from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from ..utils.logger import get_logger

logger = get_logger(__name__)

_STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
window.chrome = window.chrome || { runtime: {} };
"""

_DEFAULT_ARGS = (
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-first-run",
    "--no-default-browser-check",
)


class BrowserSession:
    """在**创建它的那个线程**里使用。Playwright 的同步 API 不是线程安全的。"""

    def __init__(
        self,
        profile_dir: Path,
        headless: bool = False,
        channel: str | None = "chrome",
        args: tuple[str, ...] = _DEFAULT_ARGS,
        locale: str = "zh-CN",
        timezone_id: str = "Asia/Shanghai",
        viewport: dict[str, int] | None = None,
    ) -> None:
        self.profile_dir = Path(profile_dir)
        self.headless = headless
        self.channel = channel
        self.args = list(args)
        self.locale = locale
        self.timezone_id = timezone_id
        self.viewport = viewport or {"width": 1280, "height": 900}

        self._pw: Any = None
        self._context: Any = None
        self.page: Any = None

    def start(self) -> Any:
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        kwargs: dict[str, Any] = dict(
            user_data_dir=str(self.profile_dir),
            headless=self.headless,
            args=self.args,
            locale=self.locale,
            timezone_id=self.timezone_id,
            viewport=self.viewport,
            ignore_https_errors=True,
        )
        if self.channel:
            kwargs["channel"] = self.channel

        try:
            self._context = self._pw.chromium.launch_persistent_context(**kwargs)
            used = self.channel or "bundled chromium"
        except Exception as exc:  # noqa: BLE001 - 系统没装 Chrome 时退回自带内核
            if not self.channel:
                raise
            logger.warning("用系统 Chrome 启动失败（%s），改用自带 Chromium", exc)
            kwargs.pop("channel", None)
            self._context = self._pw.chromium.launch_persistent_context(**kwargs)
            used = "bundled chromium"

        self._context.add_init_script(_STEALTH_SCRIPT)
        self.page = self._context.pages[0] if self._context.pages else self._context.new_page()
        logger.info("浏览器已启动（%s），profile=%s", used, self.profile_dir)
        return self.page

    @property
    def context(self) -> Any:
        return self._context

    @property
    def current_url(self) -> str:
        return self.page.url if self.page else ""

    def cookies(self) -> list[dict[str, Any]]:
        if not self._context:
            return []
        return self._context.cookies()

    def screenshot(self, name: str) -> Path | None:
        """截图存到数据目录，便于事后排查「到底卡在哪一步」。"""
        if not self.page:
            return None
        from ..utils.paths import data_dir

        shots = data_dir() / "screenshots"
        shots.mkdir(parents=True, exist_ok=True)
        path = shots / f"{name}.png"
        with contextlib.suppress(Exception):
            self.page.screenshot(path=str(path))
        return path

    def close(self) -> None:
        for closer in (
            lambda: self._context.close() if self._context else None,
            lambda: self._pw.stop() if self._pw else None,
        ):
            with contextlib.suppress(Exception):
                closer()
        self._context = None
        self._pw = None
        self.page = None
        logger.info("浏览器已关闭")

    def __enter__(self) -> "BrowserSession":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
