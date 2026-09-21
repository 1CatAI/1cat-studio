#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Browser regression: drop the final SSE events while the real chat worker finishes.

Uses the standard disposable browser fixture; no model or GPU is touched.
"""
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
ARTIFACTS = ROOT / ".artifacts/chat-completion-browser"
ARTIFACTS.mkdir(parents=True, exist_ok=True)
SEED = runpy.run_path(str(ROOT / "studio/scripts/check-browser.py"))["SEED"]

LOSE_TAIL = r"""
const originalFetch = window.fetch;
window.lostCompletionEvents = 0;
window.closedObservers = 0;
window.delayedSnapshot = false;
window.fetch = async (input, init) => {
  if (localStorage.getItem('completion-race-fixture') && String(input).endsWith('/generate'))
    await new Promise(resolve => setTimeout(resolve, 2000));
  const response = await originalFetch(input, init);
  if (!String(input).endsWith('/events') || !response.ok) return response;
  const reader = response.body.getReader();
  return new Response(new ReadableStream({
    async start(controller) {
      const decoder = new TextDecoder();
      const encoder = new TextEncoder();
      let buffer = '';
      try {
        while (true) {
          const {value, done} = await reader.read();
          if (done) return; // Keep the browser-facing connection open.
          buffer += decoder.decode(value, {stream:true});
          let boundary;
          while ((boundary = buffer.indexOf('\n\n')) >= 0) {
            const frame = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            if (frame.includes('"onecat_run"') || frame.includes('"onecat_metrics"') || frame.includes('[DONE]')) {
              window.lostCompletionEvents++;
            } else {
              if (localStorage.getItem('completion-race-fixture') && frame.includes('"onecat_snapshot"') && /"state"\s*:\s*"completed"/.test(frame)) {
                window.delayedSnapshot = true;
                await new Promise(resolve => setTimeout(resolve, 700));
              }
              controller.enqueue(encoder.encode(frame + '\n\n'));
            }
          }
        }
      } catch (_) { /* Deliberately simulate a half-open transport. */ }
    },
    cancel() { window.closedObservers++; return reader.cancel(); }
  }), {status: response.status, headers: response.headers});
};
"""


def main():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="onecat-completion-") as state, (ARTIFACTS / "server.log").open("w") as log:
        env = {**os.environ, "ONECAT_STUDIO_HOME": state, "ONECAT_AUTO_GPU_ACTIONS": "0",
               "PYTHONPATH": str(ROOT / "studio/backend"), "TEST_PORT": str(port)}
        process = subprocess.Popen([str(ROOT / ".venv/bin/python"), "-c", SEED], env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen(base + "/api/health", timeout=1)
                    break
                except OSError:
                    time.sleep(.1)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True,
                    executable_path=os.environ.get("ONECAT_BROWSER_EXECUTABLE") or None,
                    args=["--disable-gpu", "--use-angle=swiftshader"])
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                assert context.request.post(base + "/api/auth/setup", data={"password": "browser-fixture-password"}).ok
                context.add_init_script(LOSE_TAIL)
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base + "/chat")
                draft = page.get_by_role("textbox", name="消息草稿")
                draft.fill("Write a snake game")
                page.get_by_role("button", name="发送消息", exact=True).click()
                expect(page.get_by_role("button", name="停止生成", exact=True)).to_be_visible()
                page.wait_for_function("window.lostCompletionEvents >= 2", timeout=15000)
                completed = time.monotonic()
                expect(page.get_by_role("button", name="发送消息", exact=True)).to_be_visible(timeout=4500)
                expect(page.get_by_role("button", name="停止生成", exact=True)).to_have_count(0)
                delay = time.monotonic() - completed
                assert page.evaluate("window.closedObservers") >= 1
                thread_id = page.url.split("thread=")[1].split("&")[0]
                saved = context.request.get(base + "/api/chat/threads/" + thread_id).json()
                assert saved["generation"]["state"] == "completed"
                assert saved["messages"][-1]["metadata"]["usage"]["completion_tokens"] == 2048
                assert saved["messages"][-1]["content"][0]["text"].endswith("```")
                # A second submission must be usable immediately, despite the
                # previous observer's deliberately open transport.
                draft.fill("Continue the game")
                expect(page.get_by_role("button", name="发送消息", exact=True)).to_be_enabled()
                page.get_by_role("button", name="发送消息", exact=True).click()
                expect(page.get_by_role("button", name="停止生成", exact=True)).to_be_visible()
                expect(page.get_by_role("button", name="发送消息", exact=True)).to_be_visible(timeout=15000)
                page.evaluate("localStorage.setItem('completion-race-fixture', '1')")
                page.reload()
                expect(page.get_by_role("button", name="发送消息", exact=True)).to_be_visible()
                expect(page.get_by_role("button", name="停止生成", exact=True)).to_have_count(0)
                page.wait_for_function("window.delayedSnapshot")
                draft.fill("One more update")
                with page.expect_response(lambda response: response.url.endswith("/generate")):
                    page.get_by_role("button", name="发送消息", exact=True).click()
                    page.wait_for_timeout(1000)  # Old snapshot arrives while submission is pending.
                    expect(page.get_by_role("button", name="停止生成", exact=True)).to_be_visible()
                expect(page.get_by_role("button", name="发送消息", exact=True)).to_be_visible(timeout=15000)
                page.set_viewport_size({"width": 390, "height": 844})
                expect(draft).to_be_visible()
                page.screenshot(path=str(ARTIFACTS / "settled-mobile.png"))
                assert not errors, errors
                result = {"passed": True, "completion_recovery_seconds": round(delay, 3),
                          "checks": ["lost SSE tail", "held-open connection released", "exact saved tokens and final code",
                                     "second send", "reload completed conversation", "late old snapshot during next submission",
                                     "mobile composer", "no React errors"]}
                (ARTIFACTS / "result.json").write_text(json.dumps(result, indent=2) + "\n")
                print(json.dumps(result))
                browser.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
