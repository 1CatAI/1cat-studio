#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Chat cancellation and navigation regressions, with CPU-only model streams."""
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

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".artifacts/chat-lifecycle-browser"
fixture = runpy.run_path(str(ROOT / "studio/scripts/check-model-controls.py"))
EXTRA = fixture["EXTRA"] + r'''
async def lifecycle_stream(request, *args, **kwargs):
    body = await request.json()
    async def events():
        if "__wait_first__" in json.dumps(body):
            await asyncio.sleep(60)
        yield 'data: {"choices":[{"delta":{"content":"Partial reply to preserve"}}]}\n\n'
        await asyncio.sleep(60)
        yield 'data: [DONE]\n\n'
    return StreamingResponse(events(), media_type="text/event-stream")
proxy.forward = lifecycle_stream
'''
SEED = fixture["SEED"].replace("uvicorn.run(create_app()", EXTRA + "\nuvicorn.run(create_app()")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    passed, failures, errors = [], [], []
    with tempfile.TemporaryDirectory(prefix="onecat-chat-lifecycle-") as state, (OUT / "server.log").open("w") as log:
        server = subprocess.Popen([sys.executable, "-c", SEED], stdout=log, stderr=subprocess.STDOUT, env={
            **os.environ, "ONECAT_STUDIO_HOME": state, "ONECAT_AUTO_GPU_ACTIONS": "0",
            "PYTHONPATH": str(ROOT / "studio/backend"), "TEST_PORT": str(port),
            "ONECAT_FRONTEND_DIST": os.environ.get("ONECAT_FRONTEND_DIST", str(ROOT / "studio/frontend/dist")),
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
                assert context.request.post(base + "/api/auth/setup", data={"password": "lifecycle-fixture-password"}).ok
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                draft = page.get_by_role("textbox", name="消息草稿")
                stop = page.get_by_role("button", name="停止生成", exact=True)

                held_routes = []

                def check(name, action):
                    try:
                        action()
                        passed.append(name)
                        print("PASS", name, flush=True)
                    except Exception as error:
                        failures.append({"check": name, "error": str(error)[:1500]})
                        page.screenshot(path=str(OUT / (name + ".png")), full_page=True)
                        print("FAIL", name, type(error).__name__, flush=True)
                    finally:
                        for route in held_routes:
                            route.abort()
                        held_routes.clear()
                        page.unroute_all(behavior="ignoreErrors")
                        for run in context.request.get(base + "/api/chat/runs").json()["items"]:
                            context.request.post(base + "/api/chat/threads/" + run["thread_id"] + "/cancel")

                def new_thread(title):
                    return context.request.post(base + "/api/chat/threads", data={"title": title}).json()["id"]

                def submit(text):
                    draft.fill(text)
                    send = page.get_by_role("button", name="发送消息", exact=True)
                    expect(send).to_be_enabled()
                    with page.expect_response(lambda response: response.url.endswith("/generate")) as response:
                        send.click()
                    assert response.value.ok
                    return response.value.json()

                def wait_state(thread, wanted):
                    deadline = time.monotonic() + 4
                    while True:
                        run = context.request.get(base + f"/api/chat/threads/{thread}/generation").json()["run"]
                        if run["state"] == wanted:
                            return run
                        assert time.monotonic() < deadline, run
                        page.wait_for_timeout(50)

                def stop_before_first_token():
                    page.goto(base + "/chat")
                    run = submit("__wait_first__ Keep my draft before the first token")
                    page.wait_for_timeout(250)  # POST is accepted; now waiting only for model output.
                    assert page.url.endswith("/chat")
                    expect(page.locator(".oc-message-user")).to_contain_text("Keep my draft")
                    expect(page.get_by_text("等待模型回复…", exact=True)).to_be_visible()
                    expect(draft).to_have_value("")
                    assert page.locator(".oc-reply-pulse i").first.evaluate("el => getComputedStyle(el).animationName") == "none"
                    stop.click()
                    wait_state(run["thread_id"], "cancelled")
                    expect(draft).to_have_value("__wait_first__ Keep my draft before the first token")
                    expect(page.locator(".oc-message-user")).to_have_count(0)
                    expect(page.get_by_role("button", name="发送消息", exact=True)).to_be_enabled()

                check("new-chat-stop-before-first-token", stop_before_first_token)

                def stop_before_submission_response():
                    page.goto(base + "/chat")
                    responses = []

                    def hold_submission(route):
                        response = route.fetch()
                        responses.append(response)
                        held_routes.append(route)

                    page.route("**/api/chat/threads/*/generate", hold_submission)
                    draft.fill("__wait_first__ Stop while the submit response is delayed")
                    page.get_by_role("button", name="发送消息", exact=True).click()
                    deadline = time.monotonic() + 4
                    while not responses:
                        assert time.monotonic() < deadline
                        page.wait_for_timeout(50)
                    stop.click()
                    expect(stop).to_be_disabled()
                    run = responses[0].json()
                    with page.expect_request(lambda request: request.url.endswith("/cancel")) as request:
                        held_routes[0].fulfill(response=responses[0])
                        held_routes.clear()
                    assert request.value.post_data_json == {"run_id": run["id"]}
                    wait_state(run["thread_id"], "cancelled")
                    expect(draft).to_have_value("__wait_first__ Stop while the submit response is delayed")
                    expect(page.get_by_role("button", name="发送消息", exact=True)).to_be_enabled()

                check("stop-waits-for-submitted-generation-id", stop_before_submission_response)

                def optimistic_send_before_thread_creation():
                    page.emulate_media(reduced_motion="no-preference")
                    try:
                        page.goto(base + "/chat")
                        page.route("**/api/chat/threads", lambda route:
                            held_routes.append(route) if route.request.method == "POST" else route.continue_())
                        draft.fill("See my message immediately")
                        # Sample the actual interactive form, including the first paint after submit.
                        form = page.locator(".oc-chat-composer")
                        expect(page.get_by_role("button", name="发送消息", exact=True)).to_be_enabled()
                        page.evaluate("document.fonts.ready")
                        welcome_y = form.bounding_box()["y"]
                        def track_form():
                            form.evaluate("""el => {
                                const y = window.composerFrames = [];
                                function sample() {
                                    y.push([performance.now(), el.getBoundingClientRect().top]);
                                    if (y === window.composerFrames) requestAnimationFrame(sample);
                                }
                                sample();
                            }""")
                        def check_move(direction):
                            page.wait_for_timeout(650)
                            samples = page.evaluate("() => {const y = window.composerFrames; window.composerFrames = null; return y}")
                            y = [position for _, position in samples]
                            travel = (y[-1] - y[0]) * direction
                            assert travel > 100, y
                            assert len({round(p) for p in y if 0.05 < (p-y[0])*direction/travel < 0.95}) >= 6, y
                            for (t0, a), (t1, b) in zip(samples, samples[1:]):
                                if t1 - t0 <= 50:
                                    assert abs(b-a) < 0.5*travel, samples
                        track_form()
                        page.get_by_role("button", name="发送消息", exact=True).click()
                        row = page.locator(".oc-message-user")
                        expect(row).to_contain_text("See my message immediately")
                        expect(page.get_by_text("等待模型回复…", exact=True)).to_be_visible()
                        expect(draft).to_have_value("")
                        assert row.evaluate("el => getComputedStyle(el).animationName") == "oc-message-arrive"
                        assert held_routes
                        check_move(1)
                        track_form()
                        held_routes.pop().fulfill(status=503, json={"detail": "Thread unavailable"})
                        expect(draft).to_have_value("See my message immediately")
                        expect(row).to_have_count(0)
                        expect(page.get_by_text("Thread unavailable", exact=True)).to_be_visible()
                        check_move(-1)
                        assert abs(form.bounding_box()["y"] - welcome_y) < 1
                    finally:
                        page.emulate_media(reduced_motion="reduce")

                check("optimistic-send-before-thread-and-draft-recovery", optimistic_send_before_thread_creation)

                def first_reply_reuses_pending_rows():
                    page.goto(base + "/chat")
                    page.route("**/api/chat/threads/*/generate", lambda route: held_routes.append(route))
                    original_draft = draft.element_handle()
                    draft.fill("Keep the same bubbles during the first reply")
                    page.get_by_role("button", name="发送消息", exact=True).click()
                    expect(page.get_by_text("等待模型回复…", exact=True)).to_be_visible()
                    user_row = page.locator(".oc-message-user").element_handle()
                    reply_row = page.locator(".oc-message-assistant").element_handle()
                    deadline = time.monotonic() + 4
                    while not held_routes:
                        assert time.monotonic() < deadline
                        page.wait_for_timeout(20)
                    held_routes.pop().continue_()
                    expect(page.locator(".oc-message-assistant")).to_contain_text("Partial reply to preserve")
                    expect(page.locator(".oc-message-user")).to_have_count(1)
                    expect(page.locator(".oc-message-assistant")).to_have_count(1)
                    assert original_draft.evaluate("el => el === document.querySelector('.oc-chat-composer textarea')")
                    assert user_row.evaluate("el => el === document.querySelector('.oc-message-user')")
                    assert reply_row.evaluate("el => el === document.querySelector('.oc-message-assistant')")
                    stop.click()

                check("first-reply-reuses-pending-rows", first_reply_reuses_pending_rows)

                def failed_stop_can_retry():
                    identity = new_thread("Retry a failed stop")
                    page.goto(base + "/chat?thread=" + identity)
                    submit("Keep the stop button until confirmed")
                    expect(page.locator(".oc-message").filter(has_text="Partial reply to preserve")).to_be_visible()
                    page.route("**/api/chat/threads/" + identity + "/cancel", lambda route: route.fulfill(status=503, json={"detail": "Stop temporarily unavailable"}))
                    stop.click()
                    expect(page.get_by_role("alert").filter(has_text="Stop temporarily unavailable")).to_be_visible()
                    expect(stop).to_be_visible()
                    page.unroute_all(behavior="ignoreErrors")
                    stop.click()
                    wait_state(identity, "cancelled")
                    expect(page.get_by_role("button", name="发送消息", exact=True)).to_be_visible()
                    assert context.request.get(base + f"/api/chat/threads/{identity}").json()["messages"][-1]["content"]

                check("failed-stop-remains-retryable", failed_stop_can_retry)

                def stalled_stop_can_retry():
                    identity = new_thread("Retry a stalled stop")
                    page.goto(base + "/chat?thread=" + identity)
                    submit("Preserve controls through a stalled stop connection")
                    expect(page.locator(".oc-message").filter(has_text="Partial reply to preserve")).to_be_visible()
                    page.route("**/api/chat/threads/" + identity + "/cancel", lambda route: held_routes.append(route))
                    stop.click()
                    expect(stop).to_be_disabled()
                    expect(page.get_by_role("alert").filter(has_text="重试")).to_be_visible(timeout=18000)
                    expect(stop).to_be_enabled()
                    for route in held_routes:
                        route.abort()
                    held_routes.clear()
                    page.unroute_all(behavior="ignoreErrors")
                    stop.click()
                    wait_state(identity, "cancelled")

                check("stalled-stop-times-out-and-can-retry", stalled_stop_can_retry)

                def old_stop_cannot_unlock_new_chat():
                    old = new_thread("Delayed stop source")
                    new = new_thread("Destination still generating")
                    page.goto(base + "/chat?thread=" + old)
                    submit("Stop this source")
                    expect(page.locator(".oc-message").filter(has_text="Partial reply to preserve")).to_be_visible()
                    page.route("**/api/chat/threads/" + old + "/cancel", lambda route: held_routes.append(route))
                    stop.click()
                    page.get_by_role("button", name="历史记录", exact=True).click()
                    page.locator(".oc-history-link").filter(has_text="Destination still generating").click()
                    submit("Continue in destination")
                    expect(page.locator(".oc-message").filter(has_text="Partial reply to preserve")).to_be_visible()
                    assert held_routes
                    for route in held_routes:
                        response = context.request.post(route.request.url)
                        route.fulfill(response=response)
                    held_routes.clear()
                    page.wait_for_timeout(250)
                    expect(stop).to_be_visible()
                    assert page.url.endswith(new)
                    assert context.request.get(base + f"/api/chat/threads/{new}/generation").json()["run"]["state"] == "running"
                    stop.click()
                    wait_state(new, "cancelled")

                check("late-stop-does-not-change-destination", old_stop_cannot_unlock_new_chat)

                def navigation_keeps_backend_running():
                    identity = new_thread("Background chat")
                    page.goto(base + "/chat?thread=" + identity)
                    submit("Continue through navigation")
                    expect(page.locator(".oc-message").filter(has_text="Partial reply to preserve")).to_be_visible()
                    page.goto(base + "/service")
                    wait_state(identity, "running")
                    page.goto(base + "/chat?thread=" + identity)
                    expect(stop).to_be_visible()
                    expect(page.locator(".oc-message").filter(has_text="Partial reply to preserve")).to_be_visible()
                    stop.click()
                    wait_state(identity, "cancelled")

                check("navigation-reconnects-without-cancelling", navigation_keeps_backend_running)

                def new_chat_from_toolbar():
                    page.set_viewport_size({"width": 320, "height": 780})
                    title = "正在生成的长对话名称，用于检查窄屏的新建入口"
                    identity = new_thread(title)
                    page.goto(base + "/chat?thread=" + identity)
                    submit("Continue in the original thread")
                    expect(page.get_by_text("Partial reply to preserve", exact=True)).to_be_visible()
                    draft.fill("原对话的草稿")
                    header = page.locator(".oc-chat-toolbar")
                    heading = header.get_by_role("heading", name=title)
                    expect(heading.locator("span")).to_have_css("writing-mode", "vertical-rl")
                    start = header.get_by_role("button", name="新建对话", exact=True)
                    title_box, button_box = heading.bounding_box(), start.bounding_box()
                    assert title_box["x"] + title_box["width"] <= button_box["x"]
                    tools = page.locator(".oc-header-stats").get_by_role("group", name="对话工具")
                    more = tools.get_by_role("button", name="更多对话选项")
                    expect(more).to_be_visible()
                    menu_box, ready = more.bounding_box(), page.locator(".oc-header-engine-state").bounding_box()
                    assert menu_box["x"] + menu_box["width"] <= ready["x"]
                    more.click()
                    expect(page.get_by_role("menuitem", name="管理对话")).to_be_disabled()
                    page.keyboard.press("Escape")
                    start.focus()
                    page.keyboard.press("Enter")
                    expect(draft).to_be_focused()
                    expect(draft).to_have_value("")
                    expect(page.locator(".oc-message")).to_have_count(0)
                    assert wait_state(identity, "running")
                    draft.fill("暂存的草稿")
                    first_draft = page.url
                    start.click()
                    expect(draft).to_have_value("")
                    assert page.url != first_draft
                    page.go_back()
                    expect(draft).to_have_value("暂存的草稿")
                    page.go_back()
                    expect(draft).to_have_value("原对话的草稿")
                    expect(stop).to_be_visible()
                    page.set_viewport_size({"width": 1440, "height": 960})

                check("new-chat-from-visible-header-preserves-background-run-and-drafts", new_chat_from_toolbar)
                browser.close()
        finally:
            server.terminate()
            server.wait(timeout=15)
    (OUT / "result.json").write_text(json.dumps({"passed": passed, "failures": failures, "browser_errors": errors}, ensure_ascii=False, indent=2))
    assert not failures and not errors, failures or errors


if __name__ == "__main__":
    main()
