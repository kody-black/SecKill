"""适配器注册表：扫描 config/platforms/*.yaml 自动装配。

新增一个平台 = 丢一个 YAML 进去（必要时加一个继承 BaseAdapter 的子类）。
主程序不感知具体平台。
"""
from __future__ import annotations

from pathlib import Path

from ..core.events import EventBus
from ..utils.logger import get_logger
from .base import BaseAdapter
from .jd import JDAdapter
from .taobao import TaobaoAdapter

logger = get_logger(__name__)

#: YAML 里的 platform.id -> 适配器类
_ADAPTER_CLASSES: dict[str, type[BaseAdapter]] = {
    "jd": JDAdapter,
    "taobao": TaobaoAdapter,
}


def register(platform_id: str, cls: type[BaseAdapter]) -> None:
    _ADAPTER_CLASSES[platform_id] = cls


def load_adapters(config_dir: str | Path, bus: EventBus) -> dict[str, BaseAdapter]:
    """加载目录下所有平台配置，返回 {platform_id: adapter}。"""
    adapters: dict[str, BaseAdapter] = {}
    for path in sorted(Path(config_dir).glob("*.yaml")):
        try:
            import yaml

            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            platform_id = (raw.get("platform") or {}).get("id") or path.stem
            cls = _ADAPTER_CLASSES.get(platform_id, BaseAdapter)
            adapter = cls(raw, bus)
            adapter.bind_stop(lambda: False)
            adapters[adapter.platform_id] = adapter
        except Exception as exc:  # noqa: BLE001 - 单个平台配置坏掉不应拖垮整个程序
            logger.error("加载平台配置 %s 失败：%s", path.name, exc)
            bus.log(f"平台配置 {path.name} 加载失败：{exc}", "error")
    logger.info("已加载 %d 个平台：%s", len(adapters), ", ".join(adapters) or "无")
    return adapters


def detect(adapters: dict[str, BaseAdapter], url: str) -> str:
    """根据商品链接猜平台，猜不出返回空串。"""
    for platform_id, adapter in adapters.items():
        if adapter.match(url):
            return platform_id
    return ""
