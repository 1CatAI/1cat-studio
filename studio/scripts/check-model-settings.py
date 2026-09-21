#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Verify model settings against disposable storage; never launch a GPU process."""
import ast
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.request

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / ".artifacts/model-settings-browser"
ARTIFACTS.mkdir(parents=True, exist_ok=True)
module = ast.parse((ROOT / "studio/scripts/check-browser.py").read_text())
seed = next(ast.literal_eval(node.value) for node in module.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "SEED" for target in node.targets))
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
base = f"http://127.0.0.1:{port}"

with tempfile.TemporaryDirectory(prefix="onecat-model-settings-") as state:
    model = Path(state) / "model"
    model.mkdir()
    (model / "config.json").write_text('{}')
    seed = seed.replace('/test/model', str(model))
    overrides = '''
from onecat import app as app_module,engine,telemetry
async def idle_monitor():
    await asyncio.Event().wait()
app_module.lifecycle_monitor=idle_monitor
telemetry.monitor=idle_monitor
engine.status=lambda: db.get("engine","active")
db.patch("models","m",{"state":"downloaded"})
db.patch("profiles","p",{"max_model_len":65536,"gpu_memory_utilization":0.88})
'''
    seed = seed.replace('uvicorn.run(create_app()', overrides + '\nuvicorn.run(create_app()')
    env = {**os.environ, "ONECAT_STUDIO_HOME": state, "ONECAT_AUTO_GPU_ACTIONS": "0", "PYTHONPATH": str(ROOT / "studio/backend"), "TEST_PORT": str(port)}
    with (ARTIFACTS / "server.log").open("w") as log:
        server = subprocess.Popen([str(ROOT / ".venv/bin/python"), "-c", seed], env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen(base + "/api/health", timeout=1)
                    break
                except OSError:
                    time.sleep(.1)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, executable_path=os.environ.get("ONECAT_BROWSER_EXECUTABLE") or None, args=["--disable-gpu"])
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                assert context.request.post(base + "/api/auth/setup", data={"password": "settings-fixture-password"}).ok
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))

                def open_settings():
                    page.get_by_role("button", name="选择已下载模型", exact=True).first.click()
                    dialog = page.get_by_role("dialog")
                    expect(dialog.get_by_text("启动选项", exact=True)).to_have_count(0)
                    dialog.get_by_role("button", name="调整", exact=True).click()
                    expect(page.get_by_label("最大上下文 tokens", exact=True)).to_have_value("65536")
                    expect(page.locator(".oc-profile-advanced")).not_to_have_attribute("open", "")
                    page.get_by_text("高级设置", exact=True).click()
                    return page.get_by_role("dialog")

                page.goto(base + "/chat")
                dialog = open_settings()
                slider = dialog.get_by_role("slider", name="显存使用上限", exact=True)
                expect(slider).to_have_value("88")
                slider.fill("83")
                expect(dialog.locator(".oc-memory-limit output")).to_have_text("83%")
                checkboxes = dialog.locator(".oc-gpu-choices").get_by_role("checkbox")
                expect(checkboxes).to_have_count(4)
                checkboxes.nth(1).click()
                checkboxes.nth(3).click()
                expect(dialog.get_by_text("沿用已验证配置", exact=False)).to_have_count(0)
                page.screenshot(path=str(ARTIFACTS / "settings-desktop.png"))
                dialog.get_by_role("button", name="保存", exact=True).click()
                expect(page.get_by_role("dialog").get_by_role("button", name="调整", exact=True)).to_be_visible()
                saved = context.request.get(base + "/api/profiles").json()["items"][0]
                assert saved["gpu_memory_utilization"] == .83, saved
                assert saved["gpu_uuids"] == ["GPU-" + str(i)*36 for i in (0, 2)], saved
                assert saved["tensor_parallel_size"] == 2
                assert context.request.get(base + "/api/inference/models").json()["items"][0]["default_profile_id"] == "p"
                # A new document must read the persisted values, not an unsaved draft.
                page.reload()
                dialog = open_settings()
                expect(dialog.get_by_role("slider", name="显存使用上限", exact=True)).to_have_value("83")
                expect(dialog.locator(".oc-gpu-choices").get_by_role("checkbox").nth(1)).not_to_be_checked()
                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=str(ARTIFACTS / "settings-mobile.png"))
                bounds = dialog.bounding_box()
                assert bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= 390
                assert dialog.evaluate("el => el.scrollWidth <= el.clientWidth"), "Dialog overflows horizontally"
                dialog.get_by_role("button", name="取消", exact=True).click()
                page.screenshot(path=str(ARTIFACTS / "models-mobile.png"))
                assert not errors, errors
                browser.close()
                print(json.dumps({"passed": ["saved values prefilled", "slider persistence", "individual GPU selection and TP", "default model configuration", "mobile layout", "no browser errors"]}))
        finally:
            server.terminate()
            server.wait(timeout=15)
