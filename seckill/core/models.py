"""数据模型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    IDLE = "idle"
    PREPARING = "preparing"      # 登录、打开商品页、选规格
    WAITING = "waiting"          # 已就绪，等待开抢时刻
    FIRING = "firing"            # 正在抢
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StopReason(str, Enum):
    NONE = ""
    USER = "user"                # 用户点了停止
    TIMEOUT = "timeout"          # 超过总时限
    EXHAUSTED = "exhausted"      # 重试次数用尽


@dataclass(slots=True)
class SeckillTask:
    """一次抢单任务。"""

    platform: str                       # 平台 id，如 jd / taobao
    url: str                            # 商品链接
    target_time: datetime | None = None  # None 表示立即抢
    quantity: int = 1
    specs: dict[str, str] = field(default_factory=dict)   # 规格，如 {"颜色": "黑色"}
    max_attempts: int = 50              # 最大尝试次数
    timeout_seconds: int = 180          # 开抢后的总时限
    prefer_api: bool = True             # 优先接口直调，失败再退回 DOM
    headless: bool = False

    def validate(self) -> list[str]:
        """返回错误信息列表，空列表表示合法。"""
        errors: list[str] = []
        if not self.platform:
            errors.append("未指定平台")
        if not self.url or not self.url.startswith(("http://", "https://")):
            errors.append("商品链接无效")
        if self.quantity < 1:
            errors.append("购买数量必须大于 0")
        if self.max_attempts < 1:
            errors.append("最大尝试次数必须大于 0")
        if self.target_time is not None and self.target_time.tzinfo is None:
            errors.append("开抢时间必须带时区信息")
        return errors

    @property
    def is_scheduled(self) -> bool:
        return self.target_time is not None


@dataclass(slots=True)
class StepResult:
    """单个流程步骤的执行结果。"""

    ok: bool
    action: str
    message: str = ""
    elapsed_ms: int = 0


@dataclass(slots=True)
class OrderResult:
    """一次抢单尝试的结果。"""

    ok: bool
    channel: str = ""            # api / dom
    message: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
