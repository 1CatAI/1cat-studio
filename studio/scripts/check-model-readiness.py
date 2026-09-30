#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Model switching and interrupted status reads on disposable CPU-only storage."""
import json
import os
from pathlib import Path
import runpy
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import Error as PlaywrightError, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".artifacts/model-readiness-browser"
fixture = runpy.run_path(str(ROOT / "studio/scripts/check-model-controls.py"))
extra = fixture["EXTRA"] + """
for item in db.all_records('models'): db.patch('models',item['id'],{'name':'Audit '+item['id']})
db.put('profiles','a-agent',{**db.get('profiles','a'),'id':'a-agent','name':'Agent recipe','tool_calling':True})
"""
SEED = fixture["SEED"].replace("uvicorn.run(create_app()", extra + "\nuvicorn.run(create_app()")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    passed, failures, errors = [], [], []
    with tempfile.TemporaryDirectory(prefix="onecat-model-readiness-") as state, (OUT / "server.log").open("w") as log:
        server = subprocess.Popen([sys.executable, "-c", SEED], stdout=log, stderr=subprocess.STDOUT, env={
            **os.environ, "ONECAT_STUDIO_HOME": state, "ONECAT_AUTO_GPU_ACTIONS": "0",
            "PYTHONPATH": str(ROOT / "studio/backend"), "ONECAT_FRONTEND_DIST": str(ROOT / "studio/frontend/dist"), "TEST_PORT": str(port),
        })
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen(base + "/api/health", timeout=1).close()
                    break
                except OSError:
                    if server.poll() is not None:
                        raise RuntimeError(log.name)
                    time.sleep(.1)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, executable_path=os.environ.get("ONECAT_BROWSER_EXECUTABLE") or None)
                context = browser.new_context(viewport={"width": 1440, "height": 960}, reduced_motion="reduce")
                assert context.request.post(base + "/api/auth/setup", data={"password": "readiness-fixture-password"}).ok
                original = context.request.get(base + "/api/inference/status").json()
                choices = context.request.get(base + "/api/inference/models").json()
                profiles = {p["id"]: p for p in context.request.get(base + "/api/profiles").json()["items"]}
                control = {"engine": original, "network": "ok", "jobs_error": False, "job": None, "choice_reads": 0, "held": []}

                def engine_status(route):
                    if control["network"] == "stall":
                        control["held"].append(route)
                    elif control["network"] == "error":
                        route.fulfill(status=503, json={"detail": "Status service temporarily unavailable"})
                    else:
                        route.fulfill(json=control["engine"])

                def model_choices(route):
                    if "agent=true" in route.request.url:
                        route.continue_()
                        return
                    control["choice_reads"] += 1
                    route.fulfill(json={**choices, "items": [{**m, "active": m["id"] == control["engine"]["profile_id"]} for m in choices["items"]]})

                def launch(route):
                    control["last_launch"] = route.request.post_data_json
                    control["job"] = {"id": "fixture-launch", "kind": "start_model", "state": "queued", "stage": "queued", "progress": 0}
                    route.fulfill(json=control["job"])

                context.route("**/api/inference/status", engine_status)
                context.route("**/api/inference/models?*", model_choices)
                context.route("**/api/inference/load-model", launch)
                context.route("**/api/jobs", lambda route: route.fulfill(status=503, json={"detail": "Launch progress temporarily unavailable"}) if control["jobs_error"] else route.fulfill(json={"items": [control["job"]] if control["job"] else []}))
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))

                def check(name, action):
                    try:
                        action()
                        passed.append(name)
                        print("PASS", name, flush=True)
                    except Exception as error:
                        failures.append({"check": name, "error": str(error)[:1500]})
                        page.screenshot(path=str(OUT / (name + ".png")), full_page=True)
                        print("FAIL", name, type(error).__name__, flush=True)

                def agent_recipe():
                    page.goto(base + "/agent")
                    page.get_by_role("button", name="选择已下载模型", exact=True).first.click()
                    row = page.locator(".oc-model-choice").filter(has=page.get_by_text("Audit a", exact=True))
                    expect(row.get_by_role("button", name="使用", exact=True)).to_be_enabled()
                    with page.expect_response(lambda response: response.url.endswith("/api/inference/load-model")):
                        row.get_by_role("button", name="使用", exact=True).click()
                    assert control["last_launch"] == {"model_id": "a", "agent": True}
                    control["job"].update(state="cancelled")
                    expect(page.locator(".oc-model-job")).to_contain_text("已取消", timeout=6000)

                check("agent-can-activate-tool-recipe", agent_recipe)
                page.keyboard.press("Escape")

                def switched_model():
                    page.goto(base + "/chat")
                    page.get_by_role("button", name="选择已下载模型", exact=True).first.click()
                    row = page.locator(".oc-model-choice").filter(has=page.get_by_text("Audit b", exact=True))
                    expect(row).to_be_visible()
                    before = control["choice_reads"]
                    row.get_by_role("button", name="使用", exact=True).click()
                    expect(page.locator(".oc-model-job")).to_be_visible()
                    page.wait_for_function("document.querySelector('.oc-model-job') != null")
                    # Let the submission's refresh finish while the old model is still ready.
                    for _ in range(20):
                        if control["choice_reads"] > before:
                            break
                        page.wait_for_timeout(50)
                    assert control["choice_reads"] > before
                    control["engine"] = {**original, "profile_id": "b", "profile": profiles["b"], "model": {"name": "Audit b"}}
                    control["job"].update(state="completed", stage="completed")
                    expect(page.locator(".oc-model-job")).to_contain_text("模型已就绪", timeout=7000)
                    expect(row.get_by_role("button", name="使用中", exact=True)).to_be_disabled(timeout=7000)
                    other = page.locator(".oc-model-choice").filter(has=page.get_by_text("Audit a", exact=True))
                    expect(other.get_by_role("button", name="使用", exact=True)).to_be_enabled()

                check("model-switch-active-label", switched_model)
                page.keyboard.press("Escape")

                def launch_progress_failure():
                    page.get_by_role("button", name="选择已下载模型", exact=True).first.click()
                    row = page.locator(".oc-model-choice").filter(has=page.get_by_text("Audit c", exact=True))
                    row.get_by_role("button", name="使用", exact=True).click()
                    control["jobs_error"] = True
                    expect(page.get_by_role("dialog").get_by_role("alert")).to_be_visible(timeout=6000)
                    expect(page.locator(".oc-model-job")).to_contain_text("恢复", timeout=6000)
                    control["jobs_error"] = False
                    control["job"].update(state="cancelled")
                    expect(page.locator(".oc-model-job")).to_contain_text("已取消", timeout=6000)
                    expect(page.get_by_role("dialog").get_by_role("alert")).to_have_count(0)

                check("launch-progress-failure-recovery", launch_progress_failure)
                control["jobs_error"] = False
                page.keyboard.press("Escape")
                page.goto(base + "/chat")
                draft = page.get_by_role("textbox", name="消息草稿", exact=True)
                draft.fill("断线后仍要保留的草稿")
                send = page.get_by_role("button", name="发送消息", exact=True)
                expect(send).to_be_enabled()

                def interrupted_status():
                    control["network"] = "error"
                    page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
                    expect(page.locator(".oc-header-engine-state")).not_to_contain_text("就绪", timeout=6000)
                    expect(send).to_be_disabled()
                    expect(draft).to_have_value("断线后仍要保留的草稿")

                check("failed-status-never-ready", interrupted_status)
                control["network"] = "ok"
                page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
                expect(page.locator(".oc-header-engine-state")).to_have_text("就绪", timeout=6000)

                def stalled_status():
                    control["network"] = "stall"
                    page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
                    expect(page.locator(".oc-header-engine-state")).not_to_contain_text("就绪", timeout=19000)
                    expect(send).to_be_disabled()
                    expect(draft).to_have_value("断线后仍要保留的草稿")
                    control["network"] = "ok"
                    page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
                    expect(send).to_be_enabled(timeout=6000)
                    expect(draft).to_have_value("断线后仍要保留的草稿")

                check("stalled-status-timeout-recovery", stalled_status)
                control["network"] = "ok"
                page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
                expect(send).to_be_enabled(timeout=6000)
                page.screenshot(path=str(OUT / "recovered-chat.png"))
                for width in (320, 390, 768):
                    page.set_viewport_size({"width": width, "height": 960})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), width

                def service_status():
                    page.goto(base + "/service")
                    control["network"] = "error"
                    page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
                    expect(page.locator(".oc-header-engine-state")).to_have_text("连接中断", timeout=6000)
                    expect(page.locator(".oc-status-good")).to_have_count(0)
                    expect(page.get_by_text("模型就绪", exact=True)).not_to_be_visible()

                check("service-stale-readiness-hidden", service_status)
                for route in control["held"]:
                    try:
                        route.abort("timedout")
                    except PlaywrightError:
                        pass  # The timed-out request may already be closed.
                context.unroute_all(behavior="ignoreErrors")
                browser.close()
        finally:
            server.terminate()
            server.wait(timeout=15)
    result = {"passed": passed, "failures": failures, "browser_errors": errors}
    (OUT / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    assert not failures and not errors, result


if __name__ == "__main__":
    main()
