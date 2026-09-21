#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Orange workspace acceptance, CPU browser and disposable mocked hardware only."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('chat_fixture', Path(__file__).with_name('check-browser.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
OUT = ROOT / '.artifacts/workspace-browser'
OUT.mkdir(parents=True, exist_ok=True)

def run():
    with tempfile.TemporaryDirectory(prefix='onecat-workspace-') as state, (OUT / 'server.log').open('w') as log:
        env = {**os.environ, 'ONECAT_AUTO_GPU_ACTIONS':'0', 'ONECAT_STUDIO_HOME':state,
               'PYTHONPATH':str(ROOT/'studio/backend'), 'TEST_PORT':str(fixture.PORT)}
        server = subprocess.Popen([str(ROOT/'.venv/bin/python'), '-c', fixture.SEED], env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen(fixture.BASE+'/api/health', timeout=1); break
                except OSError: time.sleep(.1)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, executable_path=os.environ.get('ONECAT_BROWSER_EXECUTABLE') or None, args=['--disable-gpu','--use-angle=swiftshader'])
                context = browser.new_context(viewport={'width':1440,'height':1000}, locale='zh-CN')
                page = context.new_page(); errors=[]
                page.on('pageerror', lambda err:errors.append(str(err)))
                assert context.request.post(fixture.BASE+'/api/auth/setup', data={'password':'workspace-test-only'}).ok
                chat = context.request.post(fixture.BASE+'/api/chat/import', data={'title':'布局回归记录','messages':[{'role':'user','content':'保留这个记录'}]}).json()['id']
                page.goto(fixture.BASE+'/chat')
                expect(page.locator('.oc-chat-welcome h1')).to_contain_text('用想法，创造。')
                expect(page.locator('.oc-history-panel')).to_have_count(0)
                assert page.locator('.oc-rail').bounding_box()['width']==80
                assert page.locator('.oc-nav-capsule').bounding_box()['width']==56
                composer = page.locator('.oc-chat-composer textarea')
                composer.fill('切换布局也要保留的草稿')
                page.screenshot(path=str(OUT/'chat-light-1440.png'))
                history=page.get_by_role('button',name='历史记录',exact=True)
                history.click(); expect(page.get_by_role('dialog')).to_be_visible()
                page.get_by_role('textbox', name='搜索对话',exact=True).fill('布局')
                page.get_by_role('link',name='布局回归记录',exact=True).click()
                expect(page.get_by_role('dialog')).to_have_count(0)
                expect(history).to_be_focused()
                history.click(); page.get_by_role('button',name='固定历史',exact=True).click()
                expect(page.locator('.oc-history-pinned')).to_be_visible()
                expect(page.locator('.oc-drawer-overlay')).to_have_count(0)
                assert page.locator('.oc-history-pinned').bounding_box()['width']==304
                page.get_by_role('link',name='布局回归记录',exact=True).click()
                expect(page.locator('.oc-history-pinned')).to_be_visible()
                page.get_by_role('link',name='新对话',exact=True).click()
                expect(composer).to_have_value('切换布局也要保留的草稿')
                page.reload(); expect(page.locator('.oc-history-pinned')).to_be_visible()
                expect(composer).to_have_value('切换布局也要保留的草稿')
                page.locator('.oc-nav-capsule .oc-rail-link').first.evaluate('el=>window.conversationLink=el')
                page.get_by_role('tab',name='Agent',exact=True).click()
                expect(page.locator('.oc-history-pinned')).to_contain_text('最近任务')
                page.get_by_role('textbox',name='Agent 任务',exact=True).fill('Agent 保留的草稿')
                page.get_by_role('tab',name='聊天',exact=True).click()
                expect(composer).to_have_value('切换布局也要保留的草稿')
                assert page.locator('.oc-nav-capsule .oc-rail-link').first.evaluate('el=>el===window.conversationLink'), 'Mode switch remounted navigation icon'
                page.screenshot(path=str(OUT/'history-pinned-1440.png'))
                page.set_viewport_size({'width':768,'height':1000})
                expect(page.locator('.oc-history-pinned')).to_have_count(0)
                expect(page.get_by_role('dialog')).to_be_visible()
                page.keyboard.press('Escape'); expect(page.get_by_role('dialog')).to_have_count(0)
                page.set_viewport_size({'width':1280,'height':1000})
                expect(page.locator('.oc-history-pinned')).to_be_visible()
                page.get_by_role('button',name='取消固定',exact=True).click()
                expect(page.get_by_role('dialog')).to_be_visible()
                page.keyboard.press('Escape'); page.wait_for_timeout(350)
                expect(page.get_by_role('dialog')).to_have_count(0)
                expect(history).to_be_focused()
                # Rapid interactions must leave neither an overlay nor a pointer lock.
                for _ in range(4):
                    history.click(); page.keyboard.press('Escape')
                expect(page.locator('.oc-drawer-overlay')).to_have_count(0)
                assert page.evaluate('getComputedStyle(document.body).pointerEvents')!='none'
                for theme in ['light','dark']:
                    page.evaluate('(theme)=>{localStorage.setItem("onecat_theme",theme);document.documentElement.classList.toggle("dark",theme==="dark")}', theme)
                    for width in [320,390,768,1280,1440]:
                        page.set_viewport_size({'width':width,'height':844 if width<768 else 1000})
                        page.goto(fixture.BASE+'/chat')
                        expect(composer).to_have_value('切换布局也要保留的草稿')
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), ('overflow', theme, width)
                        model=page.locator('.oc-header-model').bounding_box(); modes=page.locator('.oc-conversation-modes').bounding_box(); status=page.locator('.oc-header-stats').bounding_box()
                        for a,b in [(model,modes),(modes,status),(model,status)]:
                            assert a['x']+a['width']<=b['x']+1 or b['x']+b['width']<=a['x']+1 or a['y']+a['height']<=b['y']+1 or b['y']+b['height']<=a['y']+1, (width,a,b)
                        if width<640:
                            menu=page.get_by_role('button',name='打开导航',exact=True)
                            menu.click(); expect(page.get_by_role('dialog')).to_be_visible()
                            page.get_by_role('button',name='历史记录',exact=True).click()
                            expect(page.get_by_role('textbox',name='搜索对话',exact=True)).to_be_visible()
                            page.keyboard.press('Escape'); expect(menu).to_be_focused()
                            menu.click(); page.get_by_role('link',name='模型库',exact=True).click()
                            expect(page.get_by_role('dialog')).to_have_count(0)
                            page.goto(fixture.BASE+'/chat')
                        page.screenshot(path=str(OUT/f'chat-{theme}-{width}.png'))
                    for route in ['agent','models','service','performance','canvas','setup','settings']:
                        for width in [390,1440]:
                            page.set_viewport_size({'width':width,'height':1000})
                            page.goto(fixture.BASE+'/'+route); page.wait_for_timeout(500)
                            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(theme,route,width)
                            page.screenshot(path=str(OUT/f'{route}-{theme}-{width}.png'))
                page.set_viewport_size({'width':1440,'height':1000})
                page.goto(fixture.BASE+'/chat')
                page.emulate_media(reduced_motion='reduce')
                expect(page.locator('html')).to_have_attribute('data-ui-motion','off')
                assert page.locator('.oc-app-header').evaluate('el=>parseFloat(getComputedStyle(el).getPropertyValue("--oc-motion-state"))')==0
                assert not errors, errors
                browser.close()
                print(json.dumps({'passed':['rail','history overlay/pin/reload/mode/responsive/search','drafts','rapid close/focus/scroll lock','five widths','light/dark pages','reduced motion','no React errors'], 'screenshots':str(OUT)}))
        finally:
            server.terminate(); server.wait(timeout=20)
if __name__=='__main__': run()
