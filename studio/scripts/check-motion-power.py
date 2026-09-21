#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""0.4 interactions on simulated GPUs; never calls a real GPU helper or inference engine."""
import json
import os
from pathlib import Path
import runpy
import socket
import subprocess
import tempfile
import time
import urllib.request

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / '.artifacts/motion-power-040/browser'
OUT.mkdir(parents=True, exist_ok=True)
seed = runpy.run_path(str(ROOT / 'studio/scripts/check-browser.py'))['SEED']
seed = seed.replace('range(4)', 'range(8)').replace('tensor_parallel_size=4','tensor_parallel_size=8').replace('"supported_graphics_clocks_mhz":[]', '"supported_graphics_clocks_mhz":[975]')
seed = seed.replace('gpu.snapshot=lambda: {"gpus":devices', 'gpu.snapshot=lambda: {"available":True,"gpus":devices')
seed = seed.replace('uvicorn.run(create_app(),', r'''
from onecat import gpu_control as control
from onecat.jobs import Job
from fastapi import Body
for d in devices:
    d['default_power_limit_w']=300
    d['graphics_clock_mhz']=300
    d['memory_clock_mhz']=877
    d['temperature_c']=35
    d['utilization']=0
    d['processes']=[{'pid':4242,'name':'external-fixture'}] if d['index']==7 else []
actual={d['uuid']:{'power_limit_w':185,'graphics_clock_mhz':None,'clock_policy_known':True,'boot_id':'fixture-boot'} for d in devices}
gpu_setup.helper_check=lambda:{'available':True,'protocol':2,'allowed_uuids':list(actual)}
gpu.control_available=lambda:True
def helper(action,**payload):
    if action=='snapshot': return {u:dict(actual[u]) for u in payload['uuids']}
    for u, setting in payload.get('settings',{}).items():
        actual[u].update({k:v for k,v in setting.items() if v is not None})
        if setting.get('reset_clocks'): actual[u]['graphics_clock_mhz']=None
        next(d for d in devices if d['uuid']==u)['power_limit_w']=actual[u]['power_limit_w']
    return {'ok':True,'devices':{u:{**actual[u],'state':'applied'} for u in payload['uuids']}}
control.helper=helper
def create(kind,payload):
    id=db.uid()
    return db.put('jobs',id,{'id':id,'kind':kind,'payload':payload,'created_at':time.time(),'stage':'waiting_for_engine','state':'running'})
control.create_job=create
db.put('engine','active',{'state':'stopped','profile_id':'p','profile':p})
app=create_app()
@app.post('/fixture/finish/{id}')
def finish(id):
    job=Job(id)
    if db.get('jobs',id).get('cancel_requested'):
        from onecat.jobs import Cancelled
        job.fail(Cancelled('Operation cancelled'))
    else:
        from onecat.jobs import Cancelled
        try: job.finish(control.execute(job))
        except Cancelled as error: job.fail(error)
    return db.get('jobs',id)
uvicorn.run(app,''')
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
BASE = f'http://127.0.0.1:{port}'


def main():
    passed = []
    with tempfile.TemporaryDirectory(prefix='onecat-motion-') as state, (OUT / 'server.log').open('w') as log:
        process = subprocess.Popen([str(ROOT / '.venv/bin/python'), '-c', seed], stdout=log, stderr=subprocess.STDOUT,
            env={**os.environ,'ONECAT_STUDIO_HOME':state,'TEST_PORT':str(port),'PYTHONPATH':str(ROOT / 'studio/backend')})
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen(BASE + '/api/health',timeout=1)
                    break
                except OSError: time.sleep(.1)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, executable_path=os.environ.get('ONECAT_BROWSER_EXECUTABLE') or None, args=['--disable-gpu', '--use-angle=swiftshader'])
                context = browser.new_context(viewport={'width':1500,'height':1100})
                context.add_init_script("if (window === window.top) localStorage.setItem('onecat_theme','dark')")
                page = context.new_page()
                errors=[]
                page.on('pageerror', lambda error: errors.append(str(error)))
                assert context.request.post(BASE+'/api/auth/setup',data={'password':'motion-fixture-password'}).ok
                context.request.put(BASE+'/api/settings',data={'theme':'dark'})
                page.goto(BASE+'/performance')
                expect(page.locator('html')).to_have_class('dark')
                eco=page.locator('.oc-power-mode').filter(has=page.get_by_role('heading',name='省电模式',exact=True,include_hidden=True))
                expect(eco).to_be_enabled()
                def open_scope():
                    page.get_by_role('button',name='更改 GPU 控制范围',exact=True).click()
                def close_scope():
                    page.get_by_role('dialog').get_by_role('button',name='完成',exact=True).click()
                    expect(page.get_by_role('dialog')).to_have_count(0)
                def choose_scope(name):
                    open_scope()
                    page.get_by_role('button',name=name,exact=True).click()
                    close_scope()
                writes=[]
                page.on('request',lambda r: writes.append(r.url) if r.method=='POST' and '/api/gpu/' in r.url else None)
                expect(page.locator('.oc-power-device')).to_have_count(0)
                assert eco.bounding_box()['height'] < 190
                assert page.locator('.oc-telemetry').bounding_box()['y'] < 700
                page.screenshot(path=str(OUT/'power-compact-dark.png'))
                open_scope()
                expect(page.locator('.oc-power-device')).to_have_count(8)
                expect(page.locator('.oc-power-device').last).to_contain_text('外部程序占用')
                expect(page.get_by_text('external-fixture (PID 4242)',exact=False)).not_to_be_visible()
                page.locator('.oc-power-occupancy summary').click()
                expect(page.get_by_text('external-fixture (PID 4242)',exact=False)).to_be_visible()
                page.screenshot(path=str(OUT/'scope-dark.png'))
                page.keyboard.press('Escape')
                expect(page.get_by_role('dialog')).to_have_count(0)
                expect(page.get_by_role('button',name='更改 GPU 控制范围',exact=True)).to_be_focused()
                assert not writes,writes
                passed.append('compact overview, on-demand GPU details and read-only keyboard selection')
                with page.expect_response(lambda r: r.request.method=='POST' and r.url.endswith('/api/gpu/power-mode')) as applied:
                    eco.click()
                assert applied.value.ok,applied.value.text()
                pending=context.request.get(BASE+'/api/gpu/power-modes').json()['pending']
                assert len(pending['gpu_uuids'])==8
                expect(page.locator('.oc-power-status')).to_contain_text('当前：均衡模式')
                expect(page.locator('.oc-power-status')).to_contain_text('等待模型启动、停止或校准完成')
                expect(page.locator('.oc-power-target')).to_have_count(1)
                page.get_by_role('button',name='任务详情',exact=True).click()
                expect(page.get_by_role('dialog').get_by_text('等待模型启动、停止或校准完成',exact=False)).to_be_visible()
                expect(page.locator('.oc-power-task-details')).to_contain_text('GPU 0, GPU 1, GPU 2, GPU 3, GPU 4, GPU 5, GPU 6, GPU 7')
                page.keyboard.press('Escape')
                assert context.request.post(BASE+'/fixture/finish/'+pending['job']).ok
                page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
                expect(eco).to_have_attribute('aria-pressed','true')
                passed.append('stopped model: all 8 GPUs including external workload; confirmed state')
                # Return stale all-GPU data for the new scope: it must never be submitted.
                stale=context.request.get(BASE+'/api/gpu/power-modes').json()
                page.route('**/api/gpu/power-modes?*',lambda route: route.fulfill(json=stale))
                open_scope()
                page.get_by_role('button',name='自选 GPU',exact=True).click()
                expect(eco).to_be_disabled()
                page.locator('.oc-power-device input').first.check()
                close_scope()
                expect(eco).to_be_disabled()
                page.unroute_all(behavior='wait')
                page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
                expect(eco).to_be_enabled()
                with page.expect_response(lambda r: r.request.method=='POST' and r.url.endswith('/api/gpu/power-mode')) as applied:
                    page.locator('.oc-power-mode').filter(has=page.get_by_role('heading',name='均衡模式',exact=True)).click()
                assert applied.value.ok,applied.value.text()
                pending=context.request.get(BASE+'/api/gpu/power-modes?gpu_uuids=GPU-'+36*'0').json()['pending']
                assert pending['gpu_uuids']==['GPU-'+36*'0']
                choose_scope('全部已授权 GPU')
                assert context.request.get(BASE+'/api/gpu/power-modes').json()['pending']['gpu_uuids']==pending['gpu_uuids']
                page.get_by_role('button',name='取消等待',exact=True).click()
                context.request.post(BASE+'/fixture/finish/'+pending['job'])
                page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
                expect(eco).to_be_enabled()
                passed.append('scope race, immutable queued scope and cancellation')
                choose_scope('自选 GPU')
                expect(eco).to_be_enabled()
                page.get_by_role('button',name='手动调节',exact=True).click()
                page.get_by_label('每卡功率上限 W',exact=True).fill('120')
                page.get_by_role('button',name='应用设置',exact=True).click()
                expect(page.get_by_role('dialog').get_by_role('alert')).to_contain_text('unsupported power limit')
                page.get_by_label('每卡功率上限 W',exact=True).fill('185')
                page.get_by_role('button',name='应用设置',exact=True).click()
                expect(page.get_by_role('dialog')).to_have_count(0)
                pending=context.request.get(BASE+'/api/gpu/power-modes').json()['pending']
                assert pending['gpu_uuids']==['GPU-'+36*'0']
                context.request.post(BASE+'/fixture/finish/'+pending['job'])
                page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
                expect(page.locator('.oc-power-mode').nth(1)).to_have_attribute('aria-pressed','true')
                choose_scope('全部已授权 GPU')
                expect(page.locator('.oc-power-status')).to_contain_text('混合配置')
                page.screenshot(path=str(OUT/'power-dark.png'),full_page=True)
                passed.append('manual controls share scope; reject unsupported values; mixed state')
                fixture=context.request.get(BASE+'/api/gpu/power-modes').json()
                fixture['active']=None
                fixture['state']='failed'
                fixture['devices'][0]['actual']['clock_policy_known']=False
                fixture['devices'][0]['actual']['recovery_pending']=True
                fixture['last_result']={'id':'failed-fixture','state':'failed','error':'模拟设置失败：GPU 0 待恢复','result':{'devices':{
                    fixture['devices'][0]['uuid']:{'state':'restore_failed','error':'fixture recovery required'},
                    fixture['devices'][1]['uuid']:{'state':'restored','restored_exact':False}}}}
                page.route('**/api/gpu/power-modes',lambda route: route.fulfill(json=fixture))
                page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
                expect(page.locator('.oc-power-section > [role=alert]')).to_contain_text('模拟设置失败')
                expect(page.locator('.oc-power-status')).to_contain_text('尚无确认读数')
                expect(page.locator('.oc-power-mode[aria-pressed=true]')).to_have_count(0)
                page.get_by_role('button',name='任务详情',exact=True).click()
                expect(page.get_by_role('dialog')).to_contain_text('待恢复：GPU 0')
                expect(page.locator('.oc-power-result')).to_contain_text('待恢复')
                expect(page.locator('.oc-power-result')).to_contain_text('原锁频未知')
                expect(page.get_by_role('button',name='重试',exact=True)).to_be_enabled()
                page.keyboard.press('Escape')
                page.unroute_all(behavior='wait')
                page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
                expect(page.locator('.oc-power-section > [role=alert]')).to_have_count(0)
                passed.append('failure stays visible; unknown state and per-GPU partial recovery remain inspectable')
                page.goto(BASE+'/settings')
                toggle=page.get_by_role('switch',name='界面动效',exact=True)
                toggle.uncheck()
                expect(page.locator('html')).to_have_attribute('data-ui-motion','off')
                assert page.evaluate("localStorage.getItem('onecat_stream_animation')")!='off'
                page.reload()
                expect(toggle).not_to_be_checked()
                toggle.check()
                expect(page.locator('html')).to_have_attribute('data-ui-motion','on')
                page.emulate_media(reduced_motion='reduce')
                expect(page.locator('html')).to_have_attribute('data-ui-motion','off')
                expect(toggle).to_be_checked()
                page.emulate_media(reduced_motion='no-preference')
                passed.append('independent motion switch, persistence and OS override')
                thread=context.request.post(BASE+'/api/chat/import',data={'title':'Motion preview','messages':[{'role':'user','content':'Make a preview'},{'role':'assistant','content':'```html\n<h1>Motion preview</h1><button onclick="this.textContent=Number(this.textContent)+1">0</button>\n```'}]}).json()['id']
                page.goto(BASE+'/chat?thread='+thread)
                preview=page.get_by_role('button',name='预览 / 运行').first
                preview.click()
                frame=page.frame_locator('iframe:not(.oc-preview-candidate)')
                expect(frame.get_by_role('heading',name='Motion preview')).to_be_visible(timeout=20000)
                frame.get_by_role('button',name='0',exact=True).click()
                page.get_by_role('button',name='全屏',exact=True).click()
                expect(page.locator('.oc-preview-full')).to_be_visible()
                expect(frame.get_by_role('button',name='1',exact=True)).to_be_visible()
                page.keyboard.press('Escape')
                page.wait_for_timeout(250)
                expect(frame.get_by_role('button',name='1',exact=True)).to_be_visible()
                # The iframe survives the exit animation, then its runtime is disposed.
                page.get_by_role('button',name='关闭预览',exact=True).evaluate('element=>element.click()')
                assert page.locator('iframe').count()>0
                expect(page.locator('iframe')).to_have_count(0)
                expect(preview).to_be_focused()
                for _ in range(3):
                    preview.click()
                    page.get_by_role('button',name='关闭预览',exact=True).click()
                expect(page.locator('.oc-preview-panel')).to_have_count(0)
                expect(page.locator('iframe')).to_have_count(0)
                passed.append('preview exit retention, fullscreen instance preservation and rapid toggles')
                footer=page.locator('.oc-stream-actions')
                before=footer.bounding_box()
                copy=footer.get_by_role('button',name='复制',exact=True)
                copy.click()
                expect(copy.locator('.lucide-check')).to_have_count(1)
                after=footer.bounding_box()
                assert all(abs(before[k]-after[k])<1 for k in ['x','y','width','height'])
                passed.append('copy confirmation preserves footer geometry')
                page.goto(BASE+'/models')
                page.get_by_role('tab',name='启动预设',exact=True).click()
                page.get_by_role('button',name='新建预设',exact=True).click()
                details=page.locator('.oc-dialog details').last
                summary=details.locator('summary').first
                summary.click()
                expect(details).to_have_attribute('open','')
                page.wait_for_function('element=>element.style.height===""',arg=details.element_handle(),timeout=1500)
                summary.evaluate('element=>element.click()')
                page.wait_for_timeout(40)
                summary.evaluate('element=>element.click()')
                page.wait_for_function('element=>element.style.height===""',arg=details.element_handle(),timeout=1500)
                expect(details).to_have_attribute('open','')
                assert details.evaluate('element=>element.style.height')==''
                passed.append('disclosure latest-intent wins and clears temporary styles')
                page.keyboard.press('Escape')
                page.goto(BASE+'/performance')
                page.set_viewport_size({'width':390,'height':844})
                page.wait_for_timeout(250)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                expect(page.locator('.oc-power-device')).to_have_count(0)
                for card in page.locator('.oc-power-mode').all():
                    assert card.bounding_box()['height'] < 115
                page.screenshot(path=str(OUT/'power-mobile.png'))
                open_scope()
                expect(page.locator('.oc-power-device')).to_have_count(8)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(OUT/'scope-mobile.png'))
                page.keyboard.press('Escape')
                expect(page.get_by_role('dialog')).to_have_count(0)
                page.evaluate("document.documentElement.classList.remove('dark');localStorage.setItem('onecat_theme','light')")
                page.set_viewport_size({'width':1500,'height':1100})
                page.screenshot(path=str(OUT/'power-light.png'),full_page=True)
                passed.append('mobile, light/dark and keyboard dismissal')
                page.evaluate("localStorage.setItem('onecat_locale','en')")
                page.reload()
                expect(page.get_by_role('heading',name='GPU power settings',exact=True)).to_be_visible()
                expect(page.locator('.oc-power-scope')).to_contain_text('All 8 authorized GPUs')
                page.get_by_role('button',name='Change GPU control scope',exact=True).click()
                expect(page.get_by_role('dialog')).to_contain_text('All authorized GPUs')
                page.get_by_role('button',name='Choose GPUs',exact=True).click()
                expect(page.locator('.oc-power-device input')).to_have_count(8)
                page.get_by_role('button',name='Done',exact=True).click()
                page.screenshot(path=str(OUT/'power-english.png'))
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(OUT/'power-english-mobile.png'))
                passed.append('English overview, chooser and mobile layout')
                assert not errors, errors
                (OUT/'result.json').write_text(json.dumps({'passed':passed,'errors':errors},indent=2))
                print(json.dumps({'passed':passed,'errors':errors}),flush=True)
                browser.close()
        finally:
            process.terminate()
            try: process.wait(timeout=8)
            except subprocess.TimeoutExpired: process.kill(); process.wait()

if __name__=='__main__': main()
