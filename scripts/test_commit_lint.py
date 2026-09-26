"""The commit-message rules, one case per rule, plus the CLI paths CI and the hook use."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import commit_lint
from scripts.commit_lint import lint

BREAKING_BODY = "\n\nThe key moved.\n\nBREAKING CHANGE: rename `picks` to `selections`."


@pytest.mark.parametrize(
    "message",
    [
        "feat: Show each candidate's tags under its thumbnail (WINNOWER-12)",
        "fix(client): Stop retrying a 400 as if it were a rate limit (WINNOWER-13)",
        "feat: Resize and pad the picks (WINNOWER-6, WINNOWER-7)",
        "docs: Fix the setup steps for Windows",
        "refactor(session)!: Rename the picks key to selections (WINNOWER-14)" + BREAKING_BODY,
        "fix: `pixabay_client` keeps the key out of the cache (WINNOWER-2)",
        "feat: Do a thing (WINNOWER-1)\n\nBody paragraph.\n\nCo-Authored-By: Claude <a@b.c>",
        "feat: Do a thing (WINNOWER-1)\n\n# a comment git would strip\nBody.",
        # A subject naming a camelCase identifier is why subject-case is not used.
        "refactor: Drop totalEpisodes from the arithmetic (WINNOWER-3)",
        # Messages git writes itself are not ours to police.
        "Merge branch 'winnower-2-lint-commit-messages'",
        'Revert "feat: Something (WINNOWER-2)"',
        "fixup! feat: Something (WINNOWER-2)",
    ],
)
def test_accepts(message):
    assert lint(message) == []


@pytest.mark.parametrize(
    ("message", "fragment"),
    [
        ("", "empty"),
        ("Added a thing", "header must look like"),
        # The task-ID-first shape Watchr retired: it parses as a header whose type is
        # the ID, so the complaint is the unknown type.
        ("WINNOWER-12: Show tags", "not one of"),
        ("feat:Show tags", "header must look like"),
        ("feat: ", "header must look like"),
        ("Feat: Show tags", "lowercase"),
        ("feature: Show tags", "not one of"),
        ("feat: show tags", "capital letter"),
        ("feat: Show tags.", "full stop"),
        ("feat(): Show tags", "scope is empty"),
        ("feat(Client): Show tags", "scope must be lowercase"),
        ("feat: " + "X" * 100, "characters"),
        ("feat: Show tags (WINNOWER12)", "task ID must be last"),
        ("feat: Show tags (winnower-12)", "task ID must be last"),
        ("feat: Show tags (WINNOWER-12) and more", "task ID must be last"),
        ("feat: Show tags\nbody with no blank line", "blank line"),
        ("feat!: Show tags (WINNOWER-2)", "BREAKING CHANGE"),
        ("feat: Show tags (WINNOWER-2)\n\nBREAKING CHANGE: it moved", "needs a `!`"),
        (
            "feat: Show tags (WINNOWER-2)\n\nBody.\nCo-Authored-By: Claude <a@b.c>",
            "footer block must be separated",
        ),
    ],
)
def test_rejects(message, fragment):
    errors = lint(message)
    assert errors, f"expected {message!r} to be rejected"
    assert any(fragment in e for e in errors), errors


def test_reports_every_problem_at_once():
    # One round trip to learn everything wrong beats fixing them one commit at a time.
    errors = lint("Feat: show tags.")
    assert len(errors) == 3


def test_edit_mode_exit_codes(tmp_path, capsys):
    good = tmp_path / "good"
    good.write_text("docs: Fix a typo\n", encoding="utf-8")
    bad = tmp_path / "bad"
    bad.write_text("fixed a typo\n", encoding="utf-8")

    assert commit_lint.main(["--edit", str(good)]) == 0
    assert commit_lint.main(["--edit", str(bad)]) == 1
    assert "1 of 1 commit message" in capsys.readouterr().err


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_range_mode_checks_commits_not_the_tree(tmp_path, monkeypatch, capsys):
    """CI catches what `--no-verify` let through, so it has to read real history."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "chore: Start it")
    base = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "feat: Fine one (WINNOWER-2)")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "added something sloppy")
    monkeypatch.chdir(tmp_path)

    assert commit_lint.main(["--range", f"{base}..HEAD"]) == 1
    err = capsys.readouterr().err
    assert "added something sloppy" in err
    assert "Fine one" not in err
    assert "1 of 2" in err

    assert commit_lint.main(["--range", f"{base}..HEAD~1"]) == 0
    assert commit_lint.main(["--last"]) == 1


@pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX sh on PATH")
def test_hook_script_runs_the_linter(tmp_path):
    """The shell script, not just the module: this is what a commit actually invokes."""
    message = tmp_path / "msg"
    message.write_text("not conventional\n", encoding="utf-8")
    hook = Path(__file__).resolve().parent.parent / ".githooks" / "commit-msg"
    result = subprocess.run(
        ["sh", str(hook), str(message)],
        cwd=hook.parent.parent,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, (sys.platform, result.stderr)
    assert "header must look like" in result.stderr
