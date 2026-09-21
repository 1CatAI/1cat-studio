#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Browser acceptance using official Codex and a fake provider; never accesses GPUs."""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.request

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get('AGENT_BROWSER_OUT', ROOT / '.artifacts/agent-integration/browser'))
OUT.mkdir(parents=True, exist_ok=True)
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    PORT = sock.getsockname()[1]
BASE = f'http://127.0.0.1:{PORT}'
SEED = r'''
import asyncio,json,os,time
import uvicorn
from onecat import db,engine,gpu,gpu_setup,telemetry
from onecat.agent import tasks
from onecat.app import create_app
from onecat.config import initialize_paths
initialize_paths()
gpu_setup.helper_check=lambda:{}
gpu.snapshot=lambda:{"gpus":[],"timestamp":time.time(),"sampled_at":time.time(),"source":"fixture"}
gpu.control_available=lambda:False
engine.status=lambda:db.get("engine","active",{"state":"stopped"})
async def idle():
    await asyncio.Event().wait()
telemetry.monitor=idle
profile={"id":"mock","name":"Local coding model (test)","served_model_name":"onecat-local-test","tool_calling":True,"max_model_len":32768,"gpu_uuids":[],"speculative_config":None,"default_sampling":{"temperature":0.7,"top_p":0.9,"max_tokens":None}}
db.put('profiles','mock',profile)
db.put('engine','active',{'state':'ready','port':1,'profile_id':'mock','instance_id':'fake','profile':profile})
html='<!doctype html><html><body><h1>Agent preview</h1><button id="count" onclick="this.textContent=Number(this.textContent)+1">0</button></body></html>'
async def fake(self,payload):
    self.record['model_calls']+=1
    await self.ingest({'method':'turn/plan/updated','params':{'plan':[{'step':'Create and verify project files','status':'completed'}]}})
    if 'slow-task' in self.prompt:
        for i in range(100):
            await asyncio.sleep(.15)
            yield {'choices':[{'delta':{'content':'Streaming progress '+str(i)+' 很长的测试内容。\n\n'},'finish_reason':None}]}
    elif self.calls_this_turn==1:
        cmd='python3 -c '+__import__('shlex').quote('from pathlib import Path; Path("index.html").write_text('+repr(html)+'); print("FILE CREATED")')
        yield {'choices':[{'delta':{'tool_calls':[{'index':0,'id':'call1','function':{'name':'exec_command','arguments':json.dumps({'cmd':cmd,'login':False})}}]},'finish_reason':'tool_calls'}]}
        return
    else:
        for text in ['已经创建项目文件。\n\n','可以从右侧文件面板预览。\n\n','```html\n'+html+'\n```']:
            await asyncio.sleep(.2)
            yield {'choices':[{'delta':{'content':text},'finish_reason':None}]}
    yield {'choices':[{'delta':{},'finish_reason':'stop'}],'usage':{'prompt_tokens':80,'completion_tokens':40,'prompt_tokens_details':{'cached_tokens':20}}}
    self.record['usage'].update(input_tokens=160,output_tokens=80,cached_tokens=40)
    self.emit({'type':'usage','usage':dict(self.record['usage']),'model_calls':self.record['model_calls']})
tasks.Run.infer=fake
uvicorn.run(create_app(),host='127.0.0.1',port=int(os.environ['AGENT_TEST_PORT']),log_level='warning')
'''


def check_conversation_navigation(page, context):
    tabs = page.get_by_role('tablist', name='对话模式', exact=True)
    chat = tabs.get_by_role('tab', name='聊天', exact=True)
    agent = tabs.get_by_role('tab', name='Agent', exact=True)
    expect(page.locator('.oc-nav-capsule .oc-rail-link')).to_have_count(5)
    expect(page.get_by_role('link', name='对话', exact=True)).to_have_count(1)
    expect(page.locator('.oc-nav-capsule .oc-rail-link').filter(has_text='Agent')).to_have_count(0)
    project = page.get_by_label('Agent 项目', exact=True).input_value()
    page.get_by_label('Agent 任务', exact=True).fill('Agent 草稿：保留项目和任务')
    chat.click()
    expect(chat).to_have_attribute('aria-selected', 'true')
    page.locator('.oc-chat-composer textarea').fill('聊天草稿 👋')
    agent.click()
    expect(page.get_by_label('Agent 任务', exact=True)).to_have_value('Agent 草稿：保留项目和任务')
    expect(page.get_by_label('Agent 项目', exact=True)).to_have_value(project)
    chat.click()
    expect(page.locator('.oc-chat-composer textarea')).to_have_value('聊天草稿 👋')
    imported = context.request.post(BASE+'/api/chat/import', data={
        'title':'模式切换检查', 'messages':[{'role':'user','content':'A retained conversation'}],
    }).json()['id']
    page.goto(BASE+'/chat?thread='+imported)
    expect(page.locator('.oc-conversation')).to_contain_text('A retained conversation')
    page.locator('.oc-chat-composer textarea').fill('这一段属于历史对话')
    chat_url = page.url
    agent.click()
    page.get_by_role('link',name='模型库',exact=True).click()
    expect(tabs).to_have_count(0)
    page.get_by_role('link',name='对话',exact=True).click()
    expect(agent).to_have_attribute('aria-selected','true')
    chat.click()
    assert page.url == chat_url
    expect(page.locator('.oc-chat-composer textarea')).to_have_value('这一段属于历史对话')
    page.go_back()
    expect(agent).to_have_attribute('aria-selected','true')
    page.go_forward()
    expect(chat).to_have_attribute('aria-selected','true')
    # Roving tab focus keeps the keyboard on the switch across route transitions.
    chat.focus()
    page.keyboard.press('ArrowRight')
    expect(agent).to_be_focused()
    expect(agent).to_have_attribute('aria-selected','true')
    page.keyboard.press('Home')
    expect(chat).to_be_focused()
    expect(chat).to_have_attribute('aria-selected','true')
    assert chat.evaluate("""element => {
        const event = new KeyboardEvent('keydown',{key:'ArrowLeft',altKey:true,bubbles:true,cancelable:true});
        element.dispatchEvent(event);
        return !event.defaultPrevented;
    }"""), 'Mode tabs must not consume browser Back shortcuts'
    expect(chat).to_have_attribute('aria-selected','true')
    page.reload()
    expect(page.locator('.oc-chat-composer textarea')).to_have_value('这一段属于历史对话')
    # Explicit context handoff stays separate from an ordinary mode switch.
    page.get_by_role('button',name='以此创建 Agent 任务',exact=True).click()
    expect(page.locator('.oc-agent-source')).to_contain_text('模式切换检查')
    handoff_url = page.url
    chat.click()
    agent.click()
    assert page.url == handoff_url
    for width in [1440,1024,768,390,320]:
        page.set_viewport_size({'width':width,'height':1000 if width>767 else 844})
        for active in [chat,agent]:
            active.click()
            expect(active).to_have_attribute('aria-selected','true')
            expect(page.get_by_role('tabpanel')).to_be_visible()
            if active == agent:
                expect(page.locator('.oc-agent-mark')).to_be_in_viewport()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
            boxes = [page.locator(selector).bounding_box() for selector in
                     ['.oc-header-model','.oc-conversation-modes','.oc-header-stats']]
            for i, a in enumerate(boxes):
                for b in boxes[i+1:]:
                    assert (a['x']+a['width']<=b['x']+1 or b['x']+b['width']<=a['x']+1 or
                            a['y']+a['height']<=b['y']+1 or b['y']+b['height']<=a['y']+1),boxes
        page.screenshot(path=str(OUT/f'conversation-switch-{width}.png'),full_page=True)
    page.set_viewport_size({'width':1440,'height':1000})
    page.goto(BASE+'/agent')
    expect(page.locator('.oc-agent-source')).to_have_count(0)
    page.get_by_label('Agent 任务',exact=True).fill('')


def run():
    with tempfile.TemporaryDirectory(prefix='onecat-agent-browser-') as state, (OUT/'server.log').open('w') as log:
        env = {**os.environ, 'ONECAT_STUDIO_HOME': state, 'ONECAT_AUTO_GPU_ACTIONS':'0', 'PYTHONPATH':str(ROOT/'studio/backend'), 'AGENT_TEST_PORT':str(PORT)}
        server = subprocess.Popen([str(ROOT/'.venv/bin/python'),'-c',SEED], env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen(BASE+'/api/health',timeout=.2).close()
                    break
                except OSError:
                    time.sleep(.1)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, executable_path=os.environ.get('ONECAT_BROWSER_EXECUTABLE') or None, args=['--disable-gpu','--use-angle=swiftshader'])
                context = browser.new_context(viewport={'width':1440,'height':1000})
                page = context.new_page()
                errors=[]
                def record_error(error):
                    errors.append(str(error))
                    print('BROWSER_ERROR:', error, flush=True)
                    page.screenshot(path=str(OUT/'browser-error.png'),full_page=True)
                page.on('pageerror',record_error)
                def record_console(message):
                    if message.type == 'error' and any(marker in message.text for marker in
                        ['TypeError:', 'Maximum update depth', 'Minified React error']):
                        errors.append(message.text)
                        print('CONSOLE_ERROR:',message.text,flush=True)
                page.on('console', record_console)
                page.goto(BASE+'/agent')
                page.locator('input[type=password]').fill('agent-browser-password')
                page.locator('button[type=submit]').click()
                expect(page.locator('.oc-agent-welcome h1')).to_contain_text('让想法，成为作品。')
                page.get_by_role('button',name='创建第一个项目').click()
                page.get_by_label('项目名称',exact=True).fill('Browser project')
                page.get_by_role('button',name='创建项目',exact=True).click()
                expect(page.get_by_role('dialog')).to_have_count(0)
                check_conversation_navigation(page, context)
                # Slash palette is a local UI action; it must not submit a model task.
                task_count=len(context.request.get(BASE+'/api/agent/tasks').json()['items'])
                composer=page.get_by_label('Agent 任务',exact=True)
                composer.fill('/re')
                expect(page.get_by_role('listbox',name='斜杠命令')).to_be_visible()
                composer.press('ArrowDown'); composer.press('Tab')
                expect(composer).to_have_value('/review ')
                composer.fill('/help '); composer.press('Enter')
                expect(page.get_by_role('dialog')).to_contain_text('Agent 命令')
                page.screenshot(path=str(OUT/'commands-help.png'),full_page=True)
                page.keyboard.press('Escape')
                expect(page.get_by_role('dialog')).to_have_count(0)
                composer.fill('/permissions ');composer.press('Enter')
                expect(page.get_by_role('dialog')).to_contain_text('工作方式')
                page.get_by_label('项目权限',exact=True).select_option('read-only')
                page.get_by_role('button',name='完成',exact=True).click()
                expect(page.locator('.oc-agent-permission')).to_contain_text('只读')
                composer.fill('/permissions workspace-write');composer.press('Enter')
                expect(page.locator('.oc-agent-permission')).to_contain_text('项目写入')
                composer.fill('/unknown-command');composer.press('Enter')
                expect(page.locator('.oc-agent-bottom')).to_contain_text('这个命令尚未接入')
                assert len(context.request.get(BASE+'/api/agent/tasks').json()['items'])==task_count
                page.get_by_label('Agent 任务',exact=True).fill('Create a preview page')
                page.get_by_role('button',name='开始任务',exact=True).click()
                expect(page.locator('.oc-agent-status')).to_contain_text('任务完成',timeout=30000)
                expect(page.locator('.oc-agent-plan')).to_contain_text('Create and verify project files')
                assert 'task=' in page.url
                task_url=page.url
                bubble=page.locator('.oc-agent-user').last.bounding_box()
                content=page.locator('.oc-agent-content').bounding_box()
                assert bubble['width'] < content['width']*.65
                composer_height=page.locator('.oc-agent-composer').bounding_box()['height']
                assert composer_height < 120,composer_height
                footer=page.locator('.oc-agent-status').bounding_box()
                assert footer['height'] < 42,footer
                expect(page.get_by_role('button',name='查看任务统计',exact=True)).to_be_visible()
                page.get_by_role('button',name='查看任务统计',exact=True).click()
                expect(page.get_by_role('dialog')).to_contain_text('累计输入')
                expect(page.get_by_role('dialog')).to_contain_text('缓存命中')
                expect(page.get_by_role('dialog')).to_contain_text('Agent 上下文预算')
                page.screenshot(path=str(OUT/'task-details.png'),full_page=True)
                page.keyboard.press('Escape')
                expect(page.get_by_role('dialog')).to_have_count(0)
                expect(page.locator('.oc-preview-panel')).to_have_count(0)
                page.screenshot(path=str(OUT/'task-complete-dark.png'),full_page=True)
                page.get_by_role('button',name='文件',exact=True).click()
                expect(page.locator('.oc-agent-file-list')).to_contain_text('index.html')
                page.locator('.oc-agent-file-list button').filter(has_text='index.html').click()
                page.get_by_role('button',name='预览',exact=True).click()
                expect(page.locator('.oc-preview-panel')).to_be_visible()
                frame=page.frame_locator('.oc-preview-panel iframe').last
                expect(frame.get_by_role('heading',name='Agent preview')).to_be_visible(timeout=15000)
                frame.get_by_role('button',name='0',exact=True).click()
                expect(frame.get_by_role('button',name='1',exact=True)).to_be_visible()
                assert page.locator('.oc-preview-panel').bounding_box()['width'] < 850
                expect(page.get_by_label('Agent 任务',exact=True)).to_be_visible()
                page.screenshot(path=str(OUT/'preview-split.png'),full_page=True)
                page.get_by_role('button',name='关闭预览').click()
                expect(page.locator('.oc-preview-panel')).to_have_count(0)
                page.reload()
                expect(page.locator('.oc-agent-status')).to_contain_text('任务完成')
                page.get_by_label('Agent 任务',exact=True).fill('Continue and improve the page')
                page.get_by_role('button',name='继续任务',exact=True).click()
                expect(page.locator('.oc-agent-user')).to_have_count(2,timeout=15000)
                expect(page.locator('.oc-agent-status')).to_contain_text('任务完成',timeout=30000)
                assert page.url==task_url
                page.get_by_role('link',name='新任务',exact=True).last.click()
                page.get_by_label('Agent 任务',exact=True).fill('slow-task')
                page.get_by_role('button',name='开始任务',exact=True).click()
                expect(page.locator('.oc-agent-answer')).to_contain_text('Streaming progress',timeout=15000)
                slow_url=page.url
                page.get_by_role('tab',name='聊天',exact=True).click()
                expect(page.locator('.oc-chat-composer textarea')).to_be_visible()
                page.get_by_role('tab',name='Agent',exact=True).click()
                assert page.url == slow_url
                expect(page.locator('.oc-agent-answer')).to_contain_text('Streaming progress')
                page.get_by_role('link',name='模型库',exact=True).click()
                page.get_by_role('link',name='对话',exact=True).click()
                assert page.url == slow_url
                expect(page.locator('.oc-agent-answer')).to_contain_text('Streaming progress')
                page.wait_for_function("(()=>{const el=document.querySelector('.oc-agent-scroll');return el.scrollHeight-el.clientHeight>250})()",timeout=10000)
                page.locator('.oc-agent-scroll').hover()
                page.mouse.wheel(0,-10000)
                page.locator('.oc-agent-scroll').evaluate('(el)=>{el.scrollTop=0}')
                page.wait_for_timeout(500)
                assert page.locator('.oc-agent-scroll').evaluate('el=>el.scrollTop') < 100
                page.get_by_role('button',name='停止任务',exact=True).click()
                expect(page.locator('.oc-agent-status')).to_contain_text('已停止',timeout=10000)
                page.screenshot(path=str(OUT/'cancelled-task.png'),full_page=True)
                for width in [390,320]:
                    page.set_viewport_size({'width':width,'height':844})
                    page.goto(task_url)
                    expect(page.get_by_label('Agent 任务',exact=True)).to_be_visible()
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
                    page.get_by_role('button',name='文件',exact=True).click()
                    expect(page.locator('.oc-agent-files')).to_be_visible()
                    page.get_by_role('button',name='关闭文件面板').click()
                    expect(page.locator('.oc-agent-files')).to_have_count(0)
                    page.screenshot(path=str(OUT/f'mobile-{width}.png'),full_page=True)
                page.set_viewport_size({'width':1440,'height':1000})
                page.evaluate("localStorage.setItem('onecat_theme','light')")
                page.emulate_media(reduced_motion='reduce')
                page.reload()
                expect(page.locator('html')).to_have_attribute('data-ui-motion','off')
                page.screenshot(path=str(OUT/'light-reduced-motion.png'),full_page=True)
                page.evaluate("localStorage.setItem('onecat_theme','dark');localStorage.setItem('onecat_locale','en')")
                page.emulate_media(reduced_motion='no-preference')
                page.reload()
                expect(page.get_by_label('Agent task',exact=True)).to_be_visible()
                expect(page.locator('html')).to_have_class(__import__('re').compile('dark'))
                page.screenshot(path=str(OUT/'english-dark.png'),full_page=True)
                page.get_by_role('button',name='Files',exact=True).click()
                expect(page.locator('.oc-agent-files')).to_be_visible()
                page.keyboard.press('Escape')
                expect(page.locator('.oc-agent-files')).to_have_count(0)
                # Source views must not reuse another project's identically named file.
                page.goto(BASE+'/agent')
                first=context.request.get(BASE+'/api/agent/tasks/'+task_url.split('task=')[1]).json()['project_id']
                expect(page.get_by_label('Agent project',exact=True)).to_have_value(first)
                second=context.request.post(BASE+'/api/agent/projects',data={'name':'Other project'}).json()['id']
                context.request.post(BASE+f'/api/agent/projects/{second}/files?path=index.html',multipart={'file':{'name':'index.html','mimeType':'text/html','buffer':b'SECOND PROJECT CONTENT'}})
                page.reload()
                page.get_by_label('Agent project',exact=True).select_option(second)
                page.get_by_role('button',name='Files',exact=True).click()
                page.locator('.oc-agent-file-list button').filter(has_text='index.html').click()
                expect(page.locator('.oc-agent-file-source')).to_contain_text('SECOND PROJECT CONTENT')
                page.get_by_label('Agent project',exact=True).select_option(first)
                held=[]
                page.route(lambda url:f'/api/agent/projects/{first}/file?' in url,lambda route:held.append(route))
                page.get_by_role('button',name='Files',exact=True).click()
                page.locator('.oc-agent-file-list button').filter(has_text='index.html').click()
                for _ in range(20):
                    if held:break
                    page.wait_for_timeout(100)
                assert held, page.locator('.oc-agent-files').inner_text()
                expect(page.locator('.oc-agent-file-source')).not_to_contain_text('SECOND PROJECT CONTENT')
                for route in held:route.fulfill(response=route.fetch())
                page.unroute_all(behavior='wait')
                expect(page.locator('.oc-agent-file-source')).to_contain_text('Agent preview')
                # Folder upload keeps relative paths; empty selection is retryable.
                with tempfile.TemporaryDirectory(prefix='onecat-folder-') as folder:
                    path=Path(folder)/'src';path.mkdir()
                    (path/'note.txt').write_text('Folder upload works')
                    (Path(folder)/'node_modules').mkdir()
                    (Path(folder)/'node_modules/cache.txt').write_text('Do not import dependencies')
                    page.locator('input[webkitdirectory]').set_input_files(folder)
                    expect(page.locator('.oc-agent-file-list')).to_contain_text('src/note.txt')
                    expect(page.locator('.oc-agent-files')).to_contain_text('skipped 1')
                # A missing task is a terminal UI error, not an endless SSE retry loop.
                missing=[]
                page.on('request',lambda request:missing.append(request.url) if '/tasks/missing' in request.url else None)
                page.goto(BASE+'/agent?task=missing')
                expect(page.locator('.oc-agent-bottom')).to_contain_text('Agent task not found')
                page.wait_for_timeout(3500)
                assert len(missing)==2,missing
                assert not errors,errors
                browser.close()
                print(json.dumps({'passed':['unified navigation/drafts/history/keyboard/handoff','responsive mode switch 1440/1024/768/390/320','project creation','real Codex tools','execution plan','completion/history','split preview/run','resume same thread','mode/page switch streaming','user scroll','cancel','mobile 390/320','light/reduced motion','cross-project stale response isolation','folder upload','missing task stops reconnecting','no React errors'], 'screenshots':str(OUT)},ensure_ascii=False))
        finally:
            server.terminate()
            try: server.wait(timeout=10)
            except subprocess.TimeoutExpired: server.kill();server.wait()


if __name__=='__main__':run()
