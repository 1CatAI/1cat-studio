#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Model provenance and native thinking controls on isolated storage, without GPU work."""

import ast
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".artifacts/model-controls-browser"
module = ast.parse((ROOT / "studio/scripts/check-browser.py").read_text())
SEED = next(
    ast.literal_eval(node.value)
    for node in module.body
    if isinstance(node, ast.Assign)
    and any(isinstance(t, ast.Name) and t.id == "SEED" for t in node.targets)
)
EXTRA = r"""
from pathlib import Path
from onecat import app as app_module,engine,telemetry
async def idle(): await asyncio.Event().wait()
app_module.lifecycle_monitor=idle
telemetry.monitor=idle
engine.status=lambda: db.get('engine','active')
for item in db.all_records('models'): db.delete('models',item['id'])
for item in db.all_records('profiles'): db.delete('profiles',item['id'])
for id,publisher,source in [('a','unsloth','modelscope'),('b','QUASAR-QAT','modelscope'),('c','QUASAR-QAT','local')]:
    path=Path(os.environ['ONECAT_STUDIO_HOME'])/id;path.mkdir()
    (path/'config.json').write_text('{}')
    (path/'chat_template.jinja').write_text("{% if enable_thinking %}{% if resolved_reasoning_effort not in ('xhigh', 'medium', 'low') %}{{ reasoning_effort }}{% endif %}{% endif %}")
    db.put('models',id,{'id':id,'name':'Qwen3.8-27B-NVFP4','path':str(path),'repo_id':publisher+'/Qwen3.8-27B-NVFP4','source':source,'state':'downloaded'})
    db.put('profiles',id,{**p,'id':id,'name':'custom preset','model_path':str(path)})
db.put('engine','active',{'state':'ready','profile_id':'a','profile':db.get('profiles','a')})
"""


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    seed = SEED.replace("uvicorn.run(create_app()", EXTRA + "\nuvicorn.run(create_app()")
    with (
        tempfile.TemporaryDirectory(prefix="studio-controls-") as state,
        (OUT / "server.log").open("w") as log,
    ):
        process = subprocess.Popen(
            [str(ROOT / ".venv/bin/python"), "-c", seed],
            stdout=log,
            stderr=subprocess.STDOUT,
            env={
                **os.environ,
                "ONECAT_STUDIO_HOME": state,
                "ONECAT_AUTO_GPU_ACTIONS": "0",
                "PYTHONPATH": str(ROOT / "studio/backend"),
                "TEST_PORT": str(port),
            },
        )
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen(base + "/api/health", timeout=1)
                    break
                except OSError:
                    time.sleep(0.1)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(
                    headless=True,
                    executable_path=os.environ.get("ONECAT_BROWSER_EXECUTABLE"),
                    args=["--disable-gpu"],
                )
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                context.add_init_script("localStorage.setItem('onecat_theme','dark')")
                assert context.request.post(
                    base + "/api/auth/setup", data={"password": "controls-fixture-password"}
                ).ok
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(base + "/chat")
                picker = page.get_by_role("button", name="选择已下载模型", exact=True).first
                expect(picker).to_contain_text("Unsloth")
                expect(picker).to_contain_text("NVFP4")
                picker.click()
                expect(page.locator(".oc-model-choice-source")).to_have_count(3)
                assert sorted(
                    page.locator(".oc-model-choice-source").all_text_contents()
                ) == sorted(["Unsloth", "QUASAR-QAT", "QUASAR-QAT · 本地导入"])
                page.screenshot(path=str(OUT / "publishers.png"))
                page.get_by_label("搜索已下载模型").fill("unsloth")
                expect(page.locator(".oc-model-choice")).to_have_count(1)
                page.keyboard.press("Escape")
                think = page.get_by_role("button", name="深度思考", exact=True)
                think.click()
                slider = page.get_by_role("slider", name="思考强度")
                expect(slider).to_have_attribute("aria-valuetext", "关闭")
                slider.fill("1")
                expect(think).to_contain_text("思考 · 低")
                slider.press("ArrowRight")
                expect(slider).to_have_attribute("aria-valuetext", "中")
                page.screenshot(path=str(OUT / "thinking-desktop.png"))
                page.keyboard.press("Escape")
                page.reload()
                expect(think).to_contain_text("思考 · 中")
                page.set_viewport_size({"width": 390, "height": 844})
                think.click()
                slider = page.get_by_role("slider", name="思考强度")
                slider.fill("3")
                expect(slider).to_have_attribute("aria-valuetext", "极高")
                popup = page.locator(".oc-thinking-popover")
                bounds = popup.bounding_box()
                assert bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= 390
                page.screenshot(path=str(OUT / "thinking-mobile.png"))
                page.keyboard.press("Escape")
                draft = page.get_by_label("消息草稿", exact=True)
                draft.fill("test strength")
                with page.expect_request(
                    lambda r: "/generate" in r.url and r.method == "POST"
                ) as request:
                    page.get_by_role("button", name="发送消息", exact=True).click()
                body = request.value.post_data_json
                assert body["settings"]["thinking_effort"] == "xhigh"
                assert body["request"]["chat_template_kwargs"] == {
                    "enable_thinking": True,
                    "reasoning_effort": "xhigh",
                }
                expect(draft).to_have_value("", timeout=20000)
                # Shared Agent control also exposes only the deployed native levels.
                page.goto(base + "/agent")
                think = page.get_by_role("button", name="深度思考", exact=True)
                expect(think).to_be_enabled()
                think.click()
                slider = page.get_by_role("slider", name="思考强度")
                slider.fill("1")
                expect(think).to_contain_text("思考 · 低")
                page.keyboard.press("Escape")
                page.reload()
                expect(think).to_contain_text("思考 · 低")
                assert not errors, errors
                browser.close()
                print(
                    json.dumps(
                        {
                            "passed": [
                                "publisher labels and publisher search",
                                "active model identity",
                                "native strength slider",
                                "keyboard and mobile layout",
                                "chat payload and persistence",
                                "Agent shared control persistence",
                                "no browser errors",
                            ]
                        }
                    )
                )
        finally:
            process.terminate()
            process.wait(timeout=15)


if __name__ == "__main__":
    main()
