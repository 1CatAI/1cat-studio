#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""CPU browser checks for old-tab upgrades, preview recovery and WebKit."""

import contextlib
import json
import os
from pathlib import Path
import runpy
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".artifacts/preview-upgrade" / os.environ.get("ONECAT_TEST_BROWSER", "chromium")
OUT.mkdir(parents=True, exist_ok=True)
OLD = Path(os.environ["ONECAT_OLD_FRONTEND"]).resolve()
SEED = runpy.run_path(str(ROOT / "studio/scripts/check-browser.py"))["SEED"]
CODE = """<!doctype html><html><body><h1>今天吃什么</h1><button id="pick" onclick="this.textContent='面条';localStorage.setItem('lunch','面条')">抽取午餐</button><input aria-label="新选项" placeholder="添加一个选项"></body></html>"""
REACT = "import React,{useState} from 'react';import {BarChart,Bar} from 'recharts';export default function App(){const [n,setN]=useState(0);return <div><button onClick={()=>setN(n+1)}>Count {n}</button><BarChart width={240} height={140} data={[{v:2},{v:3}]}><Bar dataKey='v'/></BarChart></div>}"


def launch(pw):
    if os.environ.get("ONECAT_TEST_BROWSER") != "webkit":
        return pw.chromium.launch(
            executable_path=os.environ.get("ONECAT_BROWSER_EXECUTABLE"),
            args=["--disable-gpu", "--use-angle=swiftshader"],
        )
    # CI can use a normal WebKit install; local checks may supply extracted libs.
    base = os.environ.get("ONECAT_WEBKIT_BUNDLE")
    if not base:
        return pw.webkit.launch()
    env = {
        **os.environ,
        "LD_LIBRARY_PATH": f"{base}/lib:{base}/sys/lib:" + os.environ.get("ONECAT_WEBKIT_LIBS", ""),
        "WEBKIT_EXEC_PATH": base + "/bin",
        "WEBKIT_INJECTED_BUNDLE_PATH": base + "/lib",
        "WEBKIT_INSPECTOR_RESOURCES_PATH": base + "/share",
    }
    return pw.webkit.launch(executable_path=base + "/bin/MiniBrowser", env=env)


with tempfile.TemporaryDirectory(prefix="onecat-preview-upgrade-") as directory:
    directory = Path(directory)
    old = directory / "app/releases/old/studio/frontend/dist"
    current = directory / "app/releases/current/studio/frontend/dist"
    shutil.copytree(OLD, old)
    shutil.copytree(ROOT / "studio/frontend/dist", current)
    # Old runtime is not under /assets and remains a stable API across these releases.
    if not (old / "preview").exists():
        shutil.copytree(current / "preview", old / "preview")
    for dist in (old, current):
        (dist.parents[2] / "manifest.json").write_text(
            json.dumps({"format": "onecat-studio-linux-v1"})
        )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    seed = SEED.replace(
        "uvicorn.run(create_app(),",
        """from pathlib import Path
import onecat.app as app_module
selected = [Path(os.environ['OLD_DIST'])]
app_module.frontend_dist=lambda:selected[0]
application=create_app()
@application.post('/fixture/release/{which}')
def change_release(which:str):
    selected[0]=Path(os.environ['OLD_DIST'] if which=='old' else os.environ['NEW_DIST'])
    return {'ok':True}
uvicorn.run(application,""",
    )
    env = {
        **os.environ,
        "ONECAT_STUDIO_HOME": str(directory / "state"),
        "PYTHONPATH": str(ROOT / "studio/backend"),
        "TEST_PORT": str(port),
        "OLD_DIST": str(old),
        "NEW_DIST": str(current),
        "ONECAT_AUTO_GPU_ACTIONS": "0",
    }
    with (OUT / "server.log").open("w") as log:
        proc = subprocess.Popen([sys.executable, "-c", seed], env=env, stdout=log, stderr=log)
        try:
            for _ in range(80):
                with contextlib.suppress(OSError):
                    urllib.request.urlopen(base + "/api/health", timeout=1)
                    break
                time.sleep(0.1)
            with sync_playwright() as pw:
                browser = launch(pw)
                context = browser.new_context(viewport={"width": 1440, "height": 950})
                assert context.request.post(
                    base + "/api/auth/setup", data={"password": "preview-fixture-only"}
                ).ok

                def thread(code):
                    r = context.request.post(
                        base + "/api/chat/import",
                        data={
                            "title": "Preview upgrade test",
                            "messages": [
                                {"role": "user", "content": "做个午餐抽取页面"},
                                {"role": "assistant", "content": code},
                            ],
                        },
                    )
                    assert r.ok, r.text()
                    return r.json()["id"]

                identity = thread("```html\n" + CODE + "\n```")
                before = context.new_page()
                before.goto(base + "/chat?thread=" + identity)
                expect(before.get_by_role("button", name="预览 / 运行").first).to_be_visible()
                # Reproduce the formerly served HTML-for-JS error, matching production evidence.
                before.route(
                    "**/assets/preview-panel-*.js",
                    lambda r: r.fulfill(body="<html>Studio shell</html>", content_type="text/html"),
                )
                before.get_by_role("button", name="预览 / 运行").first.click()
                expect(before.locator(".oc-preview-failed")).to_be_visible(timeout=15000)
                before.screenshot(path=str(OUT / "reproduced-old-tab.png"))
                before.close()
                tab = context.new_page()
                errors = []
                tab.on("pageerror", lambda e: errors.append(str(e) + "\n" + (e.stack or "")))
                tab.goto(base + "/chat?thread=" + identity)
                expect(tab.get_by_role("button", name="预览 / 运行").first).to_be_visible()
                context.request.post(base + "/fixture/release/new")
                tab.get_by_role("button", name="预览 / 运行").first.click()
                frame = tab.frame_locator("iframe:not(.oc-preview-candidate)")
                expect(frame.get_by_role("heading", name="今天吃什么")).to_be_visible(timeout=20000)
                frame.get_by_role("button", name="抽取午餐").click()
                expect(frame.get_by_role("button", name="面条")).to_be_visible()
                tab.screenshot(path=str(OUT / "old-tab-after-upgrade.png"))
                tab.close()
                # Fresh page has an eager shell and a retryable lazy runtime fetch.
                page = context.new_page()
                page.on("pageerror", lambda e: errors.append(str(e) + "\n" + (e.stack or "")))
                page.route(
                    "**/preview/runtime.js",
                    lambda r: r.fulfill(
                        body="<html>missing runtime</html>", content_type="text/html"
                    ),
                )
                page.goto(base + "/chat?thread=" + identity)
                page.locator(".oc-chat-composer textarea").fill("Keep my draft")
                page.get_by_role("button", name="预览 / 运行").first.click()
                expect(page.locator(".oc-preview-error")).to_contain_text(
                    "Preview runtime is unavailable"
                )
                expect(page.locator(".oc-preview-failed")).to_have_count(0)
                page.unroute("**/preview/runtime.js")
                page.get_by_role("button", name="重新运行", exact=True).click()
                frame = page.frame_locator("iframe:not(.oc-preview-candidate)")
                expect(frame.get_by_role("heading", name="今天吃什么")).to_be_visible(timeout=20000)
                page.get_by_role("button", name="全屏", exact=True).click()
                expect(page.locator(".oc-preview-full")).to_be_visible()
                page.keyboard.press("Escape")
                expect(frame.get_by_role("button", name="抽取午餐")).to_be_visible()
                expect(page.locator(".oc-chat-composer textarea")).to_have_value("Keep my draft")
                page.evaluate("""window.originalPreviewAnimate=HTMLElement.prototype.animate;
                  HTMLElement.prototype.animate=function(...args){if(this.matches('.oc-preview-panel') && window.failPreviewAnimation)throw Error('preview animation fixture');return originalPreviewAnimate.apply(this,args)};
                  window.failPreviewAnimation=true;""")
                page.get_by_role("button", name="全屏", exact=True).click()
                expect(page.get_by_role("button", name="重试预览", exact=True)).to_be_visible()
                page.evaluate("window.failPreviewAnimation=false")
                page.get_by_role("button", name="重试预览", exact=True).click()
                expect(frame.get_by_role("heading", name="今天吃什么")).to_be_visible(timeout=20000)
                for _ in range(3):
                    page.get_by_role("button", name="关闭预览", exact=True).click()
                    expect(page.locator("iframe")).to_have_count(0)
                    page.get_by_role("button", name="预览 / 运行").first.click()
                    expect(frame.get_by_role("heading", name="今天吃什么")).to_be_visible()
                # Streaming candidates preserve the previous successful result.
                data = context.request.get(base + "/api/chat/threads/" + identity).json()

                def replace(text):
                    data["messages"][-1]["content"] = [{"type": "text", "text": text}]
                    assert context.request.put(
                        base + f"/api/chat/threads/{identity}/messages",
                        data={"messages": data["messages"]},
                    ).ok
                    page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")

                replace("```html\n<h1>Unfinished</h1><script>const x =")
                expect(page.locator(".oc-preview-status")).to_contain_text(
                    "代码不完整", timeout=15000
                )
                expect(frame.get_by_role("heading", name="今天吃什么")).to_be_visible()
                replace("```tsx\n" + REACT + "\n```")
                expect(frame.get_by_role("button", name="Count 0")).to_be_visible(timeout=20000)
                frame.get_by_role("button", name="Count 0").click()
                expect(frame.get_by_role("button", name="Count 1")).to_be_visible()
                expect(frame.locator(".recharts-wrapper")).to_be_visible()
                for width in (1440, 390):
                    page.set_viewport_size({"width": width, "height": 950})
                    page.wait_for_timeout(250)
                    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                    page.screenshot(path=str(OUT / f"preview-{width}.png"))
                assert not errors, errors
                result = {
                    "browser": os.environ.get("ONECAT_TEST_BROWSER", "chromium"),
                    "old_failure_reproduced": True,
                    "old_tab_survives_upgrade": True,
                    "runtime_retry": True,
                    "html_react_chart": True,
                    "incomplete_keeps_previous": True,
                    "close_fullscreen_draft": True,
                    "errors": errors,
                }
                (OUT / "result.json").write_text(json.dumps(result, indent=2))
                print(json.dumps(result))
                browser.close()
        finally:
            proc.terminate()
            proc.wait(timeout=10)
