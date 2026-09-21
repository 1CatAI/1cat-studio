#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""CPU browser regressions for readiness, drafts and unobstructed canvas controls."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
fixture = runpy.run_path(str(ROOT / "studio/scripts/check-browser.py"))
BASE, PORT, SEED = fixture["BASE"], fixture["PORT"], fixture["SEED"]
OUT = ROOT / ".artifacts/readiness-browser"
OUT.mkdir(parents=True, exist_ok=True)

with tempfile.TemporaryDirectory(prefix="onecat-readiness-") as state, (OUT / "server.log").open("w") as log:
    process = subprocess.Popen([str(ROOT / ".venv/bin/python"), "-c", SEED], env={**os.environ,
        "ONECAT_STUDIO_HOME": state, "ONECAT_AUTO_GPU_ACTIONS": "0", "PYTHONPATH": str(ROOT / "studio/backend"), "TEST_PORT": str(PORT)}, stdout=log, stderr=subprocess.STDOUT)
    try:
        for _ in range(100):
            try: urllib.request.urlopen(BASE + "/api/health", timeout=1); break
            except OSError: time.sleep(.1)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, executable_path=os.environ.get("ONECAT_BROWSER_EXECUTABLE") or None, args=["--disable-gpu", "--use-angle=swiftshader"])
            context = browser.new_context(viewport={"width":1440,"height":1000})
            page=context.new_page(); errors=[]
            page.on("pageerror", lambda e: errors.append(str(e)))
            assert context.request.post(BASE+"/api/auth/setup",data={"password":"test-only-password"}).ok
            page.goto(BASE+"/chat")
            page.evaluate('localStorage.setItem("onecat_theme", "light")');page.reload()
            engine=context.request.get(BASE+"/api/inference/status").json()
            engine.update(state="failed", phase="failed", error="Engine process exited; inspect the startup log and retry", actions={"retry":True})
            page.route("**/api/inference/status",lambda r:r.fulfill(json=engine))
            page.goto(BASE+"/chat")
            draft=page.get_by_role("textbox",name="消息草稿")
            expect(draft).to_be_enabled(); draft.fill("保留这份尚未发送的草稿")
            draft.press("Enter");expect(draft).to_have_value("保留这份尚未发送的草稿")
            expect(page.locator(".oc-launch-banner")).to_have_count(0)
            page.reload();expect(draft).to_have_value("保留这份尚未发送的草稿")
            page.screenshot(path=str(OUT/"01-chat.png"))
            page.goto(BASE+"/service")
            expect(page.locator(".oc-service-failure")).to_have_count(1)
            expect(page.get_by_text("Engine process exited; inspect the startup log and retry",exact=True)).not_to_be_visible()
            page.get_by_text("错误详情",exact=True).click()
            expect(page.get_by_text("Engine process exited; inspect the startup log and retry",exact=True)).to_be_visible()
            page.wait_for_timeout(250)
            page.screenshot(path=str(OUT/"02-service.png"))
            support=context.request.get(BASE+"/api/models/supported").json()
            for item in support["items"]:
                item.update(compatible=False,reasons=["需要 / Requires 8 × V100 32 GB"])
            page.route("**/api/models/supported",lambda r:r.fulfill(json=support))
            page.goto(BASE+"/models")
            expect(page.get_by_text("当前硬件暂未匹配已验证配置",exact=True)).to_be_visible()
            page.get_by_role("button",name="查看全部支持配置",exact=True).click()
            expect(page.locator(".oc-support-card").first).to_be_visible()
            page.get_by_role("tab",name="启动预设",exact=True).click()
            expect(page.get_by_text("导出",exact=True)).not_to_be_visible()
            page.get_by_role("button",name="更多预设操作").first.click()
            expect(page.get_by_text("导出",exact=True)).to_be_visible();page.keyboard.press("Escape")
            page.goto(BASE+"/setup")
            expect(page.locator(".oc-setup-details")).not_to_have_attribute("open", "")
            expect(page.get_by_text("设备详情与可选组件",exact=True)).to_be_visible()
            page.goto(BASE+"/agent")
            expect(page.locator(".oc-agent-readiness")).to_be_visible()
            page.screenshot(path=str(OUT/"03-agent.png"))
            page.get_by_role("textbox",name="Agent 任务",exact=True).fill("项目创建前的任务草稿")
            page.locator(".oc-agent-readiness").get_by_role("button",name="创建项目",exact=True).click()
            page.locator(".oc-dialog input").first.fill("Draft project")
            page.get_by_role("dialog").get_by_role("button",name="创建项目",exact=True).click()
            expect(page.get_by_role("textbox",name="Agent 任务",exact=True)).to_have_value("项目创建前的任务草稿")
            page.reload()
            expect(page.get_by_role("textbox",name="Agent 任务",exact=True)).to_have_value("项目创建前的任务草稿")
            project=context.request.post(BASE+"/api/creative/projects",data={"title":"Readiness canvas","nodes":[{"id":"gen","kind":"generate","x":0,"y":0}],"viewport":{"x":0,"y":0,"k":1}}).json()
            page.goto(BASE+"/canvas?project="+project["id"])
            expect(page.locator(".oc-canvas-toolbar")).to_be_visible()
            toolbar=page.locator(".oc-canvas-toolbar").bounding_box(); viewport=page.locator(".oc-canvas-viewport").bounding_box()
            assert toolbar["y"]+toolbar["height"] <= viewport["y"], (toolbar,viewport)
            page.get_by_role("button",name="创作模型",exact=True).click()
            expect(page.get_by_text("H3 启动预设",exact=True)).to_be_visible()
            expect(page.locator(".oc-h3-preset")).to_have_count(4)
            page.screenshot(path=str(OUT/"04-h3-presets.png"));page.keyboard.press("Escape")
            for width in [320,390,768,1280,1440]:
                page.set_viewport_size({"width":width,"height":900});page.goto(BASE+"/chat")
                expect(draft).to_have_value("保留这份尚未发送的草稿")
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),width
            page.set_viewport_size({"width":390,"height":844})
            page.screenshot(path=str(OUT/"05-mobile.png"))
            assert not errors,errors
            (OUT/"result.json").write_text(json.dumps({"errors":errors,"widths":[320,390,768,1280,1440],"checks":"draft persistence, readiness, one failure, empty filter recovery, profile menu, optional setup, H3 recipes, canvas toolbar separation"},ensure_ascii=False,indent=2))
            browser.close()
    finally:
        process.terminate();process.wait(timeout=15)
print("Readiness browser checks passed")
