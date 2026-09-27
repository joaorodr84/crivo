import time
import zipfile

import pytest
from fakes import hit

from crivo.packager import (
    CREDITS_NAME,
    MAX_STEM,
    Entry,
    PackageError,
    build_credits,
    plan_names,
    safe_stem,
    write_zip,
)
from crivo.pixabay_client import parse_response


def candidate(n=1, **overrides):
    body = {"total": 1, "totalHits": 1, "hits": [hit(n, **overrides)]}
    return parse_response(body, "x", 1, 3).candidates[0]


def entry(label="apple", n=1, data=b"IMG", ext="png", term="", **hit_overrides):
    return Entry(label, candidate(n, **hit_overrides), data, ext, term)


NOW = time.mktime((2026, 9, 27, 12, 0, 0, 0, 0, -1))


def _raise_denied(*args):
    raise OSError(5, "Access is denied")


class TestSafeStem:
    @pytest.mark.parametrize(
        ("label", "expected"),
        [
            ("hot-dog", "hot-dog"),
            ("hot dog", "hot dog"),
            ("  hot   dog  ", "hot dog"),
            ("a/b", "a_b"),
            ("a\\b", "a_b"),
            ('what: "is" this? <no> |x| *', "what_ _is_ this_ _no_ _x_ _"),
            ("tab\there\nnewline\x00nul", "tab_here_newline_nul"),
            ("trailing dots...", "trailing dots"),
            ("...leading", "leading"),
            (".hidden", "hidden"),
            ("maçã", "maçã"),
            ("日本語", "日本語"),
            ("", "image"),
            ("...", "image"),
            ("   ", "image"),
            ("/", "_"),
        ],
    )
    def test_cleaning(self, label, expected):
        assert safe_stem(label) == expected

    @pytest.mark.parametrize("label", ["CON", "con", "Nul", "aux", "COM1", "lpt9", "com3.backup"])
    def test_windows_device_names_are_prefixed(self, label):
        assert safe_stem(label).startswith("_")

    @pytest.mark.parametrize("label", ["console", "COM10", "com0", "lpt", "connect"])
    def test_names_that_only_look_like_devices_are_left_alone(self, label):
        assert safe_stem(label) == label

    def test_composed_and_decomposed_accents_become_one_name(self):
        assert safe_stem("caf\u00e9") == safe_stem("cafe\u0301")

    def test_long_labels_are_cut(self):
        assert len(safe_stem("x" * 500)) == MAX_STEM

    def test_a_cut_never_leaves_a_trailing_dot_or_space(self):
        assert safe_stem("x" * (MAX_STEM - 1) + ". tail") == "x" * (MAX_STEM - 1)

    @pytest.mark.parametrize("label", ["../../etc/passwd", "..\\..\\evil", "/abs/path", "C:\\x"])
    def test_a_label_cannot_become_a_path(self, label):
        stem = safe_stem(label)
        assert "/" not in stem and "\\" not in stem and ":" not in stem
        assert not stem.startswith(".")


class TestPlanNames:
    def test_the_extension_is_added(self):
        assert plan_names([entry("a", ext="png"), entry("b", ext="jpg")]) == ["a.png", "b.jpg"]

    def test_repeats_are_numbered_in_order(self):
        names = plan_names([entry("cat"), entry("cat"), entry("cat")])
        assert names == ["cat.png", "cat-2.png", "cat-3.png"]

    def test_case_alone_does_not_make_a_name_unique(self):
        assert plan_names([entry("Cat"), entry("cat")]) == ["Cat.png", "cat-2.png"]
        assert plan_names([entry("cat"), entry("Cat")]) == ["cat.png", "Cat-2.png"]

    def test_names_that_clash_only_after_cleaning_are_kept_apart(self):
        assert plan_names([entry("a/b"), entry("a_b"), entry("a:b")]) == [
            "a_b.png",
            "a_b-2.png",
            "a_b-3.png",
        ]

    def test_a_numbered_name_that_is_already_taken_is_skipped(self):
        assert plan_names([entry("cat-2"), entry("cat"), entry("cat")]) == [
            "cat-2.png",
            "cat.png",
            "cat-3.png",
        ]

    def test_different_extensions_do_not_clash(self):
        assert plan_names([entry("cat", ext="png"), entry("cat", ext="jpg")]) == [
            "cat.png",
            "cat.jpg",
        ]

    def test_nothing_can_take_the_credits_name(self):
        assert plan_names([entry("credits", ext="txt")]) == ["credits-2.txt"]


class TestCredits:
    def test_lists_keyword_page_and_contributor_for_every_file(self):
        entries = [entry("hot-dog", 1, user="Ana Ç"), entry("apple", 2)]
        text = build_credits(entries, ["hot-dog.png", "apple.png"], "2026-09-27")
        assert "hot-dog.png\n  Keyword:      hot-dog\n" in text
        assert "  Pixabay page: https://pixabay.com/photos/thing-1/\n" in text
        assert "  Contributor:  Ana Ç\n" in text
        assert "https://pixabay.com/photos/thing-2/" in text

    def test_says_what_licence_and_when(self):
        text = build_credits([entry()], ["apple.png"], "2026-09-27")
        assert "Pixabay Content License" in text and "https://pixabay.com/service/license/" in text
        assert "2026-09-27" in text and "Crivo" in text

    def test_the_search_term_is_shown_only_when_it_differs(self):
        same = build_credits([entry("apple", term="apple")], ["apple.png"], "d")
        differs = build_credits([entry("hot-dog", term="hot dog")], ["hot-dog.png"], "d")
        assert "Searched for" not in same
        assert "  Searched for: hot dog\n" in differs


class TestWriteZip:
    def test_contains_the_images_and_the_credits(self, tmp_path):
        out = write_zip([entry("a", data=b"AAA"), entry("b", 2, data=b"BBB")], tmp_path / "o.zip")
        with zipfile.ZipFile(out) as archive:
            assert archive.namelist() == ["a.png", "b.png", CREDITS_NAME]
            assert archive.read("a.png") == b"AAA" and archive.read("b.png") == b"BBB"
            assert "Contributor:  user2" in archive.read(CREDITS_NAME).decode("utf-8")
            assert archive.testzip() is None

    def test_no_member_can_escape_the_folder_it_is_unpacked_into(self, tmp_path):
        out = write_zip([entry("../../evil"), entry("/abs"), entry("C:\\x")], tmp_path / "o.zip")
        with zipfile.ZipFile(out) as archive:
            for name in archive.namelist():
                assert "/" not in name and "\\" not in name and not name.startswith(".")

    def test_members_have_readable_permissions_and_the_given_date(self, tmp_path):
        out = write_zip([entry()], tmp_path / "o.zip", now=lambda: NOW)
        with zipfile.ZipFile(out) as archive:
            for info in archive.infolist():
                assert info.external_attr >> 16 == 0o644
                assert info.date_time[:3] == (2026, 9, 27)

    def test_creates_the_parent_directory(self, tmp_path):
        out = write_zip([entry()], tmp_path / "deep" / "er" / "o.zip")
        assert out.is_file()

    def test_nothing_picked_is_an_error_and_writes_nothing(self, tmp_path):
        with pytest.raises(PackageError, match="nothing to package"):
            write_zip([], tmp_path / "o.zip")
        assert list(tmp_path.iterdir()) == []

    def test_an_existing_file_is_not_overwritten_unless_asked(self, tmp_path):
        target = tmp_path / "o.zip"
        target.write_bytes(b"precious")
        with pytest.raises(PackageError, match="already exists"):
            write_zip([entry()], target)
        assert target.read_bytes() == b"precious"
        write_zip([entry()], target, overwrite=True)
        assert zipfile.is_zipfile(target)

    def test_a_failure_leaves_no_half_written_archive_and_no_temp_file(self, tmp_path, monkeypatch):
        target = tmp_path / "o.zip"

        def boom(*args, **kwargs):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr("crivo.packager.os.replace", boom)
        with pytest.raises(PackageError, match="No space left"):
            write_zip([entry()], target)
        assert list(tmp_path.iterdir()) == []

    def test_a_failure_while_overwriting_keeps_the_old_file(self, tmp_path, monkeypatch):
        target = tmp_path / "o.zip"
        target.write_bytes(b"old")
        monkeypatch.setattr(
            "crivo.packager.os.replace", lambda *a: (_ for _ in ()).throw(OSError(5, "denied"))
        )
        with pytest.raises(PackageError):
            write_zip([entry()], target, overwrite=True)
        assert target.read_bytes() == b"old"

    def test_an_unwritable_destination_is_a_package_error(self, tmp_path):
        blocker = tmp_path / "file"
        blocker.write_text("x", encoding="utf-8")
        with pytest.raises(PackageError, match="Could not write"):
            write_zip([entry()], blocker / "o.zip")

    def test_no_temp_files_remain_after_success(self, tmp_path):
        write_zip([entry()], tmp_path / "o.zip")
        assert [p.name for p in tmp_path.iterdir()] == ["o.zip"]
