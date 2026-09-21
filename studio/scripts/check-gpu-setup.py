#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Exercise authorization UI transitions without invoking host privilege tools."""
import json
import os
import re
from pathlib import Path
import runpy
import socket
import subprocess
import tempfile
import time
import urllib.request

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / '.artifacts/gpu-setup-20260908'
OUT.mkdir(parents=True, exist_ok=True)
seed = runpy.run_path(str(ROOT / 'studio/scripts/check-browser.py'))['SEED']
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
base = f'http://127.0.0.1:{port}'
with tempfile.TemporaryDirectory(prefix='onecat-gpu-ui-') as state, (OUT/'browser-server.log').open('w') as log:
    env={**os.environ, 'ONECAT_STUDIO_HOME':state, 'PYTHONPATH':str(ROOT/'studio/backend'), 'TEST_PORT':str(port)}
    server=subprocess.Popen([str(ROOT/'.venv/bin/python'),'-c',seed],env=env,stdout=log,stderr=log)
    try:
        for _ in range(100):
            try: urllib.request.urlopen(base+'/api/health',timeout=1); break
            except OSError: time.sleep(.1)
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True, executable_path=os.environ.get('ONECAT_BROWSER_EXECUTABLE'))
            context=browser.new_context(viewport={'width':1440,'height':1000})
            assert context.request.post(base+'/api/auth/setup',data={'password':'temporary-gpu-test-password'}).ok
            page=context.new_page()
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            data={'bundled':True,'installed':False,'available':False,'gpu_count':8,'allowed_gpu_count':0,'restricted':False,'can_authorize':True,'desktop_authorization':True,'job':None,'error':None}
            posts=[]
            def route(request):
                if request.request.method=='POST':
                    posts.append(request.request.post_data)
                    data['job']={'id':'authorization','state':'running','stage':'awaiting_system_authorization'}
                    request.fulfill(json={'id':'authorization'})
                else: request.fulfill(json=data)
            page.route(re.compile(r'/api/gpu/control(?:/setup)?$'),route)
            page.goto(base+'/settings')
            panel=page.get_by_role('region',name='GPU 控制',exact=True)
            expect(panel).to_contain_text('无需另外下载或填写参数')
            expect(panel).not_to_contain_text('install-gpu-helper.sh')
            data.update(installed=True,upgrade_required=True)
            page.goto(base+'/performance')
            expect(page.get_by_role('button',name='更新 GPU 控制',exact=True)).to_be_enabled()
            page.get_by_role('button',name='更新 GPU 控制',exact=True).click()
            expect(page.get_by_role('button',name='等待授权完成…',exact=True)).to_be_disabled()
            assert len(posts)==1
            page.screenshot(path=str(OUT/'performance-upgrade.png'))
            data.update(job=None,installed=False,upgrade_required=False)
            posts.clear()
            page.goto(base+'/settings')
            page.get_by_role('button',name='启用 GPU 控制',exact=True).click()
            expect(page.get_by_role('button',name='等待授权完成…',exact=True)).to_be_disabled()
            assert len(posts)==1
            page.screenshot(path=str(OUT/'authorization-pending.png'))
            page.reload()
            expect(page.get_by_role('button',name='等待授权完成…',exact=True)).to_be_disabled()
            data.update(job=None,error='系统授权未完成 / Authorization not completed')
            expect(page.get_by_role('button',name='启用 GPU 控制',exact=True)).to_be_enabled()
            page.get_by_role('button',name='启用 GPU 控制',exact=True).click()
            data.update(available=True,installed=True,allowed_gpu_count=8,job=None,error=None)
            expect(panel).to_contain_text('8 张已授权',timeout=7000)
            expect(page.get_by_role('button',name='启用 GPU 控制',exact=True)).to_have_count(0)
            page.screenshot(path=str(OUT/'authorization-ready.png'))
            page.get_by_label('界面语言',exact=True).select_option('en')
            expect(page.get_by_role('region',name='GPU controls',exact=True)).to_contain_text('8 authorized')
            data.update(available=False,installed=True,upgrade_required=True)
            expect(page.get_by_role('button',name='Update GPU controls',exact=True)).to_be_enabled(timeout=7000)
            page.set_viewport_size({'width':390,'height':844})
            page.get_by_role('region',name='GPU controls',exact=True).scroll_into_view_if_needed()
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(OUT/'authorization-mobile.png'))
            assert not errors, errors
            print(json.dumps({'passed':['built-in copy','performance upgrade action','authorize once','refresh pending','failure retry','enabled confirmation','English upgrade','mobile'],'errors':errors}))
            browser.close()
    finally:
        server.terminate();server.wait(timeout=15)
