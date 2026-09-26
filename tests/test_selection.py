import threading

import pytest
from fakes import hit

from winnower.keywords import Keyword
from winnower.pixabay_client import SearchResult, parse_response
from winnower.search_runner import Outcome, Status
from winnower.selection import SelectionError, SelectionSession


def result(ids, page=1, total_hits=100, per_page=3):
    body = {"total": total_hits, "totalHits": total_hits, "hits": [hit(i) for i in ids]}
    return parse_response(body, "x", page, per_page)


def found(label, ids, page=1, query=None, **kw):
    return Outcome(Keyword(label, label), Status.FOUND, query or label, result(ids, page, **kw))


def empty(label):
    empty_result = SearchResult(label, 1, 3, 0, 0, ())
    return Outcome(Keyword(label, label), Status.EMPTY, label, empty_result)


def failed(label, message="It failed."):
    return Outcome(Keyword(label, label), Status.FAILED, label, error=message)


def pending(label):
    return Outcome(Keyword(label, label), Status.PENDING, label)


def session(*outcomes, search=None, **kw):
    return SelectionSession(list(outcomes), search, **kw)


def kw(snapshot, label):
    return next(k for k in snapshot["keywords"] if k["label"] == label)


class TestPicking:
    def test_one_pick_by_default_and_a_second_replaces_it(self):
        s = session(found("apple", [1, 2, 3]))
        s.pick("apple", 1)
        s.pick("apple", 2)
        assert kw(s.snapshot(), "apple")["picked"] == [2]

    def test_several_when_multiple_is_on_in_the_order_picked(self):
        s = session(found("apple", [1, 2, 3]), multiple=True)
        for i in (3, 1):
            s.pick("apple", i)
        assert kw(s.snapshot(), "apple")["picked"] == [3, 1]

    def test_picking_the_same_one_twice_does_not_duplicate_it(self):
        s = session(found("apple", [1, 2, 3]), multiple=True)
        s.pick("apple", 1)
        s.pick("apple", 1)
        assert kw(s.snapshot(), "apple")["picked"] == [1]

    def test_unpicking(self):
        s = session(found("apple", [1, 2, 3]))
        s.pick("apple", 1)
        s.pick("apple", 1, selected=False)
        assert kw(s.snapshot(), "apple")["picked"] == []
        s.pick("apple", 2, selected=False)  # not picked: harmless

    def test_an_image_that_is_not_a_candidate_is_refused(self):
        s = session(found("apple", [1, 2, 3]))
        with pytest.raises(SelectionError, match="not one of the candidates"):
            s.pick("apple", 99)

    def test_an_unknown_keyword_is_refused(self):
        with pytest.raises(SelectionError, match="no keyword"):
            session(found("apple", [1])).pick("pear", 1)

    def test_picks_are_per_keyword(self):
        s = session(found("a", [1, 2, 3]), found("b", [1, 2, 3]))
        s.pick("a", 1)
        assert kw(s.snapshot(), "b")["picked"] == []


class TestSkipping:
    def test_skipping_clears_picks_and_counts_as_done(self):
        s = session(found("apple", [1, 2, 3]))
        s.pick("apple", 1)
        s.skip("apple")
        k = kw(s.snapshot(), "apple")
        assert k["picked"] == [] and k["skipped"] and k["done"]

    def test_picking_un_skips(self):
        s = session(found("apple", [1, 2, 3]))
        s.skip("apple")
        s.pick("apple", 1)
        assert not kw(s.snapshot(), "apple")["skipped"]

    def test_undo_skip(self):
        s = session(found("apple", [1, 2, 3]))
        s.skip("apple")
        s.skip("apple", skipped=False)
        assert not kw(s.snapshot(), "apple")["done"]


class TestSnapshot:
    def test_counts_done_and_picks(self):
        s = session(
            found("a", [1, 2, 3]), found("b", [1, 2, 3]), found("c", [1, 2, 3]), multiple=True
        )
        s.pick("a", 1)
        s.pick("a", 2)
        s.skip("b")
        snap = s.snapshot()
        assert (snap["total"], snap["done"], snap["picks"]) == (3, 2, 2)

    def test_keeps_input_order(self):
        s = session(found("z", [1]), found("a", [1]), found("m", [1]))
        assert [k["label"] for k in s.snapshot()["keywords"]] == ["z", "a", "m"]

    def test_carries_what_the_page_needs_to_show_attribution(self):
        c = kw(session(found("apple", [1])).snapshot(), "apple")["candidates"][0]
        assert c["user"] == "user1" and c["page_url"].startswith("https://pixabay.com/")
        assert c["webformat_url"] and c["preview_url"] and c["tags"]

    def test_states_of_keywords_that_have_no_candidates(self):
        s = session(empty("e"), failed("f", "Boom."), pending("p"))
        snap = s.snapshot()
        assert [k["status"] for k in snap["keywords"]] == ["empty", "failed", "pending"]
        assert kw(snap, "f")["error"] == "Boom."

    def test_says_why_the_run_stopped(self):
        assert (
            session(found("a", [1]), stopped="rate limited").snapshot()["stopped"] == "rate limited"
        )

    def test_has_more_comes_from_the_search_result(self):
        assert kw(session(found("a", [1, 2, 3], total_hits=100)).snapshot(), "a")["has_more"]
        assert not kw(session(found("a", [1, 2, 3], total_hits=3)).snapshot(), "a")["has_more"]


class TestRetry:
    def searching(self, *outcomes):
        """A search function that hands out `outcomes` in turn and records its calls."""
        calls = []
        queue = list(outcomes)

        def search(keyword, query, page):
            calls.append((keyword.label, query, page))
            return queue.pop(0)

        return search, calls

    def test_a_new_term_replaces_the_candidates_and_clears_the_picks(self):
        search, calls = self.searching(found("apple", [7, 8, 9], query="green apple"))
        s = session(found("apple", [1, 2, 3]), search=search)
        s.pick("apple", 1)
        s.retry("apple", term="  green apple ")
        k = kw(s.snapshot(), "apple")
        assert calls == [("apple", "green apple", 1)]
        assert [c["id"] for c in k["candidates"]] == [7, 8, 9]
        assert k["picked"] == [] and k["query"] == "green apple" and k["page"] == 1

    def test_the_next_page_is_appended_and_the_picks_kept(self):
        search, calls = self.searching(found("apple", [4, 5, 3], page=2))
        s = session(found("apple", [1, 2, 3]), search=search)
        s.pick("apple", 2)
        s.retry("apple", next_page=True)
        k = kw(s.snapshot(), "apple")
        assert calls == [("apple", "apple", 2)]
        assert [c["id"] for c in k["candidates"]] == [1, 2, 3, 4, 5]  # 3 not repeated
        assert k["picked"] == [2] and k["page"] == 2

    def test_an_empty_next_page_says_so_and_stops_offering_more(self):
        search, _ = self.searching(empty("apple"))
        s = session(found("apple", [1, 2, 3]), search=search)
        s.retry("apple", next_page=True)
        k = kw(s.snapshot(), "apple")
        assert not k["has_more"] and "no more results" in k["notice"]
        assert len(k["candidates"]) == 3

    def test_a_failed_retry_keeps_what_was_already_found(self):
        search, _ = self.searching(failed("apple", "Rate limited."))
        s = session(found("apple", [1, 2, 3]), search=search)
        s.pick("apple", 1)
        s.retry("apple", next_page=True)
        k = kw(s.snapshot(), "apple")
        assert k["notice"] == "Rate limited." and k["picked"] == [1] and len(k["candidates"]) == 3
        assert k["status"] == "found"

    def test_a_failed_retry_of_a_failure_stays_a_failure_with_the_new_reason(self):
        search, _ = self.searching(failed("apple", "Still down."))
        s = session(failed("apple", "Down."), search=search)
        s.retry("apple")
        k = kw(s.snapshot(), "apple")
        assert (k["status"], k["error"]) == ("failed", "Still down.")

    def test_retrying_a_failure_can_turn_it_into_candidates(self):
        search, calls = self.searching(found("apple", [1, 2, 3]))
        s = session(failed("apple"), search=search)
        s.retry("apple")
        k = kw(s.snapshot(), "apple")
        assert k["status"] == "found" and k["error"] is None and len(k["candidates"]) == 3

    def test_a_pending_keyword_is_searched_with_the_query_it_carries(self):
        search, calls = self.searching(found("apple", [1, 2, 3]))
        s = session(pending("apple"), search=search)
        s.retry("apple")
        assert calls == [("apple", "apple", 1)]

    def test_a_new_term_that_finds_nothing_says_so(self):
        search, _ = self.searching(empty("apple"))
        s = session(found("apple", [1, 2, 3]), search=search)
        s.retry("apple", term="qwertyuiop")
        k = kw(s.snapshot(), "apple")
        assert k["status"] == "empty" and k["candidates"] == []

    def test_a_blank_term_is_refused_before_any_search(self):
        search, calls = self.searching()
        s = session(found("apple", [1, 2, 3]), search=search)
        with pytest.raises(SelectionError, match="Type a search term"):
            s.retry("apple", term="   ")
        assert calls == []

    def test_without_a_search_function_retry_is_unavailable(self):
        with pytest.raises(SelectionError, match="not available"):
            session(found("apple", [1])).retry("apple")

    def test_the_search_runs_outside_the_lock_and_marks_the_keyword_busy(self):
        entered, release = threading.Event(), threading.Event()

        def slow(keyword, query, page):
            entered.set()
            assert release.wait(5)
            return found("a", [7, 8, 9])

        s = session(found("a", [1, 2, 3]), found("b", [1, 2, 3]), search=slow)
        worker = threading.Thread(target=lambda: s.retry("a", term="x"))
        worker.start()
        assert entered.wait(5)
        assert kw(s.snapshot(), "a")["busy"]  # the snapshot is readable mid-search...
        s.pick("b", 1)  # ...and so is picking elsewhere
        with pytest.raises(SelectionError, match="already being searched"):
            s.retry("a", term="y")
        with pytest.raises(SelectionError, match="still running"):
            s.finish()
        release.set()
        worker.join(5)
        assert not kw(s.snapshot(), "a")["busy"]

    def test_a_search_that_raises_does_not_leave_the_keyword_busy(self):
        def explode(keyword, query, page):
            raise RuntimeError("bug")

        s = session(found("a", [1, 2, 3]), search=explode)
        with pytest.raises(RuntimeError):
            s.retry("a", term="x")
        assert not kw(s.snapshot(), "a")["busy"]


class TestFinish:
    def test_needs_at_least_one_pick(self):
        s = session(found("a", [1, 2, 3]))
        with pytest.raises(SelectionError, match="at least one"):
            s.finish()
        s.skip("a")
        with pytest.raises(SelectionError, match="at least one"):
            s.finish()
        assert not s.finished

    def test_finishing_releases_a_waiter(self):
        s = session(found("a", [1, 2, 3]))
        s.pick("a", 1)
        assert not s.wait(0)
        s.finish()
        assert s.wait(0) and s.finished and s.snapshot()["finished"]

    def test_selections_are_the_picks_in_keyword_order(self):
        s = session(found("b", [1, 2, 3]), found("a", [4, 5, 6]), found("c", [7]), multiple=True)
        s.pick("a", 6)
        s.pick("a", 4)
        s.pick("b", 2)
        chosen = s.selections()
        assert [c.keyword.label for c in chosen] == ["b", "a"]
        assert [i.id for i in chosen[1].candidates] == [6, 4]  # the order they were picked

    def test_selections_carry_the_query_that_found_them(self):
        search = lambda k, q, p: found("a", [7, 8, 9], query=q)  # noqa: E731
        s = session(found("a", [1, 2, 3]), search=search)
        s.retry("a", term="green apple")
        s.pick("a", 8)
        assert s.selections()[0].query == "green apple"
