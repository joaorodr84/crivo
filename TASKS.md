# Winnower task ledger

Every task carries an ID of the form `WINNOWER-<n>`. IDs are assigned once, never
reused, and never renumbered.

**Next ID to assign: `WINNOWER-18`**

If this file and history ever disagree, history wins:

```sh
git log --oneline | grep -oE 'WINNOWER-[0-9]+' | sort -t- -k2 -n | tail -1
```

## How IDs work

- **An ID is claimed when a task is written down, not when it ships.** A new
  `TODO.md` entry gets its ID immediately; the commit that closes it reuses that
  same ID. Otherwise every item would be numbered twice — once as a plan, once
  as a commit — and the two numbers would name the same work.
- **One ID per task, not per commit.** A feature landed over two commits carries
  the same ID in both subjects. A commit closing two tasks names both.
- Commit subject: `feat: Show each candidate's tags under its thumbnail
  (WINNOWER-12)` — Conventional Commits, with the ID in parentheses at the end.
  See **Commit messages** in `CLAUDE.md`.
- Branch name: `winnower-12-show-candidate-tags`. The ID prefix is wanted here;
  the `p1-`/`p2-` priority prefix is banned and a different thing.
- Moving an item between `TODO.md` and this file never changes its ID.

## Releases

Which release contains a task is derived, not typed:

```sh
git describe --contains <sha>
```

Versions are tagged `v<major>.<minor>.<patch>`. While in `0.x`, breaking changes
are allowed in a minor bump.

`1.0.0` is a condition, not a date: **WINNOWER-17 is done (a live run on
Windows, macOS and Linux) and the CLI flags and session file format have stopped
moving.**

## Open

Full detail for each of these lives in [TODO.md](TODO.md); this table is the ID
registry, not a second copy of the backlog.

| ID | Pri | Status | Title |
| --- | --- | --- | --- |
| WINNOWER-5 | P1 | open | Read keywords from a file, CSV or stdin, with per-keyword search terms (FR1) |
| WINNOWER-6 | P1 | open | Pixabay client with throttle, backoff and a 24h cache (FR2, non-functional) |
| WINNOWER-7 | P1 | open | Search runner: progress, zero-result flagging (FR3) |
| WINNOWER-8 | P1 | open | Download selections into a cache of originals (FR5) |
| WINNOWER-9 | P1 | open | Resize to a common size: crop, pad or fit (FR6) |
| WINNOWER-10 | P1 | open | Package the results with CREDITS.txt into a zip (FR7) |
| WINNOWER-11 | P1 | open | Selection UI: thumbnail grid, attribution, skip and retry (FR4) |
| WINNOWER-12 | P1 | open | CLI that runs the whole pipeline end to end |
| WINNOWER-13 | P2 | open | Resumable sessions (FR8) |
| WINNOWER-14 | P1 | open | README, CONTRIBUTING and a sample keyword list |
| WINNOWER-15 | P3 | open | Type or paste keywords into a text box in the UI (rest of FR1) |
| WINNOWER-16 | P3 | open | Screenshots of the selection UI for the README |
| WINNOWER-17 | P2 | open | Run against the live Pixabay API on Windows, macOS and Linux |

## Done

| ID | Date | Status | Title | Commit |
| --- | --- | --- | --- | --- |
| WINNOWER-1 | 2026-09-26 | done | Set up the repo workflow: CLAUDE.md, agents, ledger, changelog | — |
| WINNOWER-2 | 2026-09-26 | done | Lint commit messages with a commit-msg hook | — |
| WINNOWER-3 | 2026-09-26 | done | Derive release versions from commit types | — |
| WINNOWER-4 | 2026-09-26 | done | Scaffold the package: pyproject, config loading, lint, CI | — |
