"""自动游客 cookie：先打首页领 Set-Cookie，再带 cookie 请求目标页。"""
from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from lib import web as webmod


class _Handler(BaseHTTPRequestHandler):
    hits: list[tuple[str, str]] = []

    def do_GET(self):  # noqa: N802
        cookie = self.headers.get("Cookie", "")
        _Handler.hits.append((self.path, cookie))
        if self.path == "/":
            self.send_response(200)
            self.send_header("Set-Cookie", "CLIPSHARE=abc123; Path=/")
            self.end_headers()
            self.wfile.write(b"home")
            return
        if self.path == "/video":
            if "CLIPSHARE=abc123" not in cookie:
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b"need cookie")
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"player")
            return
        if self.path == "/flaky":
            # 第一次 500，第二次 200：验证 attempts 重试
            fails = sum(1 for p, _ in _Handler.hits if p == "/flaky")
            self.send_response(500 if fails == 1 else 200)
            self.end_headers()
            self.wfile.write(b"x")
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args):
        pass


def _serve(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setitem(webmod.WARM_UP, "127.0.0.1", f"http://127.0.0.1:{srv.server_port}/")
    _Handler.hits = []
    return srv, f"http://127.0.0.1:{srv.server_port}"


def test_fetch_page_warms_up_and_reuses_cookie(monkeypatch):
    srv, base = _serve(monkeypatch)
    try:
        res = asyncio.run(webmod.fetch_page(base + "/video"))
        assert res.status_code == 200
        assert res.text == "player"
        # 首页必须排在目标页之前
        assert [p for p, _ in _Handler.hits] == ["/", "/video"]
        assert any("CLIPSHARE=abc123" in c for _, c in _Handler.hits[1:])
    finally:
        srv.shutdown()


def test_fetch_page_retries_5xx_with_fresh_client(monkeypatch):
    srv, base = _serve(monkeypatch)
    try:
        res = asyncio.run(webmod.fetch_page(base + "/flaky", attempts=2))
        assert res.status_code == 200
    finally:
        srv.shutdown()
