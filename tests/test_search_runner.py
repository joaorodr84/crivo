import requests
from fakes import FakeClock, FakeResponse, FakeSession, payload

from crivo.config import Settings
from crivo.keywords import Keyword
from crivo.pixabay_client import PixabayClient, Throttle
from crivo.search_runner import NETWORK_STRIKES, SearchRunner, Status

EMPTY = {"total": 0, "totalHits": 0, "hits": []}


def make_runner(script, *, settings=None, **client_kwargs):
    clock = FakeClock()
    session = FakeSession(script)
    client = PixabayClient(
        "k",
        session=session,
        throttle=Throttle(clock=clock, sleep=clock.sleep),
        sleep=clock.sleep,
        **client_kwargs,
    )
    return SearchRunner(client, settings or Settings(api_key="k")), session


def words(*labels):
    return [Keyword(label, label) for label in labels]


class TestRun:
    def test_every_keyword_is_searched_in_order(self):
        runner, session = make_runner([FakeResponse(200, payload())])
        report = runner.run(words("apple", "pear", "plum"))
        assert [o.keyword.label for o in report.outcomes] == ["apple", "pear", "plum"]
        assert [c["params"]["q"] for c in session.calls] == ["apple", "pear", "plum"]
        assert report.counts[Status.FOUND] == 3 and report.stopped is None

    def test_progress_says_how_many_of_how_many(self):
        runner, _ = make_runner([FakeResponse(200, payload())])
        seen = []
        runner.run(
            words("a", "b", "c"), lambda done, total, o: seen.append((done, total, o.status))
        )
        assert seen == [(1, 3, Status.FOUND), (2, 3, Status.FOUND), (3, 3, Status.FOUND)]

    def test_a_zero_result_keyword_is_flagged_and_the_run_carries_on(self):
        runner, _ = make_runner(
            [FakeResponse(200, payload()), FakeResponse(200, EMPTY), FakeResponse(200, payload())]
        )
        report = runner.run(words("apple", "qwertyuiop", "pear"))
        assert [o.status for o in report.outcomes] == [Status.FOUND, Status.EMPTY, Status.FOUND]
        assert report.stopped is None
        assert report.with_status(Status.EMPTY)[0].keyword.label == "qwertyuiop"

    def test_a_response_of_only_unreadable_hits_counts_as_empty(self):
        body = {"total": 2, "totalHits": 2, "hits": [{"id": 1}, {"id": 2}]}
        runner, _ = make_runner([FakeResponse(200, body)])
        assert runner.run(words("apple")).outcomes[0].status is Status.EMPTY

    def test_one_bad_search_is_flagged_with_its_reason_and_the_run_carries_on(self):
        runner, _ = make_runner(
            [
                FakeResponse(200, payload()),
                FakeResponse(400, text="[ERROR 400] bad query"),
                FakeResponse(200, payload()),
            ]
        )
        report = runner.run(words("apple", "weird", "pear"))
        failed = report.with_status(Status.FAILED)
        assert [o.keyword.label for o in failed] == ["weird"]
        assert "bad query" in failed[0].error
        assert report.counts[Status.FOUND] == 2 and report.stopped is None

    def test_the_settings_shape_the_query(self):
        settings = Settings(api_key="k", candidates=7, term_template="{term} icon")
        runner, session = make_runner([FakeResponse(200, payload())], settings=settings)
        report = runner.run([Keyword("hot-dog", "hot dog")])
        params = session.calls[0]["params"]
        assert (params["q"], params["per_page"]) == ("hot dog icon", "7")
        assert report.outcomes[0].query == "hot dog icon"


class TestStoppingEarly:
    def test_a_refused_key_stops_the_run_instead_of_failing_every_keyword(self):
        runner, session = make_runner([FakeResponse(403, text="bad key")])
        report = runner.run(words("a", "b", "c", "d"))
        assert len(session.calls) == 1
        assert [o.status for o in report.outcomes] == [
            Status.FAILED,
            Status.PENDING,
            Status.PENDING,
            Status.PENDING,
        ]
        assert "PIXABAY_API_KEY" in report.stopped

    def test_a_429_that_outlives_the_backoff_stops_the_run_and_keeps_what_was_found(self):
        runner, session = make_runner(
            [FakeResponse(200, payload()), FakeResponse(429)], max_attempts=2
        )
        report = runner.run(words("a", "b", "c"))
        assert [o.status for o in report.outcomes] == [
            Status.FOUND,
            Status.FAILED,
            Status.PENDING,
        ]
        assert "rate limit" in report.stopped and len(session.calls) == 3

    def test_pending_keywords_still_carry_the_query_they_would_have_used(self):
        settings = Settings(api_key="k", term_template="{term} photo")
        runner, _ = make_runner([FakeResponse(403)], settings=settings)
        report = runner.run(words("a", "b"))
        assert report.outcomes[1].query == "b photo"

    def test_three_network_failures_in_a_row_stop_the_run(self):
        runner, session = make_runner([requests.ConnectionError("down")], max_attempts=1)
        report = runner.run(words(*"abcdef"))
        assert len(session.calls) == NETWORK_STRIKES
        assert report.counts[Status.FAILED] == NETWORK_STRIKES
        assert report.counts[Status.PENDING] == 3
        assert "unreachable" in report.stopped

    def test_a_success_resets_the_count_so_a_flaky_link_is_not_stopped(self):
        down = requests.ConnectionError("down")
        ok = FakeResponse(200, payload())
        script = [down, down, ok, down, down, ok]
        runner, _ = make_runner(script, max_attempts=1)
        report = runner.run(words(*"abcdef"))
        assert report.stopped is None
        assert report.counts[Status.FAILED] == 4 and report.counts[Status.FOUND] == 2

    def test_stopping_is_not_sticky_across_runs(self):
        runner, _ = make_runner([requests.ConnectionError("down")], max_attempts=1)
        runner.run(words(*"abcd"))
        runner.client._session.script = [FakeResponse(200, payload())]
        assert runner.run(words("a", "b")).stopped is None


class TestSearchOne:
    def test_a_retry_term_is_sent_as_typed_without_the_template(self):
        settings = Settings(api_key="k", term_template="{term} icon")
        runner, session = make_runner([FakeResponse(200, payload())], settings=settings)
        outcome = runner.search_one(Keyword("hot-dog", "hot dog"), term="frankfurter")
        assert session.calls[0]["params"]["q"] == "frankfurter"
        assert outcome.query == "frankfurter" and outcome.keyword.label == "hot-dog"

    def test_the_next_page_of_the_same_query(self):
        runner, session = make_runner([FakeResponse(200, payload())])
        outcome = runner.search_one(Keyword("apple", "apple"), page=2)
        assert session.calls[0]["params"]["page"] == "2"
        assert outcome.result.page == 2

    def test_it_reports_failure_instead_of_raising(self):
        runner, _ = make_runner([FakeResponse(429)], max_attempts=1)
        outcome = runner.search_one(Keyword("apple", "apple"))
        assert outcome.status is Status.FAILED and outcome.candidates == ()
