"""结构化日志。

统一走标准库 logging：文件轮转 + 控制台，运行期可通过事件总线推给界面。
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from .paths import logs_dir

_CONFIGURED = False
_LOG_FILE: Path | None = None


def setup_logging(level: int = logging.INFO) -> Path:
    """配置根 logger，返回当前日志文件路径。"""
    global _CONFIGURED, _LOG_FILE

    log_file = logs_dir() / "seckill.log"
    _LOG_FILE = log_file

    if _CONFIGURED:
        return log_file

    root = logging.getLogger()
    root.setLevel(level)

    fmt = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d %(levelname)-7s [%(threadName)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    _CONFIGURED = True
    return log_file


def get_logger(name: str) -> logging.Logger:
    if not _CONFIGURED:
        setup_logging()
    return logging.getLogger(name)


def current_log_file() -> Path | None:
    return _LOG_FILE
