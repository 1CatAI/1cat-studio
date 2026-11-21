# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from onecat import db, log_diagnostics as analysis

SETTINGS = "/api/settings/log-analysis"
RUN = "/api/logs/engine/analysis"
API_KEY = "fake-provider-test-only-key"
ENGINE_KEY = "fake-engine-credential"
TOKEN = "fake-gated-model-token"


def configure(client, url="https://provider.example/v1", key=API_KEY):
    return client.put(SETTINGS, json={"base_url": url, "model": "diagnostic-model", "api_key": key})


def test_settings_never_echo_key_and_do_not_reuse_it_on_endpoint_change(client):
    assert client.get(SETTINGS).json()["configured"] is False
    assert client.post(RUN).status_code == 400
    result = configure(client, "https://provider.example/v1/chat/completions")
    assert result.status_code == 200
    assert result.json() == {
        "base_url": "https://provider.example/v1", "model": "diagnostic-model",
        "api_key_set": True, "configured": True,
    }
    assert API_KEY not in client.get(SETTINGS).text
    same_endpoint = client.put(SETTINGS, json={
        "base_url": "https://provider.example/v1/", "model": "another-model",
    })
    assert same_endpoint.json()["api_key_set"] is True
    changed = client.put(SETTINGS, json={"base_url": "https://second.example/v1", "model": "model"})
    assert changed.json()["api_key_set"] is False
    assert analysis.provider_settings()["api_key"] == ""
    assert configure(client, "http://provider.example/v1").status_code == 400
    assert configure(client, "https://user:secret@provider.example/v1").status_code == 400
    assert client.post("/api/logs/unlisted/analysis").status_code == 404
    assert client.post("/api/auth/logout", json={}).status_code == 200
    assert client.get(SETTINGS).status_code == 401
    assert client.post(RUN).status_code == 401


def test_capture_keeps_causal_errors_and_redacts_secrets(state):
    db.put("engine", "active", {"api_key": ENGINE_KEY, "profile": {
        "name": "test profile", "environment": {"PRIVATE": "hidden"},
    }})
    db.put("secrets", "modelscope", {"token": TOKEN})
    log = state / "logs" / "engine.log"
    log.write_text(
        f"RuntimeError: CUDA out of memory\nAuthorization: Basic fake-basic-secret\n"
        f"launch --api-key={ENGINE_KEY}\n--hf-token fake-cli-value\n"
        f"token={TOKEN}\n"
        + "x" * 18_000 + "\nException: WorkerProc initialization failed\n",
        encoding="utf-8",
    )
    payload = analysis.capture("engine", {"revision": "test"})
    assert "CUDA out of memory" in payload["error_lines"]
    assert "WorkerProc initialization failed" in payload["log_tail"]
    assert payload["profile"] == {"name": "test profile"}
    for secret in (ENGINE_KEY, TOKEN, "fake-basic-secret", "fake-cli-value"):
        assert secret not in json.dumps(payload)


def test_capture_rejects_log_symlink_outside_log_root(state):
    private = state / "private.txt"
    private.write_text("private file", encoding="utf-8")
    (state / "logs" / "engine.log").symlink_to(private)
    with pytest.raises(ValueError, match="no log to analyze"):
        analysis.capture("engine", {"revision": "test"})


def test_provider_errors_do_not_echo_response_or_follow_redirects():
    provider = {"base_url": "https://provider.example/v1", "api_key": API_KEY,
                "model": "diagnostic-model"}

    def redirect(request):
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer " + API_KEY
        return httpx.Response(307, text=API_KEY, headers={"Location": "https://other.example"})

    with httpx.Client(transport=httpx.MockTransport(redirect), follow_redirects=False) as client:
        with pytest.raises(ValueError, match="HTTP 307") as error:
            analysis._request(client, provider, [], True)
    assert API_KEY not in str(error.value)


def test_worker_runs_real_job_with_local_provider_and_only_listed_tools(client, state):
    calls = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            assert self.path == "/v1/chat/completions"
            assert self.headers["Authorization"] == "Bearer " + API_KEY
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(payload)
            if len(calls) == 1:
                message = {"content": None, "tool_calls": [
                    {"id": "resources", "function": {"name": "host_resources", "arguments": "{}"}},
                    {"id": "blocked", "function": {"name": "unlisted_tool", "arguments": "{}"}},
                    {"id": "args", "function": {"name": "engine_configuration",
                                                 "arguments": '{"path":"/unrelated"}'}},
                ]}
            else:
                message = {"content": "Conclusion: Inspect GPU memory. " + API_KEY}
            data = json.dumps({"choices": [{"message": message}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    with ThreadingHTTPServer(("127.0.0.1", 0), Provider) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            assert configure(client, f"http://127.0.0.1:{server.server_port}/v1").status_code == 200
            (state / "logs" / "engine.log").write_text(
                "RuntimeError: CUDA out of memory. Bearer " + API_KEY, encoding="utf-8"
            )
            started = client.post(RUN)
            assert started.status_code == 200, started.text
            identity = started.json()["id"]
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                job = client.get(RUN).json()["job"]
                if job and job["state"] in {"completed", "failed", "cancelled"}:
                    break
                time.sleep(0.1)
            assert job["state"] == "completed", job
            assert job["id"] == identity
            assert job["result"]["report"] == "Conclusion: Inspect GPU memory. [redacted]"
            assert API_KEY not in json.dumps(db.get("jobs", identity))
        finally:
            server.shutdown()
            thread.join(timeout=3)

    assert len(calls) == 2
    assert all(API_KEY not in json.dumps(call["messages"]) for call in calls)
    prompt = calls[0]["messages"][0]["content"]
    assert "1CatAI/1Cat-vLLM" in prompt and "1CatAI/1cat-studio" in prompt
    assert "CUDA out of memory" in calls[0]["messages"][1]["content"]
    tool_results = {
        item["tool_call_id"]: json.loads(item["content"])
        for item in calls[1]["messages"] if item["role"] == "tool"
    }
    assert "ram_available_bytes" in tool_results["resources"]
    assert tool_results["blocked"] == {"error": "Unknown diagnostic tool."}
    assert tool_results["args"] == {"error": "Diagnostic tools do not accept arguments."}
