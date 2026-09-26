"""The selection page's HTTP contract, through the real server on an ephemeral port."""

import http.client
import json
import re
import threading
from importlib import resources

import pytest
from fakes import hit

from winnower.keywords import Keyword
from winnower.pixabay_client import parse_response
from winnower.search_runner import Outcome, Status
from winnower.selection import SelectionSession
from winnower.selector_ui import MAX_BODY, TOKEN_HEADER, SelectorServer, serve


def found(label, ids, page=1, query=None, **hit_overrides):
    body = {"total": 100, "totalHits": 100, "hits": [hit(i, **hit_overrides) for i in ids]}
    result = parse_response(body, "x", page, 3)
    return Outcome(Keyword(label, label), Status.FOUND, query or label, result)


class Live:
    """A running server and a tiny client for it."""

    def __init__(self, session):
        self.session = session
        self.server = SelectorServer(session)
        self.server.start()
        self.port = self.server.server_address[1]
        self.token = self.server.token

    def call(self, method, path, body=None, headers=None, *, token=True, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        sent = dict(headers or {})
        if token:
            sent.setdefault(TOKEN_HEADER, self.token)
        payload = raw if raw is not None else (json.dumps(body) if body is not None else None)
        if payload is not None:
            sent.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=payload, headers=sent)
        response = conn.getresponse()
        data = response.read()
        headers_back = {k.lower(): v for k, v in response.getheaders()}
        conn.close()
        return response.status, headers_back, data

    def json(self, method, path, body=None, **kw):
        status, headers, data = self.call(method, path, body, **kw)
        return status, json.loads(data)

    def stop(self):
        self.server.stop()


@pytest.fixture
def live():
    made = []

    def start(*outcomes, search=None, **kw):
        item = Live(SelectionSession(list(outcomes) or [found("apple", [1, 2, 3])], search, **kw))
        made.append(item)
        return item

    yield start
    for item in made:
        item.stop()


class TestPageAndStatics:
    def test_the_page_is_served_with_the_token_and_a_strict_policy(self, live):
        app = live()
        status, headers, body = app.call("GET", f"/?t={app.token}", token=False)
        assert status == 200 and headers["content-type"].startswith("text/html")
        assert b"Winnower" in body and b"/static/selector.js" in body
        policy = headers["content-security-policy"]
        assert "script-src 'self'" in policy and "default-src 'none'" in policy
        assert headers["cache-control"] == "no-store"
        assert headers["x-content-type-options"] == "nosniff"

    @pytest.mark.parametrize("query", ["", "?t=", "?t=wrong", "?token=x"])
    def test_the_page_is_refused_without_the_right_token(self, live, query):
        status, _, _ = live().call("GET", "/" + query, token=False)
        assert status == 403

    def test_static_files(self, live):
        app = live()
        css = app.call("GET", "/static/selector.css", token=False)
        js = app.call("GET", "/static/selector.js", token=False)
        assert css[0] == 200 and css[1]["content-type"].startswith("text/css")
        assert js[0] == 200 and js[1]["content-type"].startswith("text/javascript")

    @pytest.mark.parametrize(
        "path",
        [
            "/static/../pyproject.toml",
            "/static/selector.py",
            "/static/",
            "/nope",
            "/api/nope",
            "/static/%2e%2e/selector.js",
            "/../etc/passwd",
        ],
    )
    def test_nothing_else_is_served(self, live, path):
        status, _, _ = live().call("GET", path)
        assert status == 404

    def test_the_page_never_builds_html_from_strings(self):
        """Contributor names and tags come from Pixabay; the page must only use textContent."""
        script = resources.files("winnower").joinpath("static", "selector.js").read_text("utf-8")
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            assert banned not in script

    def test_the_finish_panel_does_not_share_a_class_with_completed_keywords(self):
        """Found in the browser: a `.done` rule meant for the finish panel also centred and
        re-padded every keyword section that had a pick, because sections use `.done` too."""
        styles = resources.files("winnower").joinpath("static", "selector.css").read_text("utf-8")
        assert not re.search(r"(^|[\s,])\.done\s*[{,]", styles, re.M)


class TestTheServerIsNotOpen:
    def test_it_listens_on_loopback_only(self, live):
        assert live().server.server_address[0] == "127.0.0.1"

    @pytest.mark.parametrize("path", ["/api/state"])
    def test_reads_need_the_token(self, live, path):
        app = live()
        assert app.call("GET", path, token=False)[0] == 403
        assert app.call("GET", path, headers={TOKEN_HEADER: "wrong"}, token=False)[0] == 403
        assert app.call("GET", path)[0] == 200

    @pytest.mark.parametrize("path", ["/api/pick", "/api/skip", "/api/retry", "/api/finish"])
    def test_writes_need_the_token(self, live, path):
        app = live()
        status, _, _ = app.call("POST", path, {"label": "apple", "id": 1}, token=False)
        assert status == 403
        assert app.session.snapshot()["picks"] == 0

    def test_a_foreign_host_header_is_refused_even_with_the_token(self, live):
        """DNS rebinding: the browser thinks evil.example is same-origin and sends the token."""
        app = live()
        assert app.call("GET", "/api/state", headers={"Host": "evil.example"})[0] == 403
        assert (
            app.call("GET", f"/?t={app.token}", headers={"Host": "evil.example:1"}, token=False)[0]
            == 403
        )
        assert (
            app.call("POST", "/api/skip", {"label": "apple"}, headers={"Host": "evil.example"})[0]
            == 403
        )
        assert not app.session.snapshot()["keywords"][0]["skipped"]

    def test_localhost_by_name_is_accepted(self, live):
        app = live()
        assert app.call("GET", "/api/state", headers={"Host": f"localhost:{app.port}"})[0] == 200

    def test_no_cors_headers_are_ever_sent(self, live):
        app = live()
        for method, path in [("GET", "/api/state"), ("POST", "/api/skip")]:
            _, headers, _ = app.call(method, path, {"label": "apple"})
            assert not any(name.startswith("access-control-") for name in headers)

    def test_an_options_preflight_gets_nothing(self, live):
        status, headers, _ = live().call("OPTIONS", "/api/skip", token=False)
        assert status >= 400 and "access-control-allow-origin" not in headers

    def test_the_token_never_reaches_the_log(self, live, capfd):
        app = live()
        app.call("GET", f"/?t={app.token}", token=False)
        app.call("GET", "/api/state")
        captured = capfd.readouterr()
        assert app.token not in captured.err and app.token not in captured.out
        assert captured.err == ""

    def test_tokens_differ_between_runs(self, live):
        assert live().token != live().token


class TestApi:
    def test_state(self, live):
        status, data = live(found("apple", [1, 2, 3])).json("GET", "/api/state")
        assert status == 200
        assert (data["total"], data["done"], data["picks"], data["finished"]) == (1, 0, 0, False)
        assert [c["id"] for c in data["keywords"][0]["candidates"]] == [1, 2, 3]

    def test_pick_returns_the_new_state(self, live):
        app = live()
        status, data = app.json("POST", "/api/pick", {"label": "apple", "id": 2})
        assert status == 200 and data["keywords"][0]["picked"] == [2] and data["done"] == 1
        assert app.session.selections()[0].candidates[0].id == 2

    def test_unpick(self, live):
        app = live()
        app.json("POST", "/api/pick", {"label": "apple", "id": 2})
        _, data = app.json("POST", "/api/pick", {"label": "apple", "id": 2, "selected": False})
        assert data["keywords"][0]["picked"] == []

    def test_skip_and_undo(self, live):
        app = live()
        _, data = app.json("POST", "/api/skip", {"label": "apple"})
        assert data["keywords"][0]["skipped"] and data["done"] == 1
        _, data = app.json("POST", "/api/skip", {"label": "apple", "skipped": False})
        assert not data["keywords"][0]["skipped"]

    def test_retry_with_a_new_term_and_the_next_page(self, live):
        calls = []

        def search(keyword, query, page):
            calls.append((query, page))
            return found("apple", [10 + page, 20 + page, 30 + page], page=page, query=query)

        app = live(found("apple", [1, 2, 3]), search=search)
        _, data = app.json("POST", "/api/retry", {"label": "apple", "term": "green apple"})
        assert data["keywords"][0]["query"] == "green apple" and calls == [("green apple", 1)]
        _, data = app.json("POST", "/api/retry", {"label": "apple", "next_page": True})
        assert calls[-1] == ("green apple", 2)
        assert len(data["keywords"][0]["candidates"]) == 6

    def test_finish_releases_the_run(self, live):
        app = live()
        app.json("POST", "/api/pick", {"label": "apple", "id": 1})
        assert not app.session.finished
        status, data = app.json("POST", "/api/finish", {})
        assert status == 200 and data["finished"] and app.session.wait(1)

    def test_finishing_with_nothing_picked_is_a_conflict_not_a_crash(self, live):
        app = live()
        status, data = app.json("POST", "/api/finish", {})
        assert status == 409 and "at least one" in data["error"] and not app.session.finished

    @pytest.mark.parametrize(
        ("path", "body"),
        [
            ("/api/pick", {"label": "nope", "id": 1}),
            ("/api/pick", {"label": "apple", "id": 99}),
            ("/api/skip", {"label": "nope"}),
            ("/api/retry", {"label": "apple", "term": "x"}),
        ],
    )
    def test_requests_that_make_no_sense_are_409_with_a_reason(self, live, path, body):
        status, data = live().json("POST", path, body)  # no search function: retry is refused
        assert status == 409 and data["error"]

    @pytest.mark.parametrize(
        ("path", "body"),
        [
            ("/api/pick", {}),
            ("/api/pick", {"label": "apple"}),
            ("/api/pick", {"label": "apple", "id": "x"}),
            ("/api/skip", {"skipped": True}),
        ],
    )
    def test_missing_or_mistyped_fields_are_400(self, live, path, body):
        status, data = live().json("POST", path, body)
        assert status == 400 and data["error"]

    def test_a_body_that_is_not_json_is_400(self, live):
        status, _, _ = live().call("POST", "/api/pick", raw="{not json")
        assert status == 400

    def test_a_body_that_is_not_an_object_is_400(self, live):
        status, _, _ = live().call("POST", "/api/pick", raw="[1, 2]")
        assert status == 400

    def test_an_oversized_body_is_413(self, live):
        status, _, _ = live().call("POST", "/api/pick", raw="x" * (MAX_BODY + 1))
        assert status == 413

    def test_the_server_survives_a_bad_request(self, live):
        app = live()
        app.call("POST", "/api/pick", raw="{not json")
        assert app.call("GET", "/api/state")[0] == 200

    def test_markup_in_a_contributor_name_travels_as_data(self, live):
        payload = '<img src=x onerror="alert(1)">'
        app = live(found("apple", [1, 2, 3], user=payload, tags="<b>bold</b>, x"))
        _, data = app.json("GET", "/api/state")
        candidate = data["keywords"][0]["candidates"][0]
        assert candidate["user"] == payload and candidate["tags"][0] == "<b>bold</b>"

    def test_concurrent_picks_do_not_corrupt_the_state(self, live):
        app = live(*(found(f"k{i}", [1, 2, 3]) for i in range(8)))
        errors = []

        def work(i):
            try:
                status, _ = app.json("POST", "/api/pick", {"label": f"k{i}", "id": 2})
                assert status == 200
            except Exception as err:  # pragma: no cover - would be reported below
                errors.append(err)

        threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
        [t.start() for t in threads]
        [t.join(10) for t in threads]
        assert errors == [] and app.session.snapshot()["picks"] == 8


class TestServe:
    def test_serve_blocks_until_finish_then_shuts_the_server_down(self):
        session = SelectionSession([found("apple", [1, 2, 3])])
        said = []
        thread = threading.Thread(
            target=lambda: serve(session, open_browser=False, announce=said.append), daemon=True
        )
        thread.start()
        for _ in range(200):
            if said:
                break
            threading.Event().wait(0.05)
        match = re.search(r"http://127\.0\.0\.1:(\d+)/\?t=(\S+)", said[0])
        assert match, said
        port, token = int(match[1]), match[2]

        def post(path, body):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request("POST", path, json.dumps(body), {TOKEN_HEADER: token})
            status = conn.getresponse().status
            conn.close()
            return status

        assert post("/api/pick", {"label": "apple", "id": 1}) == 200
        assert thread.is_alive()
        assert post("/api/finish", {}) == 200
        thread.join(10)
        assert not thread.is_alive()
        with pytest.raises(OSError):
            http.client.HTTPConnection("127.0.0.1", port, timeout=2).request("GET", "/")
