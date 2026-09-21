#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Installation UI races with disposable state and mocked installers/GPU controls."""
import copy
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
OUT = ROOT / '.artifacts/runtime-audit/browser'
OUT.mkdir(parents=True, exist_ok=True)
SEED = runpy.run_path(str(ROOT / 'studio/scripts/check-runtime-releases.py'))['seed']


def main():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    failures, passed = [], []
    with tempfile.TemporaryDirectory(prefix='onecat-runtime-audit-') as state, (OUT/'server.log').open('w') as log:
        process = subprocess.Popen([str(ROOT/'.venv/bin/python'), '-c', SEED], stdout=log, stderr=subprocess.STDOUT,
            env={**os.environ, 'ONECAT_STUDIO_HOME': state, 'ONECAT_AUTO_GPU_ACTIONS': '0', 'TEST_PORT': str(port),
                 'ONECAT_RELEASE_FIXTURE': str(ROOT/'.artifacts/runtime-releases/github-releases.json'), 'PYTHONPATH': str(ROOT/'studio/backend')})
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen(base+'/api/health', timeout=1)
                    break
                except OSError:
                    time.sleep(.1)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True,
                    executable_path=os.environ.get('ONECAT_BROWSER_EXECUTABLE') or None,
                    args=['--disable-gpu', '--use-angle=swiftshader'])
                context = browser.new_context(viewport={'width': 1500, 'height': 1050})
                context.add_init_script("if(window===window.top)localStorage.setItem('onecat_theme','dark')")
                assert context.request.post(base+'/api/auth/setup', data={'password': 'audit-fixture-password'}).ok
                catalog = context.request.get(base+'/api/runtimes/releases').json()
                initial, other = catalog['items'][:2]
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                # Hold task polling to inspect the response feedback independently of its job.
                page.route('**/api/jobs', lambda route: route.fulfill(json={'items': []}))
                page.goto(base+'/setup')
                panel = page.locator('.oc-runtime-installer')
                select = panel.get_by_role('combobox')
                expect(select).to_have_value(initial['id'])
                with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/api/runtimes/install')) as response:
                    panel.get_by_role('button', name='安装独立环境', exact=True).click()
                first = response.value.json()
                newer = copy.deepcopy(initial)
                newer.update(id='github-999-aaaaaaaaaaaa', version='1.6.0', tag='v1.6.0')
                updated = {**catalog, 'items': [newer, *catalog['items']]}
                page.route('**/api/runtimes/releases', lambda route: route.fulfill(json=updated))
                page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
                try:
                    expect(select).to_have_value(initial['id'], timeout=1500)
                    assert page.evaluate("JSON.parse(localStorage.getItem('onecat:runtime-release'))") == initial['id']
                    passed.append('The default install target stays pinned when newer releases arrive')
                except AssertionError as error:
                    failures.append('Install selection drift: '+str(error)[:400])
                # Click/check feedback belongs only to the release on which it was triggered.
                select.select_option(initial['id'])
                with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/api/runtimes/install')):
                    panel.get_by_role('button', name='安装独立环境', exact=True).click()
                expect(panel.locator('.oc-runtime-install-actions .lucide-check')).to_have_count(1)
                select.select_option(other['id'])
                try:
                    expect(panel.locator('.oc-runtime-install-actions .lucide-check')).to_have_count(0, timeout=500)
                    passed.append('Success feedback resets immediately when selecting another release')
                except AssertionError as error:
                    failures.append('Stale success feedback: '+str(error)[:400])
                page.unroute('**/api/jobs')
                context.request.post(base+'/fixture/job/'+first['id'], data={'state': 'failed', 'error': 'Older failure'})
                select.select_option(initial['id'])
                page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
                with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/retry')) as response:
                    panel.get_by_role('button', name='重试安装', exact=True).click()
                current = response.value.json()
                context.request.post(base+'/fixture/job/'+current['id'], data={'state': 'running', 'stage': 'downloading',
                    'asset_name': 'current-wheel.whl', 'asset_index': 1, 'asset_count': 1, 'downloaded_bytes': 32*1024*1024,
                    'expected_bytes': 128*1024*1024, 'bytes_per_second': 8*1024*1024})
                # An older job updated last used to hide the still-running retry.
                context.request.post(base+'/fixture/job/'+first['id'], data={'state': 'failed', 'error': 'Older failure was reconciled'})
                page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
                try:
                    expect(panel.get_by_role('button', name='取消安装', exact=True)).to_be_visible(timeout=1500)
                    expect(panel.locator('.oc-runtime-job')).to_contain_text('current-wheel.whl')
                    passed.append('The live retry stays visible when an older failed job updates')
                except AssertionError as error:
                    failures.append('Old task shadows current install: '+str(error)[:400])
                panel.scroll_into_view_if_needed()
                page.screenshot(path=str(OUT/'runtime-races.png'))
                browser.close()
                result = {'passed': passed, 'failures': failures, 'browser_errors': errors}
                (OUT/'result.json').write_text(json.dumps(result, indent=2))
                print(json.dumps(result))
                assert not failures and not errors, result
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == '__main__':
    main()
