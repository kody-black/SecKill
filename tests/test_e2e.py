"""端到端测试：真实浏览器 + 真实调度器 + 真实适配器。

跑的是一个本地模拟商店页面（tests/fixtures/mock_shop.html），
它会模拟「开抢前按钮不可点、到点才可点」的行为，
因此下面三个用例覆盖了定时等待、退避重试、成功判定与中途中止。

需要本机装有 Chrome（或先执行 python -m playwright install chromium）。
运行：PYTHONPATH=. python tests/test_e2e.py
"""
from __future__ import annotations

import http.server
import threading
import time
import unittest
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path

from seckill.adapters.base import BaseAdapter
from seckill.core.clock import Clock
from seckill.core.events import Event, EventBus, EventKind
from seckill.core.models import SeckillTask
from seckill.core.scheduler import SeckillScheduler
from seckill.core.session import SessionManager

FIXTURES = Path(__file__).parent / "fixtures"

#: 指向模拟商店的配置，等价于一份 config/platforms/*.yaml
MOCK_CONFIG = {
    "platform": {"id": "e2e", "name": "模拟商店", "match": ["127.0.0.1"], "home": ""},
    "login": {},
    "dom": {
        "prepare": [
            {"action": "goto", "value": "{url}", "timeout": 20000, "desc": "打开商品页"},
            {"action": "wait_visible", "by": "css", "value": "#itemInfo", "timeout": 10000},
        ],
        "specs": [],
        "fire": [
            {"action": "click", "by": "id", "value": "buyBtn", "timeout": 3000, "desc": "立即抢购"},
            {"action": "wait_url", "value": "step=order", "timeout": 5000},
            {"action": "click", "by": "id", "value": "order-submit", "timeout": 5000, "desc": "提交订单"},
        ],
        "success_url": "step=success",
    },
}


def _start_server() -> tuple[http.server.ThreadingHTTPServer, int]:
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(FIXTURES))
    handler.log_message = lambda *a, **k: None  # 静音
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


class Recorder:
    """收集事件，便于断言。"""

    def __init__(self, bus: EventBus) -> None:
        self.events: list[Event] = []
        bus.subscribe(self.events.append)

    def kinds(self) -> list[str]:
        return [e.kind.value for e in self.events]

    def last_message(self, kind: EventKind) -> str:
        for e in reversed(self.events):
            if e.kind == kind:
                return e.message
        return ""


class EndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.httpd, cls.port = _start_server()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()

    def _url(self, open_in: float = 0.0) -> str:
        open_at = int((time.time() + open_in) * 1000)
        return f"http://127.0.0.1:{self.port}/mock_shop.html?openAt={open_at}"

    def _run(self, task: SeckillTask, stop_after: float | None = None) -> Recorder:
        bus = EventBus()
        rec = Recorder(bus)
        adapter = BaseAdapter(MOCK_CONFIG, bus)
        sched = SeckillScheduler(
            task=task,
            adapter=adapter,
            session=SessionManager(task.platform),
            bus=bus,
            clock=Clock(),
        )
        sched.start()
        if stop_after is not None:
            time.sleep(stop_after)
            sched.stop()
        sched.join(timeout=90)
        self.assertFalse(sched.is_alive(), "抢单线程应当在时限内结束")
        return rec

    # ------------------------------------------------------------------ 用例

    def test_scheduled_seckill_succeeds(self) -> None:
        """开抢时刻比按钮解禁时刻更早：前几次点击应失败并退避，最终成功。"""
        task = SeckillTask(
            platform="e2e",
            url=self._url(open_in=6.0),
            target_time=datetime.now().astimezone() + timedelta(seconds=2),
            max_attempts=20,
            timeout_seconds=60,
        )
        rec = self._run(task)
        self.assertIn(EventKind.SUCCESS.value, rec.kinds(), f"未成功：{rec.events}")
        self.assertIn(EventKind.PHASE.value, rec.kinds())

    def test_immediate_seckill_succeeds(self) -> None:
        """按钮已解禁，无定时：应当一次就成。"""
        task = SeckillTask(
            platform="e2e",
            url=self._url(open_in=0.0),
            max_attempts=10,
            timeout_seconds=30,
        )
        rec = self._run(task)
        self.assertIn(EventKind.SUCCESS.value, rec.kinds())

    def test_stop_during_waiting(self) -> None:
        """等待阶段必须能被立刻打断。"""
        task = SeckillTask(
            platform="e2e",
            url=self._url(open_in=0.0),
            target_time=datetime.now().astimezone() + timedelta(seconds=300),
            max_attempts=5,
            timeout_seconds=30,
        )
        started = time.time()
        rec = self._run(task, stop_after=2.0)
        elapsed = time.time() - started
        self.assertLess(elapsed, 20, "停止后应当迅速退出，不至等满 300 秒")
        self.assertIn(EventKind.CANCELLED.value, rec.kinds())


if __name__ == "__main__":
    unittest.main(verbosity=2)
