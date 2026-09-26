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

## Build the tool

The original requirements (FR1–FR8) split one task per module, in pipeline order.
Each lands as its own branch and commit.

- **[P1] WINNOWER-7 — Search runner (FR3).** Loop the keywords, report
  "n / total", flag zero-result and failed keywords without stopping the run.
- **[P1] WINNOWER-8 — Downloader (FR5).** Fetch `largeImageURL` (or `imageURL`
  with full API access) to a cache of originals; atomic writes; skip what is
  already cached.
- **[P1] WINNOWER-9 — Resizer (FR6).** Pillow: crop, pad or fit to the target
  size; PNG default, JPEG and WebP optional; alpha flattened for JPEG; EXIF
  orientation respected.
- **[P1] WINNOWER-10 — Packager (FR7).** Filesystem-safe unique names,
  `CREDITS.txt`, one zip.
- **[P1] WINNOWER-11 — Selection UI (FR4).** Stdlib `http.server` and vanilla
  JS: thumbnails with contributor and Pixabay link, pick one or more, skip,
  retry (next page, or a new term), running count, finish.
- **[P1] WINNOWER-12 — CLI.** `winnower run KEYWORDS` wiring search → select →
  download → resize → package, with every option from FR2 and FR6.
- **[P2] WINNOWER-13 — Resumable sessions (FR8).** Persist keywords, candidates
  and picks so a long run can be paused; `--resume`, and refuse to overwrite an
  unfinished session by accident.
- **[P1] WINNOWER-14 — Docs.** README (setup, own-API-key statement, compliance
  note, licence caveat), CONTRIBUTING, `examples/keywords_sample.txt`.

## Later

- **[P3] WINNOWER-15 — Text-box keyword entry.** FR1 also asks for a pasted or
  typed list in a text box. The CLI accepts stdin and `-k`, which covers the
  paste case, but a UI start page that takes the list needs the search to move
  behind the server so the page can be open while it runs.
- **[P3] WINNOWER-16 — README screenshots.** The spec asks for a couple of
  screenshots or GIFs of the selection UI. Needs a run against real results, so
  it waits on a key; fixtures generated in code would show a UI with nothing on
  it that a user recognises.
- **[P2] WINNOWER-17 — Live run on three OSes.** No test hits the real API, so
  nothing yet proves the client against Pixabay's actual responses and headers,
  or that the CDN serves downloads to Winnower's User-Agent. Do one small real
  run on Windows, macOS and Linux and record what was seen. This is the
  `1.0.0` condition.
