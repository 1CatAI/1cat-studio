#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Product journeys on disposable data: background generation, drafts and discovery."""
import json
import os
from pathlib import Path
import runpy
import socket
import subprocess
import tempfile
import time
import urllib.request

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".artifacts/product-upgrade"
OUT.mkdir(parents=True, exist_ok=True)
SEED = runpy.run_path(str(ROOT / "studio/scripts/check-browser.py"))["SEED"]
SEED = SEED.replace("long_reasoning='__onecat_long_reasoning__'", "continuity='__product_continuity__' in json.dumps(body.get('messages',[]))\n    long_reasoning='__onecat_long_reasoning__'")
SEED = SEED.replace(".025 if long_reasoning else .05", ".3 if continuity else (.025 if long_reasoning else .05)")


def run():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="onecat-product-") as state, (OUT / "product-server.log").open("w") as log:
        env = {**os.environ, "ONECAT_STUDIO_HOME": state, "PYTHONPATH": str(ROOT / "studio/backend"), "TEST_PORT": str(port)}
        process = subprocess.Popen([str(ROOT / ".venv/bin/python"), "-c", SEED], env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen(base + "/api/health", timeout=1)
                    break
                except OSError:
                    time.sleep(.1)
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, executable_path=os.environ.get("ONECAT_BROWSER_EXECUTABLE") or None)
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                assert context.request.post(base + "/api/auth/setup", data={"password": "disposable-product-password"}).ok
                page = context.new_page()
                errors, starts = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: starts.append(request.url) if request.method == "POST" and request.url.endswith("/generate") else None)
                page.goto(base + "/chat")
                page.get_by_role("button", name="写一个网页", exact=True).click()
                expect(page.locator(".oc-chat-composer textarea")).not_to_have_value("")
                assert not starts
                page.locator(".oc-chat-composer textarea").fill("__product_continuity__")
                page.locator(".oc-chat-composer button[type=submit]").click()
                page.wait_for_url("**/chat?thread=*")
                thread = page.url.split("thread=")[1]
                expect(page.get_by_role("button", name="停止生成", exact=True)).to_be_visible()
                first = None
                for _ in range(40):
                    first = context.request.get(base + f"/api/chat/threads/{thread}").json()["generation"]
                    if first:
                        break
                    page.wait_for_timeout(50)
                assert first["state"] == "running"
                for route in ["models", "performance", "service"]:
                    page.locator(f'a[href="/{route}"]').first.click()
                    assert context.request.get(base + f"/api/chat/threads/{thread}").json()["generation"]["id"] == first["id"]
                other = context.request.post(base + "/api/chat/threads", data={"title": "Another conversation"}).json()
                page.goto(base + "/chat?thread=" + other["id"])
                expect(page.locator(".oc-message-assistant")).to_have_count(0)
                page.goto(base + "/chat?thread=" + thread)
                expect(page.locator(".oc-message-user")).to_contain_text("product_continuity")
                context.set_offline(True)
                page.wait_for_timeout(500)
                context.set_offline(False)
                page.reload()
                expect(page.locator(".oc-message-user")).to_contain_text("product_continuity")
                page.locator(".oc-chat-composer textarea").fill("Draft survives navigation and refresh")
                expect(page.locator(".oc-stream-actions")).to_be_visible()
                toolbar_before = page.locator(".oc-stream-actions").bounding_box()
                page.evaluate("window.productFooter = document.querySelector('.oc-stream-actions')")
                expect(page.get_by_role("button", name="停止生成", exact=True)).to_have_count(0, timeout=30000)
                toolbar_after = page.locator(".oc-stream-actions").bounding_box()
                assert page.evaluate("window.productFooter === document.querySelector('.oc-stream-actions')")
                assert all(abs(toolbar_before[key] - toolbar_after[key]) < 1.5 for key in ("x", "y", "width", "height")), (toolbar_before, toolbar_after)
                final = context.request.get(base + f"/api/chat/threads/{thread}").json()
                assert final["generation"]["id"] == first["id"] and final["generation"]["state"] == "completed", final["generation"]
                assert len(final["messages"]) == 2 and len(starts) == 1
                assert final["messages"][-1]["metadata"]["usage"]["completion_tokens"] == 2048
                page.reload()
                expect(page.locator(".oc-chat-composer textarea")).to_have_value("Draft survives navigation and refresh")
                page.screenshot(path=str(OUT / "background-chat.png"))
                page.locator(".oc-chat-composer textarea").fill("__product_continuity__ cancel")
                page.locator(".oc-chat-composer button[type=submit]").click()
                expect(page.get_by_role("button", name="停止生成", exact=True)).to_be_visible()
                for _ in range(40):
                    if context.request.get(base + f"/api/chat/threads/{thread}").json()["generation"]["state"] == "running":
                        break
                    page.wait_for_timeout(50)
                page.get_by_role("button", name="停止生成", exact=True).click()
                expect(page.get_by_role("button", name="停止生成", exact=True)).to_have_count(0)
                assert context.request.get(base + f"/api/chat/threads/{thread}").json()["generation"]["state"] == "cancelled"
                page.goto(base + "/models")
                expect(page.get_by_role("tab")).to_have_count(3)
                assert page.get_by_role("tab").all_text_contents() == ["发现模型", "已下载", "启动预设"]
                page.get_by_role("tab", name="启动预设", exact=True).click()
                page.locator('a[href="/service"]').first.click()
                page.locator('a[href="/models"]').first.click()
                expect(page.get_by_role("tab", name="启动预设", exact=True)).to_have_attribute("aria-selected", "true")
                page.get_by_role("button", name="新建预设", exact=True).click()
                page.get_by_label("预设名称", exact=True).fill("Saved profile draft")
                expect(page.get_by_role("button", name="保存并启动", exact=True)).to_be_in_viewport()
                page.locator(".oc-dialog-body").evaluate("element=>element.scrollTop=element.scrollHeight")
                expect(page.get_by_role("button", name="保存并启动", exact=True)).to_be_in_viewport()
                page.get_by_role("button", name="取消", exact=True).click()
                page.get_by_role("button", name="新建预设", exact=True).click()
                expect(page.get_by_label("预设名称", exact=True)).to_have_value("Saved profile draft")
                page.screenshot(path=str(OUT / "profile.png"))
                page.get_by_role("button", name="取消", exact=True).click()
                page.get_by_role("tab", name="发现模型", exact=True).click()
                page.screenshot(path=str(OUT / "models.png"))
                page.goto(base + "/service")
                page.get_by_role("button", name="近 3 天", exact=True).click()
                page.get_by_label("请求来源", exact=True).select_option("api")
                expect(page.locator(".oc-history-scope")).to_contain_text("近 3 天")
                expect(page.locator(".oc-launch-stages")).not_to_be_visible()
                page.screenshot(path=str(OUT / "service.png"))
                page.set_viewport_size({"width": 390, "height": 844})
                page.goto(base + "/models")
                page.screenshot(path=str(OUT / "models-mobile.png"))
                page.goto(base + "/chat")
                expect(page.locator(".oc-chat-composer textarea")).to_be_in_viewport()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(OUT / "chat-mobile.png"))
                assert not errors, errors
                browser.close()
                print(json.dumps({"passed": ["navigation continues generation", "switch conversation", "offline/reconnect", "refresh resumes once", "draft restore", "explicit stop", "unified discovery", "preserved tabs", "fixed profile actions", "profile draft", "scoped service filters", "collapsed startup history", "mobile"], "errors": errors}))
        finally:
            process.terminate()
            process.wait(timeout=15)


if __name__ == "__main__":
    run()
