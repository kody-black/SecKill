"""选择器校准工具。

平台页面一改版，配置里的选择器就可能对不上。这个工具打开真实页面，
逐条核对 YAML 里的选择器是否命中，未命中时把页面上真实存在的可点击元素列出来，
方便直接抄回去。

用法：
    python tools/calibrate.py --platform jd --url https://item.jd.com/100014219124.html
    python tools/calibrate.py --platform taobao      # 只打开首页核对登录判定

依赖已登录状态时（比如要核对购物车/结算页的选择器），加上 --login 会先等你扫码。
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from seckill.adapters.registry import load_adapters  # noqa: E402
from seckill.core.events import EventBus  # noqa: E402
from seckill.core.models import SeckillTask  # noqa: E402
from seckill.drivers.browser import BrowserSession  # noqa: E402
from seckill.utils.paths import resource_dir  # noqa: E402

SKIP_ACTIONS = {"goto", "wait_url", "sleep", "js", "screenshot"}


def check_steps(page, adapter, steps: list[dict], label: str) -> int:
    print(f"\n--- {label} ---")
    if not steps:
        print("  （未配置）")
        return 0
    missed = 0
    for step in steps:
        action = step.get("action", "")
        by = step.get("by")
        value = step.get("value", "")
        if action in SKIP_ACTIONS or not by or not value:
            continue
        try:
            count = adapter._locator(page, by, value).count()
        except Exception as exc:  # noqa: BLE001
            count = -1
            print(f"  [错误] {action} {by}:{value} -> {type(exc).__name__}")
        if count > 0:
            print(f"  [命中] {action:12s} {by}:{value}   count={count}   # {step.get('desc','')}")
        else:
            missed += 1
            print(f"  [未命中] {action:12s} {by}:{value}   count={count}   # {step.get('desc','')}")
    return missed


def check_login_markers(page, adapter) -> None:
    print("\n--- 登录判定 ---")
    names = adapter.login_cfg.get("logged_in_cookies") or []
    if names:
        present = {c["name"] for c in page.context.cookies()}
        hit = [n for n in names if n in present]
        print(f"  Cookie 期望 {names}；实际命中 {hit or '无'}")
    for raw in adapter.login_cfg.get("logged_in_selectors") or []:
        by, _, value = raw.partition(":")
        try:
            count = adapter._locator(page, by or "css", value).count()
        except Exception:  # noqa: BLE001
            count = -1
        print(f"  [{'命中' if count > 0 else '未命中'}] {raw}   count={count}")
    print(f"  综合判定：{'已登录' if adapter.is_logged_in(page) else '未登录'}")


def list_candidates(page, limit: int = 30) -> None:
    print(f"\n--- 页面上的可点击元素（前 {limit} 个，可直接抄 value）---")
    try:
        elements = page.query_selector_all("button, a, input[type=button], input[type=submit]")
    except Exception as exc:  # noqa: BLE001
        print(f"  枚举失败：{exc}")
        return
    for el in elements[:limit]:
        try:
            eid = el.get_attribute("id") or ""
            cls = (el.get_attribute("class") or "").strip()
            text = " ".join((el.inner_text() or "").split())[:24]
        except Exception:  # noqa: BLE001
            continue
        if not (eid or cls or text):
            continue
        hint = f"#{eid}" if eid else (f".{cls.split()[0]}" if cls else "")
        print(f"  {hint:38s} | {text}")


def main() -> int:
    parser = argparse.ArgumentParser(description="核对平台配置里的选择器是否仍然有效")
    parser.add_argument("--platform", required=True, help="平台 id，如 jd / taobao")
    parser.add_argument("--url", default="", help="要打开的商品链接，缺省用配置里的 home")
    parser.add_argument("--login", action="store_true", help="打开后先等待扫码登录")
    parser.add_argument("--candidates", action="store_true", help="列出页面上可点击元素")
    parser.add_argument("--headless", action="store_true", help="不弹出浏览器窗口")
    args = parser.parse_args()

    bus = EventBus()
    adapters = load_adapters(resource_dir() / "config" / "platforms", bus)
    adapter = adapters.get(args.platform)
    if adapter is None:
        print(f"未找到平台 {args.platform}，可用：{', '.join(adapters)}")
        return 2

    target = args.url or adapter.meta.get("home") or ""
    if not target:
        print("请提供 --url，或在配置里补上 platform.home")
        return 2

    print(f"平台：{adapter.name}（{adapter.platform_id}）")
    print(f"打开：{target}")

    with tempfile.TemporaryDirectory() as tmp:
        session = BrowserSession(profile_dir=Path(tmp), headless=args.headless)
        session.start()
        try:
            session.page.goto(target, timeout=45_000, wait_until="domcontentloaded")
            time.sleep(2.5)

            if args.login and not adapter.is_logged_in(session.page):
                print("\n请在浏览器里扫码登录，登录成功后自动继续…")
                adapter.login(session)

            print(f"\n当前 URL：{session.page.url}")
            missed = 0
            dom = adapter.dom_cfg
            missed += check_steps(session.page, adapter, dom.get("prepare") or [], "准备阶段 dom.prepare")
            missed += check_steps(session.page, adapter, dom.get("specs") or [], "选规格 dom.specs")
            missed += check_steps(session.page, adapter, dom.get("fire") or [], "开抢阶段 dom.fire")
            check_login_markers(session.page, adapter)
            if args.candidates:
                list_candidates(session.page)

            print(f"\n未命中 {missed} 条。未命中的步骤需要按上面的候选元素改 YAML。")
        finally:
            session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
