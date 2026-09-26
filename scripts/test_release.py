"""`release.py` against a real throwaway repository, since its job is talking to git."""

import subprocess
from pathlib import Path

import pytest

from scripts import release


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def commit(cwd: Path, subject: str, body: str = "") -> None:
    message = subject if not body else f"{subject}\n\n{body}"
    git(cwd, "commit", "-q", "--allow-empty", "-m", message)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q", "-b", "main")
    monkeypatch.chdir(tmp_path)
    # `git()` above passes the identity with -c, which covers only *this file's* git
    # calls. `release --write` runs its own `git tag -a`, and an annotated tag needs a
    # committer identity: on a Linux or Windows runner with none configured it died
    # with exit 128 -- 4 of the 6 jobs in the first CI run (macOS runners ship one, so
    # they passed). Environment variables reach that subprocess; the script itself is
    # left alone, since a contributor with no identity should get git's own message.
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "T")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "t@example.com")
    return tmp_path


def test_first_release_from_no_tag(repo, capsys):
    commit(repo, "feat: Do the first thing (WINNOWER-1)")
    assert release.main([]) == 0
    out = capsys.readouterr().out
    assert "Next version:   v0.1.0" in out
    assert "Dry run" in out
    assert git(repo, "tag") == "", "a dry run must not create a tag"


def test_write_creates_an_annotated_tag_and_never_pushes(repo, capsys):
    commit(repo, "fix: Repair the first thing (WINNOWER-1)")
    assert release.main(["--write"]) == 0
    assert git(repo, "tag") == "v0.0.1"
    # Annotated, carrying the evidence for the number.
    assert git(repo, "cat-file", "-t", "v0.0.1") == "tag"
    assert "Repair the first thing" in git(repo, "tag", "-n99", "v0.0.1")
    assert "git push origin v0.0.1" in capsys.readouterr().out


def test_counts_only_commits_since_the_last_tag(repo, capsys):
    commit(repo, "feat: Old feature (WINNOWER-1)")
    git(repo, "tag", "-a", "v0.3.0", "-m", "v0.3.0")
    commit(repo, "fix: New fix (WINNOWER-2)")
    assert release.main([]) == 0
    out = capsys.readouterr().out
    assert "Commits since:  1" in out
    assert "Next version:   v0.3.1" in out
    assert "Old feature" not in out


def test_bookkeeping_only_releases_nothing_unless_patch_is_forced(repo, capsys):
    commit(repo, "feat: Old feature")
    git(repo, "tag", "-a", "v0.1.0", "-m", "v0.1.0")
    commit(repo, "docs: Tidy the readme")
    assert release.main([]) == 0
    assert "Nothing since the last tag ships behaviour" in capsys.readouterr().out
    assert release.main(["--patch"]) == 0
    assert "Next version:   v0.1.1" in capsys.readouterr().out


def test_breaking_before_1_0_is_demoted_to_a_minor(repo, capsys):
    commit(repo, "feat: Old feature")
    git(repo, "tag", "-a", "v0.2.0", "-m", "v0.2.0")
    commit(repo, "refactor!: Rename a key (WINNOWER-3)", "BREAKING CHANGE: it moved")
    release.main([])
    out = capsys.readouterr().out
    assert "demoted from major" in out
    assert "Next version:   v0.3.0" in out


def test_nothing_since_the_tag_is_an_error(repo, capsys):
    commit(repo, "feat: Something")
    git(repo, "tag", "-a", "v0.1.0", "-m", "v0.1.0")
    assert release.main([]) == 1
    assert "Nothing to release" in capsys.readouterr().err


def test_write_refuses_off_main(repo, capsys):
    commit(repo, "feat: Something")
    git(repo, "checkout", "-q", "-b", "winnower-9-something")
    assert release.main(["--write"]) == 1
    assert "releases are cut from main" in capsys.readouterr().err
    assert git(repo, "tag") == ""


def test_write_refuses_a_dirty_tree(repo, capsys):
    commit(repo, "feat: Something")
    (repo / "stray.txt").write_text("uncommitted", encoding="utf-8")
    assert release.main(["--write"]) == 1
    assert "working tree is dirty" in capsys.readouterr().err
