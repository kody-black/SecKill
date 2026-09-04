"""平台适配器基类。

设计目标：**平台改版只改 YAML，不改代码**。
流程被声明成一组步骤（goto / click / wait_visible / wait_url / fill / press / js / sleep），
由 ``run_steps`` 统一解释执行；只有登录、SKU 提取这类确实无法声明式表达的
才由子类覆写。
"""
from __future__ import annotations

import re
import time
from abc import ABC
from pathlib import Path
from typing import Any, Callable

import yaml

from ..core.events import EventBus, EventKind
from ..core.models import OrderResult, SeckillTask, StepResult
from ..utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_TIMEOUT = 3000


class StepInterrupted(Exception):
    """流程被用户中止。"""


class BaseAdapter(ABC):
    #: 平台标识，与 config/platforms/<id>.yaml 的文件名一致
    platform_id: str = ""
    #: 显示名
    name: str = ""

    def __init__(self, config: dict[str, Any], bus: EventBus) -> None:
        self.config = config
        self.bus = bus
        self.meta = config.get("platform", {})
        self.platform_id = self.meta.get("id", self.platform_id)
        self.name = self.meta.get("name", self.name)
        self.icon = self.meta.get("icon", "")
        self.login_cfg = config.get("login", {}) or {}
        self.api_cfg = config.get("api", {}) or {}
        self.dom_cfg = config.get("dom", {}) or {}
        self._stop: Callable[[], bool] | None = None

    @classmethod
    def from_yaml(cls, path: str | Path, bus: EventBus) -> "BaseAdapter":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls(data, bus)

    # ------------------------------------------------------------------ 绑定

    def bind_stop(self, stop_check: Callable[[], bool]) -> None:
        """注入「是否已被用户中止」的检查函数，每一步都会问一次。"""
        self._stop = stop_check

    def _check_stop(self) -> None:
        if self._stop and self._stop():
            raise StepInterrupted("用户中止")

    # ------------------------------------------------------------------ 匹配

    def match(self, url: str) -> bool:
        hosts = self.meta.get("match") or []
        return any(host in url for host in hosts)

    def extract_sku(self, url: str) -> str:
        pattern = self.meta.get("sku_pattern")
        if not pattern:
            return ""
        m = re.search(pattern, url)
        return m.group(1) if m else ""

    # ------------------------------------------------------------ 选择器工具

    @staticmethod
    def _locator(page: Any, by: str, value: str) -> Any:
        by = (by or "css").lower()
        if by == "css":
            return page.locator(value).first
        if by == "xpath":
            return page.locator(f"xpath={value}").first
        if by == "text":
            return page.get_by_text(value, exact=False).first
        if by == "id":
            return page.locator(f"[id='{value}']").first
        if by == "role":
            # value 形如 "button:提交订单"
            role, _, name = value.partition(":")
            return page.get_by_role(role, name=name or None).first
        raise ValueError(f"不支持的定位方式: {by}")

    # -------------------------------------------------------------- 流程引擎

    def run_steps(self, page: Any, steps: list[dict[str, Any]], task: SeckillTask) -> StepResult:
        """解释执行一组声明式步骤。任何一步失败都会中断（除非标记 optional）。"""
        for index, step in enumerate(steps, 1):
            self._check_stop()
            action = (step.get("action") or "").lower()
            desc = step.get("desc") or action
            optional = bool(step.get("optional"))
            timeout = int(step.get("timeout", DEFAULT_TIMEOUT))
            started = time.perf_counter()

            try:
                self._dispatch(page, step, action, timeout, task)
            except StepInterrupted:
                raise
            except Exception as exc:  # noqa: BLE001 - 单步失败要给出可读上下文
                elapsed = int((time.perf_counter() - started) * 1000)
                if optional:
                    self.bus.log(f"[跳过] 第 {index} 步 {desc}：{exc}", "warning")
                    continue
                msg = f"第 {index} 步「{desc}」失败：{type(exc).__name__}: {exc}"
                self.bus.log(msg, "error")
                return StepResult(False, action, msg, elapsed)
            else:
                elapsed = int((time.perf_counter() - started) * 1000)
                self.bus.log(f"第 {index} 步 {desc} 完成（{elapsed} ms）", "debug")
        return StepResult(True, "steps", "流程执行完成")

    def _dispatch(
        self, page: Any, step: dict[str, Any], action: str, timeout: int, task: SeckillTask
    ) -> None:
        by = step.get("by", "css")
        value = str(step.get("value", ""))
        rendered = self.render(value, task)

        if action == "goto":
            page.goto(rendered, timeout=max(timeout, 10_000), wait_until="domcontentloaded")
        elif action == "click":
            self._locator(page, by, rendered).click(timeout=timeout)
        elif action == "click_js":
            page.evaluate(f"document.querySelector({rendered!r})?.click()")
        elif action == "click_text":
            # 用 JS 查找包含指定文字的按钮/链接并点击，绕过 iframe 遮挡
            texts = step.get("texts", [rendered]) if isinstance(step.get("texts"), list) else [rendered]
            js = """(texts) => {
                const all = document.querySelectorAll('button, a, div[role="button"], div[class*="btn"], span[class*="btn"], [onclick]');
                for (const el of all) {
                    const t = (el.textContent || '').trim();
                    for (const kw of texts) {
                        if (t.includes(kw)) { el.click(); return true; }
                    }
                }
                return false;
            }"""
            result = page.evaluate(js, [self.render(t, task) for t in texts])
            if not result:
                raise RuntimeError(f"未找到包含 {texts} 的可点击元素")
        elif action == "wait_visible":
            self._locator(page, by, rendered).wait_for(state="visible", timeout=timeout)
        elif action == "wait_hidden":
            self._locator(page, by, rendered).wait_for(state="hidden", timeout=timeout)
        elif action == "wait_url":
            page.wait_for_url(f"**/*{rendered}*", timeout=timeout)
        elif action == "wait_title":
            # 轮询等待页面标题包含指定文字（用于 SPA 跳转，URL 不变但标题变了）
            import time as _time
            deadline = _time.time() + timeout / 1000
            while _time.time() < deadline:
                self._check_stop()
                try:
                    if rendered in (page.title() or ""):
                        return
                except Exception:
                    pass
                _time.sleep(0.3)
            raise TimeoutError(f"等待标题包含「{rendered}」超时")
        elif action == "fill":
            self._locator(page, by, rendered).fill(
                str(step.get("text", "")), timeout=timeout
            )
        elif action == "press":
            self._locator(page, by, rendered).press(step.get("key", "Enter"), timeout=timeout)
        elif action == "js":
            page.evaluate(rendered)
        elif action == "sleep":
            time.sleep(int(step.get("ms", 200)) / 1000)
        elif action == "screenshot":
            self.bus.emit(EventKind.LOG, f"已截图 {rendered}", "debug")
        else:
            raise ValueError(f"未知动作: {action}")

    def render(self, template: str, task: SeckillTask) -> str:
        """把步骤里的 ``{url}`` / ``{sku}`` / ``{quantity}`` 占位符替换掉。"""
        if "{" not in template:
            return template
        values = {
            "url": task.url,
            "sku": self.extract_sku(task.url),
            "quantity": str(task.quantity),
        }
        values.update(task.specs)
        out = template
        for key, val in values.items():
            out = out.replace(f"{{{key}}}", str(val))
        return out

    # ---------------------------------------------------------------- 登录

    def check_blocked(self, page: Any) -> str | None:
        """命中平台风控页特征时返回命中的关键词，正常页面返回 None。

        特征列表写在 YAML 的 ``platform.block_markers``，
        比如京东的 403 频控页 URL 带 ``reason=403``。
        """
        markers = self.meta.get("block_markers") or []
        if not markers:
            return None
        try:
            url = page.url or ""
            body = " ".join((page.inner_text("body") or "").split())[:800]
        except Exception:  # noqa: BLE001
            return None
        for marker in markers:
            if marker and (marker in url or marker in body):
                return marker
        return None

    def is_logged_in(self, page: Any) -> bool:
        """页面当前是否处于已登录状态。

        判定规则来自 YAML 的 ``login.logged_in_cookies`` 与
        ``login.logged_in_selectors``——平台换了判定方式，改配置就行。
        """
        cookie_names = self.login_cfg.get("logged_in_cookies") or []
        if cookie_names:
            try:
                present = {c["name"] for c in page.context.cookies()}
            except Exception:  # noqa: BLE001
                present = set()
            if not any(name in present for name in cookie_names):
                return False

        selectors = self.login_cfg.get("logged_in_selectors") or []
        if not selectors:
            return True
        for raw in selectors:
            by, _, value = raw.partition(":")
            try:
                if self._locator(page, by or "css", value).count() > 0:
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def login(self, session: Any, timeout: int | None = None) -> bool:
        """未登录时引导扫码登录。默认走配置里的 login 步骤。"""
        page = session.page
        url = self.render(self.login_cfg.get("url", ""), SeckillTask(platform=self.platform_id, url=""))
        if url:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)

        self.bus.emit(EventKind.QRCODE, "请使用手机 App 扫码登录", "warning")
        self.bus.log("等待扫码登录…")

        deadline = time.time() + (timeout or int(self.login_cfg.get("timeout", 180)))
        while time.time() < deadline:
            self._check_stop()
            if self.is_logged_in(page):
                self.bus.emit(EventKind.SESSION, "登录成功", "success")
                return True
            time.sleep(1.0)

        self.bus.log("扫码超时，登录失败", "error")
        return False

    # ------------------------------------------------------------ 对外主流程

    def prepare(self, session: Any, task: SeckillTask) -> StepResult:
        """开抢前：打开商品页、选规格数量。失败不应致命（可能只是已选好）。"""
        steps = (self.dom_cfg.get("prepare") or []) + (self.dom_cfg.get("specs") or [])
        if not steps:
            return StepResult(True, "prepare", "未配置准备步骤")
        self.bus.log("准备阶段：打开商品页并选择规格")
        return self.run_steps(session.page, steps, task)

    def fire_dom(self, session: Any, task: SeckillTask) -> OrderResult:
        """DOM 兜底：按配置点击下单。"""
        steps = self.dom_cfg.get("fire") or []
        if not steps:
            return OrderResult(False, "dom", "未配置 DOM 流程")
        result = self.run_steps(session.page, steps, task)
        if not result.ok:
            return OrderResult(False, "dom", result.message)

        success_url = self.dom_cfg.get("success_url")
        if success_url:
            try:
                session.page.wait_for_url(f"**/*{success_url}*", timeout=8000)
            except Exception as exc:  # noqa: BLE001
                return OrderResult(False, "dom", f"下单未确认：{type(exc).__name__}")
        return OrderResult(True, "dom", "下单流程已走通")

    def fire_api(self, session: Any, task: SeckillTask) -> OrderResult:
        """接口直调：优先走的通道，跳过渲染与滑块。

        需要先用抓包工具把真实请求填进 config/platforms/*.yaml 的 api 段，
        否则返回 ``None`` 语义的「未配置」，由执行器退回 DOM。
        """
        if not self.api_cfg.get("enabled"):
            return OrderResult(False, "api", "未启用接口直调")

        import requests

        cookies = {c["name"]: c["value"] for c in session.cookies()}
        if not cookies:
            return OrderResult(False, "api", "没有可用 Cookie，请先登录")

        http = requests.Session()
        http.cookies.update(cookies)
        http.headers.update(
            {
                "User-Agent": self.meta.get("user_agent", ""),
                "Referer": task.url,
            }
        )

        for step in self.api_cfg.get("steps") or []:
            self._check_stop()
            name = step.get("name", "接口调用")
            spec = step.get("request", {})
            try:
                resp = http.request(
                    method=spec.get("method", "GET"),
                    url=self.render(spec.get("url", ""), task),
                    params={
                        k: self.render(str(v), task)
                        for k, v in (spec.get("params") or {}).items()
                    },
                    data={
                        k: self.render(str(v), task)
                        for k, v in (spec.get("data") or {}).items()
                    },
                    headers={
                        k: self.render(str(v), task)
                        for k, v in (spec.get("headers") or {}).items()
                    },
                    timeout=spec.get("timeout", 5),
                )
            except Exception as exc:  # noqa: BLE001
                return OrderResult(False, "api", f"{name} 请求异常：{exc}")

            expect = step.get("expect") or {}
            if not self._match_expect(resp, expect):
                text = resp.text[:200].replace("\n", " ")
                return OrderResult(False, "api", f"{name} 未通过校验：{text}")
            self.bus.log(f"{name} 成功（HTTP {resp.status_code}）", "debug")

        return OrderResult(True, "api", "接口下单成功")

    @staticmethod
    def _match_expect(resp: Any, expect: dict[str, Any]) -> bool:
        status = expect.get("status")
        if status and resp.status_code != status:
            return False
        contains = expect.get("contains")
        if contains and contains not in resp.text:
            return False
        not_contains = expect.get("not_contains")
        if not_contains and not_contains in resp.text:
            return False
        return True
