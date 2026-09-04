"""无界面模式。

适合放在服务器或常开的机器上跑：登录一次，之后定时抢。
Ctrl+C 可以随时中止。
"""
from __future__ import annotations

import argparse
import signal
import sys
import time
from datetime import datetime

from .core.clock import Clock
from .core.events import Event, EventBus, EventKind
from .core.models import SeckillTask
from .core.scheduler import SeckillScheduler
from .core.session import SessionManager

_QUIET = {EventKind.COUNTDOWN}


def _printer(bus: EventBus) -> None:
    def handle(event: Event) -> None:
        if event.kind in _QUIET:
            return
        prefix = {
            "debug": "  ",
            "info": "  ",
            "warning": "! ",
            "error": "x ",
            "success": "* ",
        }.get(event.level, "  ")
        if event.message:
            print(f"{prefix}{event.message}", flush=True)

    bus.subscribe(handle)


def run_cli(args: argparse.Namespace, adapters, bus: EventBus, clock: Clock) -> int:
    _printer(bus)

    if not args.url:
        print("错误：--cli 模式需要 --url")
        return 2

    from .adapters.registry import detect

    platform = args.platform or detect(adapters, args.url)
    if platform not in adapters:
        print(f"错误：无法识别平台，可用平台：{', '.join(adapters)}")
        return 2

    target: datetime | None = None
    if args.time:
        try:
            naive = datetime.strptime(args.time, "%Y-%m-%d %H:%M:%S")
            target = naive.astimezone()
        except ValueError:
            print("错误：--time 格式应为 YYYY-MM-DD HH:MM:SS")
            return 2

    task = SeckillTask(
        platform=platform,
        url=args.url,
        target_time=target,
        quantity=args.qty,
        max_attempts=args.attempts,
        timeout_seconds=args.timeout,
        prefer_api=not args.no_api,
        headless=args.headless,
    )
    errors = task.validate()
    if errors:
        print("参数有误：" + "；".join(errors))
        return 2

    print("正在与时间服务器校时…")
    result = clock.sync()
    print(f"  {result}")

    scheduler = SeckillScheduler(
        task=task,
        adapter=adapters[platform],
        session=SessionManager(platform),
        bus=bus,
        clock=clock,
    )

    def on_sigint(_sig: int, _frame: object) -> None:
        print("\n收到中断信号，正在停止…")
        scheduler.stop()

    signal.signal(signal.SIGINT, on_sigint)
    scheduler.start()

    try:
        while scheduler.is_alive():
            time.sleep(0.2)
    except KeyboardInterrupt:
        scheduler.stop()
        scheduler.join(timeout=10)

    return 0 if scheduler is not None else 1
