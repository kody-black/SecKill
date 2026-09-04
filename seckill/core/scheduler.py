"""任务调度器。

阶段顺序：登录 → 选规格 → 等待开抢时刻 → 开抢。
登录与准备都在等待之前完成，到点后直接进入抢购。
"""
from __future__ import annotations

import threading
import time

from ..adapters.base import BaseAdapter, StepInterrupted
from ..drivers.browser import BrowserSession
from ..utils.logger import get_logger
from .clock import Clock
from .events import EventBus, EventKind
from .executor import SeckillExecutor
from .models import SeckillTask, TaskStatus
from .session import SessionManager

logger = get_logger(__name__)

#: 提前多少秒进入「临战」状态（此时开始高频倒计时）
FINAL_STRETCH = 5.0


class SeckillScheduler(threading.Thread):
    """一个任务一个线程。``stop()`` 之后各阶段都会尽快退出。"""

    def __init__(
        self,
        task: SeckillTask,
        adapter: BaseAdapter,
        session: SessionManager,
        bus: EventBus,
        clock: Clock,
    ) -> None:
        super().__init__(daemon=True, name=f"seckill-{task.platform}")
        self.task = task
        self.adapter = adapter
        self.session_mgr = session
        self.bus = bus
        self.clock = clock
        self.stop_event = threading.Event()
        self._browser: BrowserSession | None = None

    # ------------------------------------------------------------------ 控制

    def stop(self) -> None:
        if not self.stop_event.is_set():
            self.bus.log("收到停止指令，正在收尾…", "warning")
        self.stop_event.set()

    def _stopped(self) -> bool:
        return self.stop_event.is_set()

    def _phase(self, status: TaskStatus, message: str) -> None:
        self.bus.emit(EventKind.PHASE, message, "info", status=status.value)

    # ------------------------------------------------------------------ 主流程

    def run(self) -> None:
        try:
            self._run()
        except StepInterrupted:
            self._phase(TaskStatus.CANCELLED, "已中止")
            self.bus.emit(EventKind.CANCELLED, "抢单已中止", "warning")
        except Exception as exc:  # noqa: BLE001 - 兜底，保证浏览器一定被关掉
            logger.exception("抢单线程异常")
            self._phase(TaskStatus.FAILED, "异常终止")
            self.bus.emit(EventKind.ERROR, f"{type(exc).__name__}: {exc}", "error")
        finally:
            if self._browser:
                self._browser.close()
                self._browser = None

    def _run(self) -> None:
        self.adapter.bind_stop(self._stopped)

        # ---------- 1. 准备：启动浏览器 + 登录 + 打开商品页 ----------
        self._phase(TaskStatus.PREPARING, "正在启动浏览器")
        browser = BrowserSession(
            profile_dir=self.session_mgr.dir,
            headless=self.task.headless,
        )
        self._browser = browser
        browser.start()

        self._ensure_login(browser)

        self.bus.log("打开商品页并选择规格…")
        prep = self.adapter.prepare(browser, self.task)

        blocked = self.adapter.check_blocked(browser.page)
        if blocked:
            raise RuntimeError(
                f"页面被平台风控拦截（命中特征：{blocked}）。"
                "建议先点「重新扫码登录」建立登录态再试，或过一会儿再抢。"
            )

        if not prep.ok:
            # 准备阶段失败通常只是规格已选好或页面结构变化，不致命
            self.bus.log(f"准备阶段未完全成功：{prep.message}（继续尝试）", "warning")

        # ---------- 2. 等待：到点前保持就绪 ----------
        if self.task.target_time:
            self._phase(TaskStatus.WAITING, "已就绪，等待开抢")
            if not self._wait_until(self.task.target_time):
                raise StepInterrupted("用户中止")
        if self._stopped():
            raise StepInterrupted("用户中止")

        # ---------- 3. 开抢 ----------
        self._phase(TaskStatus.FIRING, "开始抢购")
        self.bus.emit(EventKind.STATUS, "抢购中…", "warning")
        executor = SeckillExecutor(
            adapter=self.adapter,
            session=browser,
            task=self.task,
            bus=self.bus,
            clock=self.clock,
            stop_event=self.stop_event,
        )
        result = executor.run()

        if result.ok:
            browser.screenshot("success")
            self._phase(TaskStatus.SUCCEEDED, "抢购成功")
            self.bus.emit(EventKind.SUCCESS, "抢购成功，请尽快完成付款！", "success")
        elif result.detail.get("reason") == "user":
            self._phase(TaskStatus.CANCELLED, "已中止")
            self.bus.emit(EventKind.CANCELLED, "抢单已中止", "warning")
        else:
            browser.screenshot("failure")
            self._phase(TaskStatus.FAILED, "抢购失败")
            self.bus.emit(EventKind.FAILURE, result.message or "抢购失败", "error")

    # ------------------------------------------------------------------ 细节

    def _ensure_login(self, browser: BrowserSession) -> None:
        """复用登录态；失效时才引导扫码。"""
        home = self.adapter.meta.get("home")
        page = browser.page
        if home:
            try:
                page.goto(home, wait_until="domcontentloaded", timeout=30_000)
            except Exception as exc:  # noqa: BLE001
                self.bus.log(f"打开首页失败：{exc}", "warning")

        if self.adapter.is_logged_in(page):
            self.bus.emit(EventKind.SESSION, "已复用已有登录态", "success")
            self.bus.log("登录态有效，跳过扫码")
            return

        self.bus.log("登录态失效或不存在，需要扫码登录", "warning")
        if not self.adapter.login(browser):
            raise RuntimeError("登录失败，无法继续抢单")
        self.session_mgr.mark_login("扫码登录")
        self.bus.emit(EventKind.SESSION, "登录成功", "success")

    def _wait_until(self, target) -> bool:
        """等到目标时刻。返回 False 表示被中止。"""
        last_emit = 0.0
        while not self._stopped():
            remain = self.clock.seconds_until(target)
            if remain <= 0:
                self.bus.emit(EventKind.COUNTDOWN, "", "info", seconds=0.0)
                return True

            now = time.monotonic()
            if now - last_emit >= 0.05:
                self.bus.emit(EventKind.COUNTDOWN, "", "info", seconds=remain)
                last_emit = now

            # 越接近开抢，轮询越密；但用 Event.wait 保证随时可被中止
            if remain > FINAL_STRETCH:
                self.stop_event.wait(0.5)
            elif remain > 1.0:
                self.stop_event.wait(0.05)
            else:
                self.stop_event.wait(0.005)
        return False
