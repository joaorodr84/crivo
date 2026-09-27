import json

import pytest
from fakes import FakeClock, hit

from crivo.keywords import Keyword
from crivo.pixabay_client import parse_response
from crivo.search_runner import Outcome, Status
from crivo.selection import SelectionSession
from crivo.session_store import VERSION, SessionError, SessionStore

KEY = "SECRET-KEY-123"


def a_session():
    body = {"total": 9, "totalHits": 9, "hits": [hit(i) for i in (1, 2, 3)]}
    outcome = Outcome(
        Keyword("apple", "apple"), Status.FOUND, "apple", parse_response(body, "x", 1, 3)
    )
    skipped = Outcome(Keyword("pear", "pear"), Status.PENDING, "pear")
    session = SelectionSession([outcome, skipped], stopped="rate limited")
    session.pick("apple", 2)
    return session


class TestStore:
    def test_a_missing_file_is_no_session(self, tmp_path):
        assert SessionStore(tmp_path / "s.json").load() is None

    def test_roundtrip(self, tmp_path):
        clock = FakeClock()
        store = SessionStore(tmp_path / "s.json", now=clock)
        state = a_session().export()
        store.save(state)
        saved = store.load()
        assert saved.state == json.loads(json.dumps(state))
        assert saved.saved_at == clock() and not saved.completed

    def test_creates_the_directory(self, tmp_path):
        store = SessionStore(tmp_path / "a" / "b" / "s.json")
        store.save(a_session().export())
        assert store.load() is not None

    def test_complete_marks_it_and_keeps_the_state(self, tmp_path):
        store = SessionStore(tmp_path / "s.json")
        store.save(a_session().export())
        store.complete()
        saved = store.load()
        assert saved.completed and saved.total == 2

    def test_complete_with_nothing_saved_does_nothing(self, tmp_path):
        store = SessionStore(tmp_path / "s.json")
        store.complete()
        assert store.load() is None

    def test_discard(self, tmp_path):
        store = SessionStore(tmp_path / "s.json")
        store.save(a_session().export())
        store.discard()
        store.discard()  # already gone: harmless
        assert store.load() is None

    def test_the_summary_counts_done_keywords_and_says_how_long_ago(self, tmp_path):
        clock = FakeClock()
        store = SessionStore(tmp_path / "s.json", now=clock)
        store.save(a_session().export())
        saved = store.load()
        assert saved.describe(clock()) == "1 of 2 keywords done, saved moments ago"
        assert "20 minutes ago" in saved.describe(clock() + 20 * 60)
        assert "3 hours ago" in saved.describe(clock() + 3 * 3600)
        assert "3 days ago" in saved.describe(clock() + 3 * 86400)


class TestUnreadable:
    @pytest.mark.parametrize(
        "text",
        [
            "",
            "{ not json",
            "[]",
            "{}",
            '{"version": 1}',
            '{"version": 1, "saved_at": 1, "completed": false}',
        ],
    )
    def test_a_damaged_file_says_what_to_do_instead_of_crashing(self, tmp_path, text):
        path = tmp_path / "s.json"
        path.write_text(text, encoding="utf-8")
        with pytest.raises(SessionError, match="--restart"):
            SessionStore(path).load()

    def test_a_file_that_is_not_text(self, tmp_path):
        path = tmp_path / "s.json"
        path.write_bytes(b"\xff\xfe\x00garbage")
        with pytest.raises(SessionError, match="not a readable session"):
            SessionStore(path).load()

    def test_a_different_format_version_is_named(self, tmp_path):
        path = tmp_path / "s.json"
        path.write_text(json.dumps({"version": VERSION + 1, "state": {}}), encoding="utf-8")
        with pytest.raises(SessionError, match="different version"):
            SessionStore(path).load()


class TestSaving:
    def test_a_failed_save_is_recorded_and_not_raised(self, tmp_path):
        blocker = tmp_path / "file"
        blocker.write_text("x", encoding="utf-8")
        store = SessionStore(blocker / "s.json")
        store.save(a_session().export())  # must not raise
        assert store.error

    def test_a_later_good_save_clears_the_error(self, tmp_path):
        store = SessionStore(tmp_path / "s.json")
        store.error = "old"
        store.save(a_session().export())
        assert store.error is None

    def test_a_failure_while_replacing_keeps_the_previous_file_and_leaves_no_temp(
        self, tmp_path, monkeypatch
    ):
        store = SessionStore(tmp_path / "s.json")
        first = a_session()
        store.save(first.export())
        first.pick("apple", 3)

        def boom(*args):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr("crivo.session_store.os.replace", boom)
        store.save(first.export())
        assert "No space" in store.error
        monkeypatch.undo()
        assert store.load().state["entries"][0]["picked"] == [2]  # the old state, intact
        assert [p.name for p in tmp_path.iterdir()] == ["s.json"]

    def test_no_temp_files_remain(self, tmp_path):
        store = SessionStore(tmp_path / "s.json")
        for _ in range(3):
            store.save(a_session().export())
        assert [p.name for p in tmp_path.iterdir()] == ["s.json"]

    def test_the_api_key_is_not_in_the_file(self, tmp_path):
        store = SessionStore(tmp_path / "s.json")
        store.save(a_session().export())
        assert KEY not in (tmp_path / "s.json").read_text(encoding="utf-8")
