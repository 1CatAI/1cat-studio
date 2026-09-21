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
        text = "```javascript\n" + code + "\n```"
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
            "proxy.forward=fixture_stream", USAGE + "\nproxy.forward=fixture_stream"
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
                    browser = pw.chromium.launch(headless=True)
                    context = browser.new_context(
                        viewport={"width": 1500, "height": 1000},
                        permissions=["clipboard-read", "clipboard-write"],
                    )
                    assert context.request.post(
                        fixture.BASE + "/api/auth/setup",
                        data={"password": "code-usage-fixture-password"},
                    ).ok
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(fixture.BASE + "/chat")
                    page.evaluate("""window.codeProbe={animated:false,count:0,tasks:[],removedPreview:0};
                      new MutationObserver(records=>{for(const record of records)for(const node of record.removedNodes)if(node.nodeType===1){codeProbe.removedPreview+=Number(node.matches('.oc-code-preview-button'))+node.querySelectorAll('.oc-code-preview-button').length}}).observe(document.body,{childList:true,subtree:true});
                      new PerformanceObserver(list=>{for(const e of list.getEntries())codeProbe.tasks.push(e.duration)}).observe({type:'longtask'});
                      window.codeProbeTimer=setInterval(()=>{const active=document.getAnimations().filter(a=>a.id==='onecat-code-arrival');codeProbe.animated ||= active.length>0;codeProbe.count=Math.max(codeProbe.count,active.length)},30);""")
                    page.locator(".oc-chat-composer textarea").fill("Write a long code example")
                    page.locator(".oc-chat-composer button[type=submit]").click()
                    page.wait_for_function("window.codeProbe.animated", timeout=15000)
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
                    page.evaluate(
                        "localStorage.setItem('onecat_stream_animation','off');window.dispatchEvent(new Event('onecat:animation'))"
                    )
                    page.wait_for_timeout(350)
                    assert not page.evaluate(
                        "document.getAnimations().some(a=>a.id==='onecat-code-arrival')"
                    )
                    page.evaluate(
                        "localStorage.removeItem('onecat_stream_animation');window.dispatchEvent(new Event('onecat:animation'))"
                    )
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
                    assert page.evaluate("""()=>{clearInterval(streamFooterTimer);return codeProbe.removedPreview===0 &&
                      document.querySelector('.oc-code-preview-button')===previewButton &&
                      !streamFooter.isConnected && !streamCopyButton.isConnected &&
                      streamFooterDrift<1 && streamFooterRemounts===0 &&
                      !document.querySelector('.oc-stream-footer-layer') &&
                      !!document.querySelector('.oc-message-assistant .oc-message-actions button')}"""), 'Stream controls moved, remounted or failed to settle into the message footer'
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
                    expect(page.locator('[data-usage="cache"]')).to_contain_text("1 次未采集")
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
