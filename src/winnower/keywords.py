"""Keyword input (FR1): a text file, a CSV column, stdin, or `-k` on the command line.

Every keyword is a *label* (what the output file is named after) and a *search term*
(what Pixabay is asked for). They are the same unless the input says otherwise, which is
the spec's `hot-dog` example: the label is a game key, the term is "hot dog".

Text input is one keyword per line, `label | search term` to override, `#` for comments.
The separator is a pipe because neither side of it can plausibly contain one and, unlike
a comma or a tab, it survives being pasted from a chat window or a spreadsheet cell.

CSV input always has a header row. Rejected: guessing whether the first row is a header;
"Apple" is a perfectly good keyword and "keyword" a perfectly good column name, so any
guess is wrong for somebody, and a wrong guess drops a keyword without saying so.
The delimiter is sniffed among comma, semicolon and tab, because Excel writes semicolons
in every locale that uses a decimal comma, and a Portuguese or German user's "CSV" is
that file.

Labels are unique, compared case-insensitively: they become file names, and Windows and
macOS would let `Cat.png` silently replace `cat.png`. The first one wins and the rest
are reported in `KeywordList.duplicates` rather than dropped without a word.
"""

from __future__ import annotations

import csv
import io
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

LABEL_COLUMNS = ("keyword", "label")
TERM_COLUMNS = ("term", "search", "search term", "search_term")
OVERRIDE_SEPARATOR = "|"
STDIN = "-"


class KeywordError(ValueError):
    """The keyword input is unusable. The message says where and how to fix it."""


@dataclass(frozen=True)
class Keyword:
    label: str
    term: str


@dataclass(frozen=True)
class KeywordList:
    keywords: tuple[Keyword, ...]
    # Labels that repeated an earlier one and were left out, as written.
    duplicates: tuple[str, ...] = ()

    def __len__(self) -> int:
        return len(self.keywords)

    def __iter__(self):
        return iter(self.keywords)


def parse_line(line: str, where: str = "line") -> Keyword | None:
    """One line of text input; None for a blank line or a comment."""
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    label, sep, term = (part.strip() for part in text.partition(OVERRIDE_SEPARATOR))
    if not label:
        raise KeywordError(f"{where}: nothing before {OVERRIDE_SEPARATOR!r} in {text!r}")
    if sep and not term:
        # `hot-dog |` is a half-typed override. Falling back to the label would search
        # for something the person did not mean and cost a request to find out.
        raise KeywordError(
            f"{where}: nothing after {OVERRIDE_SEPARATOR!r} in {text!r}; "
            "write the search term, or drop the separator to search for the label"
        )
    return Keyword(label, term or label)


def parse_lines(lines: Iterable[str], source: str = "input") -> KeywordList:
    found = []
    for number, line in enumerate(lines, start=1):
        keyword = parse_line(line, f"{source}, line {number}")
        if keyword:
            found.append(keyword)
    return _unique(found, source)


def parse_text(text: str, source: str = "input") -> KeywordList:
    return parse_lines(text.splitlines(), source)


def _pick(header: list[str], wanted: str | None, defaults: tuple[str, ...]) -> int | None:
    """Index of the column `wanted` names, else the first of `defaults` present."""
    lowered = [name.strip().casefold() for name in header]
    if wanted is not None:
        try:
            return lowered.index(wanted.strip().casefold())
        except ValueError:
            raise KeywordError(
                f"no column named {wanted!r}; the columns are {', '.join(map(repr, header))}"
            ) from None
    for name in defaults:
        if name in lowered:
            return lowered.index(name)
    return None


def parse_csv(
    text: str,
    column: str | None = None,
    term_column: str | None = None,
    source: str = "input",
) -> KeywordList:
    """Keywords from a CSV with a header row.

    The label comes from `column`, else a column headed `keyword` or `label`, else the
    first column. The search term comes from `term_column`, else a column headed `term`
    or `search`, else it is the label.
    """
    text = text.lstrip("﻿")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel  # a single column has no delimiter to sniff
    rows = list(csv.reader(io.StringIO(text, newline=""), dialect))
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        raise KeywordError(f"{source} is empty")
    header, body = rows[0], rows[1:]

    label_at = _pick(header, column, LABEL_COLUMNS)
    if label_at is None:
        label_at = 0
    term_at = _pick(header, term_column, TERM_COLUMNS)

    found = []
    for row in body:
        label = row[label_at].strip() if label_at < len(row) else ""
        if not label:
            continue
        term = row[term_at].strip() if term_at is not None and term_at < len(row) else ""
        found.append(Keyword(label, term or label))
    return _unique(found, source)


def read_keywords(
    source: str | Path,
    *,
    column: str | None = None,
    term_column: str | None = None,
    stdin: TextIO | None = None,
) -> KeywordList:
    """Keywords from a file (`.csv` is a CSV, anything else is text) or `-` for stdin."""
    if str(source) == STDIN:
        return parse_text((stdin or sys.stdin).read(), "stdin")
    path = Path(source)
    if not path.is_file():
        raise KeywordError(f"keyword file not found: {path}")
    # utf-8-sig: Notepad and Excel both write a BOM, which would otherwise become part
    # of the first keyword.
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as err:
        raise KeywordError(f"{path} is not UTF-8 text; re-save it as UTF-8") from err
    if path.suffix.lower() == ".csv":
        return parse_csv(text, column, term_column, path.name)
    if column or term_column:
        raise KeywordError(f"--column applies to a .csv file; {path.name} is read as text")
    return parse_text(text, path.name)


def _unique(found: list[Keyword], source: str) -> KeywordList:
    seen: set[str] = set()
    kept: list[Keyword] = []
    duplicates: list[str] = []
    for keyword in found:
        key = keyword.label.casefold()
        if key in seen:
            duplicates.append(keyword.label)
            continue
        seen.add(key)
        kept.append(keyword)
    if not kept:
        raise KeywordError(f"no keywords found in {source}")
    return KeywordList(tuple(kept), tuple(duplicates))
