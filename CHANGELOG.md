# Changelog

All notable changes to Crivo are documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Entries below are grouped by date of the work rather than by version number.
Versions are git tags derived from commit types (see `CLAUDE.md`), and a release
is cut by retitling the `## [Unreleased]` section, not by generating this file.

## [Unreleased]

Open work is tracked in [TODO.md](TODO.md), and registered by ID in
[TASKS.md](TASKS.md).

## 2026-09-27

### Added

- `winnower run` (also `python -m winnower`) takes a list of keywords, searches
  Pixabay for each, opens a page in your browser to pick images, and writes the
  picks, resized to a common size, to a zip. This is the first version that can
  be used from start to finish. It needs your own Pixabay API key.
- Keywords come from a `.txt` file (one per line), a `.csv` file with a header
  row, standard input (`-`), or `-k` on the command line, repeated for several.
  Blank lines and lines starting with `#` are ignored. Write `label | search
  term` to name the result after one thing and search for another. For a CSV,
  the label is read from a column headed `keyword` or `label` (else the first
  column) and the search term from one headed `term` or `search` (else the
  label), or name the columns with `--column` and `--term-column`. Comma,
  semicolon and tab separators are all understood. A label repeated in a
  different case is dropped and reported, because result files are named after
  labels.
- Searching shows progress as `n / total`. A keyword with no results, or whose
  search failed, is flagged and shown on the page for you to retry with another
  term, instead of stopping the run. The run pauses, and says why, when Pixabay
  refuses the API key, when the rate limit is used up, or when Pixabay cannot be
  reached three times in a row. Everything found so far is kept, and it never
  loops on its own.
- Search filters: `--image-type`, `--orientation`, `--category`, `--colors`,
  `--min-width`, `--min-height`, `--safesearch`, `--editors-choice`, `--order`
  and `--lang`, plus `--term-template` (for example `'{term} icon'`) to wrap
  every search term. Options are checked before the first request is made, so a
  typo does not cost a search.
- Search results are cached for 24 hours, so running the same list again does
  not spend requests, and requests are held to Pixabay's limit of 100 per
  minute.
- The API key is read only from the `PIXABAY_API_KEY` environment variable or a
  `.env` file, never from a command-line flag, where it would end up in shell
  history and the process list. It is not written to the cache and does not
  appear in error messages.
- The selection page shows each candidate with its contributor's name and a link
  to its Pixabay page. For each keyword you can pick an image, skip it, search
  again with a different term, or load more results without losing your picks.
  `--multiple` allows more than one pick per keyword, and `--no-browser` prints
  the address instead of opening it. The page is served on this computer only
  and needs a random token that is different on every run, so other programs and
  other websites cannot use it.
- Picked images are downloaded once the page is finished, and each one is
  checked to be a complete, readable image before it is used, so a dropped
  connection or an error page never ends up as a result. Downloads are kept in
  the work directory (`.winnower` by default, or `--work-dir`) and reused.
- Picks are resized to `--size` (default 512x512) by `--mode`: `crop` fills the
  size and cuts the overflow, `pad` fits inside and fills the rest, and `fit`
  fits inside without padding, so sizes are not uniform. Output is PNG, JPEG or
  WebP (`--format`). `--background #RRGGBB` sets the fill for `pad`, and what a
  JPEG is flattened onto; the default is transparent, or white for JPEG.
- The result is a zip (`winnower.zip`, or `-o`) of the resized images, named
  after each keyword's label, with a `CREDITS.txt` naming each image's
  contributor and Pixabay page. An existing zip is not replaced unless you pass
  `--overwrite`, and this is checked before searching, so you are not asked to
  pick images and then told the file exists.
- Exit codes: 0 when done; 1 for something you can fix, such as a bad option,
  a missing keyword file or API key, an existing output file, or no image being
  produced; 3 when the zip was written but some picked images are missing from
  it, which are listed above the result; 130 when interrupted with Ctrl-C.
  Searches and downloads stay cached after an interruption, and your picks are
  saved too (see the session entries below).
- Your searches and picks are saved to `session.json` in the work directory
  after every click, so a long run can be put down and picked up later. `winnower
  run --resume` continues it: it needs no keyword file, does not search again
  for anything already searched, and brings the page back as you left it. The
  options that shape the output (`--size`, `--mode`, `--format`, `-o`) are taken
  from the new command, so you can change your mind about them. The file holds
  Pixabay's image addresses and the keywords, and never the API key.
- Starting a new run while an unfinished session is saved is refused, so a long
  afternoon of picking is not thrown away by accident. This is checked before
  any request is spent. Pass `--restart` to discard it and start over. A session
  whose zip has been written is finished with, and no longer blocks the next
  run.
- Resuming a session more than a day old still works, but warns that Pixabay's
  image addresses are meant for short-term use, so some thumbnails or downloads
  may no longer load; a keyword can be searched again from the page. A session
  file that cannot be read is reported with `--restart` as the way out, and
  `--resume` is refused, with a reason, when there is nothing saved or the
  saved session is already complete.
- If the session file cannot be saved (a full disk, a read-only folder), this is
  reported once at the end and the run carries on, since the picks are still
  held in memory.
- `winnower run` with no keywords at all opens the page with a box to type or
  paste them into, in the same format as a `.txt` file (`label | search term`,
  `#` comments). The search then runs behind the page: it shows how far it has
  got, each keyword appears as soon as it is searched, and you can start
  choosing before the rest are done. A list that cannot be used is refused with
  the reason next to the box, and you can try again. If you close the terminal
  mid-search, `winnower run --resume` brings back everything found so far, with
  the keywords not yet reached marked as not searched.

### Changed

- The number of images shown per keyword (`-n`, `--candidates`) must be between
  3 and 200, and defaults to 5, not the 1 to 10 of the original idea. Pixabay
  refuses fewer than 3 results per page, and showing fewer than were fetched
  would make "more results" skip some.
- Pressing Ctrl-C during `winnower run` now says that your picks are saved and
  that `winnower run --resume` continues them, instead of only mentioning the
  cache.

### Fixed

- Cropping (`--mode crop`, the default) no longer fails on some real photographs
  with "box offset can't be negative". A rounding error far too small to see, in
  working out which part of the picture to keep, made two of the three
  photographs in the first run against Pixabay fail to resize, so their picks
  were left out of the zip.
- The selection page no longer occasionally shows a network error instead of a
  clear message when a request is refused (a wrong token or address). On a busy
  computer the refusal could be lost when the connection was closed with the
  request's body still unread; the body is now always read first.
