"""The pipeline end to end: real stages, fake network, and a fake person in place of the page."""

import io
import zipfile

import pytest
import requests
from fakes import FakeClock, FakeResponse, RoutingSession, hit, png_bytes
from PIL import Image

from winnower import __version__
from winnower.cli import EXIT_ERROR, EXIT_INTERRUPTED, EXIT_OK, EXIT_PARTIAL, main
from winnower.pixabay_client import API_URL

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

    def keywords(self, text, name="k.txt"):
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def __call__(self, *argv, network=None, serve_fn=None, env=None, stdin=None):
        self.network = network or Network()
        self.clock = FakeClock()
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

    def test_no_keywords_at_all(self, run):
        assert run() == EXIT_ERROR
        assert "Give a keyword file" in run.err.getvalue()

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
