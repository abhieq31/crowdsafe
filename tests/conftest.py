"""Shared test helpers."""

import socketserver
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


@pytest.fixture
def stub_server():
    """A tiny HTTP server: routes maps path -> (status, bytes). POST bodies are recorded."""
    routes, posts = {}, []

    class H(BaseHTTPRequestHandler):
        def _send(self):
            status, body = routes.get(self.path, (404, b"not found"))
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send()

        def do_POST(self):
            posts.append((self.path, self.headers.get("Authorization"),
                          self.rfile.read(int(self.headers.get("Content-Length", 0)))))
            self._send()

        def log_message(self, *a):
            pass

    class Server(HTTPServer):
        def server_bind(self):  # skip HTTPServer's reverse-DNS lookup, which can take 30 s
            socketserver.TCPServer.server_bind(self)
            self.server_name, self.server_port = "127.0.0.1", self.server_address[1]

    srv = Server(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", routes, posts
    srv.shutdown()
