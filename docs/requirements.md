# Crivo — Requirements

Sep 26, 2026 · @João

A desktop/CLI tool that searches Pixabay for a list of keywords, lets the user pick which results to keep, resizes them to a consistent size, and delivers them as a zip file. Built to be open-sourced on GitHub.

## 1. Overview

**Working name:** Pixabay Keyword Image Picker (rename freely)

**Problem it solves:** manually searching Pixabay's website for dozens or hundreds of keywords, downloading candidates one at a time, and resizing them by hand is slow and error-prone. This tool automates the repetitive parts (searching, fetching, resizing, packaging) while keeping a human in the loop for the one step that actually needs judgment: picking which image best represents each keyword.

**Primary user:** a developer or content creator who has a list of words/phrases (e.g. game content, a course glossary, a product catalog) and needs one representative image per term, in a consistent size, ready to drop into a project.

**Core flow:** provide a list of keywords → set how many candidates to fetch per keyword → review thumbnails and pick one (or more) per keyword → app resizes all selections to a common target size → app zips the results and hands back a downloadable archive.

## 2. Pixabay API Compliance

**Verdict: yes, this is authorized** — provided the app is built to the constraints below. Several existing open-source Pixabay wrapper tools already follow this same pattern, so it's a well-trodden path.

### Why it's allowed

- The Pixabay API terms (pixabay.com/api/docs) permit searching and retrieving Content for free. The app doesn't redistribute Pixabay's library — it's a tool that each end user runs with their own API key, exactly like the official examples in Pixabay's docs.
- The Pixabay Content License (pixabay.com/service/license) grants an irrevocable, worldwide, non-exclusive, royalty-free right to use, download, copy, modify or adapt Content for commercial or non-commercial purposes, with no attribution required.
- Resizing images is explicitly covered under "modify or adapt."

### Hard constraints the app MUST follow

| Rule (from Pixabay's own docs) | What it means for this app |
| --- | --- |
| No permanent hotlinking — Pixabay image URLs may only be used to temporarily display search results; images must be downloaded before real use | The app must download selected images to local storage before resizing/zipping. Never ship a build that references live Pixabay URLs post-selection. |
| "Show your users where the images are from, whenever search results are displayed" | Display each thumbnail with its Pixabay page URL and contributor username during the selection step. Not legally required by the Content License itself, but it's Pixabay's one ask in exchange for free API use. |
| Requests must be cached for 24h; do not send lots of automated queries; systematic mass downloads are not allowed | The app is human-paced by design (one search batch, human reviews, human picks) — not a scraper. Still: cache search responses locally for repeat runs, and don't auto-loop searches with no user interaction between them. |
| Rate limit: 100 requests per 60 seconds per API key | Build in a request queue/throttle and handle HTTP 429 with backoff. |
| Content License does not allow selling or distributing Content as digital Content without adding any additional elements or otherwise adding value (i.e. reselling it standalone, unmodified, as if it were stock media itself) | Fine here: each user makes the request under their own key for their own downstream project, not receiving a bundle of raw Pixabay content from you. Just don't ship the repo with a folder of pre-downloaded Pixabay images bundled in — keep the repo to code only. |
| Content may contain trademarks, logos, or recognizable people that carry separate rights Pixabay doesn't clear | Out of scope for the tool to detect automatically; note it in the README as a user responsibility to eyeball results, especially for commercial projects. |

### Open-source specific note

Since the app is meant for others to run themselves: never commit a Pixabay API key to the repository. Each user must supply their own key (via .env, config file, or CLI prompt — see §6). This is both a Pixabay ToS requirement (keys are tied to individual accounts and their rate limits) and standard open-source hygiene.

## 3. Functional Requirements

### FR1 — Keyword input

- Accept a list of keywords: a text file (one per line), a CSV column, or pasted/typed list in a text box.
- Support optional per-keyword overrides (e.g. a specific search term different from the display label — useful when the label is a game key like `hot-dog` but the search term should be "hot dog").

### FR2 — Search configuration

- Global setting: number of candidate images to fetch per keyword (e.g. 1–10).
- Filter options exposed from the Pixabay API: `image_type` (photo / illustration / vector / all), `orientation`, `category`, `colors`, `min_width`/`min_height`, `safesearch`.
- Global or per-keyword search-term override (see FR1).

### FR3 — Search execution

- For each keyword, call the Pixabay Search Images endpoint and retrieve the requested number of candidates (respecting Pixabay's 500-result-per-query cap and per\_page limits).
- Show progress (e.g. "12 / 40 keywords searched").
- Handle keywords with zero results gracefully (flag them, let the user retry with a different term).

### FR4 — Selection UI

- Display each keyword's candidates as a thumbnail grid, labeled with the keyword.
- Each thumbnail shows: preview image, Pixabay page link, contributor username (attribution — see §2).
- User can select exactly one (or optionally more than one, if multiple images per keyword is desired) image per keyword by clicking/checking it.
- Support "skip this keyword" and "none of these, retry search" actions.
- Show a running count of keywords completed vs. remaining.

### FR5 — Download and caching

- Once selected, download the full-resolution version the user's API access tier allows (`largeImageURL` by default; `imageURL`/`vectorURL` if the account has approved full API access) to local disk.
- Cache downloaded originals so re-runs don't re-fetch unchanged selections.

### FR6 — Resize/normalize

- Resize all selected images to a common target size (configurable, e.g. 512×512) using a consistent method (configurable: fit/crop/pad) so the final set is uniform.
- Preserve aspect ratio with letterboxing/padding as an option, since source images vary in orientation.
- Output format configurable (PNG default, to preserve transparency where present; JPEG optional).

### FR7 — Packaging and delivery

- Name each output file after its keyword (e.g. `hot-dog.png`), sanitized for filesystem safety.
- Zip all resized images into a single archive.
- Include a `CREDITS.txt` (or `.json`) in the zip listing, per file: keyword, Pixabay page URL, contributor username — satisfies Pixabay's attribution request and gives the user a paper trail if questions come up later.
- Provide the zip as a downloadable file (CLI: written to an output path; GUI/web: a download prompt).

### FR8 (optional/stretch) — Resumability

- Persist progress (which keywords are done, what was selected) so a long run can be paused and resumed without re-searching everything.

## 4. Technical Architecture

### Suggested stack (VS Code friendly, easy for others to run)

- **Language:** Python or Node.js — both have great VS Code support, simple image libraries, and are easy for open-source contributors to pick up. Python + Pillow is a common choice for the resize step; Node + Sharp is a fast alternative.
- **UI options** (pick one for v1):
  - *Simplest:* CLI tool that opens the thumbnail grid in the default browser (a small local web server) for selection, since a pure terminal UI can't display images.
  - *Richer:* a small local web app (e.g. Flask/FastAPI + a plain HTML/JS front end, or Node + Express) — same idea, just self-contained.
  - *Desktop:* Electron or a Python GUI (PyQt/Tkinter) if a true desktop app is preferred — more setup, not necessary for v1.

### Module breakdown

| Module | Responsibility |
| --- | --- |
| `config` | Loads API key (from `.env`/env var), target image size, resize mode, output path, per-run settings |
| `pixabay_client` | Thin wrapper around the **`pixabay_python`** library (PyPI, Apache 2.0) — handles auth, search, and `downloadList()` for the selected images. No custom HTTP/rate-limit/backoff code needed; the library covers it. |
| `search_runner` | Loops the keyword list, calls `pixabay_client` per keyword, collects candidates, reports progress, flags zero-result keywords |
| `selector_ui` | **Custom-built — no existing tool covers this.** Renders candidates for review (thumbnail grid), captures the user's picks, shows attribution, supports skip/retry |
| `downloader` | Delegates to `pixabay_python`'s `downloadList()` for the final full-resolution fetch of each selection (never keeps using the live Pixabay URL after this point) |
| `resizer` | Thin wrapper around **Pillow** (PyPI, MIT-style license) — resize/fit/crop/pad to the target size and format |
| `packager` | **Custom-built.** Writes `CREDITS.txt`, zips the output folder (stdlib `zipfile`), hands back the archive path |
| `cli` / `app` entrypoint | Wires the above together, handles arguments/config, orchestrates the run |

This is a straight top-to-bottom pipeline (search → select → download → resize → package) with one loop-back point (retry a keyword's search) — no need for a more complex architecture.

### Dependencies (use as-is — don't reinvent these)

- **`pixabay_python`** (PyPI, Apache 2.0) — Pixabay API client: `searchImage()`, `download()`, `downloadList()`. Covers FR3 and FR5 almost entirely.
  - Alternative: **`pixabay`** by Lukas0025 (PyPI, Apache 2.0) — same role, slightly different API shape (`.query()` / `.download()` per result). Pick whichever style you prefer; both are safe to depend on.
- **Pillow** (PyPI, permissive license, the de facto standard for Python image work) — resize/crop/pad for FR6. No need to write custom resize math.
- Standard library `zipfile` — archive creation for FR7, no dependency needed.
- For `selector_ui`: a small local web server (Flask/FastAPI in Python, or Express in Node) or a plain `http.server` + vanilla JS front end if you want zero extra dependencies — this part has no existing library to lean on since it's the custom piece.

### Prior art reviewed (reference only — not used as dependencies)

| Project | Why it was considered | Why it's reference-only |
| --- | --- | --- |
| `crawler_pixabay` (zzhanghub) | Keyword-JSON → Pixabay → download pattern, close to FR1–FR3 | No declared license, unversioned single script; pattern is useful, code isn't reused |
| `pixabay-py` (qodzero) | Basic Pixabay wrapper | No declared license, unmaintained; superseded by the properly licensed PyPI packages above |
| Bulk Image Downloader (Daviddix) | Keyword search → preview → select → download-as-pack UX, close to FR4 | Built on the Unsplash API with a React/Next stack; good UX reference for the selection screen, not directly reusable code |
| "Bulk Image Resizer" (Gumroad) | Batch resize tool | Proprietary/freemium (3-run free limit) — explicitly not used; Pillow replaces it entirely |
| ImageHarvest Pro / ImageHarvester / Image Harvest (older tools) | Same general "search + zip" concept | Different sources (Google Images scraping, plant-image analysis) or different scope; confirms the concept isn't novel, but none solve this exact keyword-batch + human-selection + resize + zip combination |

**Net effect:** of the original module list, `pixabay_client`, `downloader`, and `resizer` are now thin wrappers around existing, properly licensed libraries rather than modules to build and test from scratch. `selector_ui` and `packager` remain fully custom — that's the actual app being built here.

## 5. Non-Functional Requirements

- **Rate limiting:** never exceed 100 requests/60 seconds per Pixabay API key; queue requests and read the `X-RateLimit-*` response headers to self-throttle before hitting a 429.
- **Caching:** cache search results for at least 24 hours per Pixabay's terms, keyed by the exact query parameters, so repeated runs on the same keyword list don't re-hit the API needlessly.
- **Error handling:** graceful handling of network failures, HTTP 429 (rate limit) with exponential backoff, and zero-result searches — none of these should crash the whole run.
- **Performance:** searching/downloading should be async or batched where possible so the UI doesn't freeze while waiting on network calls, but throttled to respect the rate limit above.
- **Portability:** should run on Windows/macOS/Linux with a documented setup (this matters for an open-source project with unknown contributors' environments).
- **No secrets in source control:** `.env` (or equivalent) must be gitignored; a `.env.example` should ship instead.
- **Data privacy:** the tool only talks to the Pixabay API and the local filesystem — no telemetry, no third-party data collection, unless explicitly added and disclosed later.

## 6. Open-Source Project Setup

### Repository structure (suggested)

```
pixabay-keyword-picker/
├── README.md              # setup, usage, screenshots, compliance note
├── LICENSE                # e.g. MIT
├── .env.example           # PIXABAY_API_KEY=your_key_here
├── .gitignore             # must exclude .env
├── requirements.txt / package.json
├── src/
│   ├── config.py
│   ├── pixabay_client.py
│   ├── search_runner.py
│   ├── selector_ui.py
│   ├── downloader.py
│   ├── resizer.py
│   └── packager.py
├── tests/
└── examples/
    └── keywords_sample.txt
```

### README must include

- What the tool does and a quick-start (install, add API key, run).
- **A clear statement that each user needs their own free Pixabay API key** (with a link to pixabay.com/api/docs) — the project doesn't ship or share one.
- A short compliance note summarizing §2 (no hotlinking, attribution shown during selection, rate limits respected, images are downloaded for the user's own use under the Pixabay Content License) so downstream users understand their own obligations too.
- Contribution guidelines (CONTRIBUTING.md) if you want outside PRs.

### License choice

- MIT or Apache-2.0 are the standard choices for "anyone can use this as they wish" — both allow commercial and private use, modification, and redistribution of your code, with minimal obligations (MIT: just keep the copyright notice; Apache-2.0: same, plus an explicit patent grant).
- Note this covers your *code* only — it says nothing about Pixabay's Content License, which still governs anything a user downloads through the tool. Worth stating explicitly in the README to avoid confusion.

### Before publishing

- Scrub the git history for any API key you used during development.
- Include the sample keyword list and a couple of screenshots/GIFs of the selection UI — open-source projects get far more adoption when people can see what they're getting before installing.
