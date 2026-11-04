"""A small local web server for the GUI: a JSON API over ``StudioService`` plus the
static page. Standard library only (``http.server``), so the GUI adds no dependencies.

    studio gui [--runs-dir runs] [--library library] [--agents claude|stub] [--port 8765]

Routes (all JSON unless noted):

    GET  /api/info                                 defaults, agent order, allowed agent sets
    GET  /api/runs                                 every run with its status
    POST /api/runs                                 {name, story_text, agents, config} -> start
    GET  /api/runs/<id>                            steps, open gate payload, approvals, log, files
    POST /api/runs/<id>/resume                     run again from where it stopped
    POST /api/runs/<id>/gates/<agent>              {decision, notes, edited_payload, resume}
    GET  /api/runs/<id>/outputs/<agent>[?version=N]
    GET  /api/runs/<id>/files/<path>               package, exports, asset reports (raw)
    GET  /api/runs/<id>/queue                      ranked and routed build requests, metrics
    POST /api/runs/<id>/queue                      {save: true} writes routes back, like --save
    GET  /api/library                              manifest, previews, accepted generator scripts
    GET  /api/library/preview/<path>               turntable contact sheets (raw)

POST bodies must be ``application/json``; that, plus the Host check below, keeps other web
pages in the browser from driving the API.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from studio.gui.service import GuiError, StudioService

log = logging.getLogger("studio.gui")

STATIC = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _static(name: str) -> bytes:
    return resources.files("studio.gui").joinpath("static", name).read_bytes()


class Handler(BaseHTTPRequestHandler):
    server: "StudioServer"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter than stderr per request
        log.debug("%s %s", self.address_string(), fmt % args)

    # ------------------------------------------------------------ replies
    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data: Any, status: int = 200) -> None:
        self._send(status, json.dumps(data, default=str).encode(), "application/json")

    def _error(self, status: int, message: str, details: Any = None) -> None:
        self._json({"error": message, "details": details}, status)

    def _file(self, path: Path) -> None:
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix in (".md", ".csv", ".txt", ".log"):
            ctype = "text/plain; charset=utf-8"
        self._send(200, path.read_bytes(), ctype)

    # ------------------------------------------------------------ guards
    def _host_ok(self) -> bool:
        if not self.server.loopback_only:
            return True
        host = self.headers.get("Host") or ""
        if host.startswith("["):
            host = host.split("]")[0] + "]"
        elif host.count(":") == 1:
            host = host.rsplit(":", 1)[0]
        return host in LOOPBACK_HOSTS

    def _body(self) -> dict[str, Any]:
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            raise GuiError("POST bodies must be application/json", 415)
        length = int(self.headers.get("Content-Length") or 0)
        if length > self.server.max_body:
            raise GuiError("request body too large", 413)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except json.JSONDecodeError as exc:
            raise GuiError(f"body is not JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise GuiError("body must be a JSON object")
        return data

    # ------------------------------------------------------------ routing
    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        if not self._host_ok():
            self._error(403, "unexpected Host header")
            return
        url = urlparse(self.path)
        try:
            if method == "GET" and url.path in STATIC:
                name = STATIC[url.path]
                ctype = {"html": "text/html", "js": "text/javascript", "css": "text/css"}[name.rsplit(".", 1)[1]]
                self._send(200, _static(name), ctype + "; charset=utf-8")
                return
            parts = [unquote(p) for p in url.path.strip("/").split("/")]
            if not parts or parts[0] != "api":
                raise GuiError("not found", 404)
            self._api(method, parts[1:], parse_qs(url.query))
        except GuiError as exc:
            self._error(exc.status, str(exc), exc.details)
        except Exception as exc:  # pragma: no cover - shown to the user rather than a dropped socket
            log.exception("request failed")
            self._error(500, f"{type(exc).__name__}: {exc}")

    def _api(self, method: str, parts: list[str], query: dict[str, list[str]]) -> None:
        svc = self.server.service
        n = len(parts)
        if parts == ["info"] and method == "GET":
            return self._json(svc.info())
        if parts == ["runs"]:
            if method == "GET":
                return self._json(svc.list_runs())
            return self._json(svc.create_run(self._body()), 201)
        if n >= 2 and parts[0] == "runs":
            run_id = parts[1]
            if n == 2 and method == "GET":
                return self._json(svc.run_detail(run_id))
            if n == 3 and parts[2] == "resume" and method == "POST":
                self._body()
                return self._json(svc.start(run_id))
            if n == 4 and parts[2] == "gates" and method == "POST":
                return self._json(svc.answer_gate(run_id, parts[3], self._body()))
            if n == 4 and parts[2] == "outputs" and method == "GET":
                version = query.get("version", [None])[0]
                try:
                    v = int(version) if version else None
                except ValueError as exc:
                    raise GuiError("version must be a number") from exc
                return self._json(svc.output(run_id, parts[3], v))
            if n >= 4 and parts[2] == "files" and method == "GET":
                return self._file(svc.file(run_id, "/".join(parts[3:])))
            if n == 3 and parts[2] == "queue":
                save = bool(self._body().get("save")) if method == "POST" else False
                return self._json(svc.queue(run_id, save=save))
        if parts == ["library"] and method == "GET":
            return self._json(svc.library_manifest())
        if n >= 3 and parts[:2] == ["library", "preview"] and method == "GET":
            return self._file(svc.preview("/".join(parts[2:])))
        raise GuiError("not found", 404)


class StudioServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], service: StudioService, max_body: int = 8 * 1024 * 1024):
        super().__init__(address, Handler)
        self.service = service
        self.max_body = max_body
        self.loopback_only = address[0] in ("127.0.0.1", "localhost", "::1")

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}/"


def make_server(service: StudioService, host: str = "127.0.0.1", port: int = 8765) -> StudioServer:
    return StudioServer((host, port), service)


def serve(service: StudioService, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    server = make_server(service, host, port)
    print(f"Animation Studio GUI on {server.url}  (runs: {service.runs_dir}, library: {service.library}, agents: {service.default_agents})")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("warning: listening beyond this machine; the GUI has no login")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(server.url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
        service.close()
