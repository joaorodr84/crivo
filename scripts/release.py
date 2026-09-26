"""Proposes -- and with --write, creates -- the next release tag.

Releases here are git tags and nothing else: no package is published, and
`git describe --contains <sha>` is how "which release has this" gets answered
(CLAUDE.md -> Versions). The package's own version is derived from the tag by
setuptools-scm, so there is no version field anywhere to keep in step. What was
missing was the number itself, which every commit already carries the type for.
This reads those types and does the arithmetic.

Deliberately half a tool: the bump and the tag are automated, the changelog prose
stays hand-written (the `changelog-updater` agent), and this only reminds you to
retitle the Unreleased section.

Dry run by default: a bare run prints the whole plan and creates nothing.

Usage (from the repo root):
    python -m scripts.release             (dry run -- print the plan)
    python -m scripts.release --write     (create the annotated tag)
    python -m scripts.release --patch     (tag a checkpoint when nothing since
                                           the last tag ships behaviour)

The tag is created locally and never pushed: pushing a tag is what publishes a
release, and that stays a decision someone makes on purpose. The command to do it
is printed.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from scripts.version import (
    BumpResult,
    classify_bump,
    format_version,
    next_version,
    parse_version,
)

# Field and record separators that cannot appear in a commit message, so a body with
# blank lines or newlines of its own still parses.
FS = "\x1f"
RS = "\x1e"


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout.strip()


def last_release_tag() -> str | None:
    try:
        # The nearest tag reachable from HEAD, not the highest tag in the repo: the
        # range being classified has to be the one that leads here.
        return git("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*")
    except subprocess.CalledProcessError:
        return None


def commits_since(tag: str | None) -> list[tuple[str, str]]:
    rng = f"{tag}..HEAD" if tag else "HEAD"
    log = git("log", rng, f"--format=%s{FS}%b{RS}", "--no-merges")
    pairs = []
    for entry in log.split(RS):
        entry = entry.strip()
        if entry:
            subject, _, body = entry.partition(FS)
            pairs.append((subject, body))
    return pairs


def _list(label: str, commits: list) -> None:
    if commits:
        print(f"\n{label} ({len(commits)}):")
        for c in commits:
            print(f"  {c.subject}")


def _fail(message: str) -> int:
    print(f"\n{message}\n", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Propose or create the next release tag.")
    parser.add_argument("--write", action="store_true", help="create the annotated tag")
    parser.add_argument("--patch", action="store_true", help="tag a checkpoint if nothing ships behaviour")
    args = parser.parse_args(argv)

    tag = last_release_tag()
    current = parse_version(tag) if tag else (0, 0, 0)
    if current is None:
        return _fail(
            f"The last tag reachable from HEAD is `{tag}`, which is not a v<major>.<minor>.<patch>."
        )

    commits = commits_since(tag)
    if not commits:
        return _fail(f"No commits since {tag or 'the start of history'}. Nothing to release.")

    result: BumpResult = classify_bump(commits)
    bump = "patch" if result.bump == "none" and args.patch else result.bump
    version, applied, demoted = next_version(current, bump)
    proposed = format_version(version)

    print(f"Last release:   {tag or '(none -- this would be the first)'}")
    print(f"Commits since:  {len(commits)}")
    _list("Breaking", result.breaking)
    _list("Features", result.features)
    _list("Fixes", result.patches)
    _list("Not a conventional commit -- counted as a patch", result.unparsed)
    _list("Unknown type -- counted as a patch", result.unknown)

    print()
    if result.bump == "none" and not args.patch:
        print(
            "Nothing since the last tag ships behaviour: every commit is docs, ci,\n"
            "chore, refactor, test, build or style. A release would be a checkpoint\n"
            "rather than a version. Pass --patch as well if that is what you want."
        )
        return 0

    note = " (breaking, demoted from major -- still 0.x)" if demoted else ""
    print(f"Bump:           {applied}{note}")
    print(f"Next version:   {proposed}")

    if not args.write:
        print("\nDry run -- nothing created. Pass --write to tag it.")
        return 0

    # Guards, all of them about tagging something other than what you just read.
    if git("rev-parse", "--abbrev-ref", "HEAD") != "main":
        return _fail("Refusing to tag: releases are cut from main, and HEAD is on another branch.")
    if git("status", "--porcelain"):
        return _fail("Refusing to tag: the working tree is dirty, so the tag would not name what you see.")
    if git("tag", "--list", proposed):
        return _fail(f"Refusing to tag: {proposed} already exists.")

    # The annotated tag carries the evidence for the number, kept where the number is.
    notable = [*result.breaking, *result.features, *result.patches]
    lines = [
        proposed,
        "",
        f"{applied} bump over {tag or 'the start of history'}, from {len(commits)} commits.",
    ]
    if demoted:
        lines.append("Breaking change, taken as a minor while the major is 0.")
    lines += ["", *[f"- {c.subject}" for c in notable]]
    git("tag", "-a", proposed, "-m", "\n".join(lines))

    print(f"\nCreated {proposed}. It is local until you push it:")
    print(f"  git push origin {proposed}")
    print(
        "\nCHANGELOG.md is hand-written and was not touched: retitle its\n"
        f"`## [Unreleased]` section to `## [{proposed[1:]}]` if the entries\n"
        "below it are what this release contains."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
