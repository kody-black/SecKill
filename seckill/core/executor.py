"""抢单执行引擎。

三条硬约束：
  1. 指数退避 + 抖动，避免高频刷页触发风控
  2. 有限次数 + 总时限，抢不到就体面退出
  3. 每次尝试前后都检查中止信号，用户点停止能真正停下来
"""
from __future__ import annotations

import random
import threading
import time

from ..core.clock import Clock
from ..core.events import EventBus, EventKind
from ..core.models import OrderResult, SeckillTask, StopReason
from ..utils.logger import get_logger
from ..adapters.base import StepInterrupted

logger = get_logger(__name__)


class SeckillExecutor:
    """负责「开抢之后」的那一小段时间。"""

    BASE_DELAY = 0.12      # 首次重试间隔（秒）
    MAX_DELAY = 1.20       # 退避上限
    BACKOFF_FACTOR = 1.35

    def __init__(
        self,
        adapter,
        session,
        task: SeckillTask,
        bus: EventBus,
        clock: Clock,
        stop_event: threading.Event,
    ) -> None:
        self.adapter = adapter
        self.session = session
        self.task = task
        self.bus = bus
        self.clock = clock
        self.stop_event = stop_event

    def _stopped(self) -> bool:
        return self.stop_event.is_set()

    def _attempt(self, attempt: int) -> OrderResult:
        """单次尝试：接口直调优先，失败退回 DOM 点击。"""
        if self.task.prefer_api and self.adapter.api_cfg.get("enabled"):
            result = self.adapter.fire_api(self.session, self.task)
            if result.ok:
                return result
            self.bus.log(f"第 {attempt} 次接口下单未成功：{result.message}", "debug")
            if "未启用" not in result.message and "Cookie" not in result.message:
                # 接口通了但没抢到，属于正常竞争，继续尝试
                return result

        return self.adapter.fire_dom(self.session, self.task)

    def run(self) -> OrderResult:
        deadline = time.monotonic() + self.task.timeout_seconds
        delay = self.BASE_DELAY
        last = OrderResult(False, "", "未执行任何尝试")

        for attempt in range(1, self.task.max_attempts + 1):
            if self._stopped():
                return OrderResult(False, "", "已中止", {"reason": StopReason.USER})

            if time.monotonic() > deadline:
                self.bus.log(f"超过总时限 {self.task.timeout_seconds}s，停止尝试", "warning")
                return OrderResult(
                    False, "", "超过总时限", {"reason": StopReason.TIMEOUT, "last": last.message}
                )

            try:
                last = self._attempt(attempt)
            except StepInterrupted:
                return OrderResult(False, "", "已中止", {"reason": StopReason.USER})
            except Exception as exc:  # noqa: BLE001 - 记录后继续重试，不因单次异常放弃
                last = OrderResult(False, "", f"{type(exc).__name__}: {exc}")
                logger.warning("第 %d 次尝试异常：%s", attempt, exc)

            if last.ok:
                self.bus.log(f"第 {attempt} 次尝试成功（{last.channel}）：{last.message}", "success")
                return last

            if attempt == 1 or attempt % 10 == 0:
                self.bus.log(f"第 {attempt} 次尝试未成功：{last.message}", "warning")

            # 退避 + 抖动：既能避开风控，也不会所有客户端同频共振
            if self.stop_event.wait(delay + random.uniform(0, delay * 0.3)):
                return OrderResult(False, "", "已中止", {"reason": StopReason.USER})
            delay = min(delay * self.BACKOFF_FACTOR, self.MAX_DELAY)

        return OrderResult(
            False,
            "",
            f"已尝试 {self.task.max_attempts} 次仍未成功",
            {"reason": StopReason.EXHAUSTED, "last": last.message},
        )
