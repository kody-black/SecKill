"""核心逻辑自测：不依赖浏览器，秒级跑完。

重点验证这些行为约束：
  * 中止信号能真正停下循环
  * 重试退避生效且次数有上限
  * 平台配置可按 YAML 加载、链接能自动识别
"""
from __future__ import annotations

import threading
import unittest.mock
import unittest
from datetime import datetime, timedelta, timezone

from seckill.adapters.base import BaseAdapter, StepInterrupted
from seckill.core.clock import Clock
from seckill.core.events import EventBus, EventKind
from seckill.core.executor import SeckillExecutor
from seckill.core.models import SeckillTask


class FakeAdapter(BaseAdapter):
    """永远失败的适配器，用来观察重试与中止行为。"""

    def __init__(self, bus: EventBus) -> None:
        super().__init__(
            {"platform": {"id": "fake", "name": "测试", "match": ["fake.com"]}}, bus
        )
        self.calls = 0

    def fire_dom(self, session, task):
        self.calls += 1
        return seckill_ok(False)


def seckill_ok(ok: bool):
    from seckill.core.models import OrderResult

    return OrderResult(ok, "dom", "模拟结果")


class ExecutorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.clock = Clock()
        self.adapter = FakeAdapter(self.bus)

    def _executor(self, stop_event: threading.Event, **kw) -> SeckillExecutor:
        task = SeckillTask(platform="fake", url="https://fake.com/1", **kw)
        task.prefer_api = False
        return SeckillExecutor(self.adapter, None, task, self.bus, self.clock, stop_event)

    def test_retries_are_bounded(self) -> None:
        ex = self._executor(threading.Event(), max_attempts=5, timeout_seconds=60)
        result = ex.run()
        self.assertFalse(result.ok)
        self.assertEqual(self.adapter.calls, 5, "重试次数应当有硬上限")
        self.assertEqual(result.detail.get("reason"), "exhausted")

    def test_stop_actually_stops(self) -> None:
        """中止信号必须能停下循环。"""
        stop = threading.Event()
        stop.set()  # 一开始就要求停止
        ex = self._executor(stop, max_attempts=999, timeout_seconds=60)
        result = ex.run()
        self.assertEqual(self.adapter.calls, 0, "已请求停止就不该再发起尝试")
        self.assertEqual(result.detail.get("reason"), "user")

    def test_stop_during_backoff(self) -> None:
        stop = threading.Event()

        def arm_later() -> None:
            stop.set()

        threading.Timer(0.05, arm_later).start()
        ex = self._executor(stop, max_attempts=999, timeout_seconds=60)
        result = ex.run()
        self.assertLess(self.adapter.calls, 999, "应能在退避等待中被打断")
        self.assertEqual(result.detail.get("reason"), "user")


class AdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()

    def test_steps_check_stop_signal(self) -> None:
        adapter = FakeAdapter(self.bus)
        adapter.bind_stop(lambda: True)
        with self.assertRaises(StepInterrupted):
            adapter.run_steps(None, [{"action": "sleep", "ms": 1}], None)

    def test_render_placeholders(self) -> None:
        adapter = FakeAdapter(self.bus)
        adapter.config["platform"]["sku_pattern"] = r"fake\.com/(\d+)"
        task = SeckillTask(platform="fake", url="https://fake.com/42", quantity=3)
        self.assertEqual(adapter.extract_sku(task.url), "42")
        self.assertEqual(adapter.render("https://x/{sku}/{quantity}", task), "https://x/42/3")


class ClockTest(unittest.TestCase):
    def test_offset_applied(self) -> None:
        clock = Clock()
        clock._offset = 5.0
        before = datetime.now(tz=timezone.utc)
        after = clock.now()
        self.assertGreaterEqual((after - before).total_seconds(), 4.5)

    def test_seconds_until(self) -> None:
        clock = Clock()
        clock._offset = 0.0
        target = clock.now() + timedelta(seconds=10)
        self.assertAlmostEqual(clock.seconds_until(target), 10, delta=0.5)

    def test_real_ntp_query_is_sane(self) -> None:
        """回归：报文解析曾把 Receive 小数部分当成 Transmit，产出负 6 年的偏差。"""
        from seckill.core.clock import _query_ntp

        try:
            offset, rtt = _query_ntp("ntp.aliyun.com", timeout=3)
        except OSError:
            self.skipTest("网络不可用")
        self.assertLess(abs(offset), 60, "与 NTP 服务器的偏差应在分钟级以内")
        self.assertLess(rtt, 2.0, "往返时延应在秒级以内")

    def test_plausible_filter_rejects_garbage(self) -> None:
        """离谱样本必须被拒收，避免污染时钟。"""
        from seckill.core.clock import _query_ntp

        with unittest.mock.patch("seckill.core.clock.struct.unpack") as bad_unpack:
            bad_unpack.return_value = (0, 4000000000)  # 模拟解析错位
            with self.assertRaises(ValueError):
                _query_ntp("ntp.aliyun.com", timeout=3)


class EventBusTest(unittest.TestCase):
    def test_listener_exception_isolated(self) -> None:
        bus = EventBus()
        seen: list[str] = []

        def boom(_event) -> None:
            raise RuntimeError("监听器炸了")

        bus.subscribe(boom)
        bus.subscribe(lambda e: seen.append(e.message))
        bus.log("still works")
        self.assertEqual(seen, ["still works"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
