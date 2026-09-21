# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Runs INSIDE the network namespace; only the mounted model socket is reachable."""

import http.client
import http.server
import socket
import subprocess
import sys
import threading


class ModelConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(1800)
        self.sock.connect("/bridge/model.sock")


class Relay(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        if self.path != "/v1/responses":
            self.send_error(404, "Only the local model Responses endpoint is available")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8 * 1024 * 1024 or self.headers.get("Transfer-Encoding"):
                self.send_error(413)
                return
            body = self.rfile.read(length)
            connection = ModelConnection("local-model")
            connection.request(
                "POST",
                "/v1/responses",
                body,
                {
                    "Content-Type": "application/json",
                    "Content-Encoding": self.headers.get("Content-Encoding", "identity"),
                },
            )
            response = connection.getresponse()
            self.send_response(response.status)
            self.send_header(
                "Content-Type", response.getheader("Content-Type", "text/event-stream")
            )
            self.send_header("Connection", "close")
            self.end_headers()
            while data := response.read1(65536):
                self.wfile.write(data)
                self.wfile.flush()
        except (OSError, ValueError, http.client.HTTPException):
            self.close_connection = True
        finally:
            if "connection" in locals():
                connection.close()


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 9467), Relay)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # stdio stays attached to Studio. The PID namespace kills all children when
    # its supervisor exits, including commands still running after cancellation.
    process = subprocess.Popen(
        ["/opt/codex/bin/codex", "app-server"],
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    raise SystemExit(process.wait())
