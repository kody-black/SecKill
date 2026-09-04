"""SecKill 2.0 入口。

    python main.py                      # 图形界面
    python main.py --cli --url <链接>    # 无界面运行
"""
from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="seckill",
        description="配置驱动的自动化抢单工具",
    )
    parser.add_argument("--cli", action="store_true", help="无界面模式（日志输出到终端）")
    parser.add_argument("--url", help="商品链接")
    parser.add_argument("--platform", help="平台 id，如 jd / taobao；省略则按链接自动识别")
    parser.add_argument("--time", help="开抢时间，格式 YYYY-MM-DD HH:MM:SS；省略则立即抢")
    parser.add_argument("--qty", type=int, default=1, help="购买数量")
    parser.add_argument("--attempts", type=int, default=60, help="最大尝试次数")
    parser.add_argument("--timeout", type=int, default=180, help="开抢后总时限（秒）")
    parser.add_argument("--no-api", action="store_true", help="禁用接口直调，只走 DOM")
    parser.add_argument("--headless", action="store_true", help="无头浏览器")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    from seckill.utils.logger import setup_logging
    from seckill.utils.paths import resource_dir

    setup_logging()

    from seckill.adapters.registry import load_adapters
    from seckill.core.clock import Clock
    from seckill.core.events import EventBus

    bus = EventBus()
    clock = Clock()
    adapters = load_adapters(resource_dir() / "config" / "platforms", bus)
    if not adapters:
        print("未找到任何平台配置，请检查 config/platforms/ 目录")
        return 1

    if args.cli:
        from seckill.cli import run_cli

        return run_cli(args, adapters, bus, clock)

    from PySide6.QtWidgets import QApplication

    from seckill.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    window = MainWindow(adapters, bus, clock)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
