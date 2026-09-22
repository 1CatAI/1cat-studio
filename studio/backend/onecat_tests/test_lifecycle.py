import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psutil
import pytest
from onecat import db, engine, gpu
from onecat.jobs import Cancelled, Job, same_process
from onecat.runtimes import run_command


class FakeVLLM(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for event in [
            {"choices": [{"delta": {"content": "你好 🌊"}}]},
            {"choices": [{"delta": {"content": "，世界"}}]},
            {"choices": [], "usage": {"prompt_tokens": 8, "completion_tokens": 7}},
        ]:
            self.wfile.write(("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")


@pytest.mark.parametrize("key", ["normal-test-key", "-leading-dash", "--looks-like-option"])
def test_engine_api_key_is_parsed_as_a_value_and_redacted(key):
    argv = engine.build_argv(
        {"name": "test", "model_path": "/model", "runtime_id": "r"},
        {"python_path": sys.executable}, 8000, key,
    )
    parser = argparse.ArgumentParser()
    # vLLM accepts one or more API keys. A URL-safe random key can start with '-'.
    parser.add_argument("--api-key", nargs="+")
    args, _ = parser.parse_known_args(argv)
    assert args.api_key == [key]
    assert key not in engine.redact_log(" ".join(argv))


def test_engine_command_preview_omits_api_key():
    argv = engine.build_argv(
        {"name": "test", "model_path": "/model", "runtime_id": "r"},
        {"python_path": sys.executable}, 8000, None,
    )
    assert not any(arg.startswith("--api-key") for arg in argv)


def test_stream_preserves_unicode_and_uses_reported_tokens(client):
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeVLLM)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        db.put(
            "engine",
            "active",
            {
                "state": "ready",
                "port": server.server_port,
                "profile": {"served_model_name": "test", "name": "Test", "gpu_uuids": []},
            },
        )
        response = client.post(
            "/api/inference/generate/stream", json={"model": "test", "messages": [], "stream": True}
        )
        assert (
            response.status_code == 200 and "你好 🌊" in response.text and "[DONE]" in response.text
        )
        row = client.get("/api/requests").json()["items"][0]
        assert (
            row["completion_tokens"] == 7 and row["prompt_tokens"] == 8 and row["ttft"] is not None
        )
        assert engine.active_requests() == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_command_cancel_kills_descendants(tmp_path):
    pidfile = tmp_path / "child"
    code = "import subprocess,sys,time,pathlib;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);pathlib.Path(sys.argv[1]).write_text(str(p.pid));time.sleep(60)"

    class CancelWhenSpawned:
        def check_cancelled(self):
            if pidfile.exists():
                raise Cancelled("test cancellation")

    with pytest.raises(Cancelled):
        run_command(CancelWhenSpawned(), [sys.executable, "-c", code, str(pidfile)])
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                break
        except psutil.NoSuchProcess:
            break
        time.sleep(0.05)
    else:
        pytest.fail("Cancelled command left a live child process")


def test_engine_start_cancel_cleans_owned_process(monkeypatch, tmp_path):
    profile = {
        "id": "p",
        "name": "p",
        "runtime_id": "r",
        "model_path": str(tmp_path),
        "gpu_uuids": [],
        "hardware_profile": None,
    }
    runtime = {"id": "r", "python_path": sys.executable, "environment": {}}
    db.put("profiles", "p", profile)
    db.put("jobs", "j", {"id": "j", "kind": "start_model", "payload": {}, "state": "running"})
    monkeypatch.setattr(engine, "validate_profile", lambda p: (p, runtime))
    monkeypatch.setattr(
        engine, "build_argv", lambda *args: [sys.executable, "-c", "import time;time.sleep(60)"]
    )
    monkeypatch.setattr(engine, "shutil_which_systemctl", lambda: False)
    job = Job("j")
    seen = []

    def cancel():
        state = engine.private_state()
        if state.get("pid"):
            seen.append((state["pid"], state["process_created"]))
            raise Cancelled("cancel loading")

    monkeypatch.setattr(job, "check_cancelled", cancel)
    with pytest.raises(Cancelled):
        engine.start(job, "p")
    assert seen and not same_process(*seen[0]) and engine.status()["state"] == "stopped"


def test_uuid_profiles_resolve_to_current_driver_indices(monkeypatch):
    monkeypatch.setattr(gpu, "selected_devices", lambda ids: [{"index": 3}, {"index": 1}])
    env = engine.runtime_env(
        {"id": "r", "environment": {}}, {"gpu_uuids": ["stable-a", "stable-b"]}
    )
    assert env["CUDA_VISIBLE_DEVICES"] == "3,1" and env["CUDA_DEVICE_ORDER"] == "PCI_BUS_ID"


def test_imported_runtime_finds_tools_beside_real_interpreter(tmp_path, monkeypatch):
    import shutil
    import subprocess

    real_bin = tmp_path / "runtime" / "bin"
    alias_bin = tmp_path / "alias" / "bin"
    real_bin.mkdir(parents=True)
    alias_bin.mkdir(parents=True)
    interpreter = real_bin / "python"
    interpreter.touch()
    alias = alias_bin / "python"
    alias.symlink_to(interpreter)
    ninja = real_bin / "ninja"
    ninja.write_text("#!/bin/sh\nprintf 'runtime-ninja\\n'\n")
    ninja.chmod(0o755)
    monkeypatch.setenv("PATH", "/usr/bin")

    env = engine.runtime_env(
        {"id": "r", "python_path": str(alias), "environment": {}},
        {"gpu_uuids": []},
    )
    assert shutil.which("ninja", path=env["PATH"]) == str(ninja)
    assert subprocess.check_output(["ninja"], env=env, text=True) == "runtime-ninja\n"
    # A helper installed in the venv itself takes precedence over the base env.
    (alias_bin / "ninja").symlink_to(ninja)
    assert shutil.which("ninja", path=env["PATH"]) == str(alias_bin / "ninja")


def test_recent_driver_uses_wakeable_v100_idle_clocks(monkeypatch):
    monkeypatch.setattr(
        gpu,
        "selected_devices",
        lambda ids: [
            {
                "index": 1,
                "name": "Tesla V100-SXM2-32GB",
                "compute_capability": [7, 0],
            }
        ],
    )
    monkeypatch.setattr(gpu, "snapshot", lambda: {"driver": "580.173.02"})
    env = engine.runtime_env({"id": "r", "environment": {}}, {"gpu_uuids": ["stable"]})
    assert env["CUDA_DISABLE_PERF_BOOST"] == "1"


def test_v100_idle_clock_policy_requires_supported_driver_and_allows_override(monkeypatch):
    monkeypatch.setattr(
        gpu,
        "selected_devices",
        lambda ids: [
            {
                "index": 1,
                "name": "Tesla V100-SXM2-32GB",
                "compute_capability": [7, 0],
            }
        ],
    )
    monkeypatch.setattr(gpu, "snapshot", lambda: {"driver": "580.104.99"})
    old_driver = engine.runtime_env({"id": "r", "environment": {}}, {"gpu_uuids": ["stable"]})
    assert "CUDA_DISABLE_PERF_BOOST" not in old_driver

    explicit = engine.runtime_env(
        {"id": "r", "environment": {"CUDA_DISABLE_PERF_BOOST": "0"}},
        {"gpu_uuids": ["stable"]},
    )
    assert explicit["CUDA_DISABLE_PERF_BOOST"] == "0"


def test_engine_failure_keeps_root_cause_and_redacts_credentials(tmp_path):
    logfile = tmp_path / "engine.log"
    key = "do-not-expose-this-key"
    logfile.write_text(
        "non-default args: {'api_key': '" + key + "'}\n"
        "Traceback (most recent call last):\n"
        '  File "worker.py", line 7, in initialize\n'
        "RuntimeError: CUDA out of memory while allocating KV cache\n"
        + ("wrapper context that used to hide the cause\n" * 100)
        + "RuntimeError: Engine core initialization failed. See root cause above.\n"
        + "resource_tracker: There appear to be 1 leaked shared_memory objects\n"
    )

    message = engine.engine_failure_message(logfile, key)
    archived = engine.archive_engine_log(logfile, "launch/unsafe", key)

    assert "CUDA out of memory while allocating KV cache" in message
    assert "Engine core initialization failed" not in message
    assert key not in message
    assert archived == tmp_path / "engine-launch_unsafe.log"
    assert key not in archived.read_text()
    assert "[redacted]" in archived.read_text()


def test_log_redaction_works_after_engine_state_drops_api_key():
    assert engine.redact_log("args: --api-key secret-value") == ("args: --api-key [redacted]")
    assert engine.redact_log("api_key='secret-value'") == "api_key='[redacted]'"


def test_stop_of_collected_transient_service_is_idempotent(monkeypatch):
    from types import SimpleNamespace

    db.put(
        "engine",
        "active",
        {
            "state": "loading",
            "systemd_unit": "onecat-studio-engine-test.service",
            "pid": 99999999,
            "process_created": 1,
        },
    )
    monkeypatch.setattr(
        engine.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=5, stderr=b"Unit not loaded"),
    )
    db.put("jobs", "j", {"id": "j", "kind": "stop_model", "payload": {}})
    engine.stop(Job("j"), force=True)
    assert engine.status()["state"] == "stopped"
