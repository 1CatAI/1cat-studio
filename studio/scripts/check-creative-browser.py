#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Creative canvas race regressions on isolated data and a CPU image fixture.

Run after build:onecat with a Python containing Playwright, Pillow and backend
requirements. Chromium uses software rendering. Optional CHROMIUM_EXECUTABLE
selects an existing browser. No live services or GPU settings are changed.
"""

import base64
import contextlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".artifacts/creative-browser"
OUT.mkdir(exist_ok=True, parents=True)
STATE = Path(tempfile.mkdtemp(prefix="state-", dir=OUT))
image = io.BytesIO()
Image.new("RGB", (120, 80), (58, 127, 108)).save(image, "PNG")
PNG = image.getvalue()
submissions = []


class Backend(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, data, status=200, mime="application/json"):
        raw = json.dumps(data).encode() if mime == "application/json" else data
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/health":
            return self.reply({"status": "ok", "partition": "fl2va"})
        if self.path == "/v1/models":
            return self.reply({"data": [{"id": "fixture-image"}, {"id": "fixture-h3"}]})
        return self.reply({}, 404)

    def do_POST(self):
        data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        submissions.append(
            {
                "path": self.path,
                "content_type": self.headers.get("Content-Type"),
                "body": data.decode(errors="replace")[:10000],
            }
        )
        if self.path.startswith("/v1/images/"):
            time.sleep(0.5)
            return self.reply({"data": [{"b64_json": base64.b64encode(PNG).decode()}]})
        return self.reply({}, 404)

    def do_DELETE(self):
        return self.reply({"detail": "Running"}, 409)


remote = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
threading.Thread(target=remote.serve_forever, daemon=True).start()
remote_url = f"http://127.0.0.1:{remote.server_port}"
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
base = f"http://127.0.0.1:{port}"
env = {
    **os.environ,
    "ONECAT_STUDIO_HOME": str(STATE),
    "ONECAT_AUTO_GPU_ACTIONS": "0",
    "PYTHONPATH": str(ROOT / "studio/backend"),
}
log = (OUT / "manager.log").open("ab")


def manager():
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            f'from onecat import gpu,gpu_setup; gpu_setup.helper_check=lambda:dict(); gpu.snapshot=lambda:dict(gpus=[],available=True); from onecat.__main__ import main; import sys; sys.argv=["onecat","--port","{port}"];main()',
        ],
        env=env,
        stdout=log,
        stderr=log,
    )


proc = manager()
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            executable_path=os.environ.get("CHROMIUM_EXECUTABLE"),
            args=["--disable-gpu", "--use-angle=swiftshader"],
        )
        context = browser.new_context(viewport={"width": 1500, "height": 1020})
        context.add_init_script("localStorage.setItem('onecat_theme','dark')")
        for i in range(60):
            with contextlib.suppress(PlaywrightError):
                if context.request.get(base + "/api/health").ok:
                    break
            time.sleep(0.2)
        auth = context.request.get(base + "/api/auth/status").json()
        assert context.request.post(
            base + ("/api/auth/login" if auth["initialized"] else "/api/auth/setup"),
            data={"password": "canvas-tests-only"},
        ).ok

        def post(path, data=None):
            r = context.request.post(base + path, data=data or {})
            assert r.ok, (path, r.status, r.text())
            return r.json()

        image_service = post(
            "/api/creative/services",
            {
                "name": "测试图片模型",
                "kind": "image-api",
                "model": "fixture-image",
                "base_url": remote_url,
                "image_edit": True,
            },
        )
        post("/api/creative/services/" + image_service["id"] + "/load")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base + "/canvas")
        expect(page.get_by_role("heading", name="让想法在画布上延续")).to_be_visible()
        page.screenshot(path=str(OUT / "welcome-dark.png"))
        page.get_by_role("button", name="从一张图片开始").click()
        expect(page.locator(".oc-canvas-node")).to_have_count(1)
        page.locator(".oc-canvas-inspector textarea").fill("生成一张安静的森林照片")
        expect(page.get_by_role("button", name="开始生成", exact=True)).to_be_enabled()
        page.get_by_role("button", name="开始生成", exact=True).click()
        expect(page.locator(".oc-canvas-node")).to_have_count(2, timeout=20000)
        page.screenshot(path=str(OUT / "image-completed-dark.png"))
        checks = {}
        page.get_by_role("button", name="撤销", exact=True).click()
        page.wait_for_timeout(3500)
        checks["undo_result_stays_removed"] = page.locator(".oc-canvas-node").count() == 1
        project_id = context.request.get(base + "/api/creative/projects").json()["items"][0]["id"]
        expect(page.get_by_role("button", name="开始生成", exact=True)).to_be_enabled()
        held = []

        def hold_save(route):
            if route.request.method == "PUT" and not held:
                response = route.fetch()
                held.append((route, response))
                return
            route.continue_()

        page.route("**/api/creative/projects/" + project_id, hold_save)
        textarea = page.locator(".oc-canvas-inspector textarea")
        textarea.fill("旧提示词 - previous prompt")
        page.get_by_role("button", name="开始生成", exact=True).click()
        limit = time.time() + 5
        while not held and time.time() < limit:
            page.wait_for_timeout(30)
        assert held
        textarea.fill("最新提示词 - latest prompt")
        held[0][0].fulfill(response=held[0][1])
        expected = len(submissions) + 1
        limit = time.time() + 10
        while len(submissions) < expected and time.time() < limit:
            page.wait_for_timeout(50)
        latest = json.loads(
            [s for s in submissions if s["path"] == "/v1/images/generations"][-1]["body"]
        )
        checks["generate_flushes_latest_prompt"] = latest["prompt"] == "最新提示词 - latest prompt"
        page.unroute("**/api/creative/projects/" + project_id, hold_save)
        page.wait_for_timeout(1500)
        title = page.get_by_label("画布名称", exact=True)
        title.fill("")
        page.wait_for_timeout(1000)
        title.fill("修正后的标题")
        page.wait_for_timeout(1700)
        saved = context.request.get(base + "/api/creative/projects/" + project_id).json()
        checks["corrected_field_saves_automatically"] = saved["title"] == "修正后的标题"
        page.get_by_role("button", name="关闭参数", exact=True).focus()
        page.keyboard.press("Space")
        page.wait_for_timeout(500)
        checks["space_activates_inspector_button"] = (
            page.locator(".oc-canvas-inspector").count() == 0
        )
        # Loading lifecycle uses mocked status only; clicks must target the real
        # load/stop routes while the fixture leaves host GPUs untouched.
        page.locator('.oc-canvas-node').filter(has_text='最新提示词').first.click()
        fake = {**image_service, 'kind': 'h3-local', 'partition': 'fl2va',
                'instance': {'state': 'loading', 'phase': 'loading_weights', 'job_id': 'fixture-load',
                             'elapsed_s': 73, 'can_stop': True,
                             'phase_progress': {'done': 2, 'total': 4, 'percent': 50},
                             'detail': 'Loading checkpoint shards: 2/4'}}
        page.route('**/api/creative/services', lambda route: route.fulfill(json={'items': [fake]}))
        page.route('**/api/creative/services/' + image_service['id'] + '/log', lambda route: route.fulfill(json={'text': 'Loading checkpoint shards: 2/4'}))
        page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
        life = page.locator('.oc-canvas-generate-footer .oc-creative-lifecycle')
        expect(life).to_contain_text('加载权重')
        expect(life).to_contain_text('1:13')
        expect(life).to_contain_text('2 / 4')
        life.get_by_role('button', name='加载日志', exact=True).click()
        expect(page.get_by_role('dialog')).to_contain_text('Loading checkpoint shards: 2/4')
        page.keyboard.press('Escape')
        def stop_fixture(route):
            fake['instance'] = {'state': 'stopped'}
            route.fulfill(json={'id': 'fixture-stop', 'state': 'queued'})
        page.route('**/api/creative/services/' + image_service['id'] + '/stop', stop_fixture)
        life.get_by_role('button', name='取消加载并释放显存', exact=True).click()
        expect(life.get_by_role('button', name='加载所选模型', exact=True)).to_be_visible()
        fake['instance'] = {'state': 'failed', 'error': 'H3 worker exited: test failure'}
        page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
        expect(life.get_by_role('alert')).to_contain_text('H3 worker exited')
        expect(life.get_by_role('button', name='加载所选模型', exact=True)).to_be_enabled()
        for width in (1440, 390, 320):
            page.set_viewport_size({'width': width, 'height': 900})
            page.screenshot(path=str(OUT / f'load-failure-{width}.png'))
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        checks['load_progress_logs_cancel_failure_retry'] = True
        checks["no_react_errors"] = not errors
        (OUT / "result.json").write_text(json.dumps(checks, indent=2))
        print(json.dumps(checks))
        browser.close()
        assert all(checks.values()), checks
finally:
    proc.terminate()
    proc.wait(timeout=10)
    remote.shutdown()
    log.close()
