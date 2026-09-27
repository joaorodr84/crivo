# Crivo TODO

## Priorities

Every item below carries a `[P1]`–`[P4]` tag:

- **P1 — do next.** Scoped, and the payoff is immediate.
- **P2 — queued.** Real value or real risk, but needs a decision made or a
  self-contained chunk of work first.
- **P3 — wanted, not urgent.** Worth building when there's room; nothing else
  depends on it.
- **P4 — parked.** Speculative or large-and-unscoped. Revisit when something
  forces the question.

Every item also carries an ID, registered in [TASKS.md](TASKS.md): `CRIVO-<n>` is
the current prefix, and `WINNOWER-<n>` is the retired one a handful of tasks
opened before the rename (CRIVO-1) keep permanently.
The ID is claimed when the item is written and stays with it through the commit
that closes it; the priority changes, the ID never does.

## Build the tool

The original requirements (FR1–FR8) split one task per module, in pipeline order.
Each lands as its own branch and commit.


## Later

- **[P2] WINNOWER-17 — Live run on three OSes.** No test hits the real API, so a
  small real run is what proves the client against Pixabay's actual responses.
  **Windows is done (2026-09-27)**: headers, filters, pages, bad-key signalling,
  CDN downloads under Winnower's User-Agent and real thumbnails in the page were
  all checked, and it found the crop bug fixed as WINNOWER-19. What was seen is
  recorded in the comments where each finding settles a decision. **The real 429
  is done too (2026-09-27)**: see the comment above `RATE_MARGIN` in
  `pixabay_client.py` for what provoking one showed — nothing crashed, and it
  turned up that a 429 carries none of the rate-limit headers a 200 does.
  **Still to do:** the same small run on macOS and Linux (`crivo run -k apple
  -k lighthouse -n 3 --work-dir <somewhere outside the repo>`, picking in the
  browser) — no genuine Linux or macOS box has been reachable to run it from
  yet. This is the `1.0.0` condition.
