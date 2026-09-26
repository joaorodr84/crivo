"""The bump arithmetic, pure -- no git, no filesystem."""

import pytest

from scripts.version import (
    classify_bump,
    format_version,
    next_version,
    parse_commit,
    parse_version,
)


def bump(*subjects, body=""):
    return classify_bump([(s, body) for s in subjects]).bump


def test_feat_is_minor_and_fix_is_patch():
    assert bump("feat: Add a thing (WINNOWER-1)") == "minor"
    assert bump("fix: Repair a thing (WINNOWER-1)") == "patch"
    assert bump("perf: Make it quicker") == "patch"
    assert bump("revert: Undo it") == "patch"


def test_largest_bump_wins():
    assert bump("fix: A", "feat: B", "docs: C") == "minor"
    assert bump("fix: A", "feat!: B") == "major"


def test_bookkeeping_alone_releases_nothing():
    assert bump("docs: A", "chore: B", "refactor: C", "test: D", "ci: E", "build: F", "style: G") == "none"


def test_breaking_via_bang_or_footer_either_counts():
    assert bump("refactor!: Rename a key (WINNOWER-1)") == "major"
    assert bump("refactor: Rename a key", body="BREAKING CHANGE: it moved") == "major"
    assert bump("refactor: Rename a key", body="BREAKING-CHANGE: it moved") == "major"


def test_a_subject_that_does_not_parse_counts_as_a_patch_and_is_named():
    result = classify_bump([("WINNOWER-1 added a thing", ""), ("docs: A", "")])
    assert result.bump == "patch"
    assert [c.subject for c in result.unparsed] == ["WINNOWER-1 added a thing"]


def test_an_unknown_type_counts_as_a_patch_and_is_named():
    result = classify_bump([("wibble: Do a thing", "")])
    assert result.bump == "patch"
    assert [c.type for c in result.unknown] == ["wibble"]


def test_parse_commit_reads_scope_and_bang():
    c = parse_commit("feat(client)!: Drop a flag (WINNOWER-9)")
    assert (c.conventional, c.type, c.breaking) == (True, "feat", True)
    assert parse_commit("no colon here").conventional is False


@pytest.mark.parametrize(
    ("tag", "expected"),
    [("v1.2.3", (1, 2, 3)), ("0.4.0", (0, 4, 0)), (" v10.0.1\n", (10, 0, 1)), ("v1.2", None), ("release-1", None)],
)
def test_parse_version(tag, expected):
    assert parse_version(tag) == expected


def test_format_version_round_trips():
    assert format_version((0, 3, 1)) == "v0.3.1"
    assert parse_version(format_version((7, 8, 9))) == (7, 8, 9)


@pytest.mark.parametrize(
    ("current", "requested", "expected", "applied", "demoted"),
    [
        ((1, 2, 3), "major", (2, 0, 0), "major", False),
        ((1, 2, 3), "minor", (1, 3, 0), "minor", False),
        ((1, 2, 3), "patch", (1, 2, 4), "patch", False),
        ((1, 2, 3), "none", (1, 2, 3), "none", False),
        # Pre-1.0 a breaking change is a minor, and says so.
        ((0, 4, 2), "major", (0, 5, 0), "minor", True),
    ],
)
def test_next_version(current, requested, expected, applied, demoted):
    assert next_version(current, requested) == (expected, applied, demoted)
