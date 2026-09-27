# Contributing to Crivo

Thanks for looking. Crivo is small on purpose, so most changes are easy to review. This is
what you need to know to make one.

## Set up

Python 3.10 or newer.

```console
$ git clone https://github.com/joaorodr84/crivo.git
$ cd crivo
$ python -m venv .venv
$ .venv/bin/pip install -e ".[dev]"      # Windows: .venv\Scripts\pip install -e ".[dev]"
$ git config core.hooksPath .githooks    # once per clone: checks your commit messages
```

You do **not** need a Pixabay API key to work on Crivo, and you should not use yours in a
test (see below). You only need one to run the tool for real.

## Run the checks

```console
$ python -m pytest          # the package (tests/) and the repo tooling (scripts/)
$ ruff check .
$ ruff format --check .     # `ruff format .` fixes it
```

CI runs all of this on Linux, Windows and macOS, on Python 3.10 and 3.13. Portability is a
requirement of the project, so a change that only works on your system is not finished. Paths,
line endings, file names Windows will not accept and files that are still open when you try to
rename them are the usual ways to find out.

## What a change needs

- **Tests never touch the network and never need an API key.** The Pixabay client takes an
  injectable session, clock and sleep, and `tests/fakes.py` has the fakes. Test images are
  generated with Pillow, never downloaded. A test that called the real API would fail for every
  other contributor and use up the maintainer's rate limit.
- **Logic gets a unit test.** If the logic worth testing is buried in the CLI or the server,
  moving it out is part of the change.
- **A change to the selection page's HTTP contract is tested through the real server** (there
  are examples in `tests/test_selector_ui.py`, which run it on an ephemeral port), not a mock
  of it.
- **A bug fix comes with the test that would have caught it**, one that fails without the fix.
- **A change to the selection page is also looked at in a browser** before it lands. The server
  tests prove the contract; they do not prove the page reads well. The layout is checked
  narrow and wide, in light and dark.
- **No flaky tests.** No `sleep` racing a thread; inject the clock.

Comments in this codebase explain *why*, not what. A comment that restates the line below it is
noise. A good one names the alternative that was rejected and the number behind the decision:
the header of `src/crivo/pixabay_client.py` is the model.

## Pixabay's rules are design constraints

These come from Pixabay's API terms and content licence, and several design choices exist because
of them. A change that relaxes one is a decision to discuss first, not a refactor.

- **Never commit an API key, or an image downloaded through the tool.** Keys come from the
  environment or a gitignored `.env`. Never log a key, or put one in a cache key, a session file
  or an error message.
- **No permanent hotlinking.** Pixabay URLs may only *temporarily display* results. Once a
  person has picked, the image is downloaded and everything after that uses the local copy.
- **Attribution is shown at selection time**, and the zip carries a `CREDITS.txt`.
- **Search responses are cached for 24 hours**, and requests stay under 100 per 60 seconds per
  key. Crivo is human-paced by design: it must not grow an unattended search loop, and a
  retry is something a person clicks.
- **A network failure, a 429 or a keyword with no results never crashes a run.** Each is a state
  the person sees and can act on.

## Commits

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/):

```text
feat: Show each candidate's tags under its thumbnail (CRIVO-12)
fix(client): Stop retrying a 400 as if it were a rate limit (CRIVO-13)
docs: Fix the setup steps for Windows
```

The subject is `<type>[scope]: <Capitalised imperative description>`, with no full stop. Use
`feat` and `fix` for behaviour, and `docs`, `refactor`, `perf`, `test`, `build`, `ci` or `chore`
for changes that ship none. The body is where the reasoning goes: what was wrong, why this
approach, what you considered and rejected, how you checked it. A one-line commit for a real
change is under-written here.

Work is tracked as tasks with an ID like `CRIVO-12`, listed in [TASKS.md](TASKS.md) and
described in [TODO.md](TODO.md). If you are working on a task, put its ID at the end of the
subject. Pure housekeeping (a typo, a formatting pass) does not need one. `CRIVO` is the current
prefix; a handful of tasks opened before the rename (CRIVO-1) still carry the retired `WINNOWER-<n>`
prefix and keep it permanently, since an ID is never renumbered.

The commit-message hook runs `scripts/commit_lint.py`, and CI runs it over every pushed commit.
Both are plain Python with no dependencies, so you do not need Node installed to make a commit.

## Releases

Releases are git tags, and the version number is worked out from the commit types since the last
tag rather than typed by hand:

```console
$ python -m scripts.release             # dry run: prints the plan
$ python -m scripts.release --write     # creates the annotated tag (never pushes it)
```

`CHANGELOG.md` is written for people who should not need the repository open, so it describes
what changed in the tool's behaviour, not commit subjects.
