#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Exercise OTA controls in Chromium against an isolated manager and mocked release."""

import json
import os
import runpy
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".artifacts/ota/browser"
SEED = runpy.run_path(str(ROOT / "studio/scripts/check-browser.py"))["SEED"]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with (
        tempfile.TemporaryDirectory(prefix="studio-ota-browser-") as state,
        (OUT / "server.log").open("w") as log,
    ):
        process = subprocess.Popen(
            [str(ROOT / ".venv/bin/python"), "-c", SEED],
            stdout=log,
            stderr=subprocess.STDOUT,
            env={
                **os.environ,
                "ONECAT_STUDIO_HOME": state,
                "PYTHONPATH": str(ROOT / "studio/backend"),
                "ONECAT_AUTO_GPU_ACTIONS": "0",
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
                context = browser.new_context(viewport={"width": 1350, "height": 1050})
                assert context.request.post(
                    base + "/api/auth/setup", data={"password": "ota-browser-password"}
                ).ok
                release = dict(
                    current="0.4.0-dev.aaaa",
                    deployment={"mode": "source"},
                    latest="0.5.0-0123456789",
                    available=True,
                    verified=True,
                    release_id="selected-release",
                    notes="可靠更新与失败恢复",
                    size=300000000,
                    published="2026-09-22T00:00:00Z",
                    supported=True,
                    error=None,
                )
                progress = {"current": release["current"], "status": {}, "blocked_reason": None}
                calls = []

                def route(req):
                    if req.request.method == "POST":
                        calls.append(req.request.post_data_json)
                        progress["status"] = {
                            "stage": "downloading",
                            "version": release["latest"],
                            "downloaded_bytes": 150000000,
                            "total_bytes": 300000000,
                            "bytes_per_second": 10000000,
                        }
                        req.fulfill(json={"started": {"operation": "test"}})
                    elif "/progress" in req.request.url:
                        req.fulfill(json=progress)
                    else:
                        req.fulfill(json=release)

                context.route("**/api/updates**", route)
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(base + "/settings#settings-updates")
                panel = page.locator("#settings-updates")
                expect(panel).to_contain_text(release["latest"])
                assert not calls
                panel.get_by_role("button", name="更新 Studio", exact=True).click()
                dialog = page.get_by_role("dialog")
                expect(dialog).to_contain_text("原源码目录会保留")
                dialog.get_by_role("button", name="切换到正式版并更新").click()
                expect(panel.locator("progress")).to_have_attribute("value", "150000000")
                assert calls == [{"release_id": "selected-release", "switch_to_release": True}]
                expect(panel.get_by_role("button", name="更新 Studio", exact=True)).to_be_disabled()
                panel.screenshot(path=str(OUT / "downloading.png"))
                progress["status"] = {
                    "stage": "rolled_back",
                    "version": release["latest"],
                    "detail": "Health check failed; restored previous release",
                }
                expect(panel).to_contain_text("已恢复原版本", timeout=8000)
                expect(panel.get_by_role("button", name="更新 Studio", exact=True)).to_be_enabled()
                progress["status"] = {"stage": "installed", "version": release["latest"]}
                progress["current"] = release["latest"]
                expect(panel).to_contain_text("已是最新版本", timeout=8000)
                expect(panel.get_by_role("button", name="重新加载界面")).to_be_visible()
                expect(panel.get_by_role("button", name="更新 Studio", exact=True)).to_have_count(0)
                panel.screenshot(path=str(OUT / "installed.png"))
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert not errors, errors
                browser.close()
                print(
                    json.dumps(
                        {
                            "passed": [
                                "source confirmation",
                                "immutable selection",
                                "download progress",
                                "rollback retry",
                                "completion",
                                "mobile width",
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
