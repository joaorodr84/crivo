"""Search every keyword (FR3): progress, and a state for each one that a person can act on.

Every keyword ends in one of four states, and none of them is an exception:

- FOUND    candidates to choose from.
- EMPTY    Pixabay had nothing for the search term. The person retries with another term.
- FAILED   this one search could not be completed (a bad term, a garbled answer, a
           network error that outlived its retries). The person retries it.
- PENDING  never searched, because the run stopped first (see below).

The run stops early, rather than failing every remaining keyword one at a time, when the
next search cannot succeed for a reason that is not about the keyword:

- the key is refused (`Rejected.auth`): every keyword would be refused the same way;
- a 429 that survived the client's backoff: the budget is spent, and CLAUDE.md -> Pixabay
  rules says a further retry is something a person clicks, not something an unattended
  loop does;
- `NETWORK_STRIKES` network failures in a row: with the network down, each keyword costs
  the client's whole backoff (1 + 2 + 4 s) to learn what the last one already said, so a
  hundred keywords is about twelve minutes of nothing. Three in a row is a judgement, not
  a measurement; one success resets the count so a flaky link that still gets some
  through is not stopped.

Stopping early is not a crash: everything found so far is kept and the report says why it
stopped. The person fixes the cause and runs the remaining keywords again.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum

from .config import Settings
from .keywords import Keyword
from .pixabay_client import (
    NetworkFailure,
    PixabayClient,
    PixabayError,
    RateLimited,
    Rejected,
    SearchResult,
)

NETWORK_STRIKES = 3


class Status(Enum):
    FOUND = "found"
    EMPTY = "empty"
    FAILED = "failed"
    PENDING = "pending"


@dataclass(frozen=True)
class Outcome:
    keyword: Keyword
    status: Status
    # The exact string sent to Pixabay, after the term template. Shown next to a zero
    # result so the person can see what was actually searched for.
    query: str = ""
    result: SearchResult | None = None
    error: str | None = None

    @property
    def candidates(self):
        return self.result.candidates if self.result else ()


@dataclass(frozen=True)
class Report:
    outcomes: tuple[Outcome, ...]
    # Why the run stopped early, or None if every keyword was attempted.
    stopped: str | None = None

    def with_status(self, status: Status) -> list[Outcome]:
        return [o for o in self.outcomes if o.status is status]

    @property
    def counts(self) -> dict[Status, int]:
        return {s: len(self.with_status(s)) for s in Status}


# (keywords searched so far, total keywords, the outcome that was just produced)
ProgressCallback = Callable[[int, int, Outcome], None]


@dataclass
class SearchRunner:
    client: PixabayClient
    settings: Settings

    def search_one(self, keyword: Keyword, *, term: str | None = None, page: int = 1) -> Outcome:
        """Search a single keyword. Never raises for anything Pixabay can do.

        `term` replaces the keyword's search term and is sent as typed, without the term
        template: a person who types an exact query into a retry box means it. `page` asks
        for the next page of the same query.
        """
        return self._search(keyword, term, page)[0]

    def _search(
        self, keyword: Keyword, term: str | None, page: int
    ) -> tuple[Outcome, PixabayError | None]:
        query = term if term is not None else self.settings.search_term(keyword.term)
        try:
            result = self.client.search(
                query, self.settings.search, count=self.settings.candidates, page=page
            )
        except PixabayError as err:
            return Outcome(keyword, Status.FAILED, query, error=str(err)), err
        status = Status.FOUND if result.candidates else Status.EMPTY
        return Outcome(keyword, status, query, result), None

    def run(self, keywords: Iterable[Keyword], progress: ProgressCallback | None = None) -> Report:
        """Search each keyword in order, reporting `progress(done, total, outcome)`."""
        keywords = list(keywords)
        outcomes: list[Outcome] = []
        stopped: str | None = None
        network_strikes = 0
        for keyword in keywords:
            outcome, err = self._search(keyword, None, 1)
            outcomes.append(outcome)
            if progress:
                progress(len(outcomes), len(keywords), outcome)
            network_strikes = network_strikes + 1 if isinstance(err, NetworkFailure) else 0
            stopped = _stop_reason(err, network_strikes)
            if stopped:
                break
        for keyword in keywords[len(outcomes) :]:
            query = self.settings.search_term(keyword.term)
            outcomes.append(Outcome(keyword, Status.PENDING, query))
        return Report(tuple(outcomes), stopped)


def _stop_reason(err: PixabayError | None, network_strikes: int) -> str | None:
    if isinstance(err, Rejected) and err.auth:
        return f"{err} Check PIXABAY_API_KEY; the remaining keywords were not searched."
    if isinstance(err, RateLimited):
        return (
            "Pixabay's rate limit is used up, so the remaining keywords were not searched. "
            "Wait a minute, then run them again."
        )
    if network_strikes >= NETWORK_STRIKES:
        return (
            f"Pixabay was unreachable {NETWORK_STRIKES} times in a row, so the remaining "
            "keywords were not searched. Check the connection, then run them again."
        )
    return None
