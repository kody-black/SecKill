"""淘宝真实页面结构分析 v2。

从淘宝搜索结果取一个真实商品，分析商品页和下单页的真实结构。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from seckill.drivers.browser import BrowserSession
from seckill.utils.paths import sessions_dir

OUTPUT = ROOT / "taobao_analysis.json"


def extract_buttons(page, limit=60):
    results = []
    try:
        els = page.query_selector_all("button, [role='button'], a[class], div[class*='btn'], span[class*='btn'], a[class*='buy'], a[class*='action']")
    except Exception:
        return results
    for el in els[:limit]:
        try:
            if not el.is_visible():
                continue
            results.append({
                "tag": el.evaluate("e => e.tagName.toLowerCase()"),
                "id": el.get_attribute("id") or "",
                "class": " ".join((el.get_attribute("class") or "").split())[:80],
                "text": " ".join((el.inner_text() or "").split())[:30],
                "href": (el.get_attribute("href") or "")[:60],
            })
        except Exception:
            continue
    return results


def find_by_text(page, texts: list[str]) -> list[dict]:
    found = []
    for text in texts:
        try:
            els = page.get_by_text(text, exact=False).all()
            for el in els[:5]:
                try:
                    if not el.is_visible():
                        continue
                    found.append({
                        "search_text": text,
                        "tag": el.evaluate("e => e.tagName.toLowerCase()"),
                        "id": el.get_attribute("id") or "",
                        "class": " ".join((el.get_attribute("class") or "").split())[:80],
                        "text": " ".join((el.inner_text() or "").split())[:30],
                        "outer_html": el.evaluate("e => e.outerHTML.substring(0, 200)"),
                    })
                except Exception:
                    continue
        except Exception:
            continue
    return found


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--keyword", default="数据线")
    args = parser.parse_args()

    profile = sessions_dir() / "taobao"
    session = BrowserSession(profile_dir=profile, headless=args.headless)
    session.start()
    results = {}

    try:
        page = session.page

        # 1. 搜索商品
        print(f">>> 搜索: {args.keyword}")
        page.goto(f"https://s.taobao.com/search?q={args.keyword}", timeout=45_000, wait_until="domcontentloaded")
        time.sleep(6)

        if "login" in page.url.lower():
            print(">>> 需要扫码登录！请去掉 --headless 重新运行")
            session.close()
            return

        # 2. 从搜索结果取第一个商品链接
        print(">>> 提取搜索结果中的商品链接...")
        links = page.query_selector_all("a[href*='item.taobao.com'], a[href*='detail.tmall.com']")
        product_url = None
        for el in links[:20]:
            href = el.get_attribute("href") or ""
            if "item.taobao.com/item.htm" in href or "detail.tmall.com/item.htm" in href:
                product_url = href
                break

        if not product_url:
            # 尝试从 onclick 或 data 属性找
            print(">>> 从搜索结果卡片找链接...")
            cards = page.query_selector_all("[class*='Card'], [class*='item'], [class*='product']")
            for card in cards[:10]:
                try:
                    a = card.query_selector("a")
                    if a:
                        href = a.get_attribute("href") or ""
                        if "item" in href or "detail" in href:
                            product_url = href
                            break
                except Exception:
                    continue

        if not product_url:
            print("!!! 没找到商品链接，截图分析...")
            page.screenshot(path=str(ROOT / "screenshots" / "taobao_search.png"))
            results["search_page"] = {
                "url": page.url,
                "buttons": extract_buttons(page),
            }
            OUTPUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            return

        # 补全协议
        if product_url.startswith("//"):
            product_url = "https:" + product_url
        elif not product_url.startswith("http"):
            product_url = "https://" + product_url

        print(f">>> 找到商品: {product_url[:80]}")

        # 3. 打开商品页
        page.goto(product_url, timeout=45_000, wait_until="domcontentloaded")
        time.sleep(6)
        print(f">>> 商品页 URL: {page.url[:80]}")
        print(f">>> 商品页标题: {page.title()}")

        # 截图
        page.screenshot(path=str(ROOT / "screenshots" / "taobao_product.png"), full_page=False)

        # 分析商品页
        results["product_page"] = {
            "url": page.url,
            "title": page.title(),
            "all_buttons": extract_buttons(page),
            "buy_buttons": find_by_text(page, ["立即购买", "立即抢购", "马上抢", "秒杀", "加入购物车", "提交订单"]),
        }

        print(f"\n>>> 商品页购买按钮:")
        for b in results["product_page"]["buy_buttons"][:15]:
            print(f"  [{b['search_text']:8s}] {b['tag']:6s} #{b['id']:15s} .{b['class'][:35]:35s} | {b['text']}")

        # 4. 尝试点击"立即购买"
        clicked = False
        for b in results["product_page"]["buy_buttons"]:
            if b["search_text"] in ("立即购买", "立即抢购", "马上抢"):
                print(f"\n>>> 点击: {b['search_text']}")
                try:
                    if b["id"]:
                        page.locator(f"#{b['id']}").first.click(timeout=5000)
                    elif b["class"]:
                        cls = b["class"].split()[0]
                        page.locator(f".{cls}").first.click(timeout=5000)
                    else:
                        page.get_by_text(b["search_text"], exact=False).first.click(timeout=5000)
                    clicked = True
                    print("    点击成功")
                    time.sleep(6)
                    break
                except Exception as exc:
                    print(f"    点击失败: {str(exc)[:100]}")

        if clicked:
            print(f"\n>>> 下单页 URL: {page.url[:80]}")
            page.screenshot(path=str(ROOT / "screenshots" / "taobao_order.png"), full_page=False)
            results["order_page"] = {
                "url": page.url,
                "title": page.title(),
                "all_buttons": extract_buttons(page),
                "submit_buttons": find_by_text(page, ["提交订单", "确认下单", "确认订单", "提交"]),
            }
            print(f">>> 下单页提交按钮:")
            for b in results["order_page"]["submit_buttons"][:10]:
                print(f"  [{b['search_text']:8s}] {b['tag']:6s} #{b['id']:15s} .{b['class'][:35]:35s} | {b['text']}")

        OUTPUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n>>> 结果已写入: {OUTPUT}")

    except Exception as exc:
        print(f"!!! 出错: {type(exc).__name__}: {exc}")
        try:
            page.screenshot(path=str(ROOT / "screenshots" / "taobao_error.png"))
        except Exception:
            pass
    finally:
        session.close()


if __name__ == "__main__":
    main()
