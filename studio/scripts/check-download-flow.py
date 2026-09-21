#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Browser download/default-profile flow using tiny weights and a CPU-only launcher."""
import importlib.util
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

spec = importlib.util.spec_from_file_location("browser_fixture", Path(__file__).with_name("check-browser.py"))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
EXTRA = r'''
import copy,hashlib,threading,importlib
from types import SimpleNamespace
from onecat import catalog,models,engine
from onecat.jobs import Job
app_module=importlib.import_module('onecat.app')
inventory=copy.deepcopy(catalog.directory())
item=inventory['entries'][0]
contents={'config.json':json.dumps({'model_type':'qwen3_5','architectures':[item['architecture']]}).encode(),'model.safetensors':b'cpu-only-verified-fixture'}
item['files']=[{'path':n,'bytes':len(v),'sha256':hashlib.sha256(v).hexdigest()} for n,v in contents.items()]
catalog.directory=lambda:inventory
devices.append({**devices[0],'index':4,'uuid':'GPU-'+'a'*36,'name':'Quadro P400','compute_capability':[6,1],'memory_total_mib':2048})
gpu.snapshot=lambda:{'gpus':devices,'available':True,'timestamp':time.time()}
db.patch('runtimes','r',{'release':{'version':'dev'},'capabilities':{'vllm_version':'dev'}})
class Hub:
 def list_repo_files(self,*args,**kwargs):
  return [SimpleNamespace(path=f['path'],size=f['bytes'],sha256=f['sha256'],is_dir=False) for f in item['files']]
 def download_repo(self,*args,**kwargs):
  folder=kwargs['local_dir'];folder.mkdir(parents=True,exist_ok=True)
  for name,body in contents.items():
   time.sleep(.9)
   kwargs['progress_callbacks'][0](name,len(body)).update(len(body))
   (folder/name).write_bytes(body)
models.hub=lambda:Hub()
def create(kind,payload):
 id=db.uid();record={'id':id,'kind':kind,'state':'queued','stage':'queued','progress':0,'created_at':time.time(),'payload':payload}
 db.put('jobs',id,record)
 def work():
  job=Job(id)
  try:
   job.update('starting')
   if kind=='download_model':result=models.download(job)
   elif kind=='start_model':
    profile,runtime=engine.validate_profile(db.get('profiles',payload['profile_id']))
    argv=engine.build_argv(profile,runtime,12345,None)
    db.put('engine','active',{'state':'ready','profile_id':profile['id'],'profile':profile})
    result={'profile_id':profile['id'],'argv':argv,'cpu_only':True}
   else:raise ValueError('Unexpected fixture operation: '+kind)
   job.finish(result)
  except BaseException as error:job.fail(error)
 threading.Thread(target=work,daemon=True).start()
 return record
app_module.create_job=create
# Model switching has its own scheduler; keep this fixture on the CPU launcher.
from onecat import model_switch
model_switch.schedule=create
'''
SEED = fixture.SEED.replace('uvicorn.run(create_app()', EXTRA + '\nuvicorn.run(create_app()')


def check_download_speed(page):
    """Exercise the real card against task samples, including frozen requests."""
    job = {'id':'speed-fixture','kind':'download_model','state':'running',
           'stage':'downloading','progress':25,'downloaded_bytes':1024**3,
           'expected_bytes':4*1024**3,'bytes_per_second':12.5*1024**2,
           'updated_at':time.time()}
    def supported(route):
        response = route.fetch()
        data = response.json()
        data['items'][0]['job'] = job.copy()
        route.fulfill(response=response, json=data)
    def jobs(route):
        route.fulfill(json={'items':[job.copy()]})
    page.route('**/api/models/supported', supported)
    page.route('**/api/jobs', jobs)
    page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
    rate = page.locator('.oc-support-card').first.locator('.oc-download-rate')
    expect(rate).to_have_text('下载速率 12.5 MB/s')
    for speed, text in [(512*1024,'512 KB/s'),(150,'150 B/s'),(0,'0 B/s'),(None,'—')]:
        job.update(bytes_per_second=speed, updated_at=time.time())
        page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
        expect(rate).to_have_text('下载速率 '+text)
    job.update(bytes_per_second=3*1024**2, updated_at=time.time())
    page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
    expect(rate).to_have_text('下载速率 3 MB/s')
    # No new response must expire the last rate, even if the page stops polling.
    page.unroute('**/api/jobs', jobs)
    def offline(route):
        route.abort()
    page.route('**/api/jobs', offline)
    expect(rate).to_have_text('下载速率 —',timeout=8000)
    page.unroute('**/api/jobs', offline)
    page.route('**/api/jobs', jobs)
    for patch in [{'cancel_requested':True,'stage':'downloading'},
                  {'cancel_requested':False,'stage':'checking_model'}]:
        job.update(**patch, updated_at=time.time())
        page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
        expect(rate).to_have_text('下载速率 —')
    job.update(cancel_requested=False,stage='downloading',updated_at=time.time())
    page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
    expect(rate).to_have_text('下载速率 3 MB/s')
    page.set_viewport_size({'width':390,'height':844})
    rate.scroll_into_view_if_needed()
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.screenshot(path=str(fixture.ARTIFACTS/'download-speed-mobile.png'))
    page.evaluate("localStorage.setItem('onecat_locale','en')")
    page.reload()
    expect(rate).to_have_text('Download speed 3 MB/s')
    page.evaluate("localStorage.setItem('onecat_locale','zh')")
    page.unroute('**/api/jobs', jobs)
    page.unroute('**/api/models/supported', supported)
    page.set_viewport_size({'width':1500,'height':1100})
    page.reload()
    expect(rate).to_have_count(0)


def run():
    with tempfile.TemporaryDirectory(prefix="onecat-download-flow-") as state:
        env = {**os.environ, "ONECAT_STUDIO_HOME": state, "PYTHONPATH": str(fixture.ROOT / "studio/backend"), "TEST_PORT": str(fixture.PORT)}
        with (fixture.ARTIFACTS / "download-server.log").open("w") as log:
            server = subprocess.Popen([str(fixture.ROOT / ".venv/bin/python"), "-c", SEED], env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                for _ in range(100):
                    try:
                        urllib.request.urlopen(fixture.BASE + '/api/health', timeout=1)
                        break
                    except OSError:
                        time.sleep(.1)
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=True, executable_path=os.environ.get("ONECAT_BROWSER_EXECUTABLE") or None)
                    context = browser.new_context(viewport={'width':1500,'height':1100})
                    assert context.request.post(fixture.BASE+'/api/auth/setup',data={'password':'download-browser-fixture'}).ok
                    page=context.new_page();errors=[]
                    page.on('pageerror',lambda e:errors.append(str(e)))
                    page.goto(fixture.BASE+'/models')
                    page.get_by_role('button',name='全部支持',exact=True).click()
                    check_download_speed(page)
                    cards=page.locator('.oc-support-card')
                    expect(cards).to_have_count(9)
                    expect(page.get_by_role('button',name='ModelScope 下载',exact=True)).to_have_count(9)
                    assert page.get_by_role('button',name='ModelScope 下载',exact=True).evaluate_all('buttons=>buttons.every(b=>!b.disabled)')
                    card=cards.first
                    card.locator('.oc-default-recipe summary').click()
                    expect(card.locator('.oc-default-recipe')).to_contain_text('自动：跟随模型')
                    card.get_by_role('button',name='编辑默认配置').click()
                    dialog=page.get_by_role('dialog')
                    expect(dialog).to_be_visible()
                    dialog.locator('.oc-profile-advanced').evaluate("element => element.open = true")
                    expect(dialog.locator('.oc-gpu-choices [role=checkbox][data-state=checked]')).to_have_count(4)
                    expect(dialog.get_by_label('最大生成长度', exact=True)).to_have_value('')
                    expect(dialog.locator('select').filter(has=page.locator('option[value="r"]'))).to_have_value('')
                    expect(dialog.get_by_role('button',name='保存',exact=True)).to_be_disabled()
                    dialog.get_by_role('button',name='取消',exact=True).click()
                    card.get_by_role('button',name='ModelScope 下载',exact=True).click()
                    expect(card.locator('.oc-model-download')).to_be_visible()
                    expect(card.get_by_role('button',name='ModelScope 下载',exact=True)).to_be_disabled()
                    card.get_by_role('button',name='取消下载',exact=True).click()
                    expect(card.locator('.oc-model-download')).to_have_count(0,timeout=10000)
                    expect(card.get_by_role('button',name='ModelScope 下载',exact=True)).to_be_enabled()
                    cancelled=context.request.get(fixture.BASE+'/api/jobs').json()['items']
                    assert any(j['state']=='cancelled' for j in cancelled)
                    card.get_by_role('button',name='ModelScope 下载',exact=True).click()
                    page.reload()
                    try:
                        expect(card.get_by_role('link',name='准备运行环境',exact=True)).to_be_visible(timeout=15000)
                    except Exception:
                        print(json.dumps({'card':card.inner_text(),'jobs':context.request.get(fixture.BASE+'/api/jobs').json()},ensure_ascii=False),flush=True)
                        raise
                    expect(card.get_by_role('link',name='准备运行环境',exact=True)).to_be_visible()
                    entries=context.request.get(fixture.BASE+'/api/models/list').json()['items']
                    local=next(m for m in entries if m.get('catalog_id')=='QUASAR-QAT/Qwen3.8-27B-QUASAR-NVFP4')
                    jobs=context.request.get(fixture.BASE+'/api/jobs').json()['items']
                    completed=next(j for j in jobs if j['kind']=='download_model')
                    assert completed['state']=='completed' and completed['result']['profile_pending_reason']
                    subprocess.run([str(fixture.ROOT/'.venv/bin/python'),'-c',"from onecat import db; db.patch('runtimes','r',{'release':{'version':'1.5.0'},'capabilities':{'vllm_version':'1.5.0'}})"],env=env,check=True)
                    page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
                    expect(card.get_by_role('button',name='使用推荐预设启动',exact=True)).to_be_enabled()
                    card.get_by_role('button',name='使用推荐预设启动',exact=True).click()
                    page.wait_for_timeout(700)
                    state_result=context.request.get(fixture.BASE+'/api/inference/status').json()
                    profile=state_result['profile']
                    assert state_result['profile_id'].startswith('catalog-'),state_result
                    assert len(profile['gpu_uuids'])==4 and profile['dtype']=='half'
                    assert profile['kv_cache_dtype']=='fp8_e5m2' and profile['max_model_len']==262144
                    assert profile['default_sampling']['max_tokens'] is None and profile['speculative_config'] is None
                    assert profile['model_path']==local['path']
                    expect(card.get_by_role('link',name='开始聊天',exact=True)).to_be_visible()
                    page.get_by_role('tab',name='已下载',exact=True).click()
                    local_card=page.locator('.oc-support-card').filter(has_text='Qwen3.8-27B')
                    local_card.locator('.oc-default-recipe summary').click()
                    local_card.get_by_role('button',name='编辑默认配置').click()
                    expect(dialog.locator('.oc-gpu-choices [role=checkbox][data-state=checked]')).to_have_count(4)
                    dialog.get_by_role('button',name='取消',exact=True).click()
                    page.get_by_role('tab',name='发现模型',exact=True).click()
                    page.set_viewport_size({'width':390,'height':844})
                    expect(cards).to_have_count(9)
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    cards.first.scroll_into_view_if_needed()
                    page.screenshot(path=str(fixture.ARTIFACTS/'download-mobile.png'))
                    page.evaluate("localStorage.setItem('onecat_locale','en')")
                    page.reload()
                    expect(page.get_by_role('heading',name='Find a model for this machine')).to_be_visible()
                    assert not errors,errors
                    print(json.dumps({'download_without_runtime':'passed','default_config_before_download':'passed','cancel_and_retry':'passed','progress_and_reload':'passed','missing_runtime_after_download':'passed','default_profile_and_launch':'passed','local_profile_defaults':'passed','mobile_and_english':'passed','errors':errors}))
                    browser.close()
            finally:
                server.terminate();server.wait(timeout=15)


if __name__ == '__main__':
    run()
