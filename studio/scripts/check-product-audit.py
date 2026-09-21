#!/usr/bin/env python3
"""Exercise real Studio UI on a disposable database, with simulated inference."""
import json
import base64
import io
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
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / '.artifacts/audit-20260908'
OUT.mkdir(parents=True, exist_ok=True)
BEFORE = '--before' in sys.argv
seed = runpy.run_path(str(ROOT / 'studio/scripts/check-browser.py'))['SEED']
seed = seed.replace('uvicorn.run(create_app()', 'p.update(vision_enabled=True,max_images=1)\ndb.put("profiles","p",p)\ndb.put("engine","active",{"state":"ready","profile_id":"p","profile":p})\nuvicorn.run(create_app()')
seed = seed.replace('uvicorn.run(create_app()', 'from onecat import gpu_setup\ngpu_setup.helper_check=lambda:{}\nuvicorn.run(create_app()')
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
BASE = f'http://127.0.0.1:{port}'


def refresh(page):
    page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")


def thread(context, content='Reply', settings=None):
    response = context.request.post(BASE + '/api/chat/import', data={
        'title': 'Audit thread', 'settings': settings or {},
        'messages': [{'role': 'user', 'content': 'Request'}, {'role': 'assistant', 'content': content}],
    })
    assert response.ok, response.text()
    return response.json()['id']


def pristine_settings(page, context):
    page.goto(BASE + '/settings')
    expect(page.get_by_label('空闲卸载分钟数（0 为关闭）', exact=True)).to_have_value('0')
    context.request.put(BASE + '/api/settings', data={'idle_unload_minutes': 7})
    refresh(page)
    expect(page.get_by_label('空闲卸载分钟数（0 为关闭）', exact=True)).to_have_value('7')


def partial_settings_save(page, context):
    page.goto(BASE + '/settings')
    page.get_by_label('Studio 监听端口', exact=True).fill('9999')
    context.request.put(BASE + '/api/settings', data={'idle_unload_minutes': 8})
    refresh(page)
    page.wait_for_timeout(200)
    page.get_by_role('button', name='保存设置', exact=True).click()
    expect(page.get_by_role('button', name='保存设置', exact=True)).to_be_disabled()
    saved = context.request.get(BASE + '/api/settings').json()
    assert saved['idle_unload_minutes'] == 8, saved
    assert saved['port'] == 9999


def pending_save_draft(page, context):
    page.goto(BASE + '/settings')
    page.get_by_label('空闲卸载分钟数（0 为关闭）', exact=True).fill('9')
    page.evaluate('''() => { const native = fetch; window.fetch = async (url, options) => {
      if (String(url).endsWith('/api/settings') && options?.method === 'PUT') {
        window.auditSaving = true; await new Promise(r => setTimeout(r, 800));
      } return native(url, options);
    }; }''')
    page.get_by_role('button', name='保存设置', exact=True).click()
    page.wait_for_function('window.auditSaving')
    page.get_by_label('空闲卸载分钟数（0 为关闭）', exact=True).fill('10')
    expect(page.get_by_role('button', name='保存设置', exact=True)).to_be_enabled(timeout=3000)
    page.get_by_role('link', name='模型库', exact=True).click()
    page.get_by_role('link', name='设置', exact=True).click()
    expect(page.get_by_label('空闲卸载分钟数（0 为关闭）', exact=True)).to_have_value('10')


def page_scroll_restore(page, context):
    page.goto(BASE + '/settings')
    page.get_by_label('Studio 监听端口', exact=True).wait_for()
    page.locator('.oc-main').evaluate('e=>e.scrollTop=700')
    page.get_by_role('link', name='模型库', exact=True).click()
    expect(page.get_by_role('heading', name='模型库', exact=True)).to_be_in_viewport()
    page.get_by_role('link', name='设置', exact=True).click()
    page.wait_for_function("Math.abs(document.querySelector('.oc-main').scrollTop-700)<10")


def new_chat_prompt(page, context):
    id = thread(context, settings={'system_prompt': 'Only answer in pirate dialect'})
    page.goto(BASE + '/chat?thread=' + id)
    page.get_by_role('button', name='对话参数', exact=True).click()
    expect(page.get_by_label('系统提示词', exact=True)).to_have_value('Only answer in pirate dialect')
    page.get_by_role('button', name='完成', exact=True).click()
    page.get_by_role('link', name='聊天', exact=True).click()
    page.get_by_role('button', name='对话参数', exact=True).click()
    expect(page.get_by_label('系统提示词', exact=True)).to_have_value('')


def expired_session(page, context):
    page.goto(BASE + '/chat')
    page.locator('.oc-chat-composer textarea').fill('Draft survives session expiry')
    assert context.request.post(BASE + '/api/auth/logout').ok
    expect(page.locator('.oc-login')).to_be_visible(timeout=6000)
    if not BEFORE:
        page.get_by_label('密码', exact=True).fill('audit-only-password')
        page.locator('.oc-login button[type=submit]').click()
        expect(page.locator('.oc-chat-composer textarea')).to_have_value('Draft survives session expiry')


def preview_selection_while_paused(page, context):
    id = thread(context, '```html\n<h1>First artifact</h1>\n```\n\n```html\n<h1>Second artifact</h1>\n```')
    page.goto(BASE + '/chat?thread=' + id)
    page.get_by_role('button', name='预览 / 运行', exact=True).first.click()
    expect(page.frame_locator('iframe:not(.oc-preview-candidate)').get_by_text('First artifact')).to_be_visible(timeout=15000)
    page.get_by_role('button', name='暂停自动更新', exact=True).click()
    page.get_by_role('button', name='预览 / 运行', exact=True).last.click()
    expect(page.frame_locator('iframe:not(.oc-preview-candidate)').get_by_text('Second artifact')).to_be_visible(timeout=6000)


def simultaneous_image_uploads(page, context):
    page.goto(BASE + '/chat')
    expect(page.locator('.oc-chat-composer textarea')).to_be_enabled()
    image = io.BytesIO()
    Image.new('RGB', (16, 16), '#268361').save(image, format='PNG')
    page.evaluate('''(image) => {
      const native=fetch; window.uploads=0;
      window.fetch=async(url,options)=>{
        if(String(url).endsWith('/api/chat/attachments')) {
          window.uploads++; await new Promise(r=>setTimeout(r,300));
        } return native(url,options);
      };
      const input=document.querySelector('.oc-chat-composer textarea');
      for(let i=0;i<2;i++) {
        const data=new DataTransfer(); data.items.add(new File([Uint8Array.from(atob(image),c=>c.charCodeAt(0))],'image.png',{type:'image/png'}));
        input.dispatchEvent(new ClipboardEvent('paste',{bubbles:true,clipboardData:data}));
      }
    }''', base64.b64encode(image.getvalue()).decode())
    page.wait_for_timeout(700)
    assert page.evaluate('window.uploads') == 1, 'Concurrent pastes uploaded more than the model image limit'
    expect(page.locator('.oc-image-thumb')).to_have_count(1)
    page.wait_for_function("document.querySelector('.oc-image-thumb img')?.naturalWidth===16")


def mobile_settings_validation(page, context):
    page.set_viewport_size({'width':390,'height':844})
    page.goto(BASE + '/settings')
    page.get_by_label('Studio 监听端口',exact=True).fill('9')
    expect(page.get_by_role('button',name='保存设置',exact=True)).to_be_disabled()
    expect(page.locator('.oc-settings-savebar')).to_contain_text('1024–65535')
    page.get_by_role('button',name='撤销更改',exact=True).click()
    expect(page.get_by_label('Studio 监听端口',exact=True)).to_have_value('8888')
    page.get_by_label('界面语言',exact=True).select_option('en')
    page.get_by_label('Appearance',exact=True).select_option('dark')
    expect(page.get_by_role('button',name='Save settings',exact=True)).to_be_in_viewport()
    expect(page.get_by_role('button',name='Discard changes',exact=True)).to_be_in_viewport()
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    expect(page.locator('html')).to_have_class('dark')


CASES = [pristine_settings, partial_settings_save, pending_save_draft, page_scroll_restore,
         new_chat_prompt, expired_session, preview_selection_while_paused, simultaneous_image_uploads]
if not BEFORE: CASES.append(mobile_settings_validation)

with tempfile.TemporaryDirectory(prefix='onecat-product-audit-') as state, (OUT / 'ui-server.log').open('w') as log:
    env = {**os.environ, 'ONECAT_STUDIO_HOME': state, 'PYTHONPATH': str(ROOT/'studio/backend'), 'TEST_PORT': str(port)}
    server = subprocess.Popen([str(ROOT/'.venv/bin/python'), '-c', seed], env=env, stdout=log, stderr=log)
    results = []
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(BASE+'/api/health',timeout=1); break
            except OSError: time.sleep(.1)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path=os.environ.get('ONECAT_BROWSER_EXECUTABLE'))
            auth = browser.new_context()
            assert auth.request.post(BASE+'/api/auth/setup',data={'password':'audit-only-password'}).ok
            for case in CASES:
                selected = next((a.split('=', 1)[1] for a in sys.argv if a.startswith('--case=')), None)
                if selected and case.__name__ != selected:
                    continue
                context = browser.new_context(viewport={'width':1440,'height':1000})
                assert context.request.post(BASE+'/api/auth/login',data={'password':'audit-only-password'}).ok
                assert context.request.put(BASE+'/api/settings',data={'idle_unload_minutes':0,'port':8888}).ok
                page = context.new_page()
                page.set_default_timeout(7000)
                try:
                    case(page, context)
                    results.append({'case':case.__name__,'passed':True})
                except Exception as error:
                    results.append({'case':case.__name__,'passed':False,'error':str(error)[:1000]})
                page.screenshot(path=str(OUT/f'{"before" if BEFORE else "after"}-{case.__name__}.png'))
                print(json.dumps(results[-1],ensure_ascii=False),flush=True)
                context.close()
            browser.close()
    finally:
        server.terminate();server.wait(timeout=15)
    suffix = '-' + selected if selected else ''
    (OUT/f'ui-{"before" if BEFORE else "after"}{suffix}.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    if not BEFORE and any(not r['passed'] for r in results): raise SystemExit(1)
