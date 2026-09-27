"""The pipeline end to end: real stages, fake network, and a fake person in place of the page."""

import io
import json
import threading
import zipfile

import pytest
import requests
from fakes import FakeClock, FakeResponse, RoutingSession, hit, png_bytes
from PIL import Image

from winnower import __version__
from winnower.cli import EXIT_ERROR, EXIT_INTERRUPTED, EXIT_OK, EXIT_PARTIAL, main
from winnower.pixabay_client import API_URL
from winnower.selection import SelectionError

KEY = "SECRET-KEY-123"
PNG = png_bytes((80, 40), (200, 30, 30))
BASES = {"apple": 100, "pear": 200}


def search_body(term, n=3):
    if term.startswith("nothing"):
        return {"total": 0, "totalHits": 0, "hits": []}
    base = BASES.get(term.split(" ")[0], 300)
    return {"total": 50, "totalHits": 50, "hits": [hit(base + i) for i in range(1, n + 1)]}


class Network(RoutingSession):
    """The API and the CDN in one fake. `fail` maps a URL fragment to a response or error."""

    def __init__(self, fail=None):
        self.fail = fail or {}
        super().__init__(self._route)

    def _route(self, url, kwargs):
        for fragment, outcome in self.fail.items():
            if fragment in url or fragment in str(kwargs.get("params", {}).get("q", "")):
                if isinstance(outcome, BaseException):
                    raise outcome
                return outcome
        if url == API_URL:
            return FakeResponse(200, search_body(kwargs["params"]["q"]))
        return FakeResponse(200, content=PNG, headers={"Content-Length": str(len(PNG))})

    @property
    def api_calls(self):
        return [c for c in self.calls if c["url"] == API_URL]

    @property
    def downloads(self):
        return [c for c in self.calls if c["url"] != API_URL]


def picker(*labels, count=1, seen=None):
    """A fake person: picks the first `count` candidates of each of `labels`, then finishes."""

    def serve_fn(session, *, open_browser, announce):
        if seen is not None:
            seen.append({"open_browser": open_browser, "snapshot": session.snapshot()})
        for keyword in session.snapshot()["keywords"]:
            if keyword["label"] in labels:
                for candidate in keyword["candidates"][:count]:
                    session.pick(keyword["label"], candidate["id"])
        session.finish()

    return serve_fn


class Run:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.out, self.err = io.StringIO(), io.StringIO()
        self.zip = tmp_path / "out.zip"
        self.clock = FakeClock()  # one timeline across runs, so a session can grow old

    def keywords(self, text, name="k.txt"):
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def __call__(self, *argv, network=None, serve_fn=None, env=None, stdin=None):
        self.out, self.err = io.StringIO(), io.StringIO()  # this run's output only
        self.network = network or Network()
        args = ["run", *argv]
        if "-o" not in args:
            args += ["-o", str(self.zip)]
        args += ["--work-dir", str(self.tmp / "work"), "--no-browser"]
        self.code = main(
            args,
            env={"PIXABAY_API_KEY": KEY} if env is None else env,
            dotenv_path=self.tmp / "no.env",
            stdin=stdin,
            out=self.out,
            err=self.err,
            http_session=self.network,
            serve_fn=serve_fn or picker("apple", "pear"),
            sleep=self.clock.sleep,
            clock=self.clock,
            wall=self.clock,
        )
        return self.code

    def names(self):
        with zipfile.ZipFile(self.zip) as archive:
            return archive.namelist()

    def read(self, name):
        with zipfile.ZipFile(self.zip) as archive:
            return archive.read(name)


@pytest.fixture
def run(tmp_path):
    return Run(tmp_path)


class TestHappyPath:
    def test_keywords_in_a_zip_of_resized_images_with_credits(self, run):
        assert run(run.keywords("apple\npear\n"), "--size", "32x32") == EXIT_OK
        assert run.names() == ["apple.png", "pear.png", "CREDITS.txt"]
        for name in ("apple.png", "pear.png"):
            assert Image.open(io.BytesIO(run.read(name))).size == (32, 32)
        credits_text = run.read("CREDITS.txt").decode("utf-8")
        assert "Keyword:      apple" in credits_text and "Contributor:  user101" in credits_text
        assert "Wrote" in run.out.getvalue() and "2 images for 2 keywords" in run.out.getvalue()

    def test_progress_is_reported_as_n_of_total(self, run):
        run(run.keywords("apple\npear\n"))
        text = run.out.getvalue()
        assert "  1 / 2  apple" in text and "  2 / 2  pear" in text

    def test_a_label_and_a_different_search_term(self, run):
        run(run.keywords("apple\npear | apple pie\n"))
        assert [c["params"]["q"] for c in run.network.api_calls] == ["apple", "apple pie"]
        assert "pear.png" in run.names()
        assert "Searched for: apple pie" in run.read("CREDITS.txt").decode("utf-8")

    def test_the_browser_is_opened_unless_told_not_to(self, run):
        seen = []
        run(run.keywords("apple\n"), serve_fn=picker("apple", seen=seen))
        assert seen[0]["open_browser"] is False  # the harness always passes --no-browser

    def test_more_than_one_image_per_keyword(self, run):
        assert run(run.keywords("apple\n"), "--multiple", serve_fn=picker("apple", count=2)) == 0
        assert run.names() == ["apple.png", "apple-2.png", "CREDITS.txt"]

    def test_keywords_from_stdin(self, run):
        run("-", stdin=io.StringIO("apple\npear\n"))
        assert run.names()[:2] == ["apple.png", "pear.png"]

    def test_keywords_from_the_command_line(self, run):
        run("-k", "apple", "-k", "pear | pear photo")
        assert [c["params"]["q"] for c in run.network.api_calls] == ["apple", "pear photo"]

    def test_a_csv_with_named_columns(self, run):
        path = run.keywords("word,query\napple,apple\npear,pear\n", "k.csv")
        run(path, "--column", "word", "--term-column", "query")
        assert run.names()[:2] == ["apple.png", "pear.png"]

    def test_jpeg_output(self, run):
        run(run.keywords("apple\n"), "--format", "jpeg", serve_fn=picker("apple"))
        assert run.names()[0] == "apple.jpg"
        assert Image.open(io.BytesIO(run.read("apple.jpg"))).format == "JPEG"


class TestOptionsReachTheQuery:
    def test_every_filter(self, run):
        run(
            run.keywords("apple\n"),
            "-n", "7", "--image-type", "vector", "--orientation", "horizontal",
            "--category", "food", "--colors", "red, blue", "--min-width", "800",
            "--min-height", "600", "--safesearch", "--editors-choice", "--order", "latest",
            "--lang", "pt", "--term-template", "{term} icon",
            serve_fn=picker("apple"),
        )  # fmt: skip
        params = run.network.api_calls[0]["params"]
        assert params["q"] == "apple icon" and params["per_page"] == "7"
        assert params["image_type"] == "vector" and params["orientation"] == "horizontal"
        assert params["category"] == "food" and params["colors"] == "red,blue"
        assert (params["min_width"], params["min_height"]) == ("800", "600")
        assert (params["safesearch"], params["editors_choice"]) == ("true", "true")
        assert (params["order"], params["lang"]) == ("latest", "pt")

    def test_the_resize_options(self, run):
        run(run.keywords("apple\n"), "--size", "64x32", "--mode", "pad", "--background", "#00ff00",
            serve_fn=picker("apple"))  # fmt: skip
        image = Image.open(io.BytesIO(run.read("apple.png")))
        assert image.size == (64, 32)


class TestFailuresBeforeAnythingIsSpent:
    def test_no_api_key(self, run):
        assert run(run.keywords("apple\n"), env={}) == EXIT_ERROR
        assert "winnower: error: No Pixabay API key" in run.err.getvalue()
        assert run.network.calls == []

    @pytest.mark.parametrize(
        "argv", [["--size", "0x0"], ["-n", "2"], ["--colors", "mauve"], ["--term-template", "x"]]
    )
    def test_a_bad_option_is_an_error_not_a_wasted_search(self, run, argv):
        assert run(run.keywords("apple\n"), *argv) == EXIT_ERROR
        assert run.err.getvalue().startswith("winnower: error:")
        assert run.network.calls == []

    def test_a_choice_argparse_knows_is_a_usage_error(self, run):
        with pytest.raises(SystemExit) as caught:
            run(run.keywords("apple\n"), "--mode", "stretch")
        assert caught.value.code == 2

    def test_an_existing_output_is_refused_before_the_first_request(self, run):
        run.zip.write_bytes(b"precious")
        assert run(run.keywords("apple\n")) == EXIT_ERROR
        assert "already exists" in run.err.getvalue() and "--overwrite" in run.err.getvalue()
        assert run.network.calls == [] and run.zip.read_bytes() == b"precious"

    def test_overwrite_replaces_it(self, run):
        run.zip.write_bytes(b"old")
        assert run(run.keywords("apple\n"), "--overwrite", serve_fn=picker("apple")) == EXIT_OK
        assert zipfile.is_zipfile(run.zip)

    @pytest.mark.parametrize("keywords", ["", "# only a comment\n"])
    def test_an_empty_keyword_file(self, run, keywords):
        assert run(run.keywords(keywords)) == EXIT_ERROR
        assert "no keywords found" in run.err.getvalue()

    def test_a_missing_keyword_file(self, run):
        assert run(str(run.tmp / "nope.txt")) == EXIT_ERROR
        assert "not found" in run.err.getvalue()

    def test_no_keywords_at_all_means_the_page_asks_for_them(self, run):
        assert run(serve_fn=lambda session, **kw: None) == EXIT_ERROR  # the person gave none
        assert "the page will ask for them" in run.out.getvalue()
        assert run.network.calls == []

    def test_a_column_option_without_a_file_is_a_mistake_not_a_page(self, run):
        assert run("--column", "word") == EXIT_ERROR
        assert "need a .csv file" in run.err.getvalue()

    def test_a_file_and_dash_k_together(self, run):
        assert run(run.keywords("apple\n"), "-k", "pear") == EXIT_ERROR
        assert "not both" in run.err.getvalue()

    def test_a_bad_dash_k_names_itself(self, run):
        assert run("-k", " | orphan") == EXIT_ERROR
        assert "-k, line 1" in run.err.getvalue()


class TestNoSecrets:
    def test_the_key_appears_in_no_output_and_no_file(self, run, tmp_path):
        run(run.keywords("apple\npear\n"))
        assert KEY not in run.out.getvalue() and KEY not in run.err.getvalue()
        for path in tmp_path.rglob("*"):
            if path.is_file() and path.name != "no.env":
                assert KEY.encode() not in path.read_bytes(), path

    def test_the_key_is_not_a_command_line_option(self, run):
        with pytest.raises(SystemExit) as caught:
            run(run.keywords("apple\n"), "--api-key", KEY)
        assert caught.value.code == 2


class TestWhenThingsGoWrong:
    def test_a_zero_result_keyword_is_reported_and_the_rest_carry_on(self, run):
        run(run.keywords("apple\nnothing here\npear\n"))
        assert "1 found nothing" in run.out.getvalue()
        assert run.names() == ["apple.png", "pear.png", "CREDITS.txt"]

    def test_a_rate_limit_still_opens_the_page_so_the_person_can_retry(self, run):
        seen = []
        network = Network({"pear": FakeResponse(429)})
        code = run(run.keywords("apple\npear\nplum\n"), network=network,
                   serve_fn=picker("apple", seen=seen))  # fmt: skip
        assert code == EXIT_OK and len(seen) == 1
        statuses = [k["status"] for k in seen[0]["snapshot"]["keywords"]]
        assert statuses == ["found", "failed", "pending"]
        assert "rate limit" in run.err.getvalue()
        assert seen[0]["snapshot"]["stopped"]

    def test_nothing_picked_is_an_error(self, run):
        assert run(run.keywords("apple\n"), serve_fn=lambda s, **kw: None) == EXIT_ERROR
        assert "Nothing was picked" in run.err.getvalue()
        assert not run.zip.exists()

    def test_one_failed_download_still_writes_the_zip_and_says_what_is_missing(self, run):
        network = Network({"/get/201_1280.jpg": FakeResponse(403)})
        code = run(run.keywords("apple\npear\n"), network=network)
        assert code == EXIT_PARTIAL
        assert run.names() == ["apple.png", "CREDITS.txt"]
        assert "NOT in the zip" in run.err.getvalue() and "pear: " in run.err.getvalue()
        assert "HTTP 403" in run.err.getvalue()

    def test_every_download_failing_is_an_error(self, run):
        network = Network({"/get/": FakeResponse(403)})
        assert run(run.keywords("apple\n"), network=network) == EXIT_ERROR
        assert "None of the picked images" in run.err.getvalue()
        assert not run.zip.exists()

    def test_a_dropped_network_during_download_is_a_message_not_a_traceback(self, run):
        network = Network({"/get/": requests.ConnectionError("down")})
        assert run(run.keywords("apple\n"), network=network) == EXIT_ERROR
        assert "Traceback" not in run.err.getvalue()

    def test_ctrl_c_is_a_clean_exit(self, run):
        def interrupt(session, **kw):
            raise KeyboardInterrupt

        assert run(run.keywords("apple\n"), serve_fn=interrupt) == EXIT_INTERRUPTED
        assert "interrupted" in run.err.getvalue()

    def test_repeated_keywords_are_reported(self, run):
        run(run.keywords("apple\nApple\npear\n"))
        assert "Ignored 1 repeated keyword" in run.err.getvalue()
        assert len(run.network.api_calls) == 2


class TestCaching:
    def test_a_second_run_of_the_same_list_spends_no_api_requests_and_no_downloads(self, run):
        path = run.keywords("apple\npear\n")
        run(path)
        first = len(run.network.api_calls), len(run.network.downloads)
        assert first == (2, 2)
        run(path, "--overwrite")
        assert (len(run.network.api_calls), len(run.network.downloads)) == (0, 0)
        assert run.names() == ["apple.png", "pear.png", "CREDITS.txt"]


def test_version(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["--version"])
    assert caught.value.code == 0
    assert __version__ in capsys.readouterr().out


def interrupt_after_picking(*labels):
    """A person who picks some images and then closes the terminal."""

    def serve_fn(session, *, open_browser, announce):
        for keyword in session.snapshot()["keywords"]:
            if keyword["label"] in labels:
                session.pick(keyword["label"], keyword["candidates"][0]["id"])
        raise KeyboardInterrupt

    return serve_fn


def not_called(session, **kwargs):
    raise AssertionError("the page should not have been opened")


class TestSessions:
    def interrupted(self, run, text="apple\npear\n"):
        path = run.keywords(text)
        assert run(path, serve_fn=interrupt_after_picking("apple")) == EXIT_INTERRUPTED
        return path

    def test_an_interrupted_run_saves_its_picks_and_says_how_to_continue(self, run):
        self.interrupted(run)
        assert "--resume" in run.err.getvalue() and "picks are saved" in run.err.getvalue()
        assert (run.tmp / "work" / "session.json").is_file()
        assert not run.zip.exists()

    def test_resume_picks_up_where_it_stopped_without_spending_a_request(self, run):
        self.interrupted(run)
        code = run("--resume", serve_fn=picker("pear"))
        assert code == EXIT_OK
        assert run.network.api_calls == []
        assert run.names() == ["apple.png", "pear.png", "CREDITS.txt"]
        assert "Resuming the saved session: 1 of 2 keywords done" in run.out.getvalue()

    def test_the_page_comes_back_as_it_was_left(self, run):
        self.interrupted(run)
        seen = []
        run("--resume", serve_fn=picker("pear", seen=seen))
        apple = next(k for k in seen[0]["snapshot"]["keywords"] if k["label"] == "apple")
        assert len(apple["picked"]) == 1 and apple["done"]

    def test_a_new_run_will_not_silently_replace_an_unfinished_session(self, run):
        path = self.interrupted(run)
        assert run(path) == EXIT_ERROR
        message = run.err.getvalue()
        assert "unfinished session" in message and "1 of 2 keywords done" in message
        assert "--resume" in message and "--restart" in message
        assert run.network.calls == []  # refused before a request was spent

    def test_restart_throws_it_away_and_starts_over(self, run):
        path = self.interrupted(run)
        assert run(path, "--restart", serve_fn=picker("pear")) == EXIT_OK
        assert run.names() == ["pear.png", "CREDITS.txt"]  # apple's old pick is gone

    def test_a_completed_session_does_not_block_the_next_run(self, run):
        path = run.keywords("apple\n")
        assert run(path, serve_fn=picker("apple")) == EXIT_OK
        saved = json.loads((run.tmp / "work" / "session.json").read_text(encoding="utf-8"))
        assert saved["completed"] is True
        assert run(path, "--overwrite", serve_fn=picker("apple")) == EXIT_OK

    def test_resuming_a_completed_session_is_refused(self, run):
        run(run.keywords("apple\n"), serve_fn=picker("apple"))
        assert run("--resume", "--overwrite") == EXIT_ERROR
        assert "already complete" in run.err.getvalue()

    def test_resume_with_nothing_saved(self, run):
        assert run("--resume") == EXIT_ERROR
        assert "no saved session" in run.err.getvalue()

    def test_resume_takes_its_keywords_from_the_session_not_the_command_line(self, run):
        path = self.interrupted(run)
        assert run(path, "--resume") == EXIT_ERROR
        assert "reads the keywords from the saved session" in run.err.getvalue()
        assert run("-k", "kiwi", "--resume") == EXIT_ERROR

    def test_resume_and_restart_together_contradict(self, run):
        assert run("--resume", "--restart") == EXIT_ERROR
        assert "contradict" in run.err.getvalue()

    def test_a_finished_session_whose_zip_failed_goes_straight_to_the_download(self, run):
        path = run.keywords("apple\n")
        broken = Network({"/get/": FakeResponse(403)})
        assert run(path, network=broken, serve_fn=picker("apple")) == EXIT_ERROR
        assert not run.zip.exists()
        # The network is back. Nothing should need picking again.
        assert run("--resume", serve_fn=not_called) == EXIT_OK
        assert "going straight to the download" in run.out.getvalue()
        assert run.names() == ["apple.png", "CREDITS.txt"]

    def test_a_session_over_a_day_old_still_resumes_with_a_warning(self, run):
        self.interrupted(run)
        run.clock.advance(2 * 86400)
        assert run("--resume", serve_fn=picker("pear")) == EXIT_OK
        assert "over a day old" in run.err.getvalue()

    def test_a_session_from_this_morning_does_not_warn(self, run):
        self.interrupted(run)
        run.clock.advance(3600)
        run("--resume", serve_fn=picker("pear"))
        assert "over a day old" not in run.err.getvalue()

    def test_retries_made_on_the_page_survive_a_resume(self, run):
        path = run.keywords("apple\n")

        def retry_then_quit(session, *, open_browser, announce):
            session.retry("apple", term="apple pie")
            raise KeyboardInterrupt

        assert run(path, serve_fn=retry_then_quit) == EXIT_INTERRUPTED
        seen = []
        run("--resume", serve_fn=picker("apple", seen=seen))
        assert seen[0]["snapshot"]["keywords"][0]["query"] == "apple pie"

    def test_a_damaged_session_file_is_an_error_with_a_way_out(self, run):
        path = self.interrupted(run)
        (run.tmp / "work" / "session.json").write_text("{ not json", encoding="utf-8")
        assert run("--resume") == EXIT_ERROR
        assert "--restart" in run.err.getvalue()
        assert run(path, "--restart", serve_fn=picker("apple")) == EXIT_OK

    def test_the_session_file_never_holds_the_api_key(self, run):
        self.interrupted(run)
        text = (run.tmp / "work" / "session.json").read_text(encoding="utf-8")
        assert KEY not in text

    def test_ctrl_c_before_anything_is_saved_says_so(self, run):
        network = Network({"apple": KeyboardInterrupt()})
        assert run(run.keywords("apple\n"), network=network) == EXIT_INTERRUPTED
        assert "Nothing had been saved" in run.err.getvalue()

    def test_a_session_that_cannot_be_saved_does_not_stop_the_run(self, run, monkeypatch):
        def failing_save(self, state, completed=False):
            self.error = "disk full"

        monkeypatch.setattr("winnower.session_store.SessionStore.save", failing_save)
        assert run(run.keywords("apple\n"), serve_fn=picker("apple")) == EXIT_OK
        assert "Could not save the session file (disk full)" in run.err.getvalue()


class TestSessionEdges:
    def quit_at_once(self, session, *, open_browser, announce):
        raise KeyboardInterrupt

    def test_closing_the_page_before_the_first_click_still_keeps_the_search(self, run):
        """The search is the expensive part; it is saved before the page opens."""
        path = run.keywords("apple\npear\n")
        assert run(path, serve_fn=self.quit_at_once) == EXIT_INTERRUPTED
        assert (run.tmp / "work" / "session.json").is_file()
        assert "picks are saved" in run.err.getvalue()
        assert run("--resume", serve_fn=picker("apple", "pear")) == EXIT_OK
        assert run.network.api_calls == []

    def test_restart_forgets_the_old_session_even_if_the_new_run_is_cut_short(self, run):
        path = run.keywords("apple\npear\n")
        run(path, serve_fn=interrupt_after_picking("apple"))
        # A keyword that is not in the 24 h cache, so the search really is attempted.
        fresh = run.keywords("kiwi\n", "fresh.txt")
        cut_short = Network({"kiwi": KeyboardInterrupt()})
        assert run(fresh, "--restart", network=cut_short) == EXIT_INTERRUPTED
        assert not (run.tmp / "work" / "session.json").exists()
        assert "Nothing had been saved" in run.err.getvalue()

    def test_restart_also_clears_a_damaged_file_if_the_new_run_is_cut_short(self, run):
        path = run.keywords("apple\n")
        session_file = run.tmp / "work" / "session.json"
        session_file.parent.mkdir(parents=True)
        session_file.write_text("{ not json", encoding="utf-8")
        cut_short = Network({"apple": KeyboardInterrupt()})  # nothing cached in a fresh dir
        assert run(path, "--restart", network=cut_short) == EXIT_INTERRUPTED
        assert not session_file.exists()

    def test_ctrl_c_does_not_claim_picks_are_saved_because_of_an_earlier_runs_file(self, run):
        run(run.keywords("apple\n"), serve_fn=picker("apple"))  # leaves a completed session
        fresh = run.keywords("kiwi\n", "fresh.txt")
        network = Network({"kiwi": KeyboardInterrupt()})
        assert run(fresh, "--overwrite", network=network) == EXIT_INTERRUPTED
        assert "Nothing had been saved" in run.err.getvalue()
        assert "picks are saved" not in run.err.getvalue()

    def test_a_session_that_is_json_but_makes_no_sense_points_at_restart_too(self, run):
        path = run.keywords("apple\npear\n")
        run(path, serve_fn=interrupt_after_picking("apple"))
        session_file = run.tmp / "work" / "session.json"
        saved = json.loads(session_file.read_text(encoding="utf-8"))
        saved["state"]["entries"][0]["picked"] = [999999]  # not one of its candidates
        session_file.write_text(json.dumps(saved), encoding="utf-8")
        assert run("--resume") == EXIT_ERROR
        assert "cannot be used" in run.err.getvalue() and "--restart" in run.err.getvalue()
        assert run(path, "--restart", serve_fn=picker("apple")) == EXIT_OK


def pastes(text, *labels, count=1):
    """A person who pastes `text` into the page, waits for the search, then picks and finishes."""

    def serve_fn(session, *, open_browser, announce):
        assert session.phase == "start"
        session.start(text)
        assert session.wait_searched(10), "the background search did not finish"
        for keyword in session.snapshot()["keywords"]:
            if keyword["label"] in labels:
                for candidate in keyword["candidates"][:count]:
                    session.pick(keyword["label"], candidate["id"])
        session.finish()

    return serve_fn


class TestKeywordsFromThePage:
    def test_pasted_keywords_go_through_the_whole_pipeline(self, run):
        code = run(serve_fn=pastes("apple\npear | pear photo\n", "apple", "pear"))
        assert code == EXIT_OK
        assert [c["params"]["q"] for c in run.network.api_calls] == ["apple", "pear photo"]
        assert run.names() == ["apple.png", "pear.png", "CREDITS.txt"]

    def test_the_search_runs_behind_the_page_with_progress(self, run):
        seen = []

        def serve_fn(session, *, open_browser, announce):
            seen.append(session.snapshot())
            session.start("apple\npear\n")
            seen.append(session.snapshot())
            assert session.wait_searched(10)
            seen.append(session.snapshot())
            for keyword in session.snapshot()["keywords"]:
                session.pick(keyword["label"], keyword["candidates"][0]["id"])
            session.finish()

        assert run(serve_fn=serve_fn) == EXIT_OK
        before, during, after = seen
        assert before["phase"] == "start" and before["total"] == 0
        assert during["phase"] in ("searching", "picking") and during["total"] == 2
        assert after["phase"] == "picking" and after["searching"] is None
        assert "  2 / 2  pear" in run.out.getvalue()

    def test_every_keyword_is_on_the_page_at_once_as_not_searched(self, run):
        network = Network()
        release, reached = threading.Event(), threading.Event()
        inner = network._route

        def gated(url, kwargs):
            if url == API_URL and kwargs["params"]["q"] == "pear":
                reached.set()
                assert release.wait(10)
            return inner(url, kwargs)

        network.handler = gated
        states = []

        def serve_fn(session, *, open_browser, announce):
            session.start("apple\npear\nplum\n")
            assert reached.wait(10)  # apple is done, pear is in flight, plum has not started
            states.append(session.snapshot())
            release.set()
            assert session.wait_searched(10)
            for keyword in session.snapshot()["keywords"]:
                session.pick(keyword["label"], keyword["candidates"][0]["id"])
            session.finish()

        assert run(network=network, serve_fn=serve_fn) == EXIT_OK
        mid = states[0]
        assert mid["phase"] == "searching" and mid["searching"] == {"done": 1, "total": 3}
        assert [k["status"] for k in mid["keywords"]] == ["found", "pending", "pending"]
        assert run.names() == ["apple.png", "pear.png", "plum.png", "CREDITS.txt"]

    def test_an_unusable_paste_is_refused_with_a_reason_and_the_box_stays_open(self, run):
        outcome = {}

        def serve_fn(session, *, open_browser, announce):
            for bad in ("", "# just a comment\n", "  | orphan\n"):
                try:
                    session.start(bad)
                except SelectionError as err:
                    outcome[bad] = str(err)
            outcome["phase"] = session.phase
            session.start("apple\n")
            assert session.wait_searched(10)
            session.pick("apple", session.snapshot()["keywords"][0]["candidates"][0]["id"])
            session.finish()

        assert run(serve_fn=serve_fn) == EXIT_OK
        assert (
            "no keywords found" in outcome[""]
            and "no keywords found" in outcome["# just a comment\n"]
        )
        assert "nothing before" in outcome["  | orphan\n"]
        assert outcome["phase"] == "start"

    def test_the_box_can_only_be_used_once(self, run):
        def serve_fn(session, *, open_browser, announce):
            session.start("apple\n")
            try:
                session.start("pear\n")
            except SelectionError as err:
                assert "already been given" in str(err)
            else:
                raise AssertionError("a second paste was accepted")
            assert session.wait_searched(10)
            session.pick("apple", session.snapshot()["keywords"][0]["candidates"][0]["id"])
            session.finish()

        assert run(serve_fn=serve_fn) == EXIT_OK
        assert [c["params"]["q"] for c in run.network.api_calls] == ["apple"]

    def test_a_repeated_keyword_is_noted_on_the_page(self, run):
        notes = []

        def serve_fn(session, *, open_browser, announce):
            session.start("apple\nApple\npear\n")
            assert session.wait_searched(10)
            notes.extend(session.snapshot()["notes"])
            for keyword in session.snapshot()["keywords"]:
                session.pick(keyword["label"], keyword["candidates"][0]["id"])
            session.finish()

        run(serve_fn=serve_fn)
        assert notes and "repeated keyword" in notes[0]

    def test_finishing_is_refused_while_the_search_is_still_running(self, run):
        network = Network()
        release, reached = threading.Event(), threading.Event()
        inner = network._route

        def gated(url, kwargs):
            if url == API_URL and kwargs["params"]["q"] == "pear":
                reached.set()
                assert release.wait(10)
            return inner(url, kwargs)

        network.handler = gated
        refused = []

        def serve_fn(session, *, open_browser, announce):
            session.start("apple\npear\n")
            assert reached.wait(10)
            session.pick("apple", session.snapshot()["keywords"][0]["candidates"][0]["id"])
            try:
                session.finish()
            except SelectionError as err:
                refused.append(str(err))
            try:
                session.retry("pear", term="pear photo")  # still waiting for its turn
            except SelectionError as err:
                refused.append(str(err))
            release.set()
            assert session.wait_searched(10)
            session.finish()

        assert run(network=network, serve_fn=serve_fn) == EXIT_OK
        assert "still running" in refused[0] and "already being searched" in refused[1]

    def test_a_rate_limit_during_the_background_search_pauses_it_and_says_so(self, run):
        network = Network({"pear": FakeResponse(429)})
        seen = []

        def serve_fn(session, *, open_browser, announce):
            session.start("apple\npear\nplum\n")
            assert session.wait_searched(10)
            seen.append(session.snapshot())
            session.pick("apple", session.snapshot()["keywords"][0]["candidates"][0]["id"])
            session.finish()

        assert run(network=network, serve_fn=serve_fn) == EXIT_OK
        snap = seen[0]
        assert [k["status"] for k in snap["keywords"]] == ["found", "failed", "pending"]
        assert "rate limit" in snap["stopped"]

    def test_closing_the_terminal_mid_search_keeps_what_was_found_and_resumes(self, run):
        network = Network()
        release, reached = threading.Event(), threading.Event()
        inner = network._route

        def gated(url, kwargs):
            if url == API_URL and kwargs["params"]["q"] == "pear":
                reached.set()
                assert release.wait(10)
            return inner(url, kwargs)

        network.handler = gated

        def close_the_terminal(session, *, open_browser, announce):
            session.start("apple\npear\nplum\n")
            assert reached.wait(10)
            raise KeyboardInterrupt

        try:
            assert run(network=network, serve_fn=close_the_terminal) == EXIT_INTERRUPTED
            assert "picks are saved" in run.err.getvalue()
            seen = []

            def resumed(session, *, open_browser, announce):
                snap = session.snapshot()
                seen.append(snap)
                for label in ("pear", "plum"):
                    session.retry(label)  # the not-yet-searched ones have a Search button
                for keyword in session.snapshot()["keywords"]:
                    session.pick(keyword["label"], keyword["candidates"][0]["id"])
                session.finish()

            assert run("--resume", serve_fn=resumed) == EXIT_OK
        finally:
            release.set()  # let the abandoned search thread end
        snap = seen[0]
        assert snap["phase"] == "picking"
        assert [k["status"] for k in snap["keywords"]] == ["found", "pending", "pending"]
        assert "search was interrupted" in snap["stopped"]
        assert run.names() == ["apple.png", "pear.png", "plum.png", "CREDITS.txt"]

    def test_a_crash_in_the_background_search_is_reported_on_the_page_not_left_hanging(self, run):
        class Boom(Network):
            def _route(self, url, kwargs):
                raise RuntimeError("a bug")

        seen = []

        def serve_fn(session, *, open_browser, announce):
            session.start("apple\n")
            assert session.wait_searched(10), "the page would wait forever"
            seen.append(session.snapshot())
            raise KeyboardInterrupt

        run(network=Boom(), serve_fn=serve_fn)
        assert "stopped unexpectedly" in seen[0]["stopped"] and seen[0]["phase"] == "picking"
