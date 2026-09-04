"""事件总线。

核心层保持纯 Python（不依赖 Qt），所有对外通知走这里；
界面层订阅后转成 Qt 信号，从根本上避免「子线程直接操作控件」。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class EventKind(str, Enum):
    LOG = "log"                  # 一行日志
    PHASE = "phase"              # 阶段切换：准备 / 等待 / 抢购 / 收尾
    STATUS = "status"            # 状态栏文本
    COUNTDOWN = "countdown"      # 倒计时刷新
    CLOCK = "clock"              # 校时结果
    SESSION = "session"          # 登录态变化
    QRCODE = "qrcode"            # 需要扫码
    SUCCESS = "success"          # 抢购成功
    FAILURE = "failure"          # 抢购失败
    CANCELLED = "cancelled"      # 被用户中止
    ERROR = "error"              # 异常


@dataclass(slots=True)
class Event:
    kind: EventKind
    message: str = ""
    level: str = "info"          # debug / info / warning / error / success
    data: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.kind.value}] {self.message}"


Listener = Callable[[Event], None]


class EventBus:
    """线程安全的发布订阅。监听器抛异常不影响其它监听器。"""

    def __init__(self) -> None:
        self._listeners: list[Listener] = []
        self._lock = threading.Lock()

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(listener)
        return lambda: self.unsubscribe(listener)

    def unsubscribe(self, listener: Listener) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def emit(self, kind: EventKind, message: str = "", level: str = "info", **data: Any) -> None:
        event = Event(kind=kind, message=message, level=level, data=data)
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:  # noqa: BLE001 - 监听器自身故障不能拖垮抢单线程
                pass

    def log(self, message: str, level: str = "info") -> None:
        self.emit(EventKind.LOG, message, level)
