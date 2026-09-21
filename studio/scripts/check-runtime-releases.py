#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Release installation UI on a disposable database; all installation/GPU actions are mocked."""
import json
import os
from pathlib import Path
import runpy
import socket
import subprocess
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / '.artifacts/runtime-releases/browser'
OUT.mkdir(parents=True, exist_ok=True)
seed = runpy.run_path(str(ROOT / 'studio/scripts/check-browser.py'))['SEED']
seed = seed.replace('uvicorn.run(create_app(),', r'''
from onecat import runtime_releases,app as application
from pathlib import Path
entries=json.loads(Path(os.environ['ONECAT_RELEASE_FIXTURE']).read_text())
runtime_releases.fetch_releases=lambda:[row for r in entries for row in runtime_releases.parse_release(r)]
def create(kind,payload):
    assert kind=='install_runtime'
    id=db.uid()
    return db.put('jobs',id,{'id':id,'kind':kind,'payload':payload,'state':'queued','stage':'queued','created_at':time.time(),
        'runtime_release_id':payload['release_id'],'runtime_version':payload['release']['version']})
application.create_job=create
app=create_app()
@app.post('/fixture/job/{id}')
async def progress(id,request:__import__('fastapi').Request):
    payload=await request.json()
    record=db.patch('jobs',id,{**payload,'updated_at':time.time()})
    if payload.get('state')=='completed':
        release=record['payload']['release']
        db.put('runtimes',release['id'],{'id':release['id'],'name':release['version'],'validated':True,'managed':True,
            'python_path':'/fixture/python','release':release,'capabilities':{'vllm_version':release['version']}})
    return {'ok':True}
uvicorn.run(app,''')


def main():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    passed=[]
    with tempfile.TemporaryDirectory(prefix='onecat-releases-') as state, (OUT/'server.log').open('w') as log:
        process=subprocess.Popen([str(ROOT/'.venv/bin/python'),'-c',seed],stdout=log,stderr=subprocess.STDOUT,
            env={**os.environ,'ONECAT_STUDIO_HOME':state,'ONECAT_AUTO_GPU_ACTIONS':'0','TEST_PORT':str(port),
                 'ONECAT_RELEASE_FIXTURE':str(ROOT/'.artifacts/runtime-releases/github-releases.json'),'PYTHONPATH':str(ROOT/'studio/backend')})
        try:
            for _ in range(100):
                try: urllib.request.urlopen(base+'/api/health',timeout=1); break
                except OSError: time.sleep(.1)
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,executable_path=os.environ.get('ONECAT_BROWSER_EXECUTABLE') or None,args=['--disable-gpu','--use-angle=swiftshader'])
                context=browser.new_context(viewport={'width':1500,'height':1050})
                context.add_init_script("if(window===window.top)localStorage.setItem('onecat_theme','dark')")
                assert context.request.post(base+'/api/auth/setup',data={'password':'release-fixture-password'}).ok
                page=context.new_page();errors=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(base+'/setup')
                select=page.get_by_label('发行版本',exact=True)
                expect(select.locator('option')).to_have_count(8)
                expect(select).to_have_value('github-540958592-203507a6245e')
                page.locator('.oc-runtime-installer').scroll_into_view_if_needed()
                page.screenshot(path=str(OUT/'01-releases-dark.png'))
                page.get_by_role('switch',name='包含实验版',exact=True).check()
                expect(select.locator('option')).to_have_count(9)
                beta=next(o.get_attribute('value') for o in select.locator('option').all() if 'v1.1.0' in o.inner_text())
                select.select_option(beta)
                expect(page.locator('.oc-runtime-summary')).to_contain_text('实验版')
                expect(page.locator('.oc-runtime-summary')).to_contain_text('2 wheel')
                page.locator('.oc-runtime-assets summary').click()
                expect(page.locator('.oc-runtime-assets')).to_contain_text('flash_attn_v100-1.1.0')
                expect(page.locator('.oc-runtime-assets')).to_contain_text('SHA256:')
                assert context.request.get(base+'/api/jobs').json()['items']==[]
                passed.append('8 stable releases, experimental flag, grouped companion wheels and no selection side effects')
                target=next(o.get_attribute('value') for o in select.locator('option').all() if 'v1.2.2' in o.inner_text())
                select.select_option(target)
                with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/api/runtimes/install')) as response:
                    page.get_by_role('button',name='安装独立环境',exact=True).evaluate('b=>{b.click();b.click()}')
                job=response.value.json()
                assert response.value.ok,job
                assert len(context.request.get(base+'/api/jobs').json()['items'])==1
                context.request.post(base+'/fixture/job/'+job['id'],data={'state':'running','stage':'downloading','asset_name':'1cat_vllm-1.2.2-cp312-cp312-linux_x86_64.whl','asset_index':1,'asset_count':1,'downloaded_bytes':32*1024*1024,'expected_bytes':256*1024*1024,'bytes_per_second':8*1024*1024})
                page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
                expect(page.locator('.oc-runtime-job')).to_contain_text('8 MB/s')
                expect(page.locator('.oc-runtime-job progress')).to_have_attribute('value',str(32*1024*1024))
                page.goto(base+'/models'); page.goto(base+'/setup')
                expect(select).to_have_value(target)
                expect(page.locator('.oc-runtime-job')).to_contain_text('下载中')
                page.locator('.oc-runtime-installer').scroll_into_view_if_needed()
                page.screenshot(path=str(OUT/'02-installing.png'))
                page.get_by_role('button',name='取消安装',exact=True).click()
                context.request.post(base+'/fixture/job/'+job['id'],data={'state':'cancelled','stage':'downloading'})
                page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
                with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/retry')) as retry:
                    page.get_by_role('button',name='重试安装',exact=True).click()
                job=retry.value.json()
                context.request.post(base+'/fixture/job/'+job['id'],data={'state':'completed','stage':'validating','result':{'runtime_id':target}})
                page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
                expect(page.get_by_role('button',name='已安装此发行包',exact=True)).to_be_visible()
                expect(page.get_by_role('link',name='配置模型',exact=True)).to_be_visible()
                assert context.request.get(base+'/api/profiles').json()['items'][0]['runtime_id']=='r'
                passed.append('duplicate click guard, inline real-byte progress, cross-page recovery, cancel/retry and explicit runtime switching')
                snapshot=context.request.get(base+'/api/runtimes/releases').json()
                snapshot.update(stale=True,error='测试：GitHub 暂时不可用')
                page.route('**/api/runtimes/releases*',lambda route:route.fulfill(json=snapshot))
                page.get_by_role('button',name='检查更新',exact=True).click()
                expect(page.locator('.oc-runtime-installer [role=alert]')).to_contain_text('GitHub 暂时不可用')
                expect(page.locator('.oc-runtime-installer')).to_contain_text('上次成功获取')
                snapshot['items']=[r for r in snapshot['items'] if r['id']!=target]
                page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
                expect(page.locator('.oc-runtime-installer')).to_contain_text('当前列表中找不到所选发行包')
                expect(page.get_by_role('button',name='安装独立环境',exact=True)).to_have_count(0)
                snapshot['items'][0].update(available=False,reasons=['测试：不兼容的 CUDA 构建'])
                page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
                select.select_option(snapshot['items'][0]['id'])
                expect(page.get_by_role('button',name='安装独立环境',exact=True)).to_be_disabled()
                expect(page.locator('.oc-runtime-installer')).to_contain_text('不兼容的 CUDA 构建')
                page.unroute_all(behavior='wait')
                page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
                expect(page.get_by_role('button',name='安装独立环境',exact=True)).to_be_enabled()
                passed.append('offline cached list, removed selected asset and visible incompatibility reasons')
                page.emulate_media(reduced_motion='reduce')
                expect(page.locator('html')).to_have_attribute('data-ui-motion','off')
                select.focus(); page.keyboard.press('ArrowDown'); page.keyboard.press('Enter')
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                page.locator('.oc-runtime-installer').scroll_into_view_if_needed()
                page.screenshot(path=str(OUT/'03-mobile-dark.png'))
                page.evaluate("localStorage.setItem('onecat_locale','en')")
                page.reload()
                expect(page.get_by_label('Release version',exact=True)).to_be_visible()
                page.evaluate("document.documentElement.classList.remove('dark')")
                page.locator('.oc-runtime-installer').scroll_into_view_if_needed()
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                page.screenshot(path=str(OUT/'04-mobile-english-light.png'))
                passed.append('keyboard, reduced motion, Chinese/English and mobile light/dark layout')
                assert not errors,errors
                (OUT/'result.json').write_text(json.dumps({'passed':passed,'errors':errors},indent=2))
                print(json.dumps({'passed':passed,'errors':errors}))
                browser.close()
        finally:
            process.terminate()
            try:process.wait(timeout=8)
            except subprocess.TimeoutExpired:process.kill();process.wait()

if __name__=='__main__':main()
