"""API 客户端传输层：artifact 的 302 必须读 Location，不能被当成错误。"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from lib import api


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.endswith("/missing/artifact"):
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"code":"not_found","message":"x"}}')
            return
        if self.path.endswith("/artifact"):
            self.send_response(302)
            self.send_header("Location", "https://r2.example/obj?sig=1")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"data":{"id":"x"}}')

    def log_message(self, *args):  # 静音
        pass


def _serve():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def test_artifact_url_reads_location_from_302():
    srv, base = _serve()
    try:
        client = api.TroveApi(base, "k")
        assert client.artifact_url("t1") == "https://r2.example/obj?sig=1"
    finally:
        srv.shutdown()


def test_artifact_url_returns_none_on_404():
    srv, base = _serve()
    try:
        client = api.TroveApi(base, "k")
        assert client.artifact_url("missing") is None
    finally:
        srv.shutdown()
