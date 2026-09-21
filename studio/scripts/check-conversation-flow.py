#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Flow regressions: actual Studio + pinned Codex, disposable models and no GPU I/O."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright, expect

fixture = runpy.run_path(str(Path(__file__).with_name('check-agent.py')))
ROOT, BASE, PORT, OUT = (fixture[k] for k in ('ROOT','BASE','PORT','OUT'))
EXTRA = r'''
from pathlib import Path
from onecat import proxy,models
from onecat.config import state_root
from starlette.responses import StreamingResponse,JSONResponse
for ident in ('alpha','beta'):
    path=state_root()/ident;path.mkdir(exist_ok=True)
    (path/'config.json').write_text('{}');(path/'weights.safetensors').write_bytes(b'fixture')
    (path/'chat_template.jinja').write_text('{% if enable_thinking %}<think>{% endif %}')
    item=models.inspect_model(str(path));item['id']=ident;db.put('models',ident,item)
    # Keep one registry record per installed path.
    for m in db.all_records('models'):
        if m['id'] not in ('alpha','beta'):db.delete('models',m['id'])
    p={**profile,'id':ident,'name':ident+' coding model','model_path':str(path),'runtime_id':'fixture'}
    db.put('profiles',ident,p)
profile=db.get('profiles','alpha')
db.put('engine','active',{'state':'ready','port':1,'profile_id':'alpha','launch_id':'fake-launch','profile':profile})
engine.validate_profile=lambda p:(p,{})
import onecat.app as app_module
original_schedule=app_module.scheduled_job
def schedule(kind,payload):
    if kind!='start_model':return original_schedule(kind,payload)
    p=db.get('profiles',payload['profile_id']);db.put('engine','active',{'state':'ready','port':1,'profile_id':p['id'],'launch_id':'fake-'+p['id'],'profile':p})
    j={'id':db.uid(),'kind':kind,'stage':'ready','state':'completed','created_at':time.time()};db.put('jobs',j['id'],j);return j
app_module.scheduled_job=schedule
async def chat_forward(request,*args):
    data=await request.json();db.put('fixture_requests',db.uid(),data)
    if 'reject' in json.dumps(data['messages']):return JSONResponse({'error':'fixture rejected request'},status_code=400)
    async def events():
        for i in range(15):
            await asyncio.sleep(.06)
            yield 'data: '+json.dumps({'choices':[{'delta':{'content':'Reply '+str(i)+' '}}]})+'\n\n'
        yield 'data: [DONE]\n\n'
    return StreamingResponse(events())
proxy.forward=chat_forward
'''
SEED=fixture['SEED'].replace('uvicorn.run(create_app()',EXTRA+'\nuvicorn.run(create_app()')

def run():
 with tempfile.TemporaryDirectory(prefix='onecat-flow-') as state,(OUT/'flow-server.log').open('w') as log:
  server=subprocess.Popen([str(ROOT/'.venv/bin/python'),'-c',SEED],env={**os.environ,'ONECAT_STUDIO_HOME':state,'ONECAT_AUTO_GPU_ACTIONS':'0','PYTHONPATH':str(ROOT/'studio/backend'),'AGENT_TEST_PORT':str(PORT)},stdout=log,stderr=subprocess.STDOUT)
  try:
   for _ in range(100):
    try:urllib.request.urlopen(BASE+'/api/health',timeout=.2).close();break
    except OSError:time.sleep(.1)
   with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,executable_path=os.environ.get('ONECAT_BROWSER_EXECUTABLE') or None,args=['--disable-gpu','--use-angle=swiftshader'])
    context=browser.new_context(viewport={'width':1440,'height':1000});page=context.new_page();errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(BASE+'/chat');page.locator('input[type=password]').fill('flow-test-password');page.locator('button[type=submit]').click()
    draft=page.get_by_label('消息草稿',exact=True);expect(draft).to_be_visible()
    think=page.locator('.oc-chat-composer').get_by_role('button',name='深度思考',exact=True)
    expect(think).to_be_enabled();think.click();expect(think).to_have_attribute('aria-pressed','true')
    draft.fill('preserve my draft');page.get_by_role('tab',name='Agent',exact=True).click();page.get_by_role('tab',name='聊天',exact=True).click()
    expect(draft).to_have_value('preserve my draft');expect(think).to_have_attribute('aria-pressed','true')
    page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))");expect(think).to_have_attribute('aria-pressed','true')
    draft.fill('reject');page.get_by_role('button',name='发送消息',exact=True).click();expect(page.locator('.oc-chat-workspace')).to_contain_text('fixture rejected request',timeout=15000)
    expect(draft).to_have_value('reject');expect(page.get_by_role('button',name='发送消息',exact=True)).to_be_enabled()
    draft.fill('  normal reply \n');page.get_by_role('button',name='发送消息',exact=True).click();page.wait_for_url('**/chat?thread=*')
    expect(page.locator('.oc-conversation')).to_contain_text('Reply 14',timeout=15000)
    expect(page.locator('.oc-conversation')).to_contain_text('Reply 0 Reply 1')
    expect(draft).to_have_value('');tid=page.url.split('thread=')[1]
    saved=context.request.get(BASE+'/api/chat/threads/'+tid).json();assert len(saved['messages'])==2,saved
    assert saved['thread']['settings']['thinking'] is True
    # A stale view must not replace messages from another tab.
    rejected=context.request.post(BASE+f'/api/chat/threads/{tid}/generate',data={'message_id':'stale','request':{},'messages':[],'expected_message_ids':[]})
    assert rejected.status==409
    page.locator('.oc-chat-composer').get_by_role('button',name='选择已下载模型',exact=True).click()
    dialog=page.get_by_role('dialog');expect(dialog).to_have_count(1);expect(dialog).to_contain_text('alpha');expect(dialog).to_contain_text('beta')
    dialog.locator('.oc-model-choice > button').filter(has_text='beta').click();expect(dialog).to_contain_text('模型已就绪')
    page.screenshot(path=str(OUT/'01-downloaded-model-picker.png'),full_page=True);page.keyboard.press('Escape')
    expect(page.locator('.oc-chat-composer').get_by_role('button',name='选择已下载模型',exact=True)).to_be_focused()
    assert context.request.get(BASE+'/api/inference/status').json()['profile_id']=='beta'
    page.get_by_role('tab',name='Agent',exact=True).click()
    pa=context.request.post(BASE+'/api/agent/projects',data={'name':'Project A'}).json()['id']
    pb=context.request.post(BASE+'/api/agent/projects',data={'name':'Project B'}).json()['id']
    page.reload();expect(page.get_by_label('Agent 项目',exact=True)).to_be_visible();page.get_by_label('Agent 项目',exact=True).select_option(pa)
    task=context.request.post(BASE+'/api/agent/tasks',data={'project_id':pb,'prompt':'create a page','request_id':'history-project'}).json()
    assert task.get('id'),task
    page.goto(BASE+'/agent?task='+task['id']);expect(page.locator('.oc-agent-status')).to_contain_text('任务完成',timeout=30000)
    page.get_by_role('link',name='新任务',exact=True).click();expect(page.get_by_label('Agent 项目',exact=True)).to_have_value(pb)
    composer=page.get_by_label('Agent 任务',exact=True)
    agent_think=page.locator('.oc-agent-composer').get_by_role('button',name='深度思考',exact=True)
    expect(agent_think).to_be_enabled();agent_think.click()
    composer.fill('/model ');composer.press('Enter');expect(page.get_by_role('dialog')).to_have_count(1);expect(page.get_by_role('dialog')).to_contain_text('选择已下载模型');page.keyboard.press('Escape')
    expect(composer).to_be_focused()
    # Hold only the submission response, navigate away, then complete it. The task
    # keeps running but the browser must not return to that task or erase a draft.
    held=[]
    def hold(route):
     if route.request.method=='POST':held.append(route)
     else:route.continue_()
    def no_thinking(route):
     response=route.fetch();data=response.json();data['model']['thinking']={'supported':False};route.fulfill(response=response,json=data)
    page.route('**/api/agent/status',no_thinking)
    page.evaluate("window.dispatchEvent(new Event('onecat:refresh'))")
    expect(agent_think).to_be_disabled()
    page.route('**/api/agent/tasks',hold);composer.fill('create another page');composer.press('Enter')
    expect(composer).to_have_attribute('readonly','')
    page.get_by_role('tab',name='聊天',exact=True).click()
    assert held and held[0].request.post_data_json['thinking'] is False
    page.unroute('**/api/agent/status',no_thinking)
    held[0].fulfill(response=held[0].fetch());page.wait_for_timeout(400);assert '/chat' in page.url
    page.unroute('**/api/agent/tasks',hold)
    page.get_by_role('tab',name='Agent',exact=True).click()
    # Streaming steer is the real pinned Codex RPC; the provider only simulates tokens.
    for _ in range(100):
     active=context.request.get(BASE+'/api/agent/tasks').json()['items']
     if not any(t['state'] in ['starting','running','waiting','stopping'] for t in active):break
     page.wait_for_timeout(100)
    composer.fill('slow-task');composer.press('Enter');page.wait_for_url('**/agent?task=*')
    expect(page.locator('.oc-agent-scroll')).to_contain_text('Streaming progress',timeout=15000)
    composer.fill('Also verify the project');expect(page.get_by_role('button',name='补充要求',exact=True)).to_be_enabled();page.get_by_role('button',name='补充要求',exact=True).click()
    expect(page.locator('.oc-agent-user').last).to_contain_text('Also verify the project',timeout=15000)
    page.get_by_role('button',name='停止任务',exact=True).click();expect(page.locator('.oc-agent-status')).to_contain_text('已停止',timeout=15000)
    for width in [1440,768,390,320]:
     page.set_viewport_size({'width':width,'height':900});page.wait_for_timeout(250)
     assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),width
     footer=page.locator('.oc-agent-status').bounding_box();assert footer['height']<45,footer
     actions=page.locator('.oc-agent-compose-actions').bounding_box();assert actions['height']<48,actions
     page.screenshot(path=str(OUT/f'02-agent-{width}.png'),full_page=True)
    page.evaluate("localStorage.setItem('onecat_theme','dark');localStorage.setItem('onecat_locale','en')");page.reload();expect(page.get_by_label('Agent task',exact=True)).to_be_visible()
    page.screenshot(path=str(OUT/'03-dark-english.png'),full_page=True)
    assert not errors,errors
    print(json.dumps({'passed':['thinking persistence','polling retains settings','rejected send retains draft','accepted send commits one user message','stale history rejected','downloaded model switch','single model modal','focus restored','new task retains project','late submission does not navigate','official turn/steer','mobile footer','no React errors'],'output':str(OUT)}))
    browser.close()
  finally:
   server.terminate()
   try:server.wait(timeout=10)
   except subprocess.TimeoutExpired:server.kill();server.wait()

if __name__=='__main__':run()
