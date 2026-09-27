"""The local web page where a person picks images (FR4): stdlib `http.server`, no framework.

Why no Flask/FastAPI, which the spec allowed: this serves one page, three static files and
six JSON endpoints to one person on one machine. A framework would be a third runtime
dependency for that, and an ASGI server a fourth. `http.server` is enough, and it is the
option the spec lists as "zero extra dependencies".

The server is reachable by every program on the machine, and by any web page open in the
person's browser, so it is not open by default:

- It binds 127.0.0.1 only, on a port the OS picks, so nothing else on the network sees it
  and there is no port to collide with.
- Every request must carry a random per-run token (in the URL the browser is opened with,
  then in the `X-Crivo-Token` header). A page on another site can make the browser
  send a POST to `localhost:<port>`, but it cannot know the token, and a custom header
  also forces a CORS preflight the server never answers.
- The `Host` header must be the address the server is bound to. That is what stops DNS
  rebinding, where a hostile name is pointed at 127.0.0.1 so that the browser considers the
  request same-origin and the token is the only thing left in the way.
- Request logging is off. The default logger writes the request line, and the first one
  contains the token.

Responses carry a Content-Security-Policy that allows scripts and styles only from this
server, and images from `https:` (Pixabay's CDN, for the temporary display of results the
Pixabay terms allow) and `data:`. Text that came from Pixabay (contributor names, tags) is
inserted by the page with `textContent` and never as HTML, so a username containing markup
is shown as text; the policy is the second layer if that is ever got wrong.
"""

from __future__ import annotations

import hmac
import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .selection import SelectionError, SelectionSession

TOKEN_HEADER = "X-Crivo-Token"
# Room for a pasted list of a few thousand keywords; everything else is a few dozen bytes.
MAX_BODY = 256 * 1024
DRAIN_LIMIT = 1024 * 1024  # how much of an oversized body is read and discarded
STATIC = {
    "/static/selector.css": ("selector.css", "text/css; charset=utf-8"),
    "/static/selector.js": ("selector.js", "text/javascript; charset=utf-8"),
}
INDEX = ("selector.html", "text/html; charset=utf-8")
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src https: data:; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def _static(name: str) -> bytes:
    return resources.files("crivo").joinpath("static", name).read_bytes()


class _Handler(BaseHTTPRequestHandler):
    server: SelectorServer  # narrowed for type checkers

    # -- plumbing ---------------------------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        pass  # the request line holds the token; see the module docstring

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: Any) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in self.server.allowed_hosts

    def _token_ok(self, supplied: str | None) -> bool:
        return supplied is not None and hmac.compare_digest(supplied, self.server.token)

    # -- routes -----------------------------------------------------------------------

    def do_GET(self) -> None:
        if not self._host_ok():
            return self._error(403, "Unexpected Host header.")
        url = urlsplit(self.path)
        if url.path == "/":
            token = (parse_qs(url.query).get("t") or [None])[0]
            if not self._token_ok(token):
                return self._error(403, "Open the address Crivo printed, including its token.")
            return self._send(200, _static(INDEX[0]), INDEX[1])
        if url.path in STATIC:
            name, content_type = STATIC[url.path]
            return self._send(200, _static(name), content_type)
        if url.path == "/api/state":
            if not self._token_ok(self.headers.get(TOKEN_HEADER)):
                return self._error(403, "Missing or wrong token.")
            return self._json(200, self.server.session.snapshot())
        self._error(404, "Not found.")

    def do_POST(self) -> None:
        # The body is consumed before anything is decided, including a rejection. A client
        # such as http.client (or a browser on a busy machine) may send the headers and the
        # body as separate segments. A server that answers 403 from the headers alone has
        # closed by the time the body arrives, the OS answers a segment sent to a closed
        # socket with a reset, and the reset can destroy the 403 the client has not read yet:
        # `ConnectionAbortedError` (WinError 10053) instead of a response. Found by running
        # the suite six times at once, where 2 of 6 copies failed on it.
        raw = self._read_body()
        if not self._host_ok():
            return self._error(403, "Unexpected Host header.")
        if not self._token_ok(self.headers.get(TOKEN_HEADER)):
            return self._error(403, "Missing or wrong token.")
        route = urlsplit(self.path).path
        handler = self.server.routes.get(route)
        if handler is None:
            return self._error(404, "Not found.")
        if raw is None:
            return self._error(413, "The request body is too large or its length is not valid.")
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            return self._error(400, "The request body is not JSON.")
        if not isinstance(data, dict):
            return self._error(400, "The request body must be a JSON object.")
        try:
            handler(data)
        except SelectionError as err:
            return self._error(409, str(err))
        except (KeyError, TypeError, ValueError):
            return self._error(400, "The request was missing a field or had the wrong type.")
        self._json(200, self.server.session.snapshot())

    def _read_body(self) -> bytes | None:
        """The request body, or None if it is over the limit or its length is unusable.

        Either way the bytes are taken off the socket (up to `DRAIN_LIMIT`), so that the
        connection can be closed cleanly after the answer. What is read past `MAX_BODY` is
        thrown away unparsed.
        """
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if length < 0:
            return None
        if length <= MAX_BODY:
            return self.rfile.read(length)
        remaining = min(length, DRAIN_LIMIT)
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 65536))
            if not chunk:
                break
            remaining -= len(chunk)
        return None


class SelectorServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, session: SelectionSession, port: int = 0):
        super().__init__(("127.0.0.1", port), _Handler)
        self.session = session
        self.token = secrets.token_urlsafe(24)
        host, bound = self.server_address[:2]
        self.allowed_hosts = {f"{host}:{bound}", f"localhost:{bound}"}
        self.routes = {
            "/api/pick": self._pick,
            "/api/skip": self._skip,
            "/api/retry": self._retry,
            "/api/start": self._start,
            "/api/finish": lambda body: session.finish(),
        }
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/?t={self.token}"

    def _pick(self, body: dict[str, Any]) -> None:
        self.session.pick(str(body["label"]), int(body["id"]), bool(body.get("selected", True)))

    def _skip(self, body: dict[str, Any]) -> None:
        self.session.skip(str(body["label"]), bool(body.get("skipped", True)))

    def _start(self, body: dict[str, Any]) -> None:
        self.session.start(str(body["text"]))

    def _retry(self, body: dict[str, Any]) -> None:
        term = body.get("term")
        self.session.retry(
            str(body["label"]),
            term=None if term is None else str(term),
            next_page=bool(body.get("next_page", False)),
        )

    def start(self) -> None:
        # 0.05 s, not the 0.5 s default: shutdown() waits out one poll, and the default
        # made each of this suite's ~50 server tests take half a second longer.
        self._thread = threading.Thread(
            target=self.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self.shutdown()
        self.server_close()
        if self._thread:
            self._thread.join(timeout=5)


def serve(session: SelectionSession, *, open_browser: bool = True, announce=print) -> None:
    """Run the page until the person clicks Finish. Ctrl-C leaves the session untouched."""
    server = SelectorServer(session)
    server.start()
    try:
        announce(f"Pick your images at {server.url}")
        if open_browser and not webbrowser.open(server.url):
            announce("Could not open a browser; open the address above yourself.")
        while not session.wait(0.25):
            pass  # a timeout, not a bare wait(), so Ctrl-C is delivered on Windows too
    finally:
        server.stop()
