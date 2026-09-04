"""登录态持久化。

用 Playwright 的持久化上下文（真实浏览器 profile）保存登录态，
扫一次码可以撑很久，不必每次抢单都重新登录。
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from ..utils.logger import get_logger
from ..utils.paths import sessions_dir

logger = get_logger(__name__)

_META_NAME = "session.json"


class SessionManager:
    """每个平台一个独立 profile 目录，附一份元信息。"""

    def __init__(self, platform: str) -> None:
        self.platform = platform
        self.dir: Path = sessions_dir() / platform
        self.dir.mkdir(parents=True, exist_ok=True)

    @property
    def meta_path(self) -> Path:
        return self.dir / _META_NAME

    def mark_login(self, note: str = "") -> None:
        meta = {
            "platform": self.platform,
            "login_at": datetime.now(tz=timezone.utc).isoformat(),
            "note": note,
        }
        self.meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("[%s] 登录态已保存", self.platform)

    def last_login(self) -> datetime | None:
        if not self.meta_path.exists():
            return None
        try:
            meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
            return datetime.fromisoformat(meta["login_at"])
        except Exception:  # noqa: BLE001 - 元信息损坏视作未登录
            return None

    @property
    def has_session(self) -> bool:
        return self.meta_path.exists()

    def clear(self) -> bool:
        """清除登录态。返回是否真的删掉了东西。"""
        if not self.dir.exists():
            return False
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True, exist_ok=True)
        logger.info("[%s] 登录态已清除", self.platform)
        return True
