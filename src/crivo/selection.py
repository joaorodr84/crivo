"""What the person has chosen so far: the state behind the selection page (FR4).

The page is a thin view of this object. Every rule about picking, skipping and retrying is
here so that it can be tested without a browser or a socket, and so that the resumable
session (WINNOWER-13) has one thing to save and restore.

Rules a person would otherwise trip over:

- A keyword is *done* when something is picked or it is skipped. Picking un-skips it and
  skipping clears its picks, so the two never disagree.
- Without `multiple`, picking a second image replaces the first: "exactly one" is the
  default in the spec, and a click on another image means "this one instead".
- "More results" *appends* the next page to what is shown and keeps the picks, because
  someone who has already ticked an image on page one is looking for a better one, not
  starting over. A new search term replaces the candidates and clears the picks, since
  the picks no longer point at anything on screen.
- A retry that fails does not throw away what was already found. The old candidates stay,
  with the reason in `notice`; only a keyword with nothing yet shows the failure as its
  state. Losing ten candidates and a pick because a page-two request hit a 429 would be
  the tool punishing the person for using it.
- Every change is reported to `on_change` with the whole state, which is how the session is
  saved (`session_store.py`) after each click. Saving *everything* each time, rather than
  appending what changed, is what keeps a crash between two clicks from leaving a file that
  is half of one state and half of another; a hundred keywords is a few hundred kilobytes.
- A session can begin with no keywords at all (`starter`): the page shows a box to paste
  them into, `start()` hands the text to the starter, and the starter fills the session
  with every keyword as *not searched* and searches them in the background. The page reads
  the progress from `snapshot()` as they come in. Putting every keyword in at once, rather
  than adding each as it is searched, is what makes an interrupted search resumable for
  free: the saved session already lists them all, and the ones not yet reached are just
  keywords with a Search button, which already exists.
- The search runs *outside* the lock. It can wait a minute on the rate limit, and the
  page must still be able to read the state and pick in other keywords meanwhile.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .keywords import Keyword
from .pixabay_client import Candidate
from .search_runner import Outcome, Status

# (keyword, exact query, page) -> what the search runner made of it
SearchFn = Callable[[Keyword, str, int], Outcome]


class SelectionError(Exception):
    """A request that does not make sense (an unknown keyword or image). Not a run failure."""


@dataclass(frozen=True)
class Selection:
    """A keyword and the images picked for it, in the order they were picked."""

    keyword: Keyword
    query: str
    candidates: tuple[Candidate, ...]


@dataclass
class _Entry:
    keyword: Keyword
    query: str
    status: Status
    candidates: list[Candidate] = field(default_factory=list)
    page: int = 1
    has_more: bool = False
    error: str | None = None
    notice: str | None = None
    picked: list[int] = field(default_factory=list)
    skipped: bool = False
    busy: bool = False

    @property
    def done(self) -> bool:
        return bool(self.picked) or self.skipped


def _candidate_json(c: Candidate) -> dict[str, Any]:
    return {
        "id": c.id,
        "page_url": c.page_url,
        "user": c.user,
        "tags": list(c.tags),
        "preview_url": c.preview_url,
        "webformat_url": c.webformat_url,
        "width": c.width,
        "height": c.height,
    }


class SelectionSession:
    def __init__(
        self,
        outcomes: list[Outcome],
        search: SearchFn | None = None,
        *,
        multiple: bool = False,
        stopped: str | None = None,
        on_change: Callable[[dict[str, Any]], None] | None = None,
        starter: Callable[[str], None] | None = None,
    ):
        self._search = search
        self.on_change = on_change
        self._multiple = multiple
        self._stopped = stopped  # why the search run ended early, shown as a banner
        self._lock = threading.RLock()
        self._finished = threading.Event()
        self._entries: dict[str, _Entry] = {}
        for outcome in outcomes:
            self._entries[outcome.keyword.label] = self._entry_from(outcome)
        self._starter = starter
        self._awaiting = starter is not None and not outcomes  # waiting for the box
        self._starting = False
        self._progress: tuple[int, int] | None = None  # (searched, total) while searching
        self._searched = threading.Event()
        if not self._awaiting:
            self._searched.set()
        self._notes: list[str] = []

    @staticmethod
    def _entry_from(outcome: Outcome) -> _Entry:
        result = outcome.result
        return _Entry(
            keyword=outcome.keyword,
            query=outcome.query,
            status=outcome.status,
            candidates=list(outcome.candidates),
            page=result.page if result else 1,
            has_more=result.has_more if result else False,
            error=outcome.error,
        )

    @property
    def phase(self) -> str:
        """`start` (waiting for keywords), `searching`, or `picking`."""
        with self._lock:
            if self._awaiting:
                return "start"
            return "searching" if self._progress is not None else "picking"

    def start(self, text: str) -> None:
        """The person pasted their keywords. Raises SelectionError if they cannot be used."""
        with self._lock:
            if self._starter is None or not self._awaiting or self._starting:
                raise SelectionError("The keywords have already been given.")
            self._starting = True
            starter = self._starter
        try:
            starter(text)
        except ValueError as err:  # KeywordError is one: the message is written for the person
            raise SelectionError(str(err)) from None
        finally:
            with self._lock:
                self._starting = False

    def populate(self, outcomes: list[Outcome], notes: list[str] | None = None) -> None:
        """Fill an empty session with keywords (normally all `not searched`) and begin searching."""
        with self._lock:
            for outcome in outcomes:
                self._entries[outcome.keyword.label] = self._entry_from(outcome)
            self._notes = list(notes or [])
            self._awaiting = False
            self._progress = (0, len(outcomes))
            self._changed()

    def update(self, outcome: Outcome) -> None:
        """A background search produced `outcome` for one keyword."""
        with self._lock:
            self._entries[outcome.keyword.label] = self._entry_from(outcome)
            self._changed()

    def set_progress(self, done: int, total: int) -> None:
        with self._lock:
            self._progress = (done, total)

    def finish_search(self, stopped: str | None = None) -> None:
        """The background search is over, whether it got through the list or was stopped."""
        with self._lock:
            self._progress = None
            if stopped:
                self._stopped = stopped
            self._changed()
        self._searched.set()

    def wait_searched(self, timeout: float | None = None) -> bool:
        return self._searched.wait(timeout)

    def _entry(self, label: str) -> _Entry:
        try:
            return self._entries[label]
        except KeyError:
            raise SelectionError(f"There is no keyword {label!r}.") from None

    def pick(self, label: str, image_id: int, selected: bool = True) -> None:
        with self._lock:
            entry = self._entry(label)
            if image_id not in {c.id for c in entry.candidates}:
                raise SelectionError(
                    f"Image {image_id} is not one of the candidates for {label!r}."
                )
            if selected:
                if not self._multiple:
                    entry.picked.clear()
                if image_id not in entry.picked:
                    entry.picked.append(image_id)
                entry.skipped = False
            elif image_id in entry.picked:
                entry.picked.remove(image_id)
            self._changed()

    def skip(self, label: str, skipped: bool = True) -> None:
        with self._lock:
            entry = self._entry(label)
            entry.skipped = skipped
            if skipped:
                entry.picked.clear()
            self._changed()

    def retry(self, label: str, term: str | None = None, next_page: bool = False) -> None:
        """Search again: the same query (`term` None), a new `term`, or the `next_page`."""
        if self._search is None:
            raise SelectionError("Searching again is not available in this session.")
        with self._lock:
            entry = self._entry(label)
            if entry.busy or (self._progress is not None and entry.status is Status.PENDING):
                raise SelectionError(f"{label!r} is already being searched.")
            query = term.strip() if term is not None else entry.query
            if not query:
                raise SelectionError("Type a search term first.")
            page = entry.page + 1 if next_page else 1
            entry.busy = True
            keyword = entry.keyword
        try:
            outcome = self._search(keyword, query, page)
        except BaseException:
            with self._lock:
                entry.busy = False
            raise
        with self._lock:
            entry.busy = False
            self._apply(entry, outcome, query, next_page)
            self._changed()

    def _apply(self, entry: _Entry, outcome: Outcome, query: str, next_page: bool) -> None:
        entry.notice = None
        if outcome.status is Status.FAILED:
            if entry.candidates:
                entry.notice = outcome.error
            else:
                entry.status, entry.error, entry.query = Status.FAILED, outcome.error, query
            return
        if next_page:
            known = {c.id for c in entry.candidates}
            entry.candidates += [c for c in outcome.candidates if c.id not in known]
            if outcome.candidates:
                entry.page = outcome.result.page
                entry.has_more = outcome.result.has_more
            else:
                entry.has_more = False
                entry.notice = "There are no more results for this search."
            return
        fresh = self._entry_from(outcome)
        entry.query, entry.status, entry.error = query, fresh.status, None
        entry.candidates, entry.page, entry.has_more = fresh.candidates, fresh.page, fresh.has_more
        entry.picked.clear()
        entry.skipped = False

    def _changed(self) -> None:
        if self.on_change:
            self.on_change(self.export())  # called with the lock held: one state at a time

    def export(self) -> dict[str, Any]:
        """Everything needed to rebuild this session with `from_state`. JSON-safe."""
        with self._lock:
            return {
                "multiple": self._multiple,
                "stopped": self._stopped,
                "notes": list(self._notes),
                "searching": self._progress is not None,
                "finished": self._finished.is_set(),
                "entries": [
                    {
                        "keyword": {"label": e.keyword.label, "term": e.keyword.term},
                        "query": e.query,
                        "status": e.status.value,
                        "candidates": [c.to_dict() for c in e.candidates],
                        "page": e.page,
                        "has_more": e.has_more,
                        "error": e.error,
                        "notice": e.notice,
                        "picked": list(e.picked),
                        "skipped": e.skipped,
                    }
                    for e in self._entries.values()
                ],
            }

    @classmethod
    def from_state(
        cls,
        state: dict[str, Any],
        search: SearchFn | None = None,
        *,
        multiple: bool = False,
    ) -> SelectionSession:
        """Rebuild a session from `export()`. Raises SelectionError if it is not one."""
        try:
            stopped = state["stopped"]
            if state.get("searching") and not stopped:
                stopped = (
                    "The search was interrupted. Keywords marked not searched can be "
                    "searched from this page."
                )
            session = cls([], search, multiple=multiple or bool(state["multiple"]), stopped=stopped)
            session._notes = [str(n) for n in state.get("notes", [])]
            for item in state["entries"]:
                candidates = [Candidate.from_dict(c) for c in item["candidates"]]
                picked = [int(i) for i in item["picked"]]
                if not {c.id for c in candidates} >= set(picked):
                    raise ValueError("a pick that is not one of the candidates")
                label = str(item["keyword"]["label"])
                session._entries[label] = _Entry(
                    keyword=Keyword(label, str(item["keyword"]["term"])),
                    query=str(item["query"]),
                    status=Status(item["status"]),
                    candidates=candidates,
                    page=int(item["page"]),
                    has_more=bool(item["has_more"]),
                    error=item["error"],
                    notice=item["notice"],
                    picked=picked,
                    skipped=bool(item["skipped"]),
                )
            if state["finished"]:
                session._finished.set()
        except (KeyError, TypeError, ValueError, AttributeError) as err:
            raise SelectionError(f"The saved session is not readable ({err!r}).") from None
        return session

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            entries = list(self._entries.values())
            return {
                "total": len(entries),
                "done": sum(e.done for e in entries),
                "picks": sum(len(e.picked) for e in entries),
                "multiple": self._multiple,
                "finished": self._finished.is_set(),
                "stopped": self._stopped,
                "notes": list(self._notes),
                "phase": (
                    "start"
                    if self._awaiting
                    else "searching"
                    if self._progress is not None
                    else "picking"
                ),
                "searching": (
                    {"done": self._progress[0], "total": self._progress[1]}
                    if self._progress is not None
                    else None
                ),
                "keywords": [
                    {
                        "label": e.keyword.label,
                        "query": e.query,
                        "status": e.status.value,
                        "error": e.error,
                        "notice": e.notice,
                        "page": e.page,
                        "has_more": e.has_more,
                        "busy": e.busy,
                        "picked": list(e.picked),
                        "skipped": e.skipped,
                        "done": e.done,
                        "candidates": [_candidate_json(c) for c in e.candidates],
                    }
                    for e in entries
                ],
            }

    def finish(self) -> None:
        with self._lock:
            if not any(e.picked for e in self._entries.values()):
                raise SelectionError("Pick at least one image before finishing.")
            if self._progress is not None or any(e.busy for e in self._entries.values()):
                raise SelectionError("A search is still running; wait for it to finish.")
            self._finished.set()
            self._changed()

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the person has finished; True if they did."""
        return self._finished.wait(timeout)

    @property
    def finished(self) -> bool:
        return self._finished.is_set()

    def selections(self) -> list[Selection]:
        """The picks, keyword by keyword in input order, images in the order picked."""
        with self._lock:
            chosen = []
            for entry in self._entries.values():
                by_id = {c.id: c for c in entry.candidates}
                images = tuple(by_id[i] for i in entry.picked)
                if images:
                    chosen.append(Selection(entry.keyword, entry.query, images))
            return chosen
