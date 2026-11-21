#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""CPU-only streaming code, follow-scroll, and rolling usage browser regression."""

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

USAGE = r"""
with db.connect() as conn:
    for id, age, prompt, output, cached in [('today', 60,1234567,3456,1000000),('earlier',2*86400,200,30,None),('week',6*86400,400,50,100)]:
        conn.execute('INSERT INTO requests(id,started,status,source,prompt_tokens,completion_tokens,metrics) VALUES(?,?,?,?,?,?,?)',(id,time.time()-age,200,'api',prompt,output,json.dumps({'cached_tokens':cached})))
"""


def run():
    with tempfile.TemporaryDirectory(prefix="onecat-code-usage-") as temporary:
        directory = Path(temporary)
        code = "// Streaming code stays readable 🌊\n" + "\n".join(
            f'const value{i} = "第 {i} 行 · keep syntax colors and animate new lines";'
            for i in range(360)
        )
        text = "```javascript\n" + code + "\n```\n\nThe example is ready."
        transcript = directory / "stream.json"
        transcript.write_text(
            json.dumps({"messages": [{"content": [{"type": "text", "text": text}]}]})
        )
        env = {
            **os.environ,
            "ONECAT_STUDIO_HOME": str(directory / "state"),
            "PYTHONPATH": str(fixture.ROOT / "studio/backend"),
            "TEST_PORT": str(fixture.PORT),
            "ONECAT_STREAM_FIXTURE": str(transcript),
        }
        seed = fixture.SEED.replace(
            "proxy.forward=fixture_stream", USAGE + """
from starlette.responses import JSONResponse
async def code_and_title(request, endpoint, source):
    if source == "chat_title":
        return JSONResponse({"choices": [{"message": {"content": "流式代码显示示例"}}]})
    return await fixture_stream(request, endpoint, source)
proxy.forward = code_and_title
"""
        )
        with (fixture.ARTIFACTS / "code-usage-server.log").open("w") as log:
            process = subprocess.Popen(
                [str(fixture.ROOT / ".venv/bin/python"), "-c", seed],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                for _ in range(100):
                    try:
                        urllib.request.urlopen(fixture.BASE + "/api/health", timeout=1)
                        break
                    except OSError:
                        time.sleep(0.1)
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=True, executable_path=os.environ.get("ONECAT_BROWSER_EXECUTABLE") or None)
                    context = browser.new_context(
                        viewport={"width": 1500, "height": 1000},
                        permissions=["clipboard-read", "clipboard-write"],
                    )
                    assert context.request.post(
                        fixture.BASE + "/api/auth/setup",
                        data={"password": "code-usage-fixture-password"},
                    ).ok
                    page = context.new_page()
                    # Finish one older color pass after the final pass. The text
                    # must already be complete when the following paragraph paints.
                    page.add_init_script("""window.Worker = class extends Worker {
                      set onmessage(callback) {
                        super.onmessage = event => {
                          const lines = event.data?.result?.tokens;
                          const delay = lines?.length >= 356 && lines.length < 361 ? 650 : 0;
                          setTimeout(() => callback.call(this, event), delay);
                        };
                      }
                    }""")
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(fixture.BASE + "/chat")
                    page.evaluate("""expected => {
                      window.codeOrder = { frames: 0, incomplete: false };
                      function sample() {
                        const msg = document.querySelector('.oc-message-assistant');
                        if (msg?.querySelector('p:last-child') && msg.textContent.includes('The example is ready.')) {
                          const code = msg.querySelector('[data-streamdown="code-block-body"] pre > code');
                          const rows = Array.from(code?.children || []);
                          const masked = Array.from(CSS.highlights?.get('onecat-pending-code') || [])
                            .some(range => code?.contains(range.startContainer));
                          codeOrder.frames++;
                          codeOrder.incomplete ||= rows.map(row => row.textContent).join('\\n') !== expected ||
                            rows.some(row => row.style.display === 'none') || masked;
                        }
                        window.codeOrderFrame = requestAnimationFrame(sample);
                      }
                      sample();
                    }""", code)
                    page.evaluate("""window.codeProbe={animated:false,count:0,tasks:[],removedPreview:0};
                      new MutationObserver(records=>{for(const record of records)for(const node of record.removedNodes)if(node.nodeType===1){codeProbe.removedPreview+=Number(node.matches('.oc-code-preview-button'))+node.querySelectorAll('.oc-code-preview-button').length}}).observe(document.body,{childList:true,subtree:true});
                      new PerformanceObserver(list=>{for(const e of list.getEntries())codeProbe.tasks.push(e.duration)}).observe({type:'longtask'});
                      window.codeProbeTimer=setInterval(()=>{const active=document.getAnimations().filter(a=>a.id==='onecat-code-arrival');codeProbe.animated ||= active.length>0;codeProbe.count=Math.max(codeProbe.count,active.length)},30);""")
                    page.locator(".oc-chat-composer textarea").fill("Write a long code example")
                    page.locator(".oc-chat-composer button[type=submit]").click()
                    page.wait_for_function("window.codeProbe.animated", timeout=15000)
                    expect(page.locator(".oc-chat-title")).to_have_count(0)
                    page.get_by_role('button', name='预览 / 运行').wait_for()
                    page.evaluate("""window.previewButton=document.querySelector('.oc-code-preview-button');
                      window.streamFooter=document.querySelector('.oc-stream-actions');
                      window.streamCopyButton=streamFooter.querySelector('button');
                      window.streamFooterTop=streamFooter.getBoundingClientRect().top;
                      window.streamFooterDrift=0;
                      window.streamFooterRemounts=0;
                      window.streamFooterTimer=setInterval(()=>{const current=document.querySelector('.oc-stream-actions');if(current&&current!==streamFooter)streamFooterRemounts++;if(current===streamFooter)streamFooterDrift=Math.max(streamFooterDrift,Math.abs(current.getBoundingClientRect().top-streamFooterTop))},20);""")
                    scroller = page.locator(".oc-conversation")
                    page.wait_for_function(
                        "document.querySelector('.oc-conversation').scrollHeight>1500"
                    )

                    def at_bottom():
                        page.wait_for_function(
                            "(()=>{const e=document.querySelector('.oc-conversation');return e.scrollHeight-e.scrollTop-e.clientHeight<10})()",
                            timeout=3000,
                        )

                    at_bottom()
                    # Eventual bottom alignment misses a frame of displacement on every update.
                    # Watch the same fence while at least five distinct line counts pass on screen.
                    trace = scroller.evaluate("""viewport => new Promise(resolve => {
                      const box = viewport.querySelector('[data-streamdown="code-block"]');
                      const code = box.querySelector('pre > code');
                      const counts = new Set();
                      let low = Infinity, high = -Infinity, maxGap = 0, blurred = false;
                      let raw = -1, previousVisible = 0, partialFrames = 0, monotonic = true;
                      const start = performance.now();
                      function sample() {
                        const edge = box.getBoundingClientRect().bottom - viewport.getBoundingClientRect().bottom;
                        low = Math.min(low, edge); high = Math.max(high, edge);
                        maxGap = Math.max(maxGap, viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight);
                        counts.add(code.childElementCount);
                        const received = code.textContent.length;
                        let visible = received;
                        for (const row of code.children) {
                          if (row.style.display === 'none') visible -= row.textContent.length;
                        }
                        for (const range of CSS.highlights?.get('onecat-pending-code') || []) {
                          if (code.contains(range.startContainer)) visible -= range.toString().length;
                        }
                        if (received === raw && visible > previousVisible) partialFrames++;
                        monotonic &&= visible >= previousVisible;
                        raw = received; previousVisible = visible;
                        blurred ||= document.getAnimations().some(a => a.id === 'onecat-code-arrival' &&
                          a.effect.getKeyframes().some(k => k.filter?.includes('blur')));
                        if ((counts.size < 6 || partialFrames < 3) && performance.now() - start < 2500) requestAnimationFrame(sample);
                        else resolve({counts: counts.size, drift: high - low, maxGap, blurred, partialFrames, monotonic});
                      }
                      requestAnimationFrame(sample);
                    })""")
                    assert trace["counts"] >= 6 and trace["drift"] <= 2 and trace["maxGap"] <= 1 and not trace["blurred"], trace
                    assert trace["partialFrames"] >= 3 and trace["monotonic"], trace
                    # A height change after the message commit must also be followed.
                    page.locator('[data-streamdown="code-block-body"]').first.evaluate(
                        "e=>e.style.paddingBottom='120px'"
                    )
                    at_bottom()
                    scroller.click(position={"x": 4, "y": 120})
                    page.wait_for_timeout(400)
                    at_bottom()  # Clicking the gutter is not scrolling upward.
                    page.mouse.move(700, 450)
                    page.mouse.wheel(0, -600)
                    page.wait_for_timeout(150)
                    stopped_at = scroller.evaluate("e=>e.scrollTop")
                    page.wait_for_timeout(650)
                    assert abs(scroller.evaluate("e=>e.scrollTop") - stopped_at) < 40, (
                        "Reading was pulled to the end"
                    )
                    expect(page.get_by_role("button", name="回到最新", exact=True)).to_be_visible()
                    page.get_by_role("button", name="回到最新", exact=True).click()
                    at_bottom()
                    page.wait_for_timeout(400)
                    at_bottom()
                    page.screenshot(path=str(fixture.ARTIFACTS / "streaming-footer-fixed.png"))
                    first_line = page.locator(
                        '[data-streamdown="code-block-body"] pre > code > span'
                    ).first
                    assert first_line.evaluate("e=>e.getAnimations().length") == 0, (
                        "Old code line reanimated"
                    )
                    page.emulate_media(reduced_motion="reduce")
                    page.wait_for_timeout(350)
                    assert not page.evaluate(
                        "document.getAnimations().some(a=>a.id==='onecat-code-arrival')"
                    )
                    page.emulate_media(reduced_motion="no-preference")
                    page.wait_for_function(
                        "document.getAnimations().some(a=>a.id==='onecat-code-arrival')"
                    )
                    page.emulate_media(reduced_motion="reduce")
                    page.wait_for_timeout(350)
                    assert not page.evaluate(
                        "document.getAnimations().some(a=>a.id==='onecat-code-arrival')"
                    )
                    page.emulate_media(reduced_motion="no-preference")
                    page.locator(".oc-chat-composer textarea").fill(
                        "Unsent draft while code streams"
                    )
                    expect(page.get_by_role("button", name="停止生成", exact=True)).to_have_count(
                        0, timeout=30000
                    )
                    page.wait_for_timeout(400)
                    at_bottom()
                    expect(page.locator(".oc-preview-panel")).to_have_count(0)
                    expect(page.locator(".oc-chat-title")).to_have_text("流式代码显示示例")
                    # The long answer ends on the preview control. Check before any
                    # Playwright click, which would scroll an offscreen button into view.
                    assert page.evaluate("""() => {
                      const fence = document.querySelector('.oc-message-assistant [data-streamdown="code-block"]');
                      const box = previewButton.getBoundingClientRect();
                      const viewport = document.querySelector('.oc-conversation').getBoundingClientRect();
                      const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
                      const conclusion = document.querySelector('.oc-message-assistant .oc-markdown-block:last-child p');
                      return conclusion?.textContent === 'The example is ready.' && conclusion.getBoundingClientRect().bottom <= box.top
                        && fence.getBoundingClientRect().bottom <= box.top && box.top >= viewport.top
                        && box.bottom <= viewport.bottom && previewButton.contains(hit);
                    }"""), "Preview cannot be opened directly from the end of the code"
                    assert page.evaluate("""()=>{clearInterval(streamFooterTimer);return codeProbe.removedPreview===0 &&
                      document.querySelector('.oc-code-preview-button')===previewButton &&
                      document.querySelector('.oc-stream-actions')===streamFooter &&
                      streamFooter.querySelector('button')===streamCopyButton &&
                      streamFooterDrift<1 && streamFooterRemounts===0 &&
                      !streamFooter.querySelector('button:disabled')}"""), 'Docked answer controls moved, remounted or stayed disabled at completion'
                    expect(page.locator(".oc-chat-composer textarea")).to_have_value(
                        "Unsent draft while code streams"
                    )
                    assert not page.evaluate(
                        "document.getAnimations().some(a=>a.id==='onecat-code-arrival')"
                    )
                    thread_id = page.url.split("thread=")[1]
                    saved = context.request.get(
                        fixture.BASE + "/api/chat/threads/" + thread_id
                    ).json()["messages"][-1]
                    assert saved["content"] == [{"type": "text", "text": text}]
                    assert saved["metadata"]["usage"]["completion_tokens"] == 2048
                    page.wait_for_timeout(650)
                    order = page.evaluate("() => { cancelAnimationFrame(codeOrderFrame); return codeOrder }")
                    assert order["frames"] and not order["incomplete"], order
                    page.locator('[data-streamdown="code-block-copy-button"]').first.click()
                    copied = page.evaluate("navigator.clipboard.readText()")
                    assert copied.rstrip() == code, "Copy changed code or included UI markup"
                    probe = page.evaluate(
                        "()=>{clearInterval(codeProbeTimer);return {animated:codeProbe.animated,peakAnimations:codeProbe.count,maxLongTask:Math.max(0,...codeProbe.tasks)}}"
                    )
                    page.screenshot(path=str(fixture.ARTIFACTS / "streaming-code-follow.png"))
                    page.goto(fixture.BASE + "/service")
                    expect(page.locator('[data-usage="input"] strong')).to_have_text("1,234,567")
                    expect(page.locator('[data-usage="output"] strong')).to_have_text("3,456")
                    expect(page.locator('[data-usage="cache"] strong')).to_have_text("1,000,000")
                    page.get_by_role("button", name="近 3 天", exact=True).click()
                    expect(page.locator('[data-usage="input"] strong')).to_have_text("1,234,767")
                    coverage = page.locator(".oc-usage-coverage")
                    coverage.locator("summary").click()
                    expect(coverage.get_by_text("缓存命中 tokens · 已采集 1 / 2 次", exact=True)).to_be_visible()
                    page.get_by_role("button", name="近 7 天", exact=True).click()
                    expect(page.locator('[data-usage="input"] strong')).to_have_text("1,235,167")
                    expect(page.locator('[data-usage="output"] strong')).to_have_text("3,536")
                    expect(page.locator('[data-usage="cache"] strong')).to_have_text("1,000,100")
                    page.screenshot(path=str(fixture.ARTIFACTS / "usage-desktop.png"))
                    page.set_viewport_size({"width": 390, "height": 844})
                    page.locator(".oc-token-usage").scroll_into_view_if_needed()
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    page.screenshot(path=str(fixture.ARTIFACTS / "usage-mobile.png"))
                    assert not errors, errors
                    print(
                        json.dumps(
                            {
                                "code_stream": probe,
                                "code_edge": trace,
                                "auto_follow": True,
                                "manual_scroll_pauses": True,
                                "animation_preferences": True,
                                "exact_text_and_copy": True,
                                "usage_periods": [1, 3, 7],
                                "missing_cache_is_labelled": True,
                                "mobile": True,
                                "errors": errors,
                            }
                        )
                    )
                    browser.close()
            finally:
                process.terminate()
                process.wait(timeout=15)


if __name__ == "__main__":
    run()
