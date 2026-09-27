import json
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


class TestExportAndRestore:
    def busy_session(self):
        s = session(found("a", [1, 2, 3]), empty("e"), failed("f", "Boom."), pending("p"),
                    found("b", [4, 5, 6]), multiple=True, stopped="paused")  # fmt: skip
        s.pick("a", 2)
        s.pick("a", 3)
        s.skip("b")
        return s

    def test_a_restored_session_is_indistinguishable(self):
        original = self.busy_session()
        restored = SelectionSession.from_state(json.loads(json.dumps(original.export())))
        assert restored.snapshot() == original.snapshot()
        assert [s.candidates for s in restored.selections()] == [
            s.candidates for s in original.selections()
        ]

    def test_the_export_is_plain_json(self):
        json.dumps(self.busy_session().export())

    def test_busy_is_not_carried_over(self):
        s = session(found("a", [1, 2, 3]))
        s._entries["a"].busy = True
        restored = SelectionSession.from_state(s.export())
        assert not kw(restored.snapshot(), "a")["busy"]

    def test_a_finished_session_stays_finished(self):
        s = session(found("a", [1, 2, 3]))
        s.pick("a", 1)
        s.finish()
        assert SelectionSession.from_state(s.export()).finished

    def test_a_restored_session_can_search_again(self):
        search = lambda k, q, p: found("a", [7, 8, 9], query=q)  # noqa: E731
        restored = SelectionSession.from_state(session(found("a", [1, 2, 3])).export(), search)
        restored.retry("a", term="green")
        assert kw(restored.snapshot(), "a")["query"] == "green"

    def test_multiple_survives_and_can_be_turned_on_at_resume(self):
        saved = session(found("a", [1, 2, 3]), multiple=True).export()
        assert SelectionSession.from_state(saved).snapshot()["multiple"]
        plain = session(found("a", [1, 2, 3])).export()
        assert SelectionSession.from_state(plain, multiple=True).snapshot()["multiple"]

    @pytest.mark.parametrize(
        "damage",
        [
            lambda s: s.pop("entries"),
            lambda s: s["entries"][0].pop("candidates"),
            lambda s: s["entries"][0].update(status="bogus"),
            lambda s: s["entries"][0].update(picked=[99]),  # not one of the candidates
            lambda s: s["entries"][0]["candidates"][0].pop("id"),
            lambda s: s.update(entries="nope"),
        ],
    )
    def test_a_state_that_does_not_fit_is_refused_with_a_reason(self, damage):
        state = json.loads(json.dumps(session(found("a", [1, 2, 3])).export()))
        damage(state)
        with pytest.raises(SelectionError, match="not readable"):
            SelectionSession.from_state(state)


class TestOnChange:
    def watched(self, *outcomes, search=None):
        seen = []
        s = session(*outcomes, search=search)
        s.on_change = seen.append
        return s, seen

    def test_every_kind_of_change_is_reported_with_the_whole_state(self):
        search = lambda k, q, p: found("a", [7, 8, 9], query=q)  # noqa: E731
        s, seen = self.watched(found("a", [1, 2, 3]), search=search)
        s.pick("a", 1)
        s.skip("a")
        s.retry("a", term="green")
        s.pick("a", 8)
        s.finish()
        assert len(seen) == 5
        assert seen[0]["entries"][0]["picked"] == [1]
        assert seen[1]["entries"][0]["skipped"] is True
        assert seen[2]["entries"][0]["query"] == "green"
        assert seen[3]["entries"][0]["picked"] == [8]
        assert seen[4]["finished"] is True

    def test_a_refused_request_is_not_a_change(self):
        s, seen = self.watched(found("a", [1, 2, 3]))
        with pytest.raises(SelectionError):
            s.pick("a", 99)
        with pytest.raises(SelectionError):
            s.finish()
        assert seen == []

    def test_a_failed_retry_that_changes_only_the_notice_is_still_saved(self):
        search = lambda k, q, p: failed("a", "Rate limited.")  # noqa: E731
        s, seen = self.watched(found("a", [1, 2, 3]), search=search)
        s.retry("a", next_page=True)
        assert seen[-1]["entries"][0]["notice"] == "Rate limited."


class TestStartingFromABox:
    def empty(self, starter):
        return SelectionSession([], None, starter=starter)

    def test_it_begins_in_the_start_phase_with_nothing_on_it(self):
        s = self.empty(lambda text: None)
        snap = s.snapshot()
        assert (snap["phase"], snap["total"], snap["keywords"], snap["searching"]) == (
            "start", 0, [], None,
        )  # fmt: skip
        assert not s.wait_searched(0)

    def test_a_session_with_keywords_never_shows_the_box(self):
        assert session(found("a", [1, 2, 3])).phase == "picking"
        assert SelectionSession([], None).phase == "picking"  # no starter: nothing to wait for

    def test_keywords_that_are_already_there_mean_no_box(self):
        s = SelectionSession([found("a", [1, 2, 3])], None, starter=lambda text: None)
        assert s.phase == "picking"
        with pytest.raises(SelectionError, match="already been given"):
            s.start("more")

    def test_start_hands_the_text_to_the_starter(self):
        got = []
        s = self.empty(got.append)
        s.start("apple\npear")
        assert got == ["apple\npear"]

    def test_a_starter_that_rejects_the_text_leaves_the_box_open_with_the_reason(self):
        def starter(text):
            raise ValueError("no keywords found in the box")

        s = self.empty(starter)
        with pytest.raises(SelectionError, match="no keywords found"):
            s.start("")
        assert s.phase == "start"
        s._starter = lambda text: s.populate([pending("a")])  # and it can be tried again
        s.start("a")
        assert s.phase == "searching"

    def test_populate_puts_every_keyword_in_at_once_as_searching(self):
        s = self.empty(lambda text: None)
        s.populate([pending("a"), pending("b")], notes=["Ignored 1 repeated keyword(s)."])
        snap = s.snapshot()
        assert snap["phase"] == "searching" and snap["searching"] == {"done": 0, "total": 2}
        assert [k["status"] for k in snap["keywords"]] == ["pending", "pending"]
        assert snap["notes"] == ["Ignored 1 repeated keyword(s)."]

    def test_update_fills_one_keyword_in_and_progress_follows(self):
        s = self.empty(lambda text: None)
        s.populate([pending("a"), pending("b")])
        s.update(found("a", [1, 2, 3]))
        s.set_progress(1, 2)
        snap = s.snapshot()
        assert [k["status"] for k in snap["keywords"]] == ["found", "pending"]
        assert snap["searching"] == {"done": 1, "total": 2}

    def test_finishing_the_search_ends_the_phase_and_records_why_it_stopped(self):
        s = self.empty(lambda text: None)
        s.populate([pending("a")])
        s.finish_search("rate limited")
        snap = s.snapshot()
        assert snap["phase"] == "picking" and snap["searching"] is None
        assert snap["stopped"] == "rate limited" and s.wait_searched(0)

    def test_changes_are_reported_so_a_half_finished_search_is_saved(self):
        seen = []
        s = self.empty(lambda text: None)
        s.on_change = seen.append
        s.populate([pending("a"), pending("b")])
        s.update(found("a", [1, 2, 3]))
        s.finish_search()
        assert len(seen) == 3 and seen[0]["searching"] is True and seen[2]["searching"] is False

    def test_a_pending_keyword_cannot_be_searched_by_hand_while_the_search_runs(self):
        s = self.empty(lambda text: None)
        s._search = lambda k, q, p: found("a", [1, 2, 3])
        s.populate([pending("a")])
        with pytest.raises(SelectionError, match="already being searched"):
            s.retry("a")
        s.finish_search()
        s.retry("a")  # once the background search is over, it can be

    def test_finishing_is_refused_mid_search(self):
        s = self.empty(lambda text: None)
        s.populate([found("a", [1, 2, 3]), pending("b")])
        s.pick("a", 1)
        with pytest.raises(SelectionError, match="still running"):
            s.finish()

    def test_a_search_interrupted_before_it_finished_says_so_when_restored(self):
        s = self.empty(lambda text: None)
        s.populate([pending("a"), pending("b")])
        s.update(found("a", [1, 2, 3]))
        restored = SelectionSession.from_state(json.loads(json.dumps(s.export())))
        snap = restored.snapshot()
        assert snap["phase"] == "picking" and snap["searching"] is None
        assert "interrupted" in snap["stopped"]
        assert [k["status"] for k in snap["keywords"]] == ["found", "pending"]

    def test_a_search_that_finished_says_nothing_of_the_kind_when_restored(self):
        s = self.empty(lambda text: None)
        s.populate([pending("a")])
        s.update(found("a", [1, 2, 3]))
        s.finish_search()
        assert SelectionSession.from_state(s.export()).snapshot()["stopped"] is None

    def test_notes_survive_a_restore(self):
        s = self.empty(lambda text: None)
        s.populate([pending("a")], notes=["Ignored 2 repeated keyword(s)."])
        assert SelectionSession.from_state(s.export()).snapshot()["notes"] == [
            "Ignored 2 repeated keyword(s)."
        ]

    def test_two_pastes_at_once_start_only_one_search(self):
        entered, release = threading.Event(), threading.Event()
        calls = []

        def slow_starter(text):
            calls.append(text)
            entered.set()
            assert release.wait(5)

        s = self.empty(slow_starter)
        worker = threading.Thread(target=lambda: s.start("first"))
        worker.start()
        assert entered.wait(5)
        with pytest.raises(SelectionError, match="already been given"):
            s.start("second")
        release.set()
        worker.join(5)
        assert calls == ["first"]
