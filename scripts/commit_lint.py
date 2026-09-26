"""Enforces the commit-message convention documented in CLAUDE.md -> Commit messages.

Watchr does this with commitlint. Winnower is a Python project whose contributors
should not need Node installed to make a commit, so this is the same rule set as a
single dependency-free module rather than a package.json that exists only to lint
messages. Rejected: `commitizen`/`gitlint`, which would each be a dev dependency and
neither can express the two local rules below without a plugin.

The rules are commitlint's `config-conventional` (type list, lowercase type, no
trailing full stop, header at most 100 characters, a blank line before the body)
plus four local ones, because the convention here is a near-miss of the stock one:

- The description starts with a capital letter (or a backticked identifier).
  config-conventional's own `subject-case` cannot say that: 'sentence-case' also
  forces the *rest* of the subject to lowercase, which would reject a subject that
  legitimately names a camelCase identifier.
- The task ID goes last, in parentheses: `(WINNOWER-12)` or
  `(WINNOWER-6, WINNOWER-7)`. Checked only when the subject mentions WINNOWER at all,
  since bookkeeping commits are allowed to carry no ID.
- A `!` in the header and a `BREAKING CHANGE:` footer come together. The spec allows
  either alone; CLAUDE.md asks for both, so a release derived from the types
  (`scripts/version.py`) never depends on which of the two someone remembered.
- Body line length is not limited. Bodies here are long and explanatory by design,
  and a pasted table is not worth reflowing to satisfy a linter.

Usage:
    python -m scripts.commit_lint --edit .git/COMMIT_EDITMSG   (what the hook runs)
    python -m scripts.commit_lint --last                       (the tip commit)
    python -m scripts.commit_lint --range <from>..<to>         (CI: pushed commits)
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

TYPES = (
    "build",
    "chore",
    "ci",
    "docs",
    "feat",
    "fix",
    "perf",
    "refactor",
    "revert",
    "style",
    "test",
)
HEADER_MAX_LENGTH = 100

HEADER = re.compile(
    r"^(?P<type>[^\s(!:]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?: (?P<subject>.*)$"
)
TASK_ID_LAST = re.compile(r" \(WINNOWER-\d+(, WINNOWER-\d+)*\)$")
BREAKING_FOOTER = re.compile(r"^BREAKING[ -]CHANGE: \S", re.MULTILINE)

# Trailer tokens recognised when checking that the footer block is separated from
# the body. A general `Token: value` pattern would misread prose such as
# "Note: ..." inside a body paragraph, so the list is closed.
FOOTER_LINE = re.compile(
    r"^(BREAKING[ -]CHANGE|Co-Authored-By|Signed-off-by|Refs|Closes|Fixes): "
)

# Messages git or a tool writes itself; commitlint ignores the same set.
IGNORED_PREFIXES = ("Merge ", 'Revert "', "fixup! ", "squash! ", "amend! ")


def clean(message: str) -> str:
    """Drop the `#` comment lines git leaves in the edit buffer, and trailing space."""
    lines = [ln.rstrip() for ln in message.splitlines() if not ln.startswith("#")]
    return "\n".join(lines).strip("\n")


def lint(message: str) -> list[str]:
    """Return what is wrong with a commit message; an empty list means it is fine."""
    text = clean(message)
    lines = text.split("\n")
    header = lines[0]

    if not header:
        return ["the message is empty"]
    if header.startswith(IGNORED_PREFIXES):
        return []

    match = HEADER.match(header)
    if not match or not match["subject"] or not match["type"]:
        return [
            "header must look like `<type>[(scope)][!]: <Description> (WINNOWER-<n>)`, "
            f"e.g. `feat: Show each candidate's tags (WINNOWER-12)`; got {header!r}"
        ]

    errors: list[str] = []
    type_, scope, bang, subject = (
        match["type"],
        match["scope"],
        match["bang"],
        match["subject"],
    )

    if type_ not in TYPES:
        if type_.lower() in TYPES:
            errors.append(f"type must be lowercase: use `{type_.lower()}`, not `{type_}`")
        else:
            errors.append(f"type `{type_}` is not one of: {', '.join(TYPES)}")

    if scope is not None:
        if not scope:
            errors.append("scope is empty: drop the parentheses or name an area")
        elif scope != scope.lower():
            errors.append(f"scope must be lowercase, got `{scope}`")

    if len(header) > HEADER_MAX_LENGTH:
        errors.append(
            f"header is {len(header)} characters; keep it to {HEADER_MAX_LENGTH} "
            "and put the detail in the body"
        )
    if subject.endswith("."):
        errors.append("description must not end with a full stop")
    if not re.match(r"[A-Z`]", subject):
        errors.append("description must start with a capital letter (or a backticked identifier)")
    if re.search(r"winnower", subject, re.IGNORECASE) and not TASK_ID_LAST.search(subject):
        errors.append(
            "task ID must be last and formatted as (WINNOWER-12) or (WINNOWER-6, WINNOWER-7)"
        )

    if len(lines) > 1 and lines[1] != "":
        errors.append("the body must be separated from the header by a blank line")

    body = "\n".join(lines[1:])
    has_footer = bool(BREAKING_FOOTER.search(body))
    if bang and not has_footer:
        errors.append("a `!` breaking change needs a `BREAKING CHANGE: <what to migrate>` footer")
    if has_footer and not bang:
        errors.append("a `BREAKING CHANGE:` footer needs a `!` before the colon in the header")

    footer_start = _footer_block_start(lines)
    if footer_start is not None and footer_start > 1 and lines[footer_start - 1] != "":
        errors.append("the footer block must be separated from the body by a blank line")

    return errors


def _footer_block_start(lines: list[str]) -> int | None:
    """Index of the first line of the trailing run of footer lines, if any.

    Continuation lines of a multi-line footer (indented, or the second line of a
    BREAKING CHANGE explanation) are not footers themselves, so the scan only walks
    back over lines that start a recognised trailer.
    """
    start = None
    for i in range(len(lines) - 1, 0, -1):
        if FOOTER_LINE.match(lines[i]):
            start = i
        elif lines[i] == "":
            break
        elif start is None:
            # The last line is prose, not a trailer: there is no footer block.
            return None
    return start


def _report(subject: str, errors: list[str]) -> None:
    # ASCII only: a Windows console on cp1252 raises on anything fancier, and a
    # linter that crashes while reporting is worse than one that says nothing.
    print(f"x {subject}", file=sys.stderr)
    for error in errors:
        print(f"    - {error}", file=sys.stderr)


def _git_log(*args: str) -> list[str]:
    out = subprocess.run(
        ["git", "log", "--no-merges", "--format=%B%x1e", *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    return [m.strip("\n") for m in out.split("\x1e") if m.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--edit", metavar="FILE", help="lint the message in FILE (the commit-msg hook)")
    group.add_argument("--last", action="store_true", help="lint the tip commit")
    group.add_argument("--range", metavar="A..B", help="lint every commit in the range")
    args = parser.parse_args(argv)

    if args.edit:
        with open(args.edit, encoding="utf-8") as handle:
            messages = [handle.read()]
    elif args.last:
        messages = _git_log("-1")
    else:
        messages = _git_log(args.range)

    failed = 0
    for message in messages:
        errors = lint(message)
        if errors:
            failed += 1
            _report(clean(message).split("\n")[0], errors)

    if failed:
        print(
            f"\n{failed} of {len(messages)} commit message(s) break the convention in CLAUDE.md "
            "-> Commit messages.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
