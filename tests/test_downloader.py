import io

import pytest
import requests
from fakes import FakeClock, FakeResponse, FakeSession, hit, png_bytes
from PIL import Image

from winnower.downloader import MAX_BYTES, Downloader, DownloadError
from winnower.pixabay_client import Candidate, parse_response

PNG = png_bytes((40, 30))


def candidate(n=7, **overrides):
    body = {"total": 1, "totalHits": 1, "hits": [hit(n, **overrides)]}
    return parse_response(body, "x", 1, 3).candidates[0]


def ok(content=PNG, **kwargs):
    return FakeResponse(
        200, content=content, headers={"Content-Length": str(len(content))}, **kwargs
    )


def make(tmp_path, script=None, **kwargs):
    clock = FakeClock()
    session = FakeSession(script or [ok()])
    return (
        Downloader(tmp_path / "originals", session=session, sleep=clock.sleep, **kwargs),
        session,
        clock,
    )


def leftovers(directory):
    return sorted(p.name for p in directory.iterdir())


class TestFetch:
    def test_downloads_the_large_image_into_the_cache(self, tmp_path):
        downloader, session, _ = make(tmp_path)
        path = downloader.fetch(candidate(7))
        assert path == tmp_path / "originals" / "7-large.png"
        assert Image.open(path).size == (40, 30)
        assert session.calls[0]["url"] == "https://pixabay.com/get/7_1280.jpg"
        assert session.calls[0]["headers"]["User-Agent"].startswith("winnower/")

    def test_a_cached_original_is_returned_without_a_request(self, tmp_path):
        downloader, session, _ = make(tmp_path)
        first = downloader.fetch(candidate())
        assert downloader.fetch(candidate()) == first
        assert len(session.calls) == 1

    def test_the_cache_survives_a_new_downloader(self, tmp_path):
        make(tmp_path)[0].fetch(candidate())
        downloader, session, _ = make(tmp_path)
        downloader.fetch(candidate())
        assert session.calls == []

    def test_the_extension_is_what_the_bytes_are_not_what_the_url_says(self, tmp_path):
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(buffer, format="JPEG")
        downloader, _, _ = make(tmp_path, [ok(buffer.getvalue())])
        assert downloader.fetch(candidate()).suffix == ".jpg"  # url said _1280.jpg either way
        downloader, _, _ = make(tmp_path / "b", [ok(PNG)])
        assert (
            downloader.fetch(candidate(largeImageURL="https://pixabay.com/get/7_1280.jpg")).suffix
            == ".png"
        )

    def test_no_temp_files_are_left_behind(self, tmp_path):
        downloader, _, _ = make(tmp_path)
        downloader.fetch(candidate())
        assert leftovers(tmp_path / "originals") == ["7-large.png"]


class TestFullSize:
    def test_uses_image_url_when_asked_and_present(self, tmp_path):
        downloader, session, _ = make(tmp_path, full_size=True)
        path = downloader.fetch(candidate(imageURL="https://pixabay.com/get/7_full.png"))
        assert session.calls[0]["url"].endswith("7_full.png")
        assert path.name == "7-full.png"

    def test_falls_back_to_large_for_accounts_without_full_access(self, tmp_path):
        downloader, session, _ = make(tmp_path, full_size=True)
        assert downloader.fetch(candidate()).name == "7-large.png"
        assert session.calls[0]["url"].endswith("7_1280.jpg")

    def test_the_two_sizes_are_different_cache_entries(self, tmp_path):
        with_full = candidate(imageURL="https://pixabay.com/get/7_full.png")
        make(tmp_path)[0].fetch(with_full)  # large first
        downloader, session, _ = make(tmp_path, full_size=True)
        downloader.fetch(with_full)
        assert len(session.calls) == 1  # the cached large did not answer for full


class TestFailuresNeverLeaveAFileBehind:
    """Three failures of the library the spec named, as tests. See downloader.py."""

    def test_an_error_page_is_not_saved_as_an_image(self, tmp_path):
        html = b"<html>Forbidden</html>"
        downloader, _, _ = make(tmp_path, [FakeResponse(200, content=html)])
        with pytest.raises(DownloadError, match="not a complete image"):
            downloader.fetch(candidate())
        assert leftovers(tmp_path / "originals") == []

    def test_a_403_is_reported_with_its_status_and_not_retried(self, tmp_path):
        downloader, session, _ = make(tmp_path, [FakeResponse(403, content=b"<html>")])
        with pytest.raises(DownloadError, match="HTTP 403"):
            downloader.fetch(candidate())
        assert len(session.calls) == 1
        assert leftovers(tmp_path / "originals") == []

    def test_a_dropped_connection_leaves_nothing_and_the_next_run_refetches(self, tmp_path):
        dropped = ok(PNG, drop_after=len(PNG) // 2)
        downloader, session, _ = make(tmp_path, [dropped], max_attempts=1)
        with pytest.raises(DownloadError, match="connection dropped"):
            downloader.fetch(candidate())
        assert leftovers(tmp_path / "originals") == []
        downloader, session, _ = make(tmp_path)  # a later run, network back
        assert Image.open(downloader.fetch(candidate())).size == (40, 30)
        assert len(session.calls) == 1

    def test_a_body_shorter_than_its_content_length_is_incomplete(self, tmp_path):
        short = FakeResponse(200, content=PNG[:-10], headers={"Content-Length": str(len(PNG))})
        downloader, _, _ = make(tmp_path, [short], max_attempts=1)
        with pytest.raises(DownloadError, match="incomplete"):
            downloader.fetch(candidate())
        assert leftovers(tmp_path / "originals") == []

    def test_a_truncated_image_with_no_length_to_check_fails_the_decode(self, tmp_path):
        buffer = io.BytesIO()
        Image.new("RGB", (64, 64), "red").save(buffer, format="JPEG")
        cut = buffer.getvalue()[:-30]
        downloader, _, _ = make(tmp_path, [FakeResponse(200, content=cut)])
        with pytest.raises(DownloadError, match="not a complete image"):
            downloader.fetch(candidate())
        assert leftovers(tmp_path / "originals") == []


class TestRetries:
    def test_a_dropped_connection_is_retried(self, tmp_path):
        downloader, session, clock = make(tmp_path, [ok(PNG, drop_after=10), ok()])
        assert downloader.fetch(candidate()).exists()
        assert len(session.calls) == 2 and clock.slept == [1]

    def test_network_errors_back_off_then_give_up_without_the_key_or_url(self, tmp_path):
        leaky = requests.ConnectionError("Max retries exceeded with url: /get/7_1280.jpg?secret=1")
        downloader, session, clock = make(tmp_path, [leaky])
        with pytest.raises(DownloadError, match="Could not reach Pixabay") as caught:
            downloader.fetch(candidate())
        assert len(session.calls) == 3 and clock.slept == [1, 2]
        assert "secret" not in str(caught.value) and caught.value.__cause__ is None

    @pytest.mark.parametrize("status", [429, 502])
    def test_429_and_5xx_are_retried(self, tmp_path, status):
        downloader, session, _ = make(tmp_path, [FakeResponse(status), ok()])
        downloader.fetch(candidate())
        assert len(session.calls) == 2

    def test_the_response_is_always_closed(self, tmp_path):
        bad = FakeResponse(403)
        downloader, _, _ = make(tmp_path, [bad])
        with pytest.raises(DownloadError):
            downloader.fetch(candidate())
        assert bad.closed


class TestGuards:
    def test_only_https(self, tmp_path):
        downloader, session, _ = make(tmp_path)
        with pytest.raises(DownloadError, match="https"):
            downloader.fetch(candidate(largeImageURL="http://pixabay.com/get/7.jpg"))
        assert session.calls == []

    def test_a_declared_size_over_the_cap_is_refused_before_reading_the_body(self, tmp_path):
        huge = FakeResponse(200, content=b"x", headers={"Content-Length": str(MAX_BYTES + 1)})
        downloader, _, _ = make(tmp_path, [huge])
        with pytest.raises(DownloadError, match="100 MB"):
            downloader.fetch(candidate())
        assert leftovers(tmp_path / "originals") == []

    def test_a_stream_that_outgrows_the_cap_is_cut_off(self, tmp_path, monkeypatch):
        monkeypatch.setattr("winnower.downloader.MAX_BYTES", 100)
        downloader, _, _ = make(tmp_path, [FakeResponse(200, content=b"x" * 500)])
        with pytest.raises(DownloadError, match="over 100 MB"):
            downloader.fetch(candidate())
        assert leftovers(tmp_path / "originals") == []


class TestFetchAll:
    def test_one_failure_does_not_stop_the_rest(self, tmp_path):
        downloader, _, _ = make(tmp_path, [ok(), FakeResponse(403), ok()])
        seen = []
        results = downloader.fetch_all(
            [candidate(1), candidate(2), candidate(3)], lambda d, t, r: seen.append((d, t, r.ok))
        )
        assert [r.ok for r in results] == [True, False, True]
        assert "HTTP 403" in results[1].error and results[1].path is None
        assert seen == [(1, 3, True), (2, 3, False), (3, 3, True)]

    def test_results_keep_their_candidate(self, tmp_path):
        downloader, _, _ = make(tmp_path)
        (result,) = downloader.fetch_all([candidate(9)])
        assert isinstance(result.candidate, Candidate) and result.candidate.id == 9
