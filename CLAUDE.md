# Winnower

A CLI plus local web page that searches Pixabay for a list of keywords, lets a person pick which
result to keep for each one, resizes the picks to a common size and hands back a zip. Python, MIT,
built to be open-sourced: every other user runs it with their own Pixabay API key.

Source of requirements: the original spec is summarised in [README.md](README.md); what is planned
lives in [TODO.md](TODO.md) and is registered by ID in [TASKS.md](TASKS.md).

## Branch before you work

Create the branch before the first edit, not after. When a request will change files here
(code, tests, `TODO.md`, docs), the first action is `git checkout -b <name>` off `main`,
so no work lands in the working tree while `HEAD` is on `main`.

- `<name>` is a plain `kebab-case-summary` of the change, prefixed with the task ID:
  `winnower-12-show-candidate-tags`. Never use a `p1-`/`p2-` priority prefix.
- Already on a non-`main` branch: stay on it, don't branch off a branch.
- Skip branching for read-only work: answering questions, reading code, running tests,
  investigating a bug without fixing it. Branch the moment an investigation becomes a fix.
- Don't ask permission — it's cheap and reversible, and this rule *is* the standing authorization.

The `commit` agent picks up from a feature branch: commit → push → land on `main` → cleanup.
See [.claude/agents/commit.md](.claude/agents/commit.md). Until the repo has an `origin` remote it
skips the push steps and lands locally.

## Task completion workflow

When completing a task:

1. **Make code changes** and commit them (with the task ID in the subject if applicable)
2. **Do bookkeeping before the summary** — update `TASKS.md` (move task to Done, add date) and delete the `TODO.md` entry in the closing commit
3. **Then provide the summary** — at this point, the task is truly finished, all work including administration is complete

This ensures "finished" is actually finished: feature work, commits, and bookkeeping are all done in the same flow, not deferred to a follow-up.

## Task IDs

Every task carries an ID `WINNOWER-<n>`, registered in [TASKS.md](TASKS.md), which holds the next
free number at the top — take it and increment it in the same commit that uses it.

**Claim the ID when the task is written down, not when it ships.** A new `TODO.md` entry gets one
immediately; the commit that closes it reuses that same ID. One ID per *task*: a feature landed over
two commits repeats the ID in both subjects, and a commit closing two tasks names both —
`(WINNOWER-6, WINNOWER-7)`.

- Commit subject: `feat: Show each candidate's tags under its thumbnail (WINNOWER-12)` — ID in
  parentheses at the end of the description, inside the subject line so the recovery grep keeps
  working. The format around it is Conventional Commits (see **Commit messages**).
- Branch name: `winnower-12-show-candidate-tags`. This is the one prefix that belongs there; the
  banned `p1-`/`p2-` priority prefix is a different thing and still banned.
- Not every commit needs one. Bookkeeping — a typo fix, a formatting pass, a `TODO.md` tidy — goes
  in unnumbered rather than inflating the counter.
- Never renumber, never reuse, and never rewrite an ID into an existing commit message. History is
  immutable; `TASKS.md` holds the mapping precisely so no rewrite is ever needed.

**When a task ships, its closing commit does the bookkeeping too — in that same commit, not a
follow-up.** Move the task's row from the Open table to the Done table in `TASKS.md` (status `done`,
today's date, Commit column `—`) and delete its `TODO.md` entry. `TODO.md` is the active backlog and
`TASKS.md` the permanent record, so a shipped task leaves `TODO.md` in the commit that lands it — a
finished item sitting in the backlog waiting for a second commit is the failure this avoids. This is
what the `commit` agent does at its step 2; the two stay in step. (A feature landed over several
commits removes the entry in the last one, the one that actually closes it.)

**Before committing work that closes a task**, verify that the commit includes both the feature
changes AND the bookkeeping: the TODO.md deletion and the TASKS.md update. If you've committed closing
work without the bookkeeping, go back and squash or amend it in rather than creating a follow-up
commit.

If `TASKS.md` and history disagree, history wins:

```sh
git log --oneline | grep -oE 'WINNOWER-[0-9]+' | sort -t- -k2 -n | tail -1
```

## Commit messages

Winnower follows [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/):

```text
<type>[optional scope][!]: <description> (WINNOWER-<n>)

[body]

[footers]
```

- **Type** is required and lowercase. `feat` (a new capability, MINOR) and `fix` (a bug, PATCH) are
  the two the spec defines — reach for them first. Also in use: `docs`, `refactor`, `perf`, `test`,
  `build`, `ci`, `chore`, for changes that genuinely ship no behaviour.
- **Scope** is optional and names the area of the code, never the task: `feat(resizer):`,
  `fix(client):`. Leave it off when the change is broad.
- **Description**: plain-language imperative summary, capitalised, no trailing full stop.
- **Task ID** goes last, in parentheses.
- **Body stays substantial**: what was wrong, why this approach, what was decided and rejected, how
  it was verified. Governing the subject line is not licence to write a one-line commit.
- **Breaking changes** take a `!` before the colon *and* a `BREAKING CHANGE:` footer saying what to
  migrate. Winnower is `0.x`, where a breaking change is allowed in a minor bump (see **Versions**) —
  the marker is what keeps it findable regardless.
- **Footers** are `Token: value` with hyphenated tokens; `BREAKING CHANGE` keeps its space.
  `Co-Authored-By:` is a footer like any other.

Worked examples:

```text
feat: Show each candidate's tags under its thumbnail (WINNOWER-12)

fix(client): Stop retrying a 400 as if it were a rate limit (WINNOWER-13)

refactor(session)!: Rename the picks key to selections (WINNOWER-14)

docs: Fix the setup steps for Windows
```

The last carries no ID on purpose — pure bookkeeping doesn't need one.

The rules are enforced, not just written down. `.githooks/commit-msg` runs
`scripts/commit_lint.py` on every commit, and CI runs it over every pushed commit, which catches
`--no-verify`. The hook only exists in a clone that has run, once:

```sh
git config core.hooksPath .githooks
```

Both are plain Python with no dependencies — deliberately: a contributor to a Python project should
not need Node installed to make a commit.

## Versions

Releases are git tags, `v<major>.<minor>.<patch>`. Which release contains a commit is derived with
`git describe --contains <sha>`, never recorded by hand.

The number is derived too, from the commit types the subjects carry:

```sh
python -m scripts.release             # dry run — print the plan
python -m scripts.release --write     # create the annotated tag
```

It reads every commit since the last tag reachable from `HEAD` and proposes the bump: a breaking
change takes the major, a `feat` the minor, a `fix`/`perf`/`revert` the patch. A range of nothing
but `docs`, `ci`, `chore`, `refactor`, `test`, `build` or `style` releases nothing — pass `--patch`
for a checkpoint tag anyway. A subject that does not parse counts as a patch and is printed by name,
since a version claiming less than it changed is the failure worth avoiding.

`--write` creates an annotated tag and nothing else: it refuses on a dirty tree, off `main`, or when
the tag exists, and it never pushes — pushing a tag publishes a release, so that stays a deliberate
manual decision. The command is printed.

The package version is derived from the tag as well (`setuptools-scm`), so there is no `version`
field to keep in step in `pyproject.toml`. An install from a checkout with no tags reports a
`0.1.dev…` version, which is honest.

`CHANGELOG.md` is deliberately not generated from commit subjects — it is written for people who
should not need the repo open. The `changelog-updater` agent keeps it that way; the release script
only prints a reminder to retitle the `## [Unreleased]` section.

`1.0.0` is a condition, not a date: **a full run against the live Pixabay API has been done on
Windows, macOS and Linux (WINNOWER-17), and the CLI flags and the session file format have stopped
moving.** Before then a breaking change is a minor bump and the script says it was demoted.

## Pixabay rules

The hard constraints from Pixabay's API terms and Content License. They are the reason several
design choices below exist, and a change that relaxes one is a decision, not a refactor.

- **Never commit a Pixabay API key, or an image downloaded through the tool.** Keys come from
  `PIXABAY_API_KEY` in the environment or a gitignored `.env`; `.env.example` ships instead. Test
  fixtures are generated in code (Pillow), never fetched. Never log the key, and never put it in a
  cache key, a session file or an error message — `pixabay_client` builds the query without it in
  anything it persists.
- **No permanent hotlinking.** Pixabay URLs may only *temporarily display* search results. Once a
  person has picked, the image is downloaded and everything after that reads the local copy.
- **Attribution is shown at selection time.** Every thumbnail carries the contributor's username
  and a link to its Pixabay page, and the zip carries a `CREDITS.txt`.
- **24-hour cache, 100 requests per 60 seconds per key.** Search responses are cached for 24h keyed
  by the exact query; requests go through a throttle that also reads `X-RateLimit-*`; a 429 backs
  off exponentially. The tool is human-paced by design and must not grow an unattended search loop:
  a retry is something a person clicks.
- **Never crash a run on a network failure, a 429 or a zero-result keyword.** Each is a state the
  person sees and can act on.

The libraries the original spec named for the client and downloader were evaluated and rejected —
the reasons, with what was measured, are in the header of `src/winnower/pixabay_client.py`.

## Tests

Two suites, both `pytest`, run from the repo root:

| Suite | Command | What it is |
| --- | --- | --- |
| Winnower | `python -m pytest tests` | Unit and integration tests for the package; the selection UI is exercised over real HTTP on an ephemeral port |
| Repo tooling | `python -m pytest scripts` | The commit-message linter and the release derivation |

`python -m pytest` alone runs both. Lint and format: `ruff check .` and `ruff format --check .`.
CI runs all of it on Linux, Windows and macOS, because portability is a requirement, not a hope.

**Tests never touch the network and never need an API key.** The client takes an injectable session,
clock and sleep; the fakes live in `tests/fakes.py`. An integration test that hit the real API would
fail for every contributor without a key and burn the maintainer's rate limit — the live check is
WINNOWER-17, run by hand.

What a change owes them:

- **Pure logic gets a unit test.** `keywords`, `resizer`, `packager`'s naming and the throttle are
  pure or nearly so for exactly this reason. If the logic worth testing is buried in the CLI or the
  server, extracting it is part of the change, not a follow-up.
- **A change to the HTTP contract of the selection UI gets a test through the real server**, not a
  mock of it.
- **A bug fix gets the test that would have caught it**, failing against the unfixed code and
  passing after. Where that is genuinely impossible, the commit body says so and what was done
  instead.
- **A flaky test is a bug in the test.** No `sleep`s racing a thread; inject the clock.
- **A change to the selection page is also looked at in a browser** before it lands. The server
  tests prove the contract; they do not prove the page reads right.

What owes nothing: documentation, `TODO.md`/`TASKS.md`/`CHANGELOG.md` bookkeeping, and formatting.

## Comments

Comments here record *why*, name the alternative that was rejected, and carry the numbers behind
the decision.

The header of `src/winnower/pixabay_client.py` is the reference. It says why the client is not the
library the spec named, and pays for it with the four failures reproduced against it: a 429 crashes
with `AttributeError` after a single request; a cache entry older than 24h raises `KeyError`; the API
key is written in plaintext into the cache file; `min_width=None` and an unescaped `&` go out in
the query string. It also says what would change the answer: a release of that library that fixes
them.

- **Write the why, not the what.** A comment restating the line below it is noise everywhere; here
  it is also camouflage, training the reader to skim the ones that matter.
- **Name the alternative that was rejected**, so the next person doesn't spend an afternoon
  rediscovering why it doesn't work.
- **Carry the numbers.** "The library is flaky" is an opinion; "four reproduced failures, all on the
  paths the spec's non-functional requirements name" is a finding.
- **Put it where the decision lives** — above the constant, the query, the guard — not in a document
  that will not be open when the line is edited.
- **Say what would change the answer**, where there is one.

## Where decisions live

Commit bodies and code comments, and nothing else. Separate decision records (`docs/adr/`) were
rejected for the same reason Watchr rejected them: the commit that made a decision and the comment
that guards the line already carry it, and a third home would fragment the story. An ADR also has no
way to go stale loudly — the code moves, the document doesn't, and nothing fails when they disagree.

`TODO.md`, `TASKS.md` and `CHANGELOG.md` are the exceptions, and they are not decision records: what
is planned, which ID names it, and what shipped for someone who should not need the repo open.
