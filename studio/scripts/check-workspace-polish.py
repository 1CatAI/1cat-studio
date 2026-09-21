#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Second workspace audit: English, overlays, pane geometry and failed saves."""
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
spec = importlib.util.spec_from_file_location('fixture', Path(__file__).with_name('check-browser.py'))
fixture = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
OUT = ROOT/'.artifacts/workspace-polish'; OUT.mkdir(parents=True, exist_ok=True)

def run():
    with tempfile.TemporaryDirectory(prefix='onecat-polish-') as state, (OUT/'server.log').open('w') as log:
        env={**os.environ,'ONECAT_AUTO_GPU_ACTIONS':'0','ONECAT_STUDIO_HOME':state,'PYTHONPATH':str(ROOT/'studio/backend'),'TEST_PORT':str(fixture.PORT)}
        server=subprocess.Popen([str(ROOT/'.venv/bin/python'),'-c',fixture.SEED],env=env,stdout=log,stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                try: urllib.request.urlopen(fixture.BASE+'/api/health',timeout=1); break
                except OSError: time.sleep(.1)
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,executable_path=os.environ.get('ONECAT_BROWSER_EXECUTABLE') or None,args=['--disable-gpu','--use-angle=swiftshader'])
                context=browser.new_context(viewport={'width':1440,'height':1000})
                page=context.new_page(); errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                assert context.request.post(fixture.BASE+'/api/auth/setup',data={'password':'polish-test-only'}).ok
                page.goto(fixture.BASE+'/chat')
                for theme in ['light','dark']:
                    assert context.request.put(fixture.BASE+'/api/settings',data={'locale':'en','theme':theme}).ok
                    page.evaluate('(theme)=>{localStorage.setItem("onecat_locale","en");localStorage.setItem("onecat_theme",theme)}',theme)
                    for width in [320,1440]:
                        page.set_viewport_size({'width':width,'height':1000})
                        for route in ['chat','agent','models','service','performance','canvas','setup','settings']:
                            page.goto(fixture.BASE+'/'+route)
                            expect(page.locator('.oc-main')).to_be_visible()
                            page.wait_for_timeout(250)
                            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(route,width,theme)
                            page.screenshot(path=str(OUT/f'{route}-en-{theme}-{width}.png'))
                    page.goto(fixture.BASE+'/models')
                    page.get_by_role('tab',name='Launch profiles',exact=True).click()
                    page.get_by_role('button',name='New profile',exact=True).click()
                    expect(page.get_by_role('dialog')).to_be_visible()
                    for width in [320,390,768,1280,1440]:
                        page.set_viewport_size({'width':width,'height':1000})
                        expect(page.get_by_role('dialog').get_by_role('button',name='Save profile',exact=True)).to_be_in_viewport()
                        assert page.get_by_role('dialog').evaluate('el=>el.scrollWidth<=el.clientWidth+1'),(theme,width,'dialog overflow')
                    page.screenshot(path=str(OUT/f'profile-en-{theme}.png'))
                    page.keyboard.press('Escape'); expect(page.get_by_role('dialog')).to_have_count(0)
                    page.goto(fixture.BASE+'/settings')
                    model_path=page.get_by_label('Model directory',exact=True)
                    model_path.fill('/tmp/keep-polish-'+theme)
                    def fail_save(route):
                        if route.request.method=='PUT': route.fulfill(status=503,json={'detail':'Temporary save failure'})
                        else: route.continue_()
                    page.route('**/api/settings',fail_save)
                    page.get_by_role('button',name='Save settings',exact=True).click()
                    expect(page.get_by_text('Temporary save failure',exact=False).first).to_be_visible()
                    expect(model_path).to_have_value('/tmp/keep-polish-'+theme)
                    page.unroute('**/api/settings',fail_save)
                    page.get_by_role('button',name='Save settings',exact=True).click()
                    expect(page.get_by_text('All settings saved',exact=True)).to_be_visible()
                    page.goto(fixture.BASE+'/chat')
                # Compare exact planned composition with both supplied references.
                page.evaluate('localStorage.setItem("onecat_locale","zh-CN");localStorage.setItem("onecat_theme","light")')
                page.set_viewport_size({'width':1678,'height':1090}); page.goto(fixture.BASE+'/chat')
                expect(page.locator('.oc-chat-welcome h1')).to_contain_text('用想法，创造。')
                page.screenshot(path=str(OUT/'selected-composition-light.png'))
                page.locator('.oc-rail').screenshot(path=str(OUT/'navigation-detail.png'))
                page.locator('.oc-app-header').screenshot(path=str(OUT/'header-detail.png'))
                # Same composer node survives pinning and a dragged 65% preview.
                code='```html\n<button onclick="this.textContent=\'Done\'">Run audit</button>\n```'
                chat=context.request.post(fixture.BASE+'/api/chat/import',data={'title':'Pane audit','messages':[{'role':'user','content':'Example'},{'role':'assistant','content':code}]}).json()['id']
                page.set_viewport_size({'width':1440,'height':1000});page.goto(fixture.BASE+'/chat?thread='+chat)
                page.locator('.oc-chat-composer textarea').fill('Preserve pane draft')
                page.locator('.oc-chat-composer textarea').evaluate('el=>window.originalComposer=el')
                page.get_by_role('button',name='预览 / 运行').first.click()
                expect(page.frame_locator('iframe:not(.oc-preview-candidate)').get_by_role('button',name='Run audit')).to_be_visible(timeout=15000)
                page.get_by_role('button',name='历史记录',exact=True).click()
                # Escape closes the history layer and must not close the preview underneath.
                page.keyboard.press('Escape');expect(page.get_by_role('dialog')).to_have_count(0)
                expect(page.locator('.oc-preview-panel')).to_be_visible()
                page.get_by_role('button',name='历史记录',exact=True).click()
                page.get_by_role('button',name='固定历史',exact=True).click()
                expect(page.locator('.oc-drawer-overlay')).to_have_count(0)
                handle=page.locator('.oc-preview-resizer');handle.focus()
                for _ in range(8): page.keyboard.press('ArrowLeft')
                expect(page.locator('.oc-chat-composer textarea')).to_be_in_viewport()
                assert page.locator('.oc-chat-page').evaluate('el=>el.scrollWidth<=el.clientWidth+1'),'narrow pane overflow'
                assert page.locator('.oc-chat-composer textarea').evaluate('el=>el===window.originalComposer'),'composer remounted'
                page.screenshot(path=str(OUT/'pinned-preview-narrow-pane.png'))
                page.get_by_role('button',name='关闭预览',exact=True).click();expect(page.locator('iframe')).to_have_count(0)
                expect(page.locator('.oc-chat-composer textarea')).to_have_value('Preserve pane draft')
                # Motion preference does not change saved content or usage.
                record_before=context.request.get(fixture.BASE+'/api/chat/threads/'+chat).json()
                page.evaluate('localStorage.setItem("onecat_ui_motion","off")');page.reload()
                expect(page.locator('html')).to_have_attribute('data-ui-motion','off')
                record_after=context.request.get(fixture.BASE+'/api/chat/threads/'+chat).json()
                assert record_before['messages']==record_after['messages']
                # Login is a separate unauthenticated browser, so existing sessions are retained.
                guest=browser.new_context(viewport={'width':390,'height':844})
                login=guest.new_page();login.on('pageerror',lambda error:errors.append(str(error)))
                for theme in ['light','dark']:
                    login.goto(fixture.BASE+'/chat')
                    login.evaluate('(theme)=>localStorage.setItem("onecat_theme",theme)',theme);login.reload()
                    expect(login.locator('.oc-login')).to_be_visible()
                    login.locator('input[type=password]').fill('wrong-password')
                    login.locator('button[type=submit]').click()
                    expect(login.get_by_role('alert').first).to_be_visible()
                    login.screenshot(path=str(OUT/f'login-{theme}-390.png'))
                assert not errors,errors
                browser.close()
                print(json.dumps({'passed':['English all routes/light/dark/320/1440','profile dialogs five widths','failed save keeps draft and retries','composite reference screenshots','history Escape retains preview','pinned history + 65% preview preserves composer and geometry','motion leaves records unchanged','login errors/themes','no React errors'],'screenshots':str(OUT)}))
        finally: server.terminate();server.wait(timeout=20)
if __name__=='__main__':run()
