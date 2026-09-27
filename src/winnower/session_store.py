"""The saved session (FR8): what makes a long run something you can put down and pick up.

What is saved is the whole selection state (`SelectionSession.export()`): the keywords, every
candidate that was shown, what was picked and skipped, and what search state each keyword was
in. It is rewritten after every click. Resuming then needs no request at all: it does not
search again, and the page comes back as it was left.

It never holds the API key. The key is not part of the selection state to begin with, and
`tests/test_session_store.py` checks the file rather than trusting that.

A session is *unfinished* until the zip has been written. Starting a new run over an
unfinished session is refused, because the alternative is to silently throw away an afternoon
of picking, and `--restart` is the person saying they mean it. A session whose zip was
written is *completed*: it has nothing left to lose, so the next run replaces it without ceremony.

A save that fails (a full disk, a read-only folder) costs the saved session and nothing else:
the picks stay in memory and the run carries on. The failure is kept in `error` for the CLI to
report once at the end, and not raised into the page's request handler, where it would turn a
harmless click into an error the person can do nothing about.

The file is written to a temporary file next to it and renamed into place, so a power cut
leaves either the previous state or the new one, never a torn file.

Resuming a session more than a day old still works but the caller is told its age, because
Pixabay's image addresses are meant for short-term display: a thumbnail or a download in an old
session may no longer load. That is reported rather than refused, since refusing would throw
away the picks, which are the one thing that cannot be recreated by searching again.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VERSION = 1
SESSION_NAME = "session.json"


class SessionError(Exception):
    """The session file cannot be used. `str()` says what to do about it."""


@dataclass(frozen=True)
class SavedSession:
    state: dict[str, Any]
    saved_at: float
    completed: bool

    @property
    def total(self) -> int:
        return len(self.state["entries"])

    @property
    def done(self) -> int:
        return sum(bool(e["picked"]) or bool(e["skipped"]) for e in self.state["entries"])

    def describe(self, now: float) -> str:
        age = max(0, now - self.saved_at)
        if age < 90:
            when = "moments ago"
        elif age < 5400:
            when = f"{round(age / 60)} minutes ago"
        elif age < 2 * 86400:
            when = f"{round(age / 3600)} hours ago"
        else:
            when = f"{round(age / 86400)} days ago"
        return f"{self.done} of {self.total} keywords done, saved {when}"


class SessionStore:
    def __init__(self, path: Path, now: Callable[[], float] = time.time):
        self.path = Path(path)
        self._now = now
        self.error: str | None = None  # the last failed save, if any
        self.writes = 0  # successful saves by this object: is there anything of *ours* on disk?

    def load(self) -> SavedSession | None:
        """The saved session, or None if there is none. Raises SessionError if unreadable."""
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError) as err:
            raise SessionError(self._unreadable(err)) from None
        try:
            data = json.loads(raw)
            if data["version"] != VERSION:
                raise SessionError(
                    f"{self.path} was written by a different version of Winnower "
                    f"(format {data['version']}, this one reads {VERSION}). Finish it with "
                    "that version, or start over with --restart."
                )
            saved = SavedSession(data["state"], float(data["saved_at"]), bool(data["completed"]))
            saved.state["entries"]  # noqa: B018 - a malformed file should fail here, not later
            return saved
        except SessionError:
            raise
        except (ValueError, KeyError, TypeError) as err:
            raise SessionError(self._unreadable(err)) from None

    def _unreadable(self, err: object) -> str:
        return (
            f"{self.path} is not a readable session file ({type(err).__name__}). "
            "Delete it, or start over with --restart."
        )

    def save(self, state: dict[str, Any], completed: bool = False) -> None:
        payload = {
            "version": VERSION,
            "saved_at": self._now(),
            "completed": completed,
            "state": state,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".session.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle)
                os.replace(tmp, self.path)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise
            self.error = None
            self.writes += 1
        except OSError as err:
            self.error = f"{err.strerror or err}"

    def complete(self) -> None:
        """Mark the saved session as finished with: its zip has been written."""
        saved = self.load()
        if saved:
            self.save(saved.state, completed=True)

    def discard(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()
