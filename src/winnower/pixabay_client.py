"""Pixabay search client: throttle, backoff, and a 24-hour cache that never holds the key.

Why this is not `pixabay-python`
--------------------------------
The original spec (docs/requirements.md, "Module breakdown") called for a thin wrapper
around the `pixabay-python` package, on the argument that "no custom HTTP/rate-limit/
backoff code needed; the library covers it". Version 1.1.0 was installed and driven with
a faked `requests.get` on 2026-09-27, and it does not cover it. Four failures, each on a
path the spec's own non-functional requirements name:

1. A 429 crashes the run. `makeQuery` swallows the `HTTPError`, prints it and returns
   None, so `searchImage` dies with `AttributeError: 'NoneType' object has no attribute
   'json'` after exactly one request. Nothing waits and nothing retries. Its fixed
   60-second `waitToReset` only runs once its own local counter of 100 hits zero.
2. A cache entry older than 24 hours raises `KeyError`. The expiry branch deletes the
   entry and then reads it on the next line. The 24-hour rule is therefore not enforced
   but turned into a crash on the second day a keyword list is reused.
3. The API key is written to disk in plaintext, twice: as `pixabayKey.txt` and inside
   every cache key (the key is part of the query string it caches under), in
   `~/.cache/pixabaypy/`. Anyone who shares that folder, or a cache file in a bug
   report, shares the key. CLAUDE.md -> Pixabay rules forbids exactly this.
4. The query string is built by hand and is wrong two ways. `min_width=None` is sent
   literally (the library's own default for `searchImage`), and only spaces are escaped,
   so a keyword like `rock&roll=1` splits into a second, injected parameter. The API's
   `safesearch` flag is sent as `safe_search`, which is not the name its documentation
   uses (unverified against the live API until WINNOWER-17).

It also never reads an `X-RateLimit-*` header, which the spec asks for, and it `print`s
to stdout from inside library code.

Its downloader, which the spec had standing in for FR5, fails three more ways (see
`downloader.py`): it writes an error page into the image file on a non-200 when
streaming, leaves a truncated file behind on a dropped connection and then skips it
forever as "already exists", and `download()` re-fetches and overwrites a file it has
just announced it will not overwrite.

The second library the spec allowed, `pixabay` 0.0.5 (Lukas0025), was not usable at all:
`import pixabay` raises `SyntaxError: Non-UTF-8 code starting with '\\xe1'` on line 3 of
its `__init__.py`, a comment containing its author's name in Latin-1 with no encoding
declaration. That is a syntax error on every Python 3, including this project's floor.

Patching `pixabay-python` was rejected. More than half of the 684-line package is
response wrappers and enums (381 lines), and the defects are in the rest: the
constructor writes the key to disk, the cache is keyed by the URL that contains it, and
`makeQuery` discards the result of its own retry. Fixing those means rewriting
`PixabayClient` and keeping a fork, to get back the ~420 lines below, which have tests.
What would change the answer: a release that fixes all of the above.

Design
------
`search()` returns a `SearchResult` or raises a `PixabayError` subclass. The exceptions
are the states CLAUDE.md says a person must be able to see and act on (rate limited,
unreachable, rejected, unreadable); the runner turns each into a flagged keyword instead
of a crash. Nothing here retries forever: a request is retried a few times with
exponential backoff and then the error is raised, because a further retry is something a
person clicks, not something an unattended loop does (CLAUDE.md -> Pixabay rules).

The key is passed to `requests` as a parameter and appears nowhere else: not in the
cache key, not in the cache file, not in an error message. `requests` puts the full URL,
key included, in the text of its own exceptions, so those are never chained (`from
None`) or quoted; only the exception's class name is kept.

The throttle is a sliding window, not the fixed 60-second wait above. A batch of 100
keywords fits one window with nothing to wait for, and a 101st waits only as long as the
oldest request has left in the window. It also honours `X-RateLimit-Remaining` and
`X-RateLimit-Reset` (another program on the same key can have used the budget) and a
429's own reset time. `RATE_MARGIN` widens the window by a second because our timestamp
is taken before the request leaves and Pixabay's when it arrives; the size of that skew
has not been measured, so it is a guess to be checked in WINNOWER-17.

The cache is one file per query, named by a hash of the query *without* the key, so a
corrupt file costs one search and a write is a single atomic rename.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from . import __version__
from .config import MAX_CANDIDATES, MIN_CANDIDATES, SearchOptions

API_URL = "https://pixabay.com/api/"
USER_AGENT = f"winnower/{__version__} (+https://github.com/joaorodr84/winnower)"

RATE_LIMIT = 100  # requests per window, per key (Pixabay API docs)
RATE_WINDOW = 60.0  # seconds
RATE_MARGIN = 1.0  # see the module docstring
CACHE_TTL = 24 * 60 * 60  # seconds; Pixabay requires caching for 24 hours

MAX_TERM_LENGTH = 100  # Pixabay: "This value may not exceed 100 characters"
MAX_RESULTS = 500  # Pixabay: at most 500 results are reachable per query


class PixabayError(Exception):
    """A search did not produce results. `str()` is a sentence to show the person."""


class RateLimited(PixabayError):
    """Pixabay said 429 and the retries were used up."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class NetworkFailure(PixabayError):
    """Pixabay could not be reached, or answered 5xx, and the retries were used up."""


class Rejected(PixabayError):
    """Pixabay (or a check before asking it) refused this query. Retrying will not help."""

    def __init__(self, message: str, status: int | None = None, auth: bool = False):
        super().__init__(message)
        self.status = status
        # True when the key itself is the problem. Every further keyword would fail the
        # same way, so the runner stops instead of spending a request on each. Pixabay
        # does not document how a bad key is signalled; 401 and 403 are the usual ones
        # and a body that mentions the key is the fallback. To be confirmed in
        # WINNOWER-17 against the live API.
        self.auth = auth


class BadResponse(PixabayError):
    """Pixabay answered 200 with something that is not a search result."""


@dataclass(frozen=True)
class Candidate:
    """One search hit. Every URL here may only be *displayed* until the person picks it."""

    id: int
    page_url: str
    user: str
    tags: tuple[str, ...]
    kind: str
    preview_url: str
    webformat_url: str
    large_image_url: str
    width: int = 0
    height: int = 0
    user_id: int = 0
    # Only present for accounts Pixabay has approved for full API access.
    image_url: str | None = None
    vector_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tags"] = list(self.tags)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Candidate:
        """Inverse of `to_dict`; raises KeyError/TypeError/ValueError on a malformed one."""
        return cls(
            id=int(data["id"]),
            page_url=str(data["page_url"]),
            user=str(data["user"]),
            tags=tuple(str(t) for t in data["tags"]),
            kind=str(data["kind"]),
            preview_url=str(data["preview_url"]),
            webformat_url=str(data["webformat_url"]),
            large_image_url=str(data["large_image_url"]),
            width=int(data.get("width", 0)),
            height=int(data.get("height", 0)),
            user_id=int(data.get("user_id", 0)),
            image_url=data.get("image_url") or None,
            vector_url=data.get("vector_url") or None,
        )


@dataclass(frozen=True)
class SearchResult:
    term: str
    page: int
    per_page: int
    total: int
    total_hits: int
    candidates: tuple[Candidate, ...]
    cached: bool = False
    skipped: int = 0  # hits in the response that were missing a field we need

    @property
    def has_more(self) -> bool:
        return self.page * self.per_page < min(self.total_hits, MAX_RESULTS)


class Throttle:
    """A sliding window of `limit` requests per `window` seconds, plus server hints."""

    def __init__(
        self,
        limit: int = RATE_LIMIT,
        window: float = RATE_WINDOW,
        margin: float = RATE_MARGIN,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._limit = limit
        self._window = window + margin
        self._clock = clock
        self._sleep = sleep
        self._stamps: deque[float] = deque()
        self._blocked_until = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until a request may go out, then count it."""
        while True:
            with self._lock:
                now = self._clock()
                while self._stamps and self._stamps[0] <= now - self._window:
                    self._stamps.popleft()
                wait = self._blocked_until - now
                if wait <= 0 and len(self._stamps) >= self._limit:
                    wait = self._stamps[0] + self._window - now
                if wait <= 0:
                    self._stamps.append(now)
                    return
            self._sleep(wait)  # outside the lock: other threads may be reading

    def block_for(self, seconds: float) -> None:
        with self._lock:
            self._blocked_until = max(self._blocked_until, self._clock() + seconds)

    def observe(self, headers: Mapping[str, str]) -> None:
        """Read Pixabay's own count of what is left, which our count cannot know."""
        remaining = _number(_header(headers, "X-RateLimit-Remaining"))
        reset = _number(_header(headers, "X-RateLimit-Reset"))
        if remaining is not None and remaining <= 0 and reset is not None:
            self.block_for(reset)


class SearchCache:
    """Search responses on disk for 24 hours, one file per query, no key anywhere."""

    def __init__(
        self,
        directory: Path,
        ttl: float = CACHE_TTL,
        now: Callable[[], float] = time.time,
    ):
        self._dir = Path(directory)
        self._ttl = ttl
        self._now = now
        self._pruned = False

    def _path(self, key: str) -> Path:
        return self._dir / (hashlib.sha256(key.encode("utf-8")).hexdigest()[:32] + ".json")

    def _fresh(self, stored_at: Any) -> bool:
        if not isinstance(stored_at, int | float):
            return False
        # A negative age means the clock moved backwards; that is not evidence the
        # entry is young, so it is treated as stale and refetched.
        return 0 <= self._now() - stored_at < self._ttl

    def get(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            if entry["query"] == key and self._fresh(entry["stored_at"]):
                response = entry["response"]
                return response if isinstance(response, dict) else None
        except (OSError, ValueError, KeyError, TypeError):
            return None  # missing or corrupt: one search is refetched, nothing crashes
        path.unlink(missing_ok=True)
        return None

    def put(self, key: str, response: dict[str, Any]) -> None:
        entry = {"query": key, "stored_at": self._now(), "response": response}
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            if not self._pruned:
                self._pruned = True
                self.prune()
            fd, tmp = tempfile.mkstemp(dir=self._dir, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(entry, handle)
            os.replace(tmp, self._path(key))
        except OSError:
            # A full disk or a read-only work dir costs the cache, not the search.
            pass

    def prune(self) -> None:
        """Delete expired entries, so a keyword list that is never re-run leaves no litter."""
        for path in self._dir.glob("*.json"):
            try:
                if not self._fresh(json.loads(path.read_text(encoding="utf-8"))["stored_at"]):
                    path.unlink(missing_ok=True)
            except (OSError, ValueError, KeyError, TypeError):
                path.unlink(missing_ok=True)


def build_params(term: str, options: SearchOptions, per_page: int, page: int) -> dict[str, str]:
    """The query, without the key. This dict is also the cache key."""
    params = {
        "q": term,
        "lang": options.lang,
        "image_type": options.image_type,
        "orientation": options.orientation,
        "order": options.order,
        "safesearch": "true" if options.safesearch else "false",
        "editors_choice": "true" if options.editors_choice else "false",
        "page": str(page),
        "per_page": str(per_page),
    }
    if options.category:
        params["category"] = options.category
    if options.colors:
        params["colors"] = ",".join(options.colors)
    if options.min_width:
        params["min_width"] = str(options.min_width)
    if options.min_height:
        params["min_height"] = str(options.min_height)
    return params


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def _number(text: str | None) -> float | None:
    try:
        return float(text) if text is not None else None
    except ValueError:
        return None


def _candidate(hit: Any) -> Candidate | None:
    try:
        return Candidate(
            id=int(hit["id"]),
            page_url=str(hit["pageURL"]),
            user=str(hit.get("user", "")),
            tags=tuple(t.strip() for t in str(hit.get("tags", "")).split(",") if t.strip()),
            kind=str(hit.get("type", "")),
            preview_url=str(hit["previewURL"]),
            webformat_url=str(hit["webformatURL"]),
            large_image_url=str(hit["largeImageURL"]),
            width=int(hit.get("imageWidth") or 0),
            height=int(hit.get("imageHeight") or 0),
            user_id=int(hit.get("user_id") or 0),
            image_url=hit.get("imageURL") or None,
            vector_url=hit.get("vectorURL") or None,
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def parse_response(
    payload: Any, term: str, page: int, per_page: int, cached: bool = False
) -> SearchResult:
    if not isinstance(payload, dict) or not isinstance(payload.get("hits"), list):
        raise BadResponse("Pixabay's answer was not a list of search results.")
    hits = payload["hits"]
    candidates = tuple(c for c in map(_candidate, hits) if c)
    try:
        total = int(payload.get("total", len(hits)))
        total_hits = int(payload.get("totalHits", len(hits)))
    except (TypeError, ValueError):
        raise BadResponse("Pixabay's answer had a result count that is not a number.") from None
    return SearchResult(
        term, page, per_page, total, total_hits, candidates, cached, len(hits) - len(candidates)
    )


class PixabayClient:
    def __init__(
        self,
        api_key: str,
        *,
        session: Any = None,
        throttle: Throttle | None = None,
        cache: SearchCache | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 4,
        backoff_base: float = 1.0,
        backoff_cap: float = RATE_WINDOW,
        timeout: tuple[float, float] = (5, 20),
    ):
        # repr=False on Settings keeps the key out of tracebacks; a bare attribute with
        # a leading underscore does the same job here for the client.
        self._key = api_key
        self._session = session if session is not None else requests.Session()
        self._throttle = throttle if throttle is not None else Throttle(sleep=sleep)
        self._cache = cache
        self._sleep = sleep
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._timeout = timeout

    def search(
        self,
        term: str,
        options: SearchOptions | None = None,
        *,
        count: int = 5,
        page: int = 1,
    ) -> SearchResult:
        """One page of `count` candidates for `term`. Raises a `PixabayError`."""
        if not MIN_CANDIDATES <= count <= MAX_CANDIDATES:
            raise ValueError(f"count must be between {MIN_CANDIDATES} and {MAX_CANDIDATES}")
        if page < 1:
            raise ValueError("page starts at 1")
        term = " ".join(term.split())
        if not term:
            # Pixabay answers a missing `q` with *every* image, which would be a full
            # page of arbitrary results presented as if they matched the keyword.
            raise Rejected("The search term is empty.")
        if len(term) > MAX_TERM_LENGTH:
            raise Rejected(
                f"The search term is {len(term)} characters; Pixabay accepts at most "
                f"{MAX_TERM_LENGTH}. Shorten it, or give the keyword a shorter search term."
            )
        if (page - 1) * count >= MAX_RESULTS:
            return SearchResult(term, page, count, 0, 0, ())  # past the 500-result cap

        params = build_params(term, options or SearchOptions(), count, page)
        cache_key = urlencode(sorted(params.items()))
        if self._cache is not None:
            hit = self._cache.get(cache_key)
            if hit is not None:
                try:
                    return parse_response(hit, term, page, count, cached=True)
                except BadResponse:
                    pass  # an unreadable entry is a miss

        payload = self._fetch(params)
        result = parse_response(payload, term, page, count)
        if self._cache is not None:
            self._cache.put(cache_key, payload)
        return result

    def _delay(self, attempt: int) -> float:
        return min(self._backoff_base * 2**attempt, self._backoff_cap)

    def _fetch(self, params: dict[str, str]) -> Any:
        failure: PixabayError | None = None
        for attempt in range(self._max_attempts):
            self._throttle.acquire()
            try:
                response = self._session.get(
                    API_URL,
                    params={**params, "key": self._key},
                    headers={"User-Agent": USER_AGENT},
                    timeout=self._timeout,
                )
            except requests.RequestException as err:
                # Only the class name: the exception's text carries the full URL, key
                # included. `from None` for the same reason (see the module docstring).
                failure = NetworkFailure(
                    f"Could not reach Pixabay ({type(err).__name__}). "
                    "Check the connection, then retry."
                )
                retry_wait = self._delay(attempt)
            else:
                self._throttle.observe(response.headers)
                status = response.status_code
                if status == 200:
                    return self._json(response)
                if status == 429:
                    reset = _number(_header(response.headers, "X-RateLimit-Reset"))
                    retry_wait = min(max(self._delay(attempt), reset or 0), self._backoff_cap)
                    failure = RateLimited(
                        f"Pixabay says this key has used its {RATE_LIMIT} requests for the minute. "
                        "Wait a moment, then retry.",
                        retry_after=retry_wait,
                    )
                    # The wait is served by the throttle so that it also holds up
                    # whatever else is using this client, not just this one call.
                    self._throttle.block_for(retry_wait)
                    retry_wait = 0
                elif status >= 500:
                    failure = NetworkFailure(
                        f"Pixabay is having trouble (HTTP {status}). Retry in a moment."
                    )
                    retry_wait = self._delay(attempt)
                else:
                    raise self._rejected(response)
            if attempt + 1 < self._max_attempts and retry_wait:
                self._sleep(retry_wait)
        assert failure is not None  # max_attempts >= 1
        raise failure from None

    def _json(self, response: Any) -> Any:
        try:
            return response.json()
        except ValueError:
            raise BadResponse("Pixabay's answer was not valid JSON.") from None

    def _rejected(self, response: Any) -> Rejected:
        body = " ".join(str(getattr(response, "text", "")).split())[:200]
        body = body.replace(self._key, "***")
        auth = response.status_code in (401, 403) or "key" in body.lower()
        detail = f": {body}" if body else ""
        return Rejected(
            f"Pixabay refused the search (HTTP {response.status_code}){detail}",
            status=response.status_code,
            auth=auth,
        )
