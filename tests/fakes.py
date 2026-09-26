"""Fakes for the network, the clock and sleep. Tests never touch the network.

CLAUDE.md -> Tests: the client takes an injectable session, clock and sleep so that a
429, a dropped connection or a 24-hour expiry is a line in a test and not a real wait.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from typing import Any

import requests
from PIL import Image


class FakeClock:
    """One timeline for `monotonic`, `time` and `sleep`: sleeping advances it."""

    def __init__(self, start: float = 1_000_000.0):
        self.now = start
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeResponse:
    def __init__(
        self,
        status: int = 200,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
        text: str = "",
        content: bytes = b"",
        drop_after: int | None = None,
    ):
        self.status_code = status
        self._json = json_body
        self.headers = headers or {}
        self.text = text
        self.content = content
        self.drop_after = drop_after  # bytes delivered before the connection "resets"
        self.closed = False

    def json(self) -> Any:
        if self._json is None:
            raise requests.exceptions.JSONDecodeError("Expecting value", "", 0)
        return self._json

    def iter_content(self, chunk_size: int = 1) -> Iterator[bytes]:
        data = self.content if self.drop_after is None else self.content[: self.drop_after]
        for start in range(0, len(data), chunk_size):
            yield data[start : start + chunk_size]
        if self.drop_after is not None and self.drop_after < len(self.content):
            raise requests.exceptions.ChunkedEncodingError("connection reset")

    def close(self) -> None:
        self.closed = True


class FakeSession:
    """Plays back scripted responses (or raises scripted exceptions), one per call.

    The last script entry repeats, so `FakeSession([FakeResponse(429)])` is "always 429".
    """

    def __init__(self, script: list[Any] | None = None):
        self.script = list(script or [FakeResponse(200, payload())])
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, BaseException):
            raise item
        return item


class RoutingSession:
    """Answers by URL, for tests where one session serves both the API and the image CDN.

    `handler(url, kwargs)` returns a response (or raises). Every call is recorded.
    """

    def __init__(self, handler: Any):
        self.handler = handler
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> Any:
        self.calls.append({"url": url, **kwargs})
        return self.handler(url, kwargs)


def hit(n: int = 1, **overrides: Any) -> dict[str, Any]:
    """A search hit shaped like Pixabay's, with every field the client reads."""
    base = {
        "id": n,
        "pageURL": f"https://pixabay.com/photos/thing-{n}/",
        "type": "photo",
        "tags": "red apple, fruit, food",
        "previewURL": f"https://cdn.pixabay.com/photo/{n}_150.jpg",
        "webformatURL": f"https://pixabay.com/get/{n}_640.jpg",
        "largeImageURL": f"https://pixabay.com/get/{n}_1280.jpg",
        "imageWidth": 4000,
        "imageHeight": 3000,
        "user": f"user{n}",
        "user_id": 100 + n,
    }
    return {**base, **overrides}


def payload(n: int = 3, total: int | None = None) -> dict[str, Any]:
    hits = [hit(i) for i in range(1, n + 1)]
    return {"total": total if total is not None else n, "totalHits": n, "hits": hits}


def png_bytes(size: tuple[int, int] = (40, 30), color: Any = (200, 30, 30), mode: str = "RGB"):
    """A real image, generated. Fixtures are never fetched (CLAUDE.md -> Pixabay rules)."""
    buffer = io.BytesIO()
    Image.new(mode, size, color).save(buffer, format="PNG")
    return buffer.getvalue()
