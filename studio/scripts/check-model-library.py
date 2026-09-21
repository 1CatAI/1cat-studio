#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Model provenance, reader and filters against disposable CPU-only fixtures."""
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
OUT = ROOT / ".artifacts/model-library-browser"
OUT.mkdir(parents=True, exist_ok=True)
tree = ast.parse((ROOT / "studio/scripts/check-browser.py").read_text())
seed = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "SEED" for target in node.targets))
extra = r'''
from onecat import model_cards, engine, app as app_module, telemetry
async def idle_monitor():
    await asyncio.Event().wait()
app_module.lifecycle_monitor=idle_monitor
telemetry.monitor=idle_monitor
engine.status=lambda:db.get("engine","active")
def metadata(endpoint, repo):
    owner,name=repo.split('/')
    return {'Path':owner,'Name':name,'Description':'A model card from the actual checkpoint publisher, with quantization and usage details.',
      'ReadMeContent':'# Publisher model card\n\nThis checkpoint is published by **'+owner+'**.\n\n[Model paper](https://example.test/paper)\n\n[Related file](docs/usage.md)\n\n![diagram](assets/diagram.svg)\n\n[unsafe](javascript:alert(1))\n\n<script>window.modelCardInjected=true</script>',
      'BaseModel':['Qwen/Qwen3.8-27B'],'License':'apache-2.0','Downloads':12345,'Stars':23,
      'LastUpdatedTime':1789000000,'Tags':['nvfp4','reasoning']}
model_cards.fetch_metadata=metadata
'''
seed = seed.replace('uvicorn.run(create_app()', extra + '\nuvicorn.run(create_app()')
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
base = f"http://127.0.0.1:{port}"
with tempfile.TemporaryDirectory(prefix="onecat-library-") as state, (OUT / "server.log").open("w") as log:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "studio/backend"), "ONECAT_STUDIO_HOME": state, "ONECAT_AUTO_GPU_ACTIONS": "0", "TEST_PORT": str(port)}
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
            context.route("https://modelscope.cn/api/v1/models/**", lambda route: route.fulfill(content_type="image/svg+xml", body='<svg xmlns="http://www.w3.org/2000/svg" width="30" height="30"><rect width="30" height="30" fill="orange"/></svg>'))
            assert context.request.post(base + "/api/auth/setup", data={"password": "library-fixture-password"}).ok
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base + "/models")
            page.get_by_role("button", name="全部支持", exact=True).click()
            cards = page.locator(".oc-support-card")
            expect(cards).to_have_count(9)
            expect(cards.first.locator(".oc-model-description")).to_contain_text("actual checkpoint publisher")
            expect(cards.first.locator(".oc-model-publisher")).to_contain_text("QUASAR-QAT/")
            page.screenshot(path=str(OUT / "library-desktop.png"))
            page.get_by_label("模型发布者", exact=True).select_option("unsloth")
            expect(cards).to_have_count(1)
            expect(cards.first.locator(".oc-model-publisher")).to_contain_text("unsloth/Qwen3.8-27B-NVFP4")
            expect(cards.first).to_contain_text("NVFP4 / FP8")
            page.get_by_label("模型发布者", exact=True).select_option("QuantTrio")
            expect(cards).to_have_count(3)
            assert all("QuantTrio/" in card.inner_text() for card in cards.all())
            page.reload()
            expect(page.get_by_label("模型发布者", exact=True)).to_have_value("QuantTrio")
            page.get_by_label("模型发布者", exact=True).select_option("")
            search = page.get_by_placeholder("搜索模型、发布者或量化…")
            search.fill("QUASAR-QAT/Qwen3.8")
            expect(cards).to_have_count(1)
            cards.first.locator(".oc-model-name-button").click()
            dialog = page.get_by_role("dialog")
            expect(dialog.locator(".oc-model-readme")).to_contain_text("Publisher model card")
            expect(dialog.locator(".oc-model-base")).to_contain_text("Qwen/Qwen3.8-27B")
            expect(dialog.locator(".oc-model-facts")).to_contain_text("apache-2.0")
            expect(dialog.locator(".oc-model-stats")).to_contain_text("12,345")
            expect(dialog.get_by_role("link", name="ModelScope", exact=True)).to_have_attribute("href", "https://modelscope.cn/models/QUASAR-QAT/Qwen3.8-27B-QUASAR-NVFP4")
            (OUT / "reader.html").write_text(dialog.inner_html())
            expect(dialog.get_by_role("link", name="Related file", exact=True)).to_have_attribute("href", "https://modelscope.cn/api/v1/models/QUASAR-QAT/Qwen3.8-27B-QUASAR-NVFP4/repo?Revision=master&FilePath=docs%2Fusage.md")
            assert not page.evaluate("window.modelCardInjected")
            assert dialog.locator('a[href^="javascript:"]').count() == 0
            page.screenshot(path=str(OUT / "model-card-desktop.png"))
            page.set_viewport_size({"width": 390, "height": 844})
            assert dialog.evaluate("el => el.scrollWidth <= el.clientWidth")
            page.screenshot(path=str(OUT / "model-card-mobile.png"))
            page.keyboard.press("Escape")
            expect(dialog).to_have_count(0)
            expect(cards.first.locator(".oc-model-name-button")).to_be_focused()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            # A failed fetch keeps the source link and does not break download controls.
            page.route("**/api/models/card?**", lambda route: route.fulfill(json={"repo_id":"QUASAR-QAT/Qwen3.8-27B-QUASAR-NVFP4","publisher":"QUASAR-QAT","endpoint":"https://modelscope.cn","source_url":"https://modelscope.cn/models/QUASAR-QAT/Qwen3.8-27B-QUASAR-NVFP4","description":"","readme":"","base_models":[],"tags":[],"license":None,"downloads":None,"likes":None,"updated_at":None,"status":"unavailable"}))
            cards.first.locator(".oc-model-name-button").click()
            expect(dialog).to_contain_text("暂时无法读取模型介绍")
            expect(dialog.get_by_role("link", name="ModelScope", exact=True)).to_be_visible()
            page.keyboard.press("Escape")
            expect(cards.first.get_by_role("button", name="ModelScope 下载", exact=True)).to_be_enabled()
            assert not errors, errors
            browser.close()
            print(json.dumps({"passed":["publisher identity", "publisher filter persistence", "full repository search", "model card and base author", "source link", "relative README links", "sanitized Markdown", "mobile layout", "focus restoration", "unavailable metadata fallback"]}))
    finally:
        server.terminate()
        server.wait(timeout=15)
