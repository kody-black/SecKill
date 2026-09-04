"""京东适配器。

绝大多数流程都在 config/platforms/jd.yaml 里声明，
这里只在需要时覆写京东特有的行为。
"""
from __future__ import annotations

from typing import Any

from .base import BaseAdapter


class JDAdapter(BaseAdapter):
    platform_id = "jd"
    name = "京东"

    def is_logged_in(self, page: Any) -> bool:
        # 京东登录后首页右上角会出现昵称；未登录则是「你好，请登录」
        if "passport.jd.com" in (page.url or ""):
            return False
        return super().is_logged_in(page)
