import json

import pytest
import requests
from fakes import FakeClock, FakeResponse, FakeSession, hit, payload

from crivo.config import SearchOptions
from crivo.pixabay_client import (
    API_URL,
    CACHE_TTL,
    BadResponse,
    NetworkFailure,
    PixabayClient,
    RateLimited,
    Rejected,
    SearchCache,
    Throttle,
    build_params,
    parse_response,
)

KEY = "SECRET-KEY-123"


def make_client(script=None, *, clock=None, cache=None, throttle=None, **kwargs):
    clock = clock or FakeClock()
    session = FakeSession(script)
    throttle = throttle or Throttle(clock=clock, sleep=clock.sleep)
    client = PixabayClient(
        KEY, session=session, throttle=throttle, cache=cache, sleep=clock.sleep, **kwargs
    )
    return client, session, clock


class TestThrottle:
    def test_lets_a_full_window_through_without_waiting(self):
        clock = FakeClock()
        throttle = Throttle(limit=100, clock=clock, sleep=clock.sleep)
        for _ in range(100):
            throttle.acquire()
        assert clock.slept == []

    def test_the_101st_waits_for_the_oldest_to_leave_the_window(self):
        clock = FakeClock()
        throttle = Throttle(limit=100, window=60, margin=1, clock=clock, sleep=clock.sleep)
        for _ in range(100):
            throttle.acquire()
        clock.advance(10)
        throttle.acquire()
        assert clock.slept == [pytest.approx(51.0)]  # 60 + 1 margin - 10 already elapsed

    def test_the_window_slides_rather_than_resetting(self):
        clock = FakeClock()
        throttle = Throttle(limit=2, window=10, margin=0, clock=clock, sleep=clock.sleep)
        throttle.acquire()  # t=0
        clock.advance(6)
        throttle.acquire()  # t=6
        clock.advance(5)  # t=11: the first has left, the second has not
        throttle.acquire()
        assert clock.slept == []
        throttle.acquire()  # a third in the window [6, 16): waits for t=6 to leave
        assert clock.slept == [pytest.approx(5.0)]

    def test_an_exhausted_budget_in_the_headers_blocks_until_reset(self):
        clock = FakeClock()
        throttle = Throttle(clock=clock, sleep=clock.sleep)
        throttle.observe({"x-ratelimit-remaining": "0", "X-RateLimit-Reset": "17"})
        throttle.acquire()
        assert clock.slept == [pytest.approx(17.0)]

    def test_a_healthy_budget_in_the_headers_changes_nothing(self):
        clock = FakeClock()
        throttle = Throttle(clock=clock, sleep=clock.sleep)
        throttle.observe({"X-RateLimit-Remaining": "40", "X-RateLimit-Reset": "17"})
        throttle.observe({"X-RateLimit-Remaining": "junk"})
        throttle.acquire()
        assert clock.slept == []


class TestParams:
    def test_defaults(self):
        params = build_params("hot dog", SearchOptions(), 5, 1)
        assert params == {
            "q": "hot dog",
            "lang": "en",
            "image_type": "all",
            "orientation": "all",
            "order": "popular",
            "safesearch": "false",
            "editors_choice": "false",
            "page": "1",
            "per_page": "5",
        }

    def test_every_filter_the_spec_lists(self):
        options = SearchOptions(
            image_type="vector",
            orientation="vertical",
            category="food",
            colors=("red", "blue"),
            min_width=800,
            min_height=600,
            safesearch=True,
            editors_choice=True,
            order="latest",
            lang="pt",
        )
        params = build_params("x", options, 10, 2)
        assert params["image_type"] == "vector"
        assert params["orientation"] == "vertical"
        assert params["category"] == "food"
        assert params["colors"] == "red,blue"
        assert (params["min_width"], params["min_height"]) == ("800", "600")
        assert (params["safesearch"], params["editors_choice"]) == ("true", "true")
        assert (params["order"], params["lang"], params["page"]) == ("latest", "pt", "2")

    def test_an_unset_minimum_is_left_out_not_sent_as_the_word_none(self):
        params = build_params("x", SearchOptions(), 5, 1)
        assert "min_width" not in params and "min_height" not in params
        assert "None" not in params.values()


class TestSearch:
    def test_returns_candidates_with_attribution(self):
        client, session, _ = make_client([FakeResponse(200, payload(3, total=1234))])
        result = client.search("apple", count=3)
        assert (result.total, result.total_hits, result.page, result.cached) == (1234, 3, 1, False)
        first = result.candidates[0]
        assert first.user == "user1"
        assert first.page_url == "https://pixabay.com/photos/thing-1/"
        assert first.tags == ("red apple", "fruit", "food")
        assert first.large_image_url.endswith("1_1280.jpg")
        assert (first.width, first.height) == (4000, 3000)

    def test_sends_the_key_and_a_user_agent_as_parameters_not_in_the_url(self):
        client, session, _ = make_client()
        client.search("apple")
        call = session.calls[0]
        assert call["url"] == API_URL
        assert call["params"]["key"] == KEY
        assert call["headers"]["User-Agent"].startswith("crivo/")
        assert call["timeout"]

    def test_special_characters_are_left_to_requests_to_encode(self):
        client, session, _ = make_client()
        client.search("rock&roll=1  x")
        params = session.calls[0]["params"]
        assert params["q"] == "rock&roll=1 x"  # one parameter, whitespace collapsed
        assert list(params).count("q") == 1

    def test_an_empty_term_is_refused_because_pixabay_would_return_everything(self):
        client, session, _ = make_client()
        with pytest.raises(Rejected, match="empty"):
            client.search("   ")
        assert session.calls == []

    def test_a_term_over_100_characters_is_refused_before_a_request_is_spent(self):
        client, session, _ = make_client()
        with pytest.raises(Rejected, match="101 characters"):
            client.search("a" * 101)
        assert session.calls == []

    @pytest.mark.parametrize("count", [2, 201])
    def test_a_page_size_pixabay_refuses_is_a_programming_error(self, count):
        client, _, _ = make_client()
        with pytest.raises(ValueError, match="count"):
            client.search("apple", count=count)

    def test_a_page_past_the_500_result_cap_is_empty_without_a_request(self):
        client, session, _ = make_client()
        result = client.search("apple", count=100, page=6)  # results 501-600
        assert result.candidates == () and not result.has_more
        assert session.calls == []

    def test_has_more_follows_the_total_and_the_cap(self):
        page = {**payload(3), "totalHits": 500}
        client, _, _ = make_client([FakeResponse(200, page)])
        assert client.search("apple", count=3, page=1).has_more
        client, _, _ = make_client([FakeResponse(200, {**page, "totalHits": 3})])
        assert not client.search("apple", count=3, page=1).has_more

    def test_zero_results_is_a_result_not_an_error(self):
        client, _, _ = make_client([FakeResponse(200, {"total": 0, "totalHits": 0, "hits": []})])
        result = client.search("qwertyuiop")
        assert result.candidates == () and result.total == 0

    def test_full_access_urls_are_kept_when_present(self):
        body = {"total": 1, "totalHits": 1, "hits": [hit(1, imageURL="https://x/full.png")]}
        client, _, _ = make_client([FakeResponse(200, body)])
        candidate = client.search("apple", count=3).candidates[0]
        assert candidate.image_url == "https://x/full.png" and candidate.vector_url is None


class TestParsing:
    def test_a_malformed_hit_is_skipped_and_counted_not_fatal(self):
        body = {"total": 3, "totalHits": 3, "hits": [hit(1), {"id": 2}, "junk", hit(4)]}
        result = parse_response(body, "x", 1, 5)
        assert [c.id for c in result.candidates] == [1, 4]
        assert result.skipped == 2

    @pytest.mark.parametrize("body", [None, [], "text", {"hits": "nope"}, {"total": 1}])
    def test_a_response_that_is_not_a_result_list(self, body):
        with pytest.raises(BadResponse):
            parse_response(body, "x", 1, 5)

    def test_a_count_that_is_not_a_number(self):
        with pytest.raises(BadResponse, match="count"):
            parse_response({"hits": [], "total": "many"}, "x", 1, 5)

    def test_a_200_that_is_not_json(self):
        client, _, _ = make_client([FakeResponse(200, None)])
        with pytest.raises(BadResponse, match="JSON"):
            client.search("apple")


class TestRateLimit:
    """The library the spec named crashed with AttributeError on the first 429."""

    def test_a_429_is_waited_out_and_retried_and_the_run_carries_on(self):
        client, session, clock = make_client(
            [FakeResponse(429, headers={"X-RateLimit-Reset": "20"}), FakeResponse(200, payload())]
        )
        result = client.search("apple")
        assert len(result.candidates) == 3
        assert len(session.calls) == 2
        assert sum(clock.slept) == pytest.approx(20.0)  # the server's own reset time

    def test_without_a_reset_header_the_backoff_is_exponential(self):
        client, session, clock = make_client(
            [FakeResponse(429)] * 3 + [FakeResponse(200, payload())]
        )
        client.search("apple")
        assert len(session.calls) == 4
        assert clock.slept == [pytest.approx(1.0), pytest.approx(2.0), pytest.approx(4.0)]

    def test_persistent_429_ends_in_an_error_a_person_can_retry_not_a_loop(self):
        client, session, clock = make_client([FakeResponse(429)], max_attempts=3)
        with pytest.raises(RateLimited, match="Wait a moment") as caught:
            client.search("apple")
        assert len(session.calls) == 3
        assert caught.value.retry_after

    def test_the_wait_after_a_final_429_still_holds_the_next_search_back(self):
        client, session, clock = make_client(
            [FakeResponse(429, headers={"X-RateLimit-Reset": "30"})], max_attempts=1
        )
        with pytest.raises(RateLimited):
            client.search("apple")
        assert clock.slept == []  # nothing slept for a call that gives up
        client._session.script = [FakeResponse(200, payload())]
        client.search("pear")
        assert sum(clock.slept) == pytest.approx(30.0)

    def test_the_backoff_is_capped(self):
        client, _, clock = make_client(
            [FakeResponse(500)] * 7 + [FakeResponse(200, payload())], max_attempts=8, backoff_cap=5
        )
        client.search("apple")
        assert max(clock.slept) == 5

    def test_a_budget_the_headers_say_is_spent_delays_the_next_request(self):
        exhausted = FakeResponse(
            200, payload(), headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "12"}
        )
        client, _, clock = make_client([exhausted, FakeResponse(200, payload())])
        client.search("apple")
        assert clock.slept == []
        client.search("pear")
        assert clock.slept == [pytest.approx(12.0)]


class TestFailures:
    def test_a_dropped_connection_is_retried_then_reported(self):
        client, session, _ = make_client([requests.ConnectionError("boom")], max_attempts=3)
        with pytest.raises(NetworkFailure, match="Could not reach Pixabay"):
            client.search("apple")
        assert len(session.calls) == 3

    def test_a_timeout_then_success(self):
        client, session, _ = make_client([requests.Timeout("slow"), FakeResponse(200, payload())])
        assert len(client.search("apple").candidates) == 3
        assert len(session.calls) == 2

    def test_a_5xx_is_retried_then_reported(self):
        client, session, _ = make_client([FakeResponse(503)], max_attempts=2)
        with pytest.raises(NetworkFailure, match="HTTP 503"):
            client.search("apple")
        assert len(session.calls) == 2

    def test_a_400_is_not_retried_and_carries_pixabays_reason(self):
        client, session, _ = make_client([FakeResponse(400, text='[ERROR 400] "q" is bad')])
        with pytest.raises(Rejected, match=r"HTTP 400.*\"q\" is bad") as caught:
            client.search("apple")
        assert len(session.calls) == 1
        assert caught.value.status == 400 and not caught.value.auth

    @pytest.mark.parametrize(
        ("status", "text"), [(401, ""), (403, "forbidden"), (400, "Invalid API key")]
    )
    def test_a_bad_key_is_marked_so_the_run_can_stop_instead_of_failing_every_keyword(
        self, status, text
    ):
        client, _, _ = make_client([FakeResponse(status, text=text)])
        with pytest.raises(Rejected) as caught:
            client.search("apple")
        assert caught.value.auth


class TestTheKeyStaysOut:
    """CLAUDE.md: never in a cache key, a session file, a log line or an error message."""

    def test_not_in_a_network_error_though_requests_puts_the_url_in_its_own(self):
        leaky = requests.ConnectionError(f"Max retries exceeded with url: /api/?key={KEY}&q=x")
        client, _, _ = make_client([leaky], max_attempts=1)
        with pytest.raises(NetworkFailure) as caught:
            client.search("apple")
        error = caught.value
        assert KEY not in str(error) and KEY not in repr(error)
        assert error.__cause__ is None and error.__suppress_context__

    def test_not_in_a_rejection_that_echoes_the_url(self):
        client, _, _ = make_client([FakeResponse(400, text=f"bad request key={KEY}")])
        with pytest.raises(Rejected) as caught:
            client.search("apple")
        assert KEY not in str(caught.value)

    def test_not_in_the_cache_directory(self, tmp_path):
        cache = SearchCache(tmp_path)
        client, _, _ = make_client(cache=cache)
        client.search("apple")
        files = list(tmp_path.iterdir())
        assert files
        for path in files:
            assert KEY not in path.name
            assert KEY not in path.read_text(encoding="utf-8")


class TestCache:
    def test_a_repeat_search_is_served_from_disk_without_a_request(self, tmp_path):
        client, session, _ = make_client(cache=SearchCache(tmp_path))
        first = client.search("apple")
        second = client.search("apple")
        assert len(session.calls) == 1
        assert (first.cached, second.cached) == (False, True)
        assert second.candidates == first.candidates

    def test_it_survives_a_new_process(self, tmp_path):
        make_client(cache=SearchCache(tmp_path))[0].search("apple")
        client, session, _ = make_client(cache=SearchCache(tmp_path))
        assert client.search("apple").cached
        assert session.calls == []

    def test_the_key_is_the_exact_query(self, tmp_path):
        client, session, _ = make_client(cache=SearchCache(tmp_path))
        client.search("apple")
        client.search("apple", count=4)
        client.search("apple", page=2)
        client.search("apple", SearchOptions(image_type="vector"))
        client.search("Apple")
        assert len(session.calls) == 5

    def test_an_entry_is_good_for_24_hours_and_no_longer(self, tmp_path):
        """The library the spec named raised KeyError on its first expired entry."""
        clock = FakeClock()
        cache = SearchCache(tmp_path, now=clock)
        client, session, _ = make_client(cache=cache, clock=clock)
        client.search("apple")
        clock.advance(CACHE_TTL - 1)
        assert client.search("apple").cached
        clock.advance(2)
        assert not client.search("apple").cached
        assert len(session.calls) == 2

    def test_a_failure_is_not_cached(self, tmp_path):
        client, session, _ = make_client(
            [FakeResponse(503), FakeResponse(503), FakeResponse(200, payload())],
            cache=SearchCache(tmp_path),
            max_attempts=2,
        )
        with pytest.raises(NetworkFailure):
            client.search("apple")
        assert list(tmp_path.iterdir()) == []
        assert len(client.search("apple").candidates) == 3

    def test_a_corrupt_file_costs_one_search_and_is_replaced(self, tmp_path):
        cache = SearchCache(tmp_path)
        client, session, _ = make_client(cache=cache)
        client.search("apple")
        (path,) = tmp_path.iterdir()
        path.write_text("{ not json", encoding="utf-8")
        assert not client.search("apple").cached
        assert client.search("apple").cached
        assert len(session.calls) == 2

    def test_an_entry_whose_body_is_not_a_result_is_treated_as_a_miss(self, tmp_path):
        cache = SearchCache(tmp_path)
        client, session, _ = make_client(cache=cache)
        client.search("apple")
        (path,) = tmp_path.iterdir()
        entry = json.loads(path.read_text(encoding="utf-8"))
        entry["response"] = {"hits": "nope"}
        path.write_text(json.dumps(entry), encoding="utf-8")
        assert not client.search("apple").cached

    def test_a_clock_that_went_backwards_does_not_make_an_entry_immortal(self, tmp_path):
        clock = FakeClock()
        cache = SearchCache(tmp_path, now=clock)
        cache.put("q", {"hits": []})
        clock.advance(-3600)
        assert cache.get("q") is None

    def test_expired_files_are_pruned_on_the_first_write(self, tmp_path):
        clock = FakeClock()
        cache = SearchCache(tmp_path, now=clock)
        cache.put("old", {"hits": []})
        clock.advance(CACHE_TTL + 1)
        later = SearchCache(tmp_path, now=clock)
        later.put("new", {"hits": []})
        names = [json.loads(p.read_text(encoding="utf-8"))["query"] for p in tmp_path.iterdir()]
        assert names == ["new"]

    def test_an_unwritable_cache_costs_the_cache_not_the_search(self, tmp_path):
        blocker = tmp_path / "not-a-directory"
        blocker.write_text("x", encoding="utf-8")
        client, _, _ = make_client(cache=SearchCache(blocker / "cache"))
        assert len(client.search("apple").candidates) == 3

    def test_no_temp_files_are_left_behind(self, tmp_path):
        client, _, _ = make_client(cache=SearchCache(tmp_path))
        client.search("apple")
        assert [p.suffix for p in tmp_path.iterdir()] == [".json"]
