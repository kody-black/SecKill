"""淘宝适配器。

淘宝的登录页默认展示密码框，需要先切到二维码标签页——
这一步无法用声明式步骤表达，所以在代码里覆写。
"""
from __future__ import annotations

import time
from typing import Any

from .base import BaseAdapter


class TaobaoAdapter(BaseAdapter):
    platform_id = "taobao"
    name = "淘宝"

    def login(self, session: Any, timeout: int | None = None) -> bool:
        page = session.page
        switch = (self.login_cfg.get("qr_switch") or {})
        if switch:
            by = switch.get("by", "css")
            value = switch.get("value", "")
            if value:
                try:
                    self._locator(page, by, value).click(timeout=5000)
                    time.sleep(0.6)
                except Exception:  # noqa: BLE001 - 已经默认展示二维码时不必强求
                    pass
        return super().login(session, timeout)
