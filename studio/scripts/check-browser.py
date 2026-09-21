#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Playwright acceptance on a disposable Studio database and simulated GPU inventory.

Run after build:onecat, using a Python with Playwright and Chromium installed.
No inference service or GPU configuration is changed.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / ".artifacts/studio-browser"
ARTIFACTS.mkdir(parents=True, exist_ok=True)
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    PORT = sock.getsockname()[1]
BASE = f"http://127.0.0.1:{PORT}"
SEED = r'''
import os,time,asyncio,json
import uvicorn
from onecat import catalog,db,gpu,gpu_setup,proxy
gpu_setup.helper_check=lambda:{}
from starlette.responses import StreamingResponse
from onecat.config import initialize_paths
from onecat.schemas import Profile
from onecat.app import create_app
initialize_paths()
devices=[{"uuid":"GPU-"+str(i)*36,"name":"Tesla V100-SXM2-32GB","index":i,"compute_capability":[7,0],"memory_total_mib":32768,"memory_used_mib":16000,"processes":[],"power_w":50,"power_limit_w":185,"power_min_w":150,"power_max_w":300,"supported_graphics_clocks_mhz":[]} for i in range(4)]
gpu.snapshot=lambda: {"gpus":devices,"timestamp":time.time(),"sampled_at":time.time(),"source":"test_fixture"}
gpu.selected_devices=lambda ids:[d for d in devices if d["uuid"] in ids]
gpu.control_available=lambda:False
db.put("runtimes","r",{"id":"r","name":"1Cat-vLLM 1.5.0","validated":True,"python_path":"/test/python","release":{"version":"1.5.0"},"capabilities":{"vllm_version":"1.5.0"}})
db.put("models","m",{"id":"m","name":"Qwen3.8-27B-NVFP4","path":"/test/model","bytes":10000000000,"source":"local","quantization":"NVFP4","max_context":262144})
real_capabilities=catalog.capabilities
catalog.capabilities=lambda path,runtime_id: ({"verified":True,"tool_parser":"qwen3_coder","tool_reason":"","vision":True,"vision_reason":"","max_images":1,"accelerators":["mtp"],"mtp_token_options":[1,2,3,4],"recommended":{"max_model_len":262144}} if path=="/test/model" else real_capabilities(path,runtime_id))
p=Profile(id="p",name="Qwen3.8-27B NVFP4",model_path="/test/model",runtime_id="r",gpu_uuids=[d["uuid"] for d in devices],tensor_parallel_size=4).model_dump()
db.put("profiles","p",p)
db.put("engine","active",{"state":"ready","profile_id":"p","profile":p})
async def fixture_stream(request, *args, **kwargs):
    body=await request.json()
    long_reasoning='__onecat_long_reasoning__' in json.dumps(body.get('messages',[]))
    path=os.environ.get('ONECAT_REASONING_FIXTURE' if long_reasoning else 'ONECAT_STREAM_FIXTURE')
    if path:
        from pathlib import Path
        reply=json.loads(Path(path).read_text())['messages'][-1]
        parts=[part for part in reply['content'] if long_reasoning or part['type']=='text']
    elif long_reasoning:
        parts=[{'type':'reasoning','text':('Thinking about animation and scrolling. 保留动效，减少重复渲染。\n\n' * 1500)}, {'type':'text','text':'```html\n<h1>Streamed snake</h1><canvas></canvas>\n```'}]
    else:
        parts=[{'type':'text','text':'```html\n<h1>Streamed snake</h1><canvas></canvas><script>localStorage.setItem("score","1")</script>\n' + '<!-- incremental preview -->\n' * 160 + '```'}]
    async def events():
        size=320 if long_reasoning else 120
        for part in parts:
            text=part.get('text','')
            field='reasoning_content' if part['type']=='reasoning' else 'content'
            for offset in range(0,len(text),size):
                await asyncio.sleep(.025 if long_reasoning else .05)
                yield 'data: '+json.dumps({'choices':[{'delta':{field:text[offset:offset+size]}}]})+'\n\n'
        yield 'data: '+json.dumps({'choices':[{'delta':{},'finish_reason':'stop'}],'usage':{'prompt_tokens':1,'completion_tokens':2048},'onecat_metrics':{'pending':False}})+'\n\n'
        yield 'data: [DONE]\n\n'
    return StreamingResponse(events(),media_type='text/event-stream')
proxy.forward=fixture_stream
uvicorn.run(create_app(),host="127.0.0.1",port=int(os.environ["TEST_PORT"]),log_level="warning")
'''


def run():
    with tempfile.TemporaryDirectory(prefix="onecat-browser-") as state, (ARTIFACTS / "server.log").open("w") as log:
        env = {**os.environ, "ONECAT_STUDIO_HOME": state, "PYTHONPATH": str(ROOT / "studio/backend"), "TEST_PORT": str(PORT)}
        process = subprocess.Popen([str(ROOT / ".venv/bin/python"), "-c", SEED], env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen(BASE + "/api/health", timeout=1)
                    break
                except OSError:
                    time.sleep(.1)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, executable_path=os.environ.get("ONECAT_BROWSER_EXECUTABLE") or None, args=['--disable-gpu','--use-angle=swiftshader'])
                context = browser.new_context(viewport={"width": 1500, "height": 1000})
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.on('console', lambda message: errors.append(message.text) if message.type=='error' and any(value in message.text for value in ['Maximum update depth','Minified React error','onecat message renderer']) else None)
                assert context.request.post(BASE + "/api/auth/setup", data={"password": "browser-fixture-password"}).ok
                page.goto(BASE + "/models")
                page.get_by_role("tab", name="启动预设", exact=True).click()
                page.get_by_role("button", name="新建预设", exact=True).click()
                expect(page.get_by_role("dialog")).to_be_visible()
                page.locator('.oc-profile-advanced').evaluate("element => element.open = true")
                expect(page.locator('option[value="bfloat16"]')).to_be_disabled()
                expect(page.get_by_label("最大生成长度", exact=True)).to_have_value("")
                expect(page.get_by_label("最大上下文 tokens")).to_have_value("262144")
                acceleration = page.get_by_label("推理加速")
                expect(acceleration.locator('option[value="mtp"]')).to_be_enabled()
                acceleration.select_option("mtp")
                mtp = page.get_by_role("radiogroup", name="MTP 预测 token 数")
                expect(mtp.get_by_role("radio", name="4", exact=True)).to_be_checked()
                assert mtp.get_by_role("radio").all_text_contents() == ["1", "2", "3", "4"]
                mtp.get_by_role("radio", name="2", exact=True).click()
                expect(mtp.get_by_role("radio", name="2", exact=True)).to_be_checked()
                tool = page.get_by_role("switch", name="工具调用（API）")
                vision = page.get_by_role("switch", name="图片理解")
                expect(tool).to_be_enabled()
                expect(vision).to_be_enabled()
                tool.click()
                vision.click()
                expect(tool).to_be_checked()
                expect(vision).to_be_checked()
                page.screenshot(path=str(ARTIFACTS / "profile-mtp-capabilities.png"))
                page.get_by_role("button", name="取消", exact=True).click()

                def thread(source):
                    value = context.request.post(BASE + "/api/chat/import", data={"title": "Preview acceptance", "messages": [{"role": "user", "content": "Please build this example"}, {"role": "assistant", "content": source}]}).json()
                    page.goto(BASE + "/chat?thread=" + value["id"])
                    return value["id"]

                page.evaluate("localStorage.setItem('studio-private-fixture', 'private')")
                html = '<h1>Snake test</h1><canvas width="320" height="180"></canvas><button id="inc">Run</button><p id="score">0</p><script>if(localStorage.getItem("studio-private-fixture"))throw Error("Parent storage exposed");localStorage.setItem("score","0");sessionStorage.setItem("session","local");let n=0;document.getElementById("inc").onclick=()=>{localStorage.setItem("score",String(++n));document.getElementById("score").textContent=localStorage.getItem("score")};</script>'
                html = '<button id="edge" style="position:fixed;left:0;top:0;width:8px;padding:0;border:0" onclick="this.textContent=Number(this.textContent)+1">0</button>' + html
                thread("```html\n" + html + "\n```")
                page.get_by_role("button", name="预览 / 运行").first.click()
                original_url = page.url
                panel = page.locator('.oc-preview-panel').bounding_box()
                workspace = page.locator('.oc-chat-workspace').bounding_box()
                assert panel and workspace and .45 <= panel['width'] / workspace['width'] <= .55
                handle = page.locator('.oc-preview-resizer').bounding_box()
                chat = page.locator('.oc-chat-page').bounding_box()
                assert handle['x'] >= chat['x'] + chat['width'] - 1, (handle, chat)
                page.locator('.oc-chat-composer textarea').fill('Keep chatting alongside the preview')
                assert page.url == original_url
                frame = page.frame_locator('iframe:not(.oc-preview-candidate)')
                expect(frame.get_by_text("Snake test")).to_be_visible(timeout=20000)
                frame.locator('#edge').click()
                expect(frame.locator('#edge')).to_have_text('1')
                frame.get_by_role("button", name="Run", exact=True).click()
                expect(frame.locator("#score")).to_have_text("1")
                page.screenshot(path=str(ARTIFACTS / "html-preview.png"))
                page.get_by_role("button", name="关闭预览").click()
                expect(page.locator("iframe")).to_have_count(0)

                react = '''import React, {useState} from 'react';
import {Zap} from 'lucide-react';
import {BarChart, Bar, XAxis} from 'recharts';
export default function App(){const [n,setN]=useState(0);return <div className="p-6 bg-red-500"><Zap/><button onClick={()=>setN(n+1)}>Count {n}</button><BarChart width={300} height={200} data={[{name:'A',value:12},{name:'B',value:20}]}><XAxis dataKey="name"/><Bar dataKey="value"/></BarChart></div>}'''
                id = thread("```tsx\n" + react + "\n```")
                page.get_by_role("button", name="预览 / 运行").first.click()
                frame = page.frame_locator('iframe:not(.oc-preview-candidate)')
                try:
                    expect(frame.get_by_role("button", name="Count 0")).to_be_visible(timeout=15000)
                except Exception:
                    print("Preview diagnostics", page.locator(".oc-preview-panel").inner_text(), flush=True)
                    page.screenshot(path=str(ARTIFACTS / "failure.png"))
                    raise
                frame.get_by_role("button", name="Count 0").click()
                expect(frame.get_by_role("button", name="Count 1")).to_be_visible()
                expect(frame.locator(".recharts-wrapper")).to_be_visible()
                expect(frame.locator(".lucide")).to_be_visible()
                color = frame.locator(".bg-red-500").evaluate("e=>getComputedStyle(e).backgroundColor")
                assert color not in ["rgba(0, 0, 0, 0)", "rgb(255, 255, 255)"], color

                data = context.request.get(BASE + "/api/chat/threads/" + id).json()
                data["messages"][-1]["content"] = [{"type": "text", "text": "```tsx\nimport missing from 'not-installed'; export default function App(){return <div>{missing}</div>}\n```"}]
                assert context.request.put(BASE + f"/api/chat/threads/{id}/messages", data={"messages": data["messages"]}).ok
                page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
                expect(page.get_by_role("alert")).to_contain_text("Unsupported dependency", timeout=20000)
                expect(frame.get_by_role("button", name="Count 1")).to_be_visible()
                page.screenshot(path=str(ARTIFACTS / "react-last-good.png"))
                page.get_by_role("button", name="关闭预览").click()

                outbound = []
                context.route("https://onecat-preview.invalid/**", lambda route: (outbound.append(route.request.url), route.abort()))
                isolation = '''<p id="navigation">navigation blocked</p><p id="parent">checking</p><p id="cookie">checking</p><p id="network">checking</p><script>
try{parent.document.body;document.getElementById('parent').textContent='BAD'}catch(e){document.getElementById('parent').textContent='parent blocked'}
try{document.cookie;document.getElementById('cookie').textContent='BAD'}catch(e){document.getElementById('cookie').textContent='cookie blocked'}
fetch('/api/settings').then(()=>document.getElementById('network').textContent='BAD').catch(()=>document.getElementById('network').textContent='network blocked');</script><button onclick="location.href='https://onecat-preview.invalid/navigation'">Navigate away</button>'''
                thread("```html\n" + isolation + "\n```")
                page.get_by_role("button", name="预览 / 运行").first.click()
                frame = page.frame_locator('iframe:not(.oc-preview-candidate)')
                expect(frame.get_by_text("parent blocked")).to_be_visible(timeout=20000)
                expect(frame.get_by_text("cookie blocked")).to_be_visible()
                expect(frame.get_by_text("network blocked")).to_be_visible()
                expect(frame.get_by_text("navigation blocked")).to_be_visible()
                frame.get_by_role("button", name="Navigate away").click()
                page.wait_for_timeout(500)
                assert not outbound, outbound
                page.set_viewport_size({"width": 390, "height": 844})
                expect(page.locator(".oc-preview-panel")).to_have_css("position", "relative")
                expect(page.locator('.oc-chat-composer textarea')).to_be_in_viewport()
                panel = page.locator('.oc-preview-panel').bounding_box()
                assert panel and panel['height'] < 844 * .65 and panel['y'] > 200
                page.screenshot(path=str(ARTIFACTS / "mobile-preview.png"))
                page.get_by_role("button", name="关闭预览").click()
                page.goto(BASE + "/service")
                page.screenshot(path=str(ARTIFACTS / "mobile-service.png"))
                page.set_viewport_size({"width": 1500, "height": 1000})
                fixture = os.environ.get('ONECAT_PREVIEW_REGRESSION')
                source = Path(fixture).read_text() if fixture else None
                payload = json.loads(source) if source else {
                    'title': 'Truncated HTML regression', 'settings': {'max_tokens': 1024},
                    'messages': [{'role': 'user', 'content': 'Build an HTML snake game'},
                                 {'role': 'assistant', 'content': '```html\n<html><head><style>body { color:', 'metadata': {'finish_reason':'length'}}]}
                truncated = context.request.post(BASE + '/api/chat/import', data=payload).json()['id']
                page.goto(BASE + '/chat?thread=' + truncated)
                expect(page.locator('.oc-output-limit')).to_contain_text('生成长度限制')
                page.get_by_role('button', name='预览 / 运行').last.click()
                expect(page.locator('.oc-preview-status')).to_contain_text('代码不完整', timeout=15000)
                for width in [1000, 850, 1500]:
                    page.set_viewport_size({'width':width, 'height':1000})
                    page.locator('.oc-chat-composer textarea').fill('Conversation remains usable')
                    expect(page.locator('.oc-chat-composer textarea')).to_be_in_viewport()
                    page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
                    page.wait_for_timeout(300)
                page.get_by_role('button', name='关闭预览').click()
                expect(page.locator('iframe')).to_have_count(0)
                broken = context.new_page()
                broken.route('**/preview/runtime.js', lambda route: route.abort())
                broken.goto(BASE + '/chat?thread=' + truncated)
                broken.locator('.oc-chat-composer textarea').fill('Unsaved draft survives')
                broken.get_by_role('button', name='预览 / 运行').last.click()
                expect(broken.locator('.oc-preview-error')).to_be_visible()
                expect(broken.locator('.oc-preview-failed')).to_have_count(0)
                expect(broken.locator('.oc-chat-composer textarea')).to_have_value('Unsaved draft survives')
                broken.get_by_role('button', name='关闭预览').click()
                expect(broken.locator('.oc-preview-panel')).to_have_count(0)
                broken.close()
                page.goto(BASE + '/chat?thread=' + truncated)
                page.get_by_role('button', name='预览 / 运行').last.click()
                page.get_by_role('button', name='自动长度重新生成', exact=True).click()
                expect(page.get_by_role('button', name='停止生成', exact=True)).to_be_visible()
                page.locator('.oc-chat-composer textarea').fill('Keep this draft during generation')
                for _ in range(8):
                    page.locator('.oc-message-actions button').last.hover(force=True, timeout=3000)
                    page.wait_for_timeout(100)
                    page.mouse.move(1490, 50)
                expect(page.get_by_role('button', name='停止生成', exact=True)).to_have_count(0, timeout=20000)
                expect(page.locator('.oc-preview-status')).to_contain_text('运行中', timeout=20000)
                expect(page.frame_locator('iframe:not(.oc-preview-candidate)').locator('canvas')).to_be_visible()
                expect(page.locator('.oc-chat-composer textarea')).to_have_value('Keep this draft during generation')
                page.get_by_role('button', name='关闭预览').click()
                page.goto(BASE + '/chat')
                page.locator('.oc-chat-composer textarea').fill('__onecat_long_reasoning__')
                page.locator('.oc-chat-composer button[type=submit]').click()
                # Generation acceptance precedes the asynchronous new-thread navigation.
                # Measure DOM stability after the destination conversation has mounted.
                page.wait_for_url("**/chat?thread=*")
                page.locator('.oc-reasoning summary').click(timeout=15000)
                page.evaluate("""window.streamProbe={maxAnimated:0,markerMoved:false,marker:null,animated:false,tasks:[]};
                  new PerformanceObserver(list=>{for(const entry of list.getEntries())streamProbe.tasks.push(entry.duration)}).observe({type:'longtask',buffered:false});
                  window.streamProbeTimer=setInterval(()=>{const status=document.querySelector('.oc-generation-status');
                    if(status){if(streamProbe.marker && streamProbe.marker!==status)streamProbe.markerMoved=true;streamProbe.marker=status;}
                    streamProbe.maxAnimated=Math.max(streamProbe.maxAnimated,document.querySelectorAll('[data-sd-animate]').length);
                    streamProbe.animated ||= document.getAnimations().some(a=>a.animationName==='sd-blurIn');
                  },50);""")
                page.wait_for_timeout(700)
                scroller=page.locator('.oc-conversation')
                page.mouse.move(700,450)
                page.mouse.wheel(0,-50000)
                scroller.evaluate('(element)=>element.scrollTop=0')
                page.wait_for_timeout(800)
                assert scroller.evaluate('(element)=>element.scrollTop') < 40, 'Reading was pulled back to the end'
                page.get_by_role('button',name='历史记录',exact=True).click()
                page.get_by_role('button',name='固定历史',exact=True).click()
                expect(page.locator('.oc-history-pinned')).to_be_visible()
                page.set_viewport_size({'width':1440,'height':1000})
                page.wait_for_timeout(300)
                assert scroller.evaluate('(element)=>element.scrollTop') < 40, 'Pinning history interrupted reading'
                expect(page.locator('.oc-chat-composer textarea')).to_be_in_viewport()
                page.locator('.oc-chat-composer textarea').fill('Draft preserved while reading thoughts')
                expect(page.get_by_role('button',name='停止生成',exact=True)).to_have_count(0,timeout=30000)
                page.wait_for_timeout(450)
                expect(page.locator('.oc-preview-panel')).to_have_count(0)
                expect(page.locator('.oc-chat-composer textarea')).to_have_value('Draft preserved while reading thoughts')
                probe=page.evaluate("""()=>{clearInterval(streamProbeTimer);return {maxAnimated:streamProbe.maxAnimated,settledAnimated:document.querySelectorAll('[data-sd-animate]').length,animated:streamProbe.animated,markerMoved:streamProbe.markerMoved,maxLongTask:Math.max(0,...streamProbe.tasks)}}""")
                # Allow scheduling variation under this deliberately accelerated
                # replay; old spans must disappear, not accumulate with history.
                assert probe['animated'] and not probe['markerMoved'] and probe['maxAnimated'] < 10000 and probe['settledAnimated'] == 0, probe
                thread_id=page.url.split('thread=')[1]
                saved=context.request.get(BASE + '/api/chat/threads/' + thread_id).json()['messages'][-1]
                assert saved['metadata']['finish_reason']=='stop'
                reasoning_fixture=os.environ.get('ONECAT_REASONING_FIXTURE')
                if reasoning_fixture:
                    expected=json.loads(Path(reasoning_fixture).read_text())['messages'][-1]['content']
                    assert saved['content']==expected, 'Streamed text was lost or rewritten'
                print('Long reasoning regression',json.dumps(probe),flush=True)
                page.screenshot(path=str(ARTIFACTS/'long-reasoning.png'))
                page.get_by_role('button',name='关闭历史',exact=True).click()
                expect(page.locator('.oc-history-pinned')).to_have_count(0)
                page.set_viewport_size({'width':1500,'height':1000})
                page.get_by_role('button',name='预览 / 运行').last.click()
                expect(page.locator('.oc-preview-toolbar')).to_be_visible()
                expect(page.locator('.oc-chat-composer textarea')).to_have_value('Draft preserved while reading thoughts')
                page.get_by_role('button',name='关闭预览').click()
                # A live chat keeps generating while the other mode is mounted.
                page.goto(BASE+'/chat')
                page.locator('.oc-chat-composer textarea').fill('Check mode switching during generation')
                with page.expect_response(lambda response: response.url.endswith('/generate') and response.request.method=='POST') as started:
                    page.locator('.oc-chat-composer button[type=submit]').click()
                assert started.value.ok
                expect(page.get_by_role('button',name='停止生成',exact=True)).to_be_visible()
                page.wait_for_url("**/chat?thread=*")
                live_url=page.url
                page.locator('.oc-chat-composer textarea').fill('Do not lose this live draft')
                page.get_by_role('tab',name='Agent',exact=True).click()
                expect(page.get_by_label('Agent 任务',exact=True)).to_be_visible()
                page.get_by_role('tab',name='聊天',exact=True).click()
                assert page.url==live_url,(page.url,live_url)
                expect(page.locator('.oc-chat-composer textarea')).to_have_value('Do not lose this live draft')
                for _ in range(100):
                    restored=context.request.get(BASE+'/api/chat/threads/'+live_url.split('thread=')[1]).json()
                    if restored['messages'][-1].get('metadata',{}).get('finish_reason')=='stop':
                        break
                    page.wait_for_timeout(100)
                else:
                    raise AssertionError('Chat did not complete after mode switching: '+repr(restored))
                expect(page.get_by_role('button',name='停止生成',exact=True)).to_have_count(0,timeout=5000)
                expect(page.locator('.oc-preview-panel')).to_have_count(0)
                engine_state = context.request.get(BASE + "/api/inference/status").json()
                loading = {**engine_state, "state": "loading", "phase": "compiling", "elapsed_s": 120, "job_id": "fixture", "startup_job": {"id": "fixture", "state": "running"}, "actions": {"cancel": True, "retry": False, "stop": False}}
                observed = []
                def status_route(route):
                    observed.append(time.monotonic())
                    route.fulfill(json=loading)
                page.route("**/api/inference/status", status_route)
                for destination in ["models", "performance", "service", "chat"]:
                    page.goto(BASE + "/" + destination)
                    expect(page.locator(".oc-launch-progress" if destination == "service" else ".oc-launch-banner")).to_contain_text("编译算子")
                previous_reads = len(observed)
                page.evaluate("window.dispatchEvent(new Event('online'))")
                page.wait_for_timeout(200)
                assert len(observed) > previous_reads
                loading.update({"state": "ready", "phase": "ready"})
                expect(page.locator(".oc-launch-banner")).to_have_count(0, timeout=4000)
                page.unroute("**/api/inference/status", status_route)
                page.goto(BASE + "/settings")
                page.locator("select").filter(has=page.locator('option[value="zh-CN"]')).select_option("en")
                page.locator("select").filter(has=page.locator('option[value="dark"]')).select_option("dark")
                page.get_by_role("button", name="Save settings", exact=True).last.click()
                expect(page.get_by_role("heading", name="Settings", exact=True)).to_be_visible()
                page.goto(BASE + "/service")
                expect(page.get_by_role("heading", name="Service", exact=True)).to_be_visible()
                page.screenshot(path=str(ARTIFACTS / "dark-english-service.png"))
                assert not errors, errors
                browser.close()
                print(json.dumps({"passed": ["new preset", "BF16 hardware gate", "automatic output", "HTML interaction", "React hooks", "Lucide", "Recharts", "Tailwind", "unknown dependency", "last good preview", "sandbox DOM/cookies/network", "mobile split pane", "desktop half-width pane", "truncated transcript", "streaming regeneration with preview", "long reasoning scroll and preserved animation", "manual preview only", "isolated preview storage", "preview load failure preserves draft", "iframe cleanup", "startup navigation", "online resync", "ready recovery", "English and dark theme"], "screenshots": str(ARTIFACTS)}))
        finally:
            process.terminate()
            process.wait(timeout=15)


if __name__ == "__main__":
    run()
