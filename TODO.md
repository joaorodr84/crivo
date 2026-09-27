# Winnower TODO

## Priorities

Every item below carries a `[P1]`–`[P4]` tag:

- **P1 — do next.** Scoped, and the payoff is immediate.
- **P2 — queued.** Real value or real risk, but needs a decision made or a
  self-contained chunk of work first.
- **P3 — wanted, not urgent.** Worth building when there's room; nothing else
  depends on it.
- **P4 — parked.** Speculative or large-and-unscoped. Revisit when something
  forces the question.

Every item also carries a `WINNOWER-<n>` ID, registered in [TASKS.md](TASKS.md).
The ID is claimed when the item is written and stays with it through the commit
that closes it; the priority changes, the ID never does.

## Now

- **[P1] CRIVO-1 — Rename the project from Winnower to Crivo.** New repo home:
  https://github.com/joaorodr84/crivo.git. One task, full rebrand, so nothing is
  left half-renamed:
  - **Package & CLI**: `pyproject.toml` `name = "crivo"`; move `src/winnower/` to
    `src/crivo/`; `[project.scripts]` entry becomes `crivo = "crivo.cli:cli"`;
    update every `import winnower` / `from winnower...` in `src/` and `tests/`.
  - **Defaults that leak the old name**: `config.py`'s `work_dir` default
    (`.winnower` → `.crivo`) and `output` default (`winnower.zip` → `crivo.zip`);
    `pixabay_client.py`'s `USER_AGENT` string (currently
    `winnower/{version} (+github.com/joaorodr84/winnower)`) repointed at the new
    repo.
  - **Docs**: README.md, CONTRIBUTING.md, docs/requirements.md, CLAUDE.md itself
    (title, branch-naming examples, commit examples, every other `winnower`
    reference), `.claude/agents/*.md` (commit, changelog-updater, test-runner all
    name Winnower today). CHANGELOG.md's already-dated entries keep saying
    Winnower — that's accurate history, not a typo — only its ongoing header
    text and future entries say Crivo.
  - **Task-ID scheme**: the prefix switches to `CRIVO-<n>`, starting at this task
    (`CRIVO-1`, this entry). Update `scripts/commit_lint.py`'s regexes
    (`TASK_ID_LAST`, the "subject mentions winnower" check) and
    `scripts/test_commit_lint.py`, and TASKS.md's/CLAUDE.md's own description of
    the scheme. **Leave existing `WINNOWER-<n>` IDs exactly as they are** —
    including the still-open WINNOWER-16, WINNOWER-17 and WINNOWER-20 — IDs are
    never renumbered, so they keep the old prefix permanently even once the app
    is Crivo. `WINNOWER-22` is retired unused rather than reassigned.
  - **Repo/remote**: `git remote set-url origin
    https://github.com/joaorodr84/crivo.git` and push, so the renamed history
    lands in the new repo rather than the old one.
  - **Verification**: `python -m pytest`, `ruff check .`, `ruff format --check .`,
    and a manual `crivo run -k ...` smoke test, since this touches the entry
    point and every import path. Not in scope: reserving/publishing the `crivo`
    name on PyPI — the project isn't published yet.

## Build the tool

The original requirements (FR1–FR8) split one task per module, in pipeline order.
Each lands as its own branch and commit.


## Later

- **[P3] WINNOWER-20 — Tidy the tags under each thumbnail.** Found in the live
  run: real Pixabay tags repeat and run long ("apple, apple, apple, apple, red,
  fruit, ..." to twenty words), which makes the cards uneven and noisy. Show each
  tag once (case-insensitively) and cap the list at about eight; the full list can
  stay in the image's alt text. Display only; `Candidate.tags` is unchanged.
- **[P3] WINNOWER-16 — README screenshots.** The spec asks for a couple of
  screenshots or GIFs of the selection UI. Needs a run against real results, so
  it waits on a key; fixtures generated in code would show a UI with nothing on
  it that a user recognises.
- **[P2] WINNOWER-17 — Live run on three OSes.** No test hits the real API, so a
  small real run is what proves the client against Pixabay's actual responses.
  **Windows is done (2026-09-27)**: headers, filters, pages, bad-key signalling,
  CDN downloads under Winnower's User-Agent and real thumbnails in the page were
  all checked, and it found the crop bug fixed as WINNOWER-19. What was seen is
  recorded in the comments where each finding settles a decision. **Still to do:**
  the same small run on macOS and Linux (`winnower run -k apple -k lighthouse
  -n 3 --work-dir <somewhere outside the repo>`, picking in the browser), and
  never provoked so far: a real 429 (it means using 100 requests inside a
  minute) and so the `RATE_MARGIN` guess. This is the `1.0.0` condition.
