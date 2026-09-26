"""Derives the next version number from the commit types since the last tag.

With `feat`/`fix` in every subject the next version follows from the history rather
than being chosen, which suits a repo where releases are git tags and
`git describe --contains` answers "which release has this".

Everything here is pure -- no git, no filesystem, no process -- so the rules are
unit-testable without a repository to run them against (CLAUDE.md -> Tests).
`release.py` is the half that talks to git.

Deliberately not `python-semantic-release` or `commitizen bump`: they also generate
CHANGELOG.md by listing commit subjects, and this changelog is written for people
who should not need the repo open. Taking half a tool means writing this half --
about 60 lines -- rather than adopting one and then fighting the other half of it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# One definition of what a header and a breaking footer look like, shared with the
# linter. What the hook accepts and what the release reads are then the same set by
# construction, the same reason Watchr's repair scripts share their audit's rule.
from scripts.commit_lint import BREAKING_FOOTER, HEADER

# Which types move which digit. `perf` sits with `fix` because a performance change
# is user-visible in the same way a bug fix is; the rest ship no behaviour and are
# listed so that "unknown type" stays a distinct answer from "type that releases
# nothing".
FEATURE_TYPES = {"feat"}
PATCH_TYPES = {"fix", "perf", "revert"}
SILENT_TYPES = {"docs", "refactor", "test", "build", "ci", "chore", "style"}


@dataclass
class Commit:
    subject: str
    body: str = ""
    conventional: bool = False
    type: str | None = None
    breaking: bool = False


def parse_commit(subject: str, body: str = "") -> Commit:
    match = HEADER.match(subject.strip())
    if not match:
        return Commit(subject=subject, body=body)
    return Commit(
        subject=subject,
        body=body,
        conventional=True,
        type=match["type"],
        # Either marker counts: `feat!:` in the subject or a `BREAKING CHANGE:`
        # footer in the body. The linter demands both together, and this accepts
        # either so a release is never under-bumped by a hook that was bypassed.
        breaking=bool(match["bang"]) or bool(BREAKING_FOOTER.search(body)),
    )


@dataclass
class BumpResult:
    bump: str  # 'major' | 'minor' | 'patch' | 'none'
    breaking: list[Commit] = field(default_factory=list)
    features: list[Commit] = field(default_factory=list)
    patches: list[Commit] = field(default_factory=list)
    unparsed: list[Commit] = field(default_factory=list)
    unknown: list[Commit] = field(default_factory=list)


def classify_bump(commits: list[tuple[str, str]]) -> BumpResult:
    """The largest bump any one commit justifies, given (subject, body) pairs.

    'none' when nothing in the range ships behaviour -- bookkeeping only.
    """
    parsed = [parse_commit(subject, body) for subject, body in commits]
    result = BumpResult(bump="none")
    for c in parsed:
        if not c.conventional:
            # An unparsed subject could be anything, so it is treated as at least a
            # patch. Under-bumping is the failure that matters: a version that
            # claims less than it changed is the one nobody can act on.
            result.unparsed.append(c)
        elif c.breaking:
            result.breaking.append(c)
        elif c.type in FEATURE_TYPES:
            result.features.append(c)
        elif c.type in PATCH_TYPES:
            result.patches.append(c)
        elif c.type not in SILENT_TYPES:
            result.unknown.append(c)

    if result.patches or result.unparsed or result.unknown:
        result.bump = "patch"
    if result.features:
        result.bump = "minor"
    if result.breaking:
        result.bump = "major"
    return result


Version = tuple[int, int, int]


def parse_version(tag: str) -> Version | None:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", tag.strip())
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def format_version(version: Version) -> str:
    return "v{}.{}.{}".format(*version)


def next_version(current: Version, bump: str) -> tuple[Version, str, bool]:
    """Apply a bump, including the 0.x rule from CLAUDE.md -> Versions.

    While the major is 0 a breaking change is allowed in a minor bump, so a `!`
    commit before 1.0.0 produces a minor and says it was demoted, rather than
    tagging v1.0.0 by arithmetic and declaring an interface stable that isn't.
    Returns (version, bump actually applied, whether it was demoted).
    """
    major, minor, patch = current
    demoted = bump == "major" and major == 0
    applied = "minor" if demoted else bump
    if applied == "major":
        return (major + 1, 0, 0), applied, demoted
    if applied == "minor":
        return (major, minor + 1, 0), applied, demoted
    if applied == "patch":
        return (major, minor, patch + 1), applied, demoted
    return current, "none", demoted
