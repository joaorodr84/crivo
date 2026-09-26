"""Download the picked images to a local cache of originals (FR5).

This is the point at which Pixabay's URLs stop being used: they may only *temporarily
display* search results (CLAUDE.md -> Pixabay rules), so from here on the resizer and the
packager read the files this module returns and never a URL.

Why not `pixabay-python`'s `downloadList()`, which the spec named: its downloader fails
three ways, each reproduced on 2026-09-27 against 1.1.0 (the search-side failures are in
the header of `pixabay_client.py`):

1. With `streamToFile=True` it writes whatever the server sent, so a 403 leaves the HTML
   error page on disk under a `.jpg` name. Without streaming a non-200 silently writes
   nothing and returns None, so the caller cannot tell a failure from a success.
2. It writes straight to the final name. A connection dropped at 512 of 1000 bytes left a
   512-byte `.jpg` behind, and the next run skipped it as "already exists" without
   making a request. A truncated original is then in the cache for good, and the person
   finds out when the resize step chokes on it, or worse, does not.
3. `download()` prints "already exists and is not overwritten" and then downloads and
   overwrites it anyway.

The same three properties are what this module is written to have, as tests:

- Nothing is kept unless it is a complete image. The body streams to a temporary file in
  the cache directory, is decoded in full by Pillow, and only then renamed into place
  (`os.replace` is atomic on POSIX and a single `MoveFileEx` call on Windows, and neither
  can cross filesystems, which is why the temporary file is created in the cache
  directory and not the system temp dir). A truncated JPEG fails the decode; an HTML
  error page fails it too. The check is on content rather than on `Content-Type`
  because a CDN error page can be served as either.
- The extension comes from the decoded format, not the URL, so what is on disk is named
  for what it is.
- A file that is already in the cache is returned without a request, and it can only be
  a file that passed the check above.

Cached files are named `<pixabay id>-<large|full>.<ext>`: the same picture at the two
sizes are different files, so switching `--full-size` on cannot be answered with the
smaller copy.

Retries are bounded (three attempts, 1 s then 2 s) and only for what can pass: network
errors, 429 and 5xx. A 4xx is the server's answer about this URL and is reported as it is.
Downloads are sequential and are not counted against the API's 100-per-minute budget,
which is for the API and not the CDN, but they are one at a time and only for images a
person has picked, which is the human pace the API terms ask for.

Whether Pixabay's CDN serves these requests to Winnower's User-Agent has not been
checked against the live service. If it answers 403, that is what WINNOWER-17 is for.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from PIL import Image

from .pixabay_client import USER_AGENT, Candidate

MAX_BYTES = 100 * 1024 * 1024  # a 1280 px original is well under 1 MB; this is a runaway guard
CHUNK = 64 * 1024
EXTENSIONS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif"}


class DownloadError(Exception):
    """One image could not be fetched. `str()` says why, for the person."""


@dataclass(frozen=True)
class DownloadResult:
    candidate: Candidate
    path: Path | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.path is not None


class Downloader:
    def __init__(
        self,
        directory: Path,
        *,
        full_size: bool = False,
        session: Any = None,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 3,
        timeout: tuple[float, float] = (5, 30),
    ):
        self._dir = Path(directory)
        self._full_size = full_size
        self._session = session if session is not None else requests.Session()
        self._sleep = sleep
        self._max_attempts = max_attempts
        self._timeout = timeout

    def _variant(self, candidate: Candidate) -> tuple[str, str]:
        # `imageURL` is only in responses for accounts Pixabay approved for full API
        # access; for everyone else asking for full size quietly means the large one.
        if self._full_size and candidate.image_url:
            return "full", candidate.image_url
        return "large", candidate.large_image_url

    def cached_path(self, candidate: Candidate) -> Path | None:
        variant, _ = self._variant(candidate)
        for path in sorted(self._dir.glob(f"{candidate.id}-{variant}.*")):
            if path.suffix != ".part" and path.stat().st_size > 0:
                return path
        return None

    def fetch(self, candidate: Candidate) -> Path:
        """The local original for `candidate`, downloading it unless it is cached."""
        cached = self.cached_path(candidate)
        if cached:
            return cached
        variant, url = self._variant(candidate)
        if not url.startswith("https://"):
            raise DownloadError(f"Refusing to download {url!r}: not an https URL.")
        self._dir.mkdir(parents=True, exist_ok=True)

        last: DownloadError | None = None
        for attempt in range(self._max_attempts):
            try:
                return self._download_once(candidate, variant, url)
            except _Retryable as err:
                last = DownloadError(str(err))
            if attempt + 1 < self._max_attempts:
                self._sleep(2**attempt)
        assert last is not None
        raise last

    def fetch_all(
        self,
        candidates: Iterable[Candidate],
        progress: Callable[[int, int, DownloadResult], None] | None = None,
    ) -> list[DownloadResult]:
        """Fetch each in order. A failure is a result, and the rest still download."""
        candidates = list(candidates)
        results = []
        for candidate in candidates:
            try:
                result = DownloadResult(candidate, self.fetch(candidate))
            except DownloadError as err:
                result = DownloadResult(candidate, error=str(err))
            results.append(result)
            if progress:
                progress(len(results), len(candidates), result)
        return results

    def _download_once(self, candidate: Candidate, variant: str, url: str) -> Path:
        try:
            response = self._session.get(
                url, stream=True, headers={"User-Agent": USER_AGENT}, timeout=self._timeout
            )
        except requests.RequestException as err:
            raise _Retryable(
                f"Could not reach Pixabay to download image {candidate.id} "
                f"({type(err).__name__}). Check the connection, then retry."
            ) from None
        with contextlib.closing(response):
            status = response.status_code
            if status == 429 or status >= 500:
                raise _Retryable(f"Pixabay's image server said HTTP {status} for {candidate.id}.")
            if status != 200:
                raise DownloadError(
                    f"Pixabay refused the download of image {candidate.id} (HTTP {status})."
                )
            tmp = self._stream_to_temp(response, candidate.id)
        try:
            extension = _verify_image(tmp, candidate.id)
            final = self._dir / f"{candidate.id}-{variant}.{extension}"
            os.replace(tmp, final)
            return final
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink()  # only still there if verification or the rename failed

    def _stream_to_temp(self, response: Any, image_id: int) -> Path:
        expected = _content_length(response)
        if expected is not None and expected > MAX_BYTES:
            raise DownloadError(f"Image {image_id} is {expected // 2**20} MB; the limit is 100 MB.")
        fd, name = tempfile.mkstemp(dir=self._dir, prefix=f"{image_id}-", suffix=".part")
        tmp = Path(name)
        received = 0
        try:
            with os.fdopen(fd, "wb") as handle:
                for chunk in response.iter_content(chunk_size=CHUNK):
                    received += len(chunk)
                    if received > MAX_BYTES:
                        raise DownloadError(f"Image {image_id} is over 100 MB; gave up on it.")
                    handle.write(chunk)
        except requests.RequestException as err:
            tmp.unlink(missing_ok=True)
            raise _Retryable(
                f"The connection dropped while downloading image {image_id} ({type(err).__name__})."
            ) from None
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        if expected is not None and received != expected:
            tmp.unlink(missing_ok=True)
            raise _Retryable(
                f"Image {image_id} arrived incomplete ({received} of {expected} bytes)."
            )
        return tmp


class _Retryable(Exception):
    """A failure worth another attempt; becomes a DownloadError if the attempts run out."""


def _content_length(response: Any) -> int | None:
    for key, value in response.headers.items():
        if key.lower() == "content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


def _verify_image(path: Path, image_id: int) -> str:
    """Decode the whole file; return the extension for what it really is."""
    try:
        with Image.open(path) as image:
            image.load()
            fmt = image.format or ""
    except Exception as err:  # Pillow raises OSError, ValueError, DecompressionBombError...
        raise DownloadError(
            f"What Pixabay sent for image {image_id} is not a complete image "
            f"({type(err).__name__}); it was not kept."
        ) from None
    return EXTENSIONS.get(fmt, fmt.lower() or "img")
