"""`sloth ui`: a small local web UI over the same core as the CLI.

Binds to 127.0.0.1 only. Every POST must carry a token that is generated per
run and embedded in the page, and requests whose Host header isn't this server
are refused, so other websites open in your browser can't drive it.
"""

from __future__ import annotations

import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlparse

from . import config
from .service import Service


def make_handler(service: Service, token: str, port: int):
    page = resources.files("sloth").joinpath("static/index.html").read_text(encoding="utf-8")
    page = page.replace("{{TOKEN}}", token)
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "sloth"

        def log_message(self, *args):  # keep the terminal quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data) -> None:
            self._send(code, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _host_ok(self) -> bool:
            if self.headers.get("Host") not in allowed_hosts:
                self._json(403, {"error": "unexpected Host header"})
                return False
            return True

        def do_GET(self):
            if not self._host_ok():
                return
            url = urlparse(self.path)
            q = parse_qs(url.query)
            try:
                if url.path == "/":
                    self._send(200, page.encode(), "text/html; charset=utf-8")
                elif url.path == "/api/state":
                    self._json(200, service.state())
                elif url.path == "/api/explain":
                    self._json(200, service.explain(int(q["index"][0])))
                elif url.path == "/api/browse":
                    self._json(200, service.browse(q.get("path", [None])[0]))
                elif url.path == "/api/undo/preview":
                    self._json(200, service.undo_preview(q.get("batch", [None])[0]))
                else:
                    self._json(404, {"error": "not found"})
            except (ValueError, KeyError, PermissionError) as e:
                self._json(400, {"error": str(e)})

        def do_POST(self):
            if not self._host_ok():
                return
            if not secrets.compare_digest(self.headers.get("X-Sloth-Token", ""), token):
                self._json(403, {"error": "missing or wrong token"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(length)) if length else {}
                path = urlparse(self.path).path
                if path == "/api/plan":
                    service.start_plan(bool(data.get("recursive")))
                    self._json(202, {"ok": True})
                elif path == "/api/override":
                    service.set_override(int(data["index"]), data.get("override"))
                    self._json(200, {"ok": True})
                elif path == "/api/decision":
                    service.set_decision(str(data["key"]), data.get("decision"))
                    self._json(200, {"ok": True})
                elif path == "/api/apply":
                    self._json(200, service.apply())
                elif path == "/api/undo":
                    self._json(200, service.undo(data.get("batch"), bool(data.get("skip_changed"))))
                elif path == "/api/folders":
                    service.set_folders(list(data.get("watched") or []), str(data.get("target") or ""))
                    self._json(200, {"ok": True})
                elif path == "/api/subjects":
                    service.add_subject(data)
                    self._json(200, {"ok": True})
                else:
                    self._json(404, {"error": "not found"})
            except (ValueError, KeyError, IndexError, TypeError, PermissionError, json.JSONDecodeError) as e:
                self._json(400, {"error": str(e)})

    return Handler


def serve(cfg: config.Config, port: int = 8765, open_browser: bool = False) -> None:
    service = Service(cfg)
    token = secrets.token_urlsafe(24)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(service, token, port))
    url = f"http://127.0.0.1:{port}/"
    print(f"sloth UI on {url}  (only reachable from this computer; Ctrl+C to stop)")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
