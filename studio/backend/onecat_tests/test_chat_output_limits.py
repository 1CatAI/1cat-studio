import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from onecat import db
from onecat.chat_settings import normalize
from onecat.migrations import upgrade_output_mode
from onecat import chat_store as studio_db


def test_legacy_limit_and_explicit_limit_are_distinct():
    assert normalize({"max_tokens": 1024})["max_tokens"] is None
    assert normalize({"max_tokens": 1024, "max_tokens_mode": "manual"})["max_tokens"] == 1024
    assert normalize({"max_tokens": 4096})["max_tokens"] == 4096
    assert normalize({"max_tokens": 4096, "max_tokens_mode": "auto"})["max_tokens"] is None


def test_existing_chat_backup_and_stale_browser_writes(client):
    thread = client.post("/api/chat/threads", json={"title": "Legacy"}).json()
    studio_db.update_chat_thread(thread["id"], {"settings": {"max_tokens": 1024}})
    with db.connect() as conn:
        conn.execute("DELETE FROM records WHERE bucket='migrations' AND id='chat-output-mode-v1'")
    upgrade_output_mode()
    assert studio_db.get_chat_thread(thread["id"])["settings"]["max_tokens"] is None
    backup = Path(db.get("migrations", "chat-output-mode-v1")["backup"]) / "studio.db"
    with sqlite3.connect(backup) as conn:
        raw = conn.execute(
            "SELECT settings_json FROM chat_threads WHERE id=?", (thread["id"],)
        ).fetchone()[0]
    assert json.loads(raw)["max_tokens"] == 1024
    for settings, expected in [
        ({"max_tokens": 1024}, None),
        ({"max_tokens": 1024, "max_tokens_mode": "manual"}, 1024),
        ({"max_tokens": 3072}, 3072),
    ]:
        response = client.patch("/api/chat/threads/" + thread["id"], json={"settings": settings})
        assert response.status_code == 200
        assert (
            client.get("/api/chat/threads/" + thread["id"]).json()["thread"]["settings"][
                "max_tokens"
            ]
            == expected
        )


def test_proxy_normalizes_only_studio_legacy_limits(client):
    observed = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            observed.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"choices":[],"usage":{"prompt_tokens":1,"completion_tokens":1}}')

    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    db.put(
        "engine",
        "active",
        {
            "state": "ready",
            "port": server.server_port,
            "profile": {"served_model_name": "test", "name": "test", "gpu_uuids": []},
        },
    )
    try:
        for endpoint, settings, expected in [
            ("/api/inference/generate/stream", {"max_tokens": 1024}, None),
            (
                "/api/inference/generate/stream",
                {"max_tokens": 1024, "max_tokens_mode": "manual"},
                1024,
            ),
            ("/v1/chat/completions", {"max_tokens": 1024}, 1024),
        ]:
            response = client.post(endpoint, json={"model": "test", "messages": [], **settings})
            assert response.status_code == 200, response.text
            assert observed[-1].get("max_tokens") == expected
            assert "max_tokens_mode" not in observed[-1]
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
