import io
from pathlib import Path

import pytest

from winnower.keywords import (
    Keyword,
    KeywordError,
    parse_csv,
    parse_line,
    parse_text,
    read_keywords,
)


class TestText:
    def test_one_per_line_with_the_label_as_the_search_term(self):
        assert parse_text("apple\nhot dog\n").keywords == (
            Keyword("apple", "apple"),
            Keyword("hot dog", "hot dog"),
        )

    def test_override_after_a_pipe(self):
        assert parse_text("hot-dog | hot dog").keywords == (Keyword("hot-dog", "hot dog"),)

    def test_only_the_first_pipe_separates(self):
        assert parse_line("a | b | c") == Keyword("a", "b | c")

    def test_blank_lines_comments_and_padding_are_ignored(self):
        text = "\n  # a comment\n   apple   \n\n\tpear\t\n"
        assert [k.label for k in parse_text(text)] == ["apple", "pear"]

    def test_unicode_survives(self):
        assert parse_text("maçã | maçã vermelha").keywords == (Keyword("maçã", "maçã vermelha"),)

    def test_a_missing_label_names_the_line(self):
        with pytest.raises(KeywordError, match=r"list\.txt, line 2"):
            parse_text("apple\n | orphan term\n", "list.txt")

    def test_a_half_typed_override_is_an_error_not_a_silent_fallback(self):
        with pytest.raises(KeywordError, match="nothing after"):
            parse_text("hot-dog |")

    def test_no_keywords_at_all_is_an_error(self):
        with pytest.raises(KeywordError, match="no keywords found in x"):
            parse_text("# only a comment\n\n", "x")


class TestDuplicates:
    def test_first_wins_and_the_rest_are_reported(self):
        found = parse_text("cat | cat photo\ndog\nCAT | other")
        assert found.keywords == (Keyword("cat", "cat photo"), Keyword("dog", "dog"))
        assert found.duplicates == ("CAT",)

    def test_the_comparison_ignores_case_because_filesystems_do(self):
        assert len(parse_text("Cat\ncat\nCAT")) == 1


class TestCsv:
    def test_prefers_a_column_headed_keyword_then_label(self):
        text = "id,label,keyword\n1,ignored,apple\n2,ignored,pear\n"
        assert [k.label for k in parse_csv(text)] == ["apple", "pear"]

    def test_falls_back_to_the_first_column_and_skips_the_header(self):
        assert [k.label for k in parse_csv("fruit,colour\napple,red\npear,green\n")] == [
            "apple",
            "pear",
        ]

    def test_a_term_column_overrides_the_search_term_row_by_row(self):
        text = "label,term\nhot-dog,hot dog\napple,\n"
        assert parse_csv(text).keywords == (
            Keyword("hot-dog", "hot dog"),
            Keyword("apple", "apple"),
        )

    def test_columns_can_be_named_and_the_match_ignores_case(self):
        text = "id,Word,Query\n1,hot-dog,hot dog\n"
        found = parse_csv(text, column="word", term_column="QUERY")
        assert found.keywords == (Keyword("hot-dog", "hot dog"),)

    def test_an_unknown_column_lists_the_ones_there_are(self):
        with pytest.raises(KeywordError, match="no column named 'nope'.*'a', 'b'"):
            parse_csv("a,b\n1,2\n", column="nope")

    def test_semicolons_as_written_by_excel_in_europe(self):
        assert parse_csv("keyword;term\nhot-dog;hot dog\n").keywords == (
            Keyword("hot-dog", "hot dog"),
        )

    def test_a_single_column_has_no_delimiter_to_sniff(self):
        assert [k.label for k in parse_csv("keyword\napple\npear\n")] == ["apple", "pear"]

    def test_quoted_cells_keep_their_commas(self):
        assert parse_csv('keyword\n"hot dog, grilled"\n').keywords == (
            Keyword("hot dog, grilled", "hot dog, grilled"),
        )

    def test_blank_rows_and_empty_labels_are_skipped(self):
        assert [k.label for k in parse_csv("keyword,term\n\napple,a\n,orphan\npear,p\n")] == [
            "apple",
            "pear",
        ]

    def test_a_header_only_file_is_an_error(self):
        with pytest.raises(KeywordError, match="no keywords found"):
            parse_csv("keyword\n")

    def test_an_empty_file_is_an_error(self):
        with pytest.raises(KeywordError, match="is empty"):
            parse_csv("  \n")


class TestReadKeywords:
    def test_a_text_file(self, tmp_path):
        path = tmp_path / "k.txt"
        path.write_text("apple\npear | pear fruit\n", encoding="utf-8")
        assert read_keywords(path).keywords == (
            Keyword("apple", "apple"),
            Keyword("pear", "pear fruit"),
        )

    def test_a_csv_file_is_chosen_by_its_suffix(self, tmp_path):
        path = tmp_path / "k.CSV"
        path.write_text("keyword,term\nhot-dog,hot dog\n", encoding="utf-8")
        assert read_keywords(path).keywords == (Keyword("hot-dog", "hot dog"),)

    def test_a_windows_bom_does_not_become_part_of_the_first_keyword(self, tmp_path):
        path = tmp_path / "k.txt"
        path.write_bytes(b"\xef\xbb\xbfapple\npear\n")
        assert read_keywords(path).keywords[0] == Keyword("apple", "apple")

    def test_a_bom_does_not_hide_a_csv_header(self, tmp_path):
        path = tmp_path / "k.csv"
        path.write_bytes(b"\xef\xbb\xbfkeyword,term\nhot-dog,hot dog\n")
        assert read_keywords(path).keywords == (Keyword("hot-dog", "hot dog"),)

    def test_stdin_with_a_dash(self):
        found = read_keywords("-", stdin=io.StringIO("apple\npear\n"))
        assert [k.label for k in found] == ["apple", "pear"]

    def test_a_missing_file_says_so(self, tmp_path):
        with pytest.raises(KeywordError, match="not found"):
            read_keywords(tmp_path / "nope.txt")

    def test_a_file_that_is_not_utf8_says_so(self, tmp_path):
        path = tmp_path / "k.txt"
        path.write_bytes("maçã".encode("latin-1"))
        with pytest.raises(KeywordError, match="UTF-8"):
            read_keywords(path)

    def test_a_column_option_on_a_text_file_is_refused_rather_than_ignored(self, tmp_path):
        path = tmp_path / "k.txt"
        path.write_text("apple\n", encoding="utf-8")
        with pytest.raises(KeywordError, match="--column"):
            read_keywords(path, column="word")


def test_the_sample_list_in_the_readme_parses():
    sample = Path(__file__).parent.parent / "examples" / "keywords_sample.txt"
    found = read_keywords(sample)
    assert len(found) == 12 and not found.duplicates
    assert Keyword("hot-dog", "hot dog") in found.keywords
