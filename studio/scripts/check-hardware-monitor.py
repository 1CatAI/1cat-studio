#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""CPU-only browser regression: all hardware stays visible across model changes."""
import importlib.util
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

spec = importlib.util.spec_from_file_location(
    "browser_fixture", Path(__file__).with_name("check-browser.py")
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
seed = fixture.SEED.replace(
    'gpu.snapshot=lambda:',
    'devices.append({**devices[0], "index":4, "uuid":"GPU-display", '
    '"name":"Quadro P400", "power_w":None, "power_limit_w":None, '
    '"memory_total_mib":2048, "memory_used_mib":264})\ngpu.snapshot=lambda:',
).replace(
    '{"gpus":devices,"timestamp":', '{"available":True,"gpus":devices,"timestamp":'
).replace(
    'gpu_uuids=[d["uuid"] for d in devices],tensor_parallel_size=4',
    'gpu_uuids=[devices[0]["uuid"]],tensor_parallel_size=1',
)


def run():
    with tempfile.TemporaryDirectory(prefix="onecat-hardware-monitor-") as state:
        env = {
            **os.environ,
            "ONECAT_STUDIO_HOME": state,
            "PYTHONPATH": str(fixture.ROOT / "studio/backend"),
            "TEST_PORT": str(fixture.PORT),
        }
        with (fixture.ARTIFACTS / "hardware-server.log").open("w") as log:
            server = subprocess.Popen(
                [str(fixture.ROOT / ".venv/bin/python"), "-c", seed],
                env=env, stdout=log, stderr=subprocess.STDOUT,
            )
            try:
                for _ in range(100):
                    try:
                        urllib.request.urlopen(fixture.BASE + "/api/health", timeout=1)
                        break
                    except OSError:
                        time.sleep(.1)
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=True)
                    context = browser.new_context(viewport={"width": 1500, "height": 1100})
                    assert context.request.post(
                        fixture.BASE + "/api/auth/setup", data={"password": "monitor-fixture-password"}
                    ).ok
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(fixture.BASE + "/performance")
                    monitor = page.locator(".oc-telemetry")
                    cards = monitor.locator(".oc-gpu-card")
                    expect(cards).to_have_count(5)
                    expect(monitor.locator(".oc-gpu-model-badge")).to_have_count(1)
                    expect(cards.first).to_contain_text("当前模型使用")
                    expect(monitor.get_by_test_id("live-power")).to_have_text("200 W")
                    expect(monitor.get_by_test_id("power-coverage")).to_contain_text("4/5 GPU")
                    expect(monitor.locator(".oc-telemetry-stats")).to_contain_text("130 GiB")
                    expect(cards.last).to_contain_text("此显卡未提供功率读数")
                    initial = context.request.get(fixture.BASE + "/api/gpu/live").json()
                    assert len(initial["gpus"]) == 5
                    assert initial["history"][-1]["power_w"] is None
                    assert initial["history"][-1]["measured_power_w"] == 200
                    page.evaluate("window.originalGPUCards=[...document.querySelectorAll('.oc-gpu-card')]")

                    # Change only the disposable instance's assignment, without launching anything.
                    def assign(model_state, ids):
                        subprocess.run(
                            [str(fixture.ROOT / ".venv/bin/python"), "-c",
                             "from onecat import db; state=db.get('engine','active'); "
                             f"state['state']={model_state!r}; state['profile']['gpu_uuids']={ids!r}; "
                             "db.put('engine','active',state)"],
                            env=env, check=True,
                        )
                        page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")

                    assign("ready", ["GPU-" + str(i) * 36 for i in (1, 2)])
                    expect(monitor.locator(".oc-gpu-model-badge")).to_have_count(2)
                    expect(cards.nth(1)).to_contain_text("当前模型使用")
                    expect(monitor.get_by_test_id("live-power")).to_have_text("200 W")
                    assign("stopped", ["GPU-" + "0" * 36])
                    expect(monitor.locator(".oc-gpu-model-badge")).to_have_count(0)
                    expect(cards).to_have_count(5)
                    expect(monitor.get_by_test_id("live-power")).to_have_text("200 W")
                    assert page.evaluate(
                        "originalGPUCards.every((e,i)=>e===document.querySelectorAll('.oc-gpu-card')[i])"
                    )
                    later = context.request.get(fixture.BASE + "/api/gpu/live").json()
                    assert later["timestamp"] > initial["timestamp"]
                    assert all(point["measured_power_w"] == 200 for point in later["history"])
                    assert later["model_gpu_uuids"] == []
                    monitor.screenshot(path=str(fixture.ARTIFACTS / "hardware-all-gpus.png"))

                    # Stale data must not masquerade as live zeroes; reconnect restores all cards.
                    stale = {**later, "timestamp": time.time() - 20, "stale": True}
                    page.route("**/api/gpu/live", lambda route: route.fulfill(json=stale))
                    page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
                    expect(monitor.get_by_test_id("live-power")).to_have_text("— W")
                    expect(monitor).to_contain_text("数据已暂停")
                    expect(cards).to_have_count(5)
                    page.unroute("**/api/gpu/live")
                    page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
                    expect(monitor.get_by_test_id("live-power")).to_have_text("200 W")

                    for width, theme in ((1500, "light"), (390, "dark")):
                        page.set_viewport_size({"width": width, "height": 844})
                        page.evaluate("theme=>document.documentElement.classList.toggle('dark',theme==='dark')", theme)
                        expect(cards).to_have_count(5)
                        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                        monitor.screenshot(path=str(fixture.ARTIFACTS / f"hardware-{theme}-{width}.png"))
                    page.evaluate("localStorage.setItem('onecat_locale','en')")
                    page.reload()
                    expect(monitor).to_contain_text("All hardware")
                    expect(cards).to_have_count(5)
                    expect(monitor.get_by_test_id("power-coverage")).to_contain_text("Missing readings excluded")
                    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                    assert not errors, errors
                    print(json.dumps({
                        "cards": 5, "model_assignments": [1, 2, 0],
                        "measured_power_w": 200, "power_reporting": "4/5",
                        "total_memory_gib": 130, "stale_and_reconnect": "passed",
                        "desktop_and_mobile": "passed", "errors": errors,
                    }))
                    browser.close()
            finally:
                server.terminate()
                server.wait(timeout=15)


if __name__ == "__main__":
    run()
