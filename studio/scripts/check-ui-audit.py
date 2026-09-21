#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Focused browser regressions for preview lifetimes and chat cancellation.

Uses the disposable, CPU-only fixture from check-browser.py. No GPU requests.
"""
import importlib.util
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

spec = importlib.util.spec_from_file_location("browser_fixture", Path(__file__).with_name("check-browser.py"))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def run():
    failures = []
    passed = []
    with tempfile.TemporaryDirectory(prefix="onecat-ui-audit-") as state:
        env = {**os.environ, "ONECAT_STUDIO_HOME": state, "PYTHONPATH": str(fixture.ROOT / "studio/backend"), "TEST_PORT": str(fixture.PORT)}
        with (fixture.ARTIFACTS / "audit-server.log").open("w") as log:
            server = subprocess.Popen([str(fixture.ROOT / ".venv/bin/python"), "-c", fixture.SEED], env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                for _ in range(100):
                    try:
                        urllib.request.urlopen(fixture.BASE + "/api/health", timeout=1)
                        break
                    except OSError:
                        time.sleep(.1)
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=True, ignore_default_args=["--hide-scrollbars"])
                    context = browser.new_context(viewport={"width": 1500, "height": 1000})
                    assert context.request.post(fixture.BASE + "/api/auth/setup", data={"password": "audit-fixture-password"}).ok

                    def thread(code, title="Audit fixture"):
                        return context.request.post(fixture.BASE + "/api/chat/import", data={"title": title, "messages": [{"role": "user", "content": "Example"}, {"role": "assistant", "content": code}]}).json()["id"]

                    def update(page, id, code):
                        messages = context.request.get(fixture.BASE + "/api/chat/threads/" + id).json()["messages"]
                        messages[-1]["content"] = [{"type": "text", "text": code}]
                        assert context.request.put(fixture.BASE + f"/api/chat/threads/{id}/messages", data={"messages": messages}).ok
                        page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")

                    def preview(page, code):
                        id = thread(code)
                        page.goto(fixture.BASE + "/chat?thread=" + id)
                        page.get_by_role("button", name="预览 / 运行").first.click()
                        return id

                    def inline_handlers(page):
                        preview(page, '```html\n<button onclick="increment()">Increment</button><p id="value">0</p><script>let n=0;function increment(){document.getElementById("value").textContent=String(++n)}</script>\n```')
                        frame = page.frame_locator("iframe:not(.oc-preview-candidate)")
                        frame.get_by_role("button", name="Increment").click(timeout=15000)
                        expect(frame.locator("#value")).to_have_text("1", timeout=2000)

                    def failed_frame(page):
                        id = preview(page, '```html\n<button onclick="throw Error(\'retained-frame-error\')">Still active</button>\n```')
                        frame = page.frame_locator("iframe:not(.oc-preview-candidate)")
                        expect(frame.get_by_role("button", name="Still active")).to_be_visible(timeout=15000)
                        update(page, id, "```tsx\nimport missing from 'not-installed'; export default function App(){return <div>{missing}</div>}\n```")
                        expect(page.get_by_role("alert")).to_contain_text("Unsupported dependency", timeout=15000)
                        expect(page.locator("iframe")).to_have_count(1, timeout=2000)
                        frame.get_by_role("button", name="Still active").click()
                        expect(page.get_by_role("alert")).to_contain_text("retained-frame-error", timeout=2000)
                        page.get_by_role("button", name="关闭预览").click()
                        expect(page.locator("iframe")).to_have_count(0)

                    def repeated_ready(page):
                        preview(page, '```html\n<h1>Still visible</h1>\n```')
                        title = page.frame_locator("iframe:not(.oc-preview-candidate)").get_by_text("Still visible")
                        expect(title).to_be_visible(timeout=15000)
                        title.evaluate('()=>parent.postMessage({onecat_preview:true,type:"ready"},"*")')
                        page.wait_for_timeout(300)
                        expect(page.locator("iframe:not(.oc-preview-candidate)")).to_have_count(1, timeout=1000)

                    def completion_metadata(page):
                        code = "<script>const x = ;</script>"
                        id = preview(page, "```html\n" + code)
                        expect(page.locator(".oc-preview-status")).to_contain_text("代码不完整", timeout=15000)
                        update(page, id, "```html\n" + code + "\n```")
                        expect(page.locator(".oc-preview-status")).to_contain_text("运行错误", timeout=4000)

                    def switching_chat(page):
                        old = thread("Old reply", "Audit old chat")
                        new = thread("Destination reply", "Audit destination chat")
                        page.goto(fixture.BASE + "/chat?thread=" + old)
                        page.evaluate("""() => { const original = window.fetch; window.fetch = async (url, options) => {
                          if (String(url).endsWith('/api/chat/threads/""" + old + """') && options?.method === 'PATCH') {
                            window.auditSaving = true; await new Promise(resolve => setTimeout(resolve,1500));
                          } return original(url,options);
                        }; }""")
                        page.locator(".oc-chat-composer textarea").fill("Request before switching")
                        page.locator(".oc-chat-composer button[type=submit]").click()
                        page.wait_for_function("window.auditSaving")
                        page.get_by_role("link", name="Audit destination chat", exact=True).click()
                        expect(page.locator(".oc-message").filter(has_text="Destination reply")).to_be_visible(timeout=1000)
                        page.locator(".oc-chat-composer textarea").fill("Destination draft survives")
                        page.wait_for_timeout(1800)
                        expect(page.locator(".oc-chat-composer textarea")).to_have_value("Destination draft survives")
                        assert page.url.endswith(new)
                        expect(page.locator(".oc-chat-composer button[type=submit]")).to_be_enabled()

                    def old_cleanup_during_new_request(page):
                        old = thread("Old reply", "Audit delayed old chat")
                        new = thread("New reply", "Audit concurrent destination")
                        page.goto(fixture.BASE + "/chat?thread=" + old)
                        page.evaluate("""() => { const original = window.fetch; window.fetch = async (url, options) => {
                          if (String(url).endsWith('/api/chat/threads/""" + old + """') && options?.method === 'PATCH') {
                            window.auditSaving = true; await new Promise(resolve => setTimeout(resolve,1500));
                          }
                          if (String(url).endsWith('/api/inference/generate/stream')) {
                            return new Response(new ReadableStream({start(controller){setTimeout(()=>{
                              controller.enqueue(new TextEncoder().encode('data: {"choices":[{"delta":{"content":"New run completed"},"finish_reason":"stop"}]}\\n\\ndata: [DONE]\\n\\n'));controller.close();
                            },3000)}}),{headers:{'Content-Type':'text/event-stream'}});
                          }
                          return original(url,options);
                        }; }""")
                        page.locator(".oc-chat-composer textarea").fill("Old request")
                        page.locator(".oc-chat-composer button[type=submit]").click()
                        page.wait_for_function("window.auditSaving")
                        page.get_by_role("link", name="Audit concurrent destination", exact=True).click()
                        page.locator(".oc-chat-composer textarea").fill("New request")
                        page.locator(".oc-chat-composer button[type=submit]").click()
                        page.wait_for_timeout(1800)
                        expect(page.get_by_role("button", name="停止生成", exact=True)).to_be_visible()
                        expect(page.locator(".oc-message").filter(has_text="New run completed")).to_be_visible(timeout=5000)
                        expect(page.get_by_role("button", name="停止生成", exact=True)).to_have_count(0)
                        saved = context.request.get(fixture.BASE + "/api/chat/threads/" + new).json()["messages"][-1]
                        assert saved["metadata"]["finish_reason"] == "stop"

                    def premature_eof(page):
                        page.route("**/api/inference/generate/stream", lambda route: route.fulfill(status=200, content_type="text/event-stream", body='data: {"choices":[{"delta":{"content":"Partial reply 🌊"}}]}\n\n'))
                        id = thread("Previous answer")
                        page.goto(fixture.BASE + "/chat?thread=" + id)
                        page.locator(".oc-chat-composer textarea").fill("Next request")
                        page.locator(".oc-chat-composer button[type=submit]").click()
                        expect(page.locator(".oc-message").filter(has_text="Partial reply 🌊")).to_be_visible(timeout=15000)
                        page.wait_for_timeout(500)
                        saved = context.request.get(fixture.BASE + "/api/chat/threads/" + id).json()["messages"][-1]
                        assert saved["metadata"]["finish_reason"] == "error", saved["metadata"]
                        expect(page.get_by_role("alert").first).to_contain_text("中断")
                        page.reload()
                        expect(page.locator(".oc-output-interrupted")).to_contain_text("生成中断")

                    def inspect_reasoning(page):
                        id = thread([{"type": "reasoning", "text": "\n\n".join(f"Step {i}: Inspect the reasoning without losing your reading position." for i in range(180))}, {"type": "text", "text": "Final answer."}])
                        page.goto(fixture.BASE + "/chat?thread=" + id)
                        summary = page.locator(".oc-reasoning summary")
                        summary.click()
                        page.wait_for_timeout(600)
                        expect(summary).to_be_in_viewport(timeout=1500)
                        expect(page.get_by_role("button", name="回到最新", exact=True)).to_be_visible()
                        page.get_by_role("button", name="回到最新", exact=True).click()
                        page.wait_for_function("(()=>{const e=document.querySelector('.oc-conversation');return e.scrollHeight-e.scrollTop-e.clientHeight<10})()")

                    def native_scrollbar_reading(page):
                        code = "\n\n".join(f"Line {i}: Keep reading while content arrives below." for i in range(180))
                        id = thread(code)
                        page.goto(fixture.BASE + "/chat?thread=" + id)
                        page.add_style_tag(content=".oc-conversation{scrollbar-width:auto!important}.oc-conversation::-webkit-scrollbar{width:16px!important}")
                        page.wait_for_function("(()=>{const e=document.querySelector('.oc-conversation');return e&&e.scrollHeight>4000&&e.scrollHeight-e.scrollTop-e.clientHeight<10})()")
                        page.wait_for_timeout(350)
                        scroller = page.locator(".oc-conversation")
                        bounds = scroller.bounding_box()
                        assert scroller.evaluate("e=>e.offsetWidth-e.clientWidth") > 0, "Native scrollbar gutter must be visible for this test"
                        scroller.evaluate("e=>{window.nativeScrolls=[e.scrollTop];e.addEventListener('scroll',()=>window.nativeScrolls.push(e.scrollTop))}")
                        x, bottom = bounds["x"] + bounds["width"] - 5, bounds["y"] + bounds["height"] - 30
                        page.mouse.move(x, bottom)
                        page.mouse.down()
                        page.mouse.move(x, bounds["y"] + bounds["height"] / 2, steps=20)
                        page.mouse.up()
                        page.wait_for_timeout(400)
                        position = scroller.evaluate("e=>e.scrollTop")
                        assert scroller.evaluate("e=>e.scrollHeight-e.scrollTop-e.clientHeight") > 200, "Scrollbar reading was lost: " + str(page.evaluate("window.nativeScrolls"))
                        update(page, id, code + "\n\nNewly arrived content.\n\nMore content.")
                        page.wait_for_timeout(700)
                        assert abs(scroller.evaluate("e=>e.scrollTop") - position) < 80, "New content pulled native scrollbar reading back to the end"

                    def small_upward_gesture(page):
                        code = "\n\n".join(f"Paragraph {i}: Read with a precise trackpad gesture." for i in range(80))
                        id = thread(code)
                        page.goto(fixture.BASE + "/chat?thread=" + id)
                        page.wait_for_function("(()=>{const e=document.querySelector('.oc-conversation');return e&&e.scrollHeight>2000&&e.scrollHeight-e.scrollTop-e.clientHeight<1})()")
                        page.wait_for_timeout(300)
                        scroller = page.locator(".oc-conversation")
                        page.mouse.move(700, 450)
                        page.mouse.wheel(0, -2)
                        page.wait_for_timeout(150)
                        position = scroller.evaluate("e=>e.scrollTop")
                        update(page, id, code + "\n\nAdditional output while reading.\n\nAnother paragraph.")
                        page.wait_for_timeout(600)
                        assert abs(scroller.evaluate("e=>e.scrollTop") - position) < 10, "A small upward gesture was mistaken for returning to the bottom"
                        page.mouse.wheel(0, 5000)
                        page.wait_for_timeout(300)
                        page.wait_for_function("(()=>{const e=document.querySelector('.oc-conversation');return e.scrollHeight-e.scrollTop-e.clientHeight<10})()")

                        update(page, id, code + "\n\nAdditional output while reading.\n\nAnother paragraph.\n\nFollowing resumes after scrolling down.")
                        page.wait_for_timeout(500)
                        page.wait_for_function("(()=>{const e=document.querySelector('.oc-conversation');return e.scrollHeight-e.scrollTop-e.clientHeight<10})()")

                    def duplicate_code_preview(page):
                        html = "<p class='demo'>Duplicate source</p>"
                        id = thread(f"```html\n{html}\n```\n```css\n.demo{{color:rgb(255,0,0)}}\n```\n```html\n{html}\n```\n```css\n.demo{{color:rgb(0,0,255)}}\n```")
                        page.goto(fixture.BASE + "/chat?thread=" + id)
                        buttons = page.get_by_role("button", name="预览 / 运行")
                        buttons.nth(0).click()
                        frame = page.frame_locator("iframe:not(.oc-preview-candidate)")
                        expect(frame.locator('.demo')).to_have_css('color', 'rgb(255, 0, 0)', timeout=15000)
                        page.get_by_role('button',name='关闭预览').click()
                        buttons.nth(2).click()
                        expect(frame.locator('.demo')).to_have_css('color', 'rgb(0, 0, 255)', timeout=15000)

                    def live_decode(page):
                        page.goto(fixture.BASE + '/chat')
                        page.locator('.oc-chat-composer textarea').wait_for()
                        page.evaluate(r'''()=>{const native=fetch;window.fetch=async(url,init)=>{
                          if(String(url).endsWith('/api/inference/generate/stream'))return new Response(new ReadableStream({start(controller){
                            window.emitLive=event=>controller.enqueue(new TextEncoder().encode('data: '+JSON.stringify(event)+'\n\n'));
                            window.endLive=()=>{controller.enqueue(new TextEncoder().encode('data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"completion_tokens":20}}\n\ndata: [DONE]\n\n'));controller.close()};
                          }}),{headers:{'Content-Type':'text/event-stream','X-Request-ID':'live-fixture'}});
                          return native(url,init);
                        }}''')
                        page.locator('.oc-chat-composer textarea').fill('Live token test')
                        page.locator('.oc-chat-composer button[type=submit]').click()
                        page.wait_for_function("typeof window.emitLive === 'function'")
                        page.evaluate("emitLive({choices:[{delta:{reasoning_content:'Reasoning tokens count too.'},token_ids:[1,2,3,4,5,6,7,8]}]})")
                        expect(page.locator('.oc-live-decode strong')).to_have_text('—')
                        page.evaluate("emitLive({request_id:'another-request',onecat_live_metrics:{decode_tokens_s:999,output_tokens:900}})")
                        page.wait_for_timeout(100)
                        expect(page.locator('.oc-live-decode strong')).to_have_text('—')
                        page.evaluate("emitLive({request_id:'live-fixture',onecat_live_metrics:{decode_tokens_s:8,output_tokens:12,reason:null}})")
                        expect(page.locator('.oc-live-decode strong')).to_have_text('8.00')
                        expect(page.locator('.oc-live-decode')).to_contain_text('12 tokens')
                        expect(page.get_by_role('button',name='停止生成',exact=True)).to_be_visible()
                        page.evaluate("emitLive({request_id:'live-fixture',onecat_live_metrics:{decode_tokens_s:10.5,output_tokens:20,reason:null}})")
                        expect(page.locator('.oc-live-decode strong')).to_have_text('10.50')
                        page.evaluate('endLive()')
                        expect(page.locator('.oc-live-decode')).to_have_count(0)

                    def supported_inventory(page):
                        page.goto(fixture.BASE + '/models')
                        cards = page.locator('.oc-support-card')
                        expect(cards).to_have_count(8)
                        page.get_by_role('combobox',name='模型系列').select_option('DeepSeek')
                        expect(cards).to_have_count(0)
                        page.locator('.oc-support-references > summary').click()
                        expect(page.locator('.oc-support-reference')).to_contain_text('8 × V100')
                        expect(page.get_by_role('button',name='ModelScope 下载')).to_have_count(0)
                        page.get_by_role('combobox',name='模型系列').select_option('')
                        page.get_by_placeholder('搜索模型、量化或能力…').fill('Qwen3.6-27B')
                        expect(cards).to_have_count(2)
                        page.get_by_placeholder('搜索模型、量化或能力…').fill('')
                        expect(cards).to_have_count(8)
                        page.screenshot(path=str(fixture.ARTIFACTS/'supported-models.png'))
                        page.set_viewport_size({'width':390,'height':844})
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                        page.screenshot(path=str(fixture.ARTIFACTS/'supported-models-mobile.png'))

                    for check in [inline_handlers, failed_frame, repeated_ready, completion_metadata, switching_chat, old_cleanup_during_new_request, premature_eof, inspect_reasoning, native_scrollbar_reading, small_upward_gesture, duplicate_code_preview, live_decode, supported_inventory]:
                        selected = os.environ.get("ONECAT_UI_AUDIT_ONLY")
                        if selected and check.__name__ not in selected.split(","):
                            continue
                        page = context.new_page()
                        try:
                            check(page)
                            passed.append(check.__name__)
                        except Exception as error:
                            failures.append({"test": check.__name__, "error": str(error)[:800]})
                        finally:
                            page.close()
                    browser.close()
            finally:
                server.terminate()
                server.wait(timeout=15)
    print(json.dumps({"passed": passed, "failures": failures}, ensure_ascii=False, indent=2))
    assert not failures, "UI audit regressions failed"


if __name__ == "__main__":
    run()
