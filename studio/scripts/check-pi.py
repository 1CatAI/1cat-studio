#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Browser acceptance for engine controls and native PI event rendering.

Uses a deterministic task double, not a model/performance benchmark.
Run after build:onecat with playwright and its Chromium runtime installed.
"""
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

STUDIO = Path(__file__).resolve().parents[1]
SEED = r'''
import asyncio,os
import uvicorn
from pathlib import Path
from onecat import db
from onecat.config import initialize_paths
from onecat.agent import runtime,pi_runtime,tasks
from onecat.decode_metrics import aggregator
initialize_paths()
db.put('engine','active',{'state':'ready','port':1234,'profile_id':'test','profile':{
 'name':'Test model','served_model_name':'local','tool_calling':True,'gpu_uuids':[],
 'max_model_len':32768,'max_num_seqs':2,'model_path':'/missing-test-model'}})
runtime.info=lambda:{'installed':True,'sandbox_ready':True,'version':runtime.VERSION}
pi_runtime.info=lambda:{'installed':Path(os.environ['PI_READY']).exists(),'ready':Path(os.environ['PI_READY']).exists(),
 'sandbox_ready':True,'version':pi_runtime.VERSION,'runtime_version':pi_runtime.VERSION,'protocol_version':2}
async def execute(self):
 try:
  self.status('running',current_turn='test-turn')
  await self.ingest_pi({'type':'subagent_lifecycle','payload':{'id':'child','agent':'scout','status':'started'}})
  await self.ingest_pi({'type':'subagent_event','payload':{'id':'child','event':{'type':'tool_execution_start','toolCallId':'nested-spawn'}}})
  await self.ingest_pi({'type':'subagent_lifecycle','payload':{'id':'nested','agent':'reviewer','status':'started','parentToolCallId':'nested-spawn'}})
  await self.ingest_pi({'type':'subagent_progress','payload':{'agent':'scout','progress':{'id':'child','status':'running','requests':3,'durationMs':1300,'lastIntent':'Inspect project files'}}})
  await asyncio.Event().wait()
 except asyncio.CancelledError:
  # Finish with a recent sample in the durable snapshot. The live card must
  # subsequently decay even though the task's SSE connection has closed.
  request_id='browser-'+self.record['id']
  aggregator.begin(request_id,source='agent',bucket='pi',task_id=self.record['id'])
  aggregator.observe(request_id,{'choices':[{'token_ids':[1]}]})
  aggregator.observe(request_id,{'choices':[{'token_ids':[2,3]}]})
  aggregator.finish(request_id)
  self.record['decode_aggregate']=aggregator.snapshot(2,self.record['id'])
  self.status('cancelled',settled=True)
 finally:
  self.record['settled']=True
  self.save()
tasks.PiRun.execute=execute
from onecat.app import create_app
uvicorn.run(create_app(),host='127.0.0.1',port=int(os.environ['PI_BROWSER_PORT']),log_level='error')
'''


def main():
    with socket.socket() as socket_:
        socket_.bind(("127.0.0.1", 0))
        port = socket_.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="onecat-pi-browser-") as temporary:
        root = Path(temporary)
        marker = root / "ready"
        env = {**os.environ, "ONECAT_STUDIO_HOME": str(root / "state"), "ONECAT_AUTO_GPU_ACTIONS": "0",
               "PYTHONPATH": str(STUDIO / "backend"), "PI_READY": str(marker), "PI_BROWSER_PORT": str(port)}
        base = f"http://127.0.0.1:{port}"
        with (root / "server.log").open("w") as log:
            server = subprocess.Popen([sys.executable, "-c", SEED], env=env, stdout=log, stderr=log)
            try:
                for _ in range(100):
                    try:
                        urllib.request.urlopen(base + "/api/health", timeout=.2).close()
                        break
                    except OSError:
                        if server.poll() is not None:
                            raise RuntimeError((root / "server.log").read_text())
                        time.sleep(.1)
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=True, executable_path=os.environ.get("ONECAT_BROWSER_EXECUTABLE") or None)
                    context = browser.new_context(viewport={"width": 1440, "height": 1000})
                    context.request.post(base + "/api/auth/setup", data={"password": "browser-test-password"})
                    project = context.request.post(base + "/api/agent/projects", data={"name": "PI browser"}).json()
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(base + "/agent")
                    page.get_by_label("Agent 项目", exact=True).select_option(project["id"])
                    picker = page.get_by_role("group", name="Agent 引擎", exact=True)
                    pi = picker.get_by_role("button", name="PI", exact=True)
                    codex = picker.get_by_role("button", name="Codex", exact=True)
                    mode = page.get_by_label("协作模式", exact=True)
                    pi.click()
                    expect(pi).to_have_attribute("aria-pressed", "true")
                    expect(page.locator(".oc-agent-start-hint")).to_contain_text("PI")
                    page.get_by_label("Agent 任务", exact=True).fill("Inspect the project")
                    mode.select_option("swarm")
                    page.reload()
                    expect(pi).to_have_attribute("aria-pressed", "true")
                    expect(mode).to_have_value("swarm")
                    expect(page.get_by_role("button", name="开始任务", exact=True)).to_be_disabled()
                    assert not context.request.get(base + "/api/agent/tasks").json()["items"]
                    marker.touch()
                    page.reload()
                    expect(pi).to_have_attribute("aria-pressed", "true")
                    mode.select_option("auto")
                    codex.click()
                    expect(mode).to_have_value("single")
                    expect(mode).to_be_disabled()
                    pi.click()
                    mode.select_option("swarm")
                    page.goto(base + "/service")
                    page.goto(base + "/agent")
                    expect(pi).to_have_attribute("aria-pressed", "true")
                    expect(mode).to_have_value("swarm")
                    expect(page.get_by_label("Agent 任务", exact=True)).to_have_value("Inspect the project")
                    page.get_by_role("button", name="开始任务", exact=True).click()
                    expect(pi).to_be_disabled()
                    expect(codex).to_be_disabled()
                    expect(mode).to_be_disabled()
                    expect(page.locator(".oc-agent-subagents").first).to_contain_text("Inspect project files")
                    expect(page.locator(".oc-agent-subagents .oc-agent-subagents")).to_contain_text("reviewer")
                    expect(page.locator(".oc-agent-subagents").first).to_contain_text("3 请求")
                    task_url = page.url
                    page.reload()
                    expect(page.locator(".oc-agent-subagents").first).to_contain_text("scout")
                    for width in (1440, 768, 390, 320):
                        page.set_viewport_size({"width": width, "height": 1000})
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), width
                    page.set_viewport_size({"width": 1440, "height": 1000})
                    page.get_by_role("button", name="停止任务", exact=True).click()
                    expect(page.locator(".oc-agent-status")).to_contain_text("已停止")
                    task_rate = page.locator(".oc-agent-swarm-card").filter(has=page.get_by_text("本任务聚合 Decode", exact=True)).locator(".oc-request-stats > div").first
                    expect(task_rate).to_contain_text("1 tok/s")
                    expect(task_rate).to_contain_text("0 tok/s", timeout=6000)
                    failed_metrics = "**/api/requests/aggregate?window_s=2&agent_task_id=*"
                    page.route(failed_metrics, lambda route: route.fulfill(status=503, json={"detail": "offline"}))
                    expect(task_rate).to_contain_text("— tok/s")
                    page.unroute(failed_metrics)
                    expect(task_rate).to_contain_text("0 tok/s")
                    assert page.url == task_url
                    page.goto(base + "/agent")
                    page.get_by_label("Agent 任务", exact=True).fill("Keep this draft")
                    expect(page.get_by_role("button", name="开始任务", exact=True)).to_be_enabled()
                    page.route("**/api/agent/status", lambda route: route.fulfill(status=503, json={"detail": "offline"}))
                    expect(page.locator(".oc-agent-start-hint")).to_contain_text("暂时无法确认 Agent 状态", timeout=6000)
                    expect(page.get_by_role("button", name="开始任务", exact=True)).to_be_disabled()
                    expect(page.get_by_label("Agent 任务", exact=True)).to_have_value("Keep this draft")
                    page.unroute("**/api/agent/status")
                    page.goto(base + "/service")
                    expect(page.locator(".oc-header-stats").get_by_role("button", name="实时聚合 Decode", exact=True)).to_be_visible()
                    assert not errors, errors
                    browser.close()
                    print("Passed: sibling engines, saved engine/mode, unavailable PI, Codex mode reset, task controls, nested SSE updates, reconnect, cancellation, stale data rejection, responsive widths, global Decode status")
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()


if __name__ == "__main__":
    main()
