# Crivo task ledger

Every task carries an ID. IDs are assigned once, never reused, and never
renumbered.

**The project was renamed from Winnower to Crivo (`CRIVO-1`).** The ID prefix
switched over with it: existing `WINNOWER-<n>` IDs (1–21, plus the still-open 16,
17 and 20) keep their prefix forever, but every task claimed from `CRIVO-1` onward
uses `CRIVO-<n>`. `WINNOWER-22`, the number that would have been next, is retired
unused rather than reassigned.

**Next ID to assign: `CRIVO-2`** (`WINNOWER-<n>` is frozen at 21 — the highest ever
used — and assigns no more numbers.)

If this file and history ever disagree, history wins:

```sh
git log --oneline | grep -oE 'CRIVO-[0-9]+' | sort -t- -k2 -n | tail -1
```

## How IDs work

- **An ID is claimed when a task is written down, not when it ships.** A new
  `TODO.md` entry gets its ID immediately; the commit that closes it reuses that
  same ID. Otherwise every item would be numbered twice — once as a plan, once
  as a commit — and the two numbers would name the same work.
- **One ID per task, not per commit.** A feature landed over two commits carries
  the same ID in both subjects. A commit closing two tasks names both.
- Commit subject: `feat: Show each candidate's tags under its thumbnail
  (CRIVO-12)` — Conventional Commits, with the ID in parentheses at the end.
  See **Commit messages** in `CLAUDE.md`.
- Branch name: `crivo-12-show-candidate-tags`. The ID prefix is wanted here;
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
| WINNOWER-16 | P3 | open | Screenshots of the selection UI for the README |
| WINNOWER-17 | P2 | in progress | Run against the live Pixabay API on Windows, macOS and Linux (Windows done) |
| WINNOWER-20 | P3 | open | Tidy the tags under each thumbnail: show each once, cap the list |

## Done

| ID | Date | Status | Title | Commit |
| --- | --- | --- | --- | --- |
| CRIVO-1 | 2026-09-27 | done | Rename the project from Winnower to Crivo | — |
| WINNOWER-1 | 2026-09-26 | done | Set up the repo workflow: CLAUDE.md, agents, ledger, changelog | — |
| WINNOWER-2 | 2026-09-26 | done | Lint commit messages with a commit-msg hook | — |
| WINNOWER-3 | 2026-09-26 | done | Derive release versions from commit types | — |
| WINNOWER-4 | 2026-09-26 | done | Scaffold the package: pyproject, config loading, lint, CI | — |
| WINNOWER-18 | 2026-09-27 | done | Give the release tests a git identity so they pass on CI runners | — |
| WINNOWER-5 | 2026-09-27 | done | Read keywords from a file, CSV or stdin, with per-keyword search terms (FR1) | — |
| WINNOWER-6 | 2026-09-27 | done | Pixabay client with throttle, backoff and a 24h cache (FR2, non-functional) | — |
| WINNOWER-7 | 2026-09-27 | done | Search runner: progress, zero-result flagging (FR3) | — |
| WINNOWER-8 | 2026-09-27 | done | Download selections into a cache of originals (FR5) | — |
| WINNOWER-9 | 2026-09-27 | done | Resize to a common size: crop, pad or fit (FR6) | — |
| WINNOWER-10 | 2026-09-27 | done | Package the results with CREDITS.txt into a zip (FR7) | — |
| WINNOWER-11 | 2026-09-27 | done | Selection UI: thumbnail grid, attribution, skip and retry (FR4) | — |
| WINNOWER-12 | 2026-09-27 | done | CLI that runs the whole pipeline end to end | — |
| WINNOWER-14 | 2026-09-27 | done | README, CONTRIBUTING and a sample keyword list | — |
| WINNOWER-13 | 2026-09-27 | done | Resumable sessions (FR8) | — |
| WINNOWER-19 | 2026-09-27 | done | Fix crop failing on real photos: float rounding made the source box offset negative | — |
| WINNOWER-21 | 2026-09-27 | done | Fix a rejected POST losing its response: read the body before answering | — |
| WINNOWER-15 | 2026-09-27 | done | Type or paste keywords into a text box in the UI (rest of FR1) | — |
