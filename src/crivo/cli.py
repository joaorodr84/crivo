"""`crivo run KEYWORDS`: search, pick, download, resize, zip.

The pipeline is a straight line with one loop back (retry a keyword's search, which lives
in the selection page), so this module is only wiring: parse the options into `Settings`,
run each stage, and say plainly what happened. Anything worth testing on its own lives in
the stage's module; what is tested here is that the stages are connected and that every
failure ends as a sentence and an exit code instead of a traceback.

Exit codes: 0 done; 1 something the person can fix (bad option, bad keyword file, no API
key, output file already there, no image could be produced); 2 unusable command line
(argparse's own); 3 the zip was written but some picked images are missing from it, listed
above it, so a script does not mistake a partial result for a complete one; 130 Ctrl-C.

The API key is deliberately not an option. A flag lands in shell history and in the process
list (see `config.py`), so it comes from `PIXABAY_API_KEY` or a gitignored `.env`.

Two things are checked before the first request is spent: every option (so a typo in
`--size` does not cost a full search) and that the output file can be written (so the person
is not made to pick forty images and then told the zip already exists).
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

import requests

from . import __version__
from .config import (
    CATEGORIES,
    IMAGE_TYPES,
    LANGUAGES,
    MAX_CANDIDATES,
    MIN_CANDIDATES,
    ORDERS,
    ORIENTATIONS,
    OUTPUT_FORMATS,
    RESIZE_MODES,
    ConfigError,
    SearchOptions,
    Settings,
    load_settings,
    parse_size,
)
from .downloader import Downloader
from .keywords import KeywordError, KeywordList, parse_lines, parse_text, read_keywords
from .packager import Entry, PackageError, write_zip
from .pixabay_client import PixabayClient, SearchCache, Throttle
from .resizer import extension, render
from .search_runner import Outcome, SearchRunner, Status
from .selection import SelectionError, SelectionSession
from .selector_ui import serve
from .session_store import SESSION_NAME, SavedSession, SessionError, SessionStore

EXIT_OK, EXIT_ERROR, EXIT_PARTIAL, EXIT_INTERRUPTED = 0, 1, 3, 130


@dataclass
class Deps:
    """What touches the outside world, so a test can replace all of it."""

    http: Any
    serve_fn: Callable[..., None]
    sleep: Callable[[float], None]
    clock: Callable[[], float]  # monotonic, for the rate limiter
    wall: Callable[[], float]  # wall time, for cache and session ages


def _size(text: str) -> tuple[int, int]:
    try:
        return parse_size(text)
    except ConfigError as err:
        raise argparse.ArgumentTypeError(str(err)) from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crivo",
        description=(
            "Search Pixabay for a list of keywords, pick one image for each in your browser, "
            "and get them back resized to a common size in a zip. Needs your own free Pixabay "
            "API key (https://pixabay.com/api/docs/) in PIXABAY_API_KEY or a .env file."
        ),
    )
    parser.add_argument("--version", action="version", version=f"crivo {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    run = commands.add_parser(
        "run", help="search, pick, download, resize and zip", description=run_help()
    )

    src = run.add_argument_group("keywords")
    src.add_argument(
        "keywords",
        nargs="?",
        metavar="KEYWORDS",
        help="a .txt file (one keyword per line, 'label | search term' to override), a .csv "
        "file (with a header row), or - for stdin. Leave it out, and -k, to paste the keywords "
        "into the page instead",
    )
    src.add_argument(
        "-k", "--keyword", action="append", default=[], metavar="TEXT",
        help="a keyword given directly, 'label | search term' allowed; repeat for several "
        "(instead of KEYWORDS)",
    )  # fmt: skip
    src.add_argument("--column", metavar="NAME", help="CSV column holding the label")
    src.add_argument("--term-column", metavar="NAME", help="CSV column holding the search term")

    find = run.add_argument_group("search")
    find.add_argument(
        "-n", "--candidates", type=int, metavar="N",
        help=f"images to show per keyword, {MIN_CANDIDATES}-{MAX_CANDIDATES} (default 5)",
    )  # fmt: skip
    find.add_argument(
        "--term-template", metavar="TEMPLATE",
        help="wrap every search term, e.g. '{term} icon'; {term} is replaced by each keyword's",
    )  # fmt: skip
    find.add_argument("--image-type", choices=IMAGE_TYPES)
    find.add_argument("--orientation", choices=ORIENTATIONS)
    find.add_argument("--category", choices=CATEGORIES)
    find.add_argument("--colors", metavar="LIST", help="comma-separated, e.g. red,blue")
    find.add_argument("--min-width", type=int, metavar="PX")
    find.add_argument("--min-height", type=int, metavar="PX")
    find.add_argument("--safesearch", action="store_true", help="only images safe for all ages")
    find.add_argument("--editors-choice", action="store_true", help="only Editor's Choice images")
    find.add_argument("--order", choices=ORDERS)
    find.add_argument("--lang", choices=LANGUAGES, metavar="CODE", help="search language")
    find.add_argument(
        "--multiple", action="store_true", help="allow more than one image per keyword"
    )

    out = run.add_argument_group("output")
    out.add_argument("--size", type=_size, metavar="WxH", help="target size (default 512x512)")
    out.add_argument(
        "--mode", dest="resize_mode", choices=RESIZE_MODES,
        help="crop: fill the size and cut the overflow; pad: fit inside and fill the rest; "
        "fit: fit inside, sizes not uniform (default crop)",
    )  # fmt: skip
    out.add_argument("--format", dest="output_format", choices=OUTPUT_FORMATS, help="default png")
    out.add_argument(
        "--background", metavar="#RRGGBB",
        help="fill for pad, and what JPEG is flattened onto (default: transparent, or white "
        "for JPEG)",
    )  # fmt: skip
    out.add_argument(
        "--full-size", action="store_true",
        help="download imageURL instead of largeImageURL (needs Pixabay full API access; "
        "others get the large one)",
    )  # fmt: skip
    resume = run.add_argument_group("sessions")
    resume.add_argument(
        "--resume", action="store_true",
        help="continue the saved session in the work directory: no keyword file, and no new "
        "searches for what was already searched",
    )  # fmt: skip
    resume.add_argument(
        "--restart", action="store_true",
        help="throw away an unfinished saved session and start a new run",
    )  # fmt: skip
    out.add_argument("-o", "--output", type=Path, metavar="ZIP", help="default crivo.zip")
    out.add_argument("--overwrite", action="store_true", help="replace the zip if it exists")
    out.add_argument(
        "--work-dir", type=Path, metavar="DIR",
        help="search cache and downloaded originals (default .crivo)",
    )  # fmt: skip
    out.add_argument(
        "--no-browser", action="store_true", help="print the address instead of opening it"
    )
    return parser


def run_help() -> str:
    return (
        "Searches every keyword, opens a page in your browser to pick images, then downloads, "
        "resizes and zips the picks. Search results are cached for 24 hours, so running the "
        "same list again does not spend requests."
    )


def _settings_from(args: argparse.Namespace, env: Mapping[str, str], dotenv: Path) -> Settings:
    filters: dict[str, Any] = {
        name: getattr(args, name)
        for name in ("image_type", "orientation", "category", "min_width", "min_height",
                     "order", "lang")
        if getattr(args, name) is not None
    }  # fmt: skip
    if args.colors:
        filters["colors"] = tuple(c.strip() for c in args.colors.split(",") if c.strip())
    if args.safesearch:
        filters["safesearch"] = True
    if args.editors_choice:
        filters["editors_choice"] = True
    overrides = {
        "candidates": args.candidates,
        "term_template": args.term_template,
        "size": args.size,
        "resize_mode": args.resize_mode,
        "output_format": args.output_format,
        "background": args.background,
        "full_size": True if args.full_size else None,
        "work_dir": args.work_dir,
        "output": args.output,
        "search": SearchOptions(**filters),
    }
    return load_settings(overrides, env=env, dotenv_path=dotenv)


def _keywords_from(args: argparse.Namespace, stdin: TextIO) -> KeywordList | None:
    """The keywords from the command line, or None to ask for them on the page."""
    if args.keyword and args.keywords:
        raise KeywordError("Give a keyword file or -k keywords, not both.")
    if args.keyword:
        return parse_lines(args.keyword, "-k")
    if not args.keywords:
        if args.column or args.term_column:
            raise KeywordError("--column and --term-column need a .csv file to read.")
        return None
    return read_keywords(
        args.keywords, column=args.column, term_column=args.term_column, stdin=stdin
    )


def _summary(outcomes: Sequence[Outcome]) -> str:
    counts = {s: sum(o.status is s for o in outcomes) for s in Status}
    parts = [f"{counts[Status.FOUND]} with results"]
    for status, phrase in ((Status.EMPTY, "found nothing"), (Status.FAILED, "failed"),
                           (Status.PENDING, "not searched")):  # fmt: skip
        if counts[status]:
            parts.append(f"{counts[status]} {phrase}")
    return ", ".join(parts)


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    dotenv_path: Path = Path(".env"),
    stdin: TextIO | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
    http_session: Any = None,
    serve_fn: Callable[..., None] = serve,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    wall: Callable[[], float] = time.time,
) -> int:
    """Run the command line and return the exit code. The keyword arguments are for tests."""
    out, err = out or sys.stdout, err or sys.stderr
    args = build_parser().parse_args(argv)
    say = lambda text="": print(text, file=out, flush=True)  # noqa: E731
    warn = lambda text: print(text, file=err, flush=True)  # noqa: E731
    try:
        return _run(
            args,
            os.environ if env is None else env,
            dotenv_path,
            stdin or sys.stdin,
            say,
            warn,
            Deps(
                http_session if http_session is not None else requests.Session(),
                serve_fn,
                sleep,
                clock,
                wall,
            ),
        )
    except (ConfigError, KeywordError, PackageError, SessionError, SelectionError) as problem:
        warn(f"crivo: error: {problem}")
        return EXIT_ERROR
    except KeyboardInterrupt:
        warn("\ncrivo: interrupted. Searches stay cached for 24 hours and downloaded "
             "images stay in the work directory, so a second run picks up cheaply.")  # fmt: skip
        return EXIT_INTERRUPTED


STALE_AFTER = 24 * 60 * 60  # Pixabay's image addresses are for short-term display


def _check_session(args, store: SessionStore, warn, wall) -> SavedSession | None:
    """Decide, before any request is spent, whether this run may go ahead.

    Returns the saved session to resume, or None for a fresh run.
    """
    if args.resume and args.restart:
        raise SessionError("--resume and --restart contradict each other; use one.")
    try:
        saved = store.load()
    except SessionError:
        # An unreadable file is exactly what --restart is for: the error message tells the
        # person to use it, so it has to be allowed to get past the file it names.
        if not args.restart:
            raise
        store.discard()
        saved = None
    where = store.path.parent
    if args.resume:
        if args.keywords or args.keyword:
            raise KeywordError("--resume reads the keywords from the saved session; give none.")
        if saved is None:
            raise SessionError(f"There is no saved session in {where} to resume.")
        if saved.completed:
            raise SessionError(
                f"The saved session in {where} is already complete (its zip was written). "
                "Start a new run from a keyword list."
            )
        if wall() - saved.saved_at > STALE_AFTER:
            warn(
                "That session is over a day old. Pixabay's image addresses are meant for "
                "short-term use, so some thumbnails or downloads may no longer load; a keyword "
                "can be searched again from the page."
            )
        return saved
    if saved is not None and not saved.completed:
        if not args.restart:
            raise SessionError(
                f"An unfinished session is saved in {where} ({saved.describe(wall())}). "
                "Continue it with --resume, or throw it away and start over with --restart."
            )
        store.discard()
    return None


def _run(args, env, dotenv, stdin, say, warn, deps: Deps) -> int:
    settings = _settings_from(args, env, dotenv)
    store = SessionStore(settings.work_dir / SESSION_NAME, now=deps.wall)
    saved = _check_session(args, store, warn, deps.wall)
    keywords = None if saved else _keywords_from(args, stdin)
    if settings.output.exists() and not args.overwrite:
        raise PackageError(
            f"{settings.output} already exists. Choose another name with -o, or pass --overwrite."
        )
    try:
        return _pipeline(args, settings, store, saved, keywords, say, warn, deps)
    except KeyboardInterrupt:
        hint = (
            "Your picks are saved: continue with `crivo run --resume`."
            if saved is not None or store.writes  # not just any file left by an earlier run
            else "Nothing had been saved yet."
        )
        warn(
            f"\ncrivo: interrupted. {hint} Searches stay cached for 24 hours and "
            "downloaded images stay in the work directory."
        )
        return EXIT_INTERRUPTED


def _pipeline(args, settings, store, saved, keywords, say, warn, deps: Deps) -> int:
    client = PixabayClient(
        settings.api_key,
        session=deps.http,
        throttle=Throttle(clock=deps.clock, sleep=deps.sleep),
        cache=SearchCache(settings.work_dir / "cache" / "search", now=deps.wall),
        sleep=deps.sleep,
    )
    runner = SearchRunner(client, settings)
    retry = lambda keyword, query, page: runner.search_one(keyword, term=query, page=page)  # noqa: E731

    def start_from_page(session: SelectionSession, text: str) -> None:
        """The person pasted keywords: add them all as not searched, then search behind the page."""
        found = parse_text(text, "the box")  # a KeywordError here is shown next to the box
        notes = []
        if found.duplicates:
            notes.append(f"Ignored {len(found.duplicates)} repeated keyword(s), keeping the first.")
        session.populate(
            [Outcome(k, Status.PENDING, settings.search_term(k.term)) for k in found], notes
        )
        say(f"Searching {len(found)} keyword{'s' if len(found) != 1 else ''} on Pixabay...")

        def progress(done: int, total: int, outcome: Outcome) -> None:
            session.update(outcome)
            session.set_progress(done, total)
            say(f"  {done} / {total}  {outcome.keyword.label}")

        def search() -> None:
            try:
                report = runner.run(found, progress)
                say(_summary(report.outcomes))
                session.finish_search(report.stopped)
            except BaseException as problem:  # a bug here must not leave the page waiting forever
                session.finish_search(
                    f"The search stopped unexpectedly ({type(problem).__name__}). Keywords marked "
                    "not searched can be searched from this page."
                )

        threading.Thread(target=search, daemon=True).start()

    if saved:
        try:
            session = SelectionSession.from_state(saved.state, retry, multiple=args.multiple)
        except SelectionError as problem:
            raise SessionError(
                f"The saved session in {store.path.parent} cannot be used: {problem} "
                "Start over with --restart."
            ) from None
        say(f"Resuming the saved session: {saved.describe(deps.wall())}.")
    elif keywords is None:
        # No keyword source: the page asks for them, and the search happens behind it.
        session = SelectionSession(
            [], retry, multiple=args.multiple, starter=lambda text: start_from_page(session, text)
        )
        say("No keywords were given, so the page will ask for them.")
    else:
        if keywords.duplicates:
            shown = ", ".join(keywords.duplicates[:5]) + (
                ", ..." if len(keywords.duplicates) > 5 else ""
            )
            warn(
                f"Ignored {len(keywords.duplicates)} repeated keyword(s), "
                f"keeping the first: {shown}"
            )
        # 1. Search
        total = len(keywords)
        say(f"Searching {total} keyword{'s' if total != 1 else ''} on Pixabay...")
        report = runner.run(
            keywords, lambda done, n, outcome: say(f"  {done} / {n}  {outcome.keyword.label}")
        )
        say(_summary(report.outcomes))
        if report.stopped:
            warn(report.stopped)
        session = SelectionSession(
            list(report.outcomes), retry, multiple=args.multiple, stopped=report.stopped
        )
        store.save(session.export())
    session.on_change = store.save

    # 2. Pick
    if session.finished:
        say("That session was already finished; going straight to the download.")
    else:
        deps.serve_fn(session, open_browser=not args.no_browser, announce=say)
    selections = session.selections()
    if not selections:
        raise PackageError("Nothing was picked, so there is nothing to package.")

    # 3. Download
    wanted = [(s, c) for s in selections for c in s.candidates]
    say(f"Downloading {len(wanted)} image{'s' if len(wanted) != 1 else ''}...")
    downloader = Downloader(
        settings.work_dir / "originals",
        full_size=settings.full_size,
        session=deps.http,
        sleep=deps.sleep,
    )
    downloads = downloader.fetch_all(
        [c for _, c in wanted],
        lambda done, n, r: say(f"  {done} / {n}" + ("" if r.ok else f"  FAILED: {r.error}")),
    )

    # 4. Resize
    entries: list[Entry] = []
    missing: list[str] = []
    for (selection, candidate), download in zip(wanted, downloads, strict=True):
        label = selection.keyword.label
        if not download.ok:
            missing.append(f"{label}: {download.error}")
            continue
        try:
            data = render(
                download.path, settings.size, settings.resize_mode,
                settings.output_format, settings.background,
            )  # fmt: skip
        except (OSError, ValueError) as problem:
            missing.append(f"{label}: could not resize {download.path.name} ({problem})")
            continue
        entries.append(
            Entry(label, candidate, data, extension(settings.output_format), selection.query)
        )
    if not entries:
        raise PackageError(
            "None of the picked images could be produced:\n  " + "\n  ".join(missing)
        )

    # 5. Package
    path = write_zip(entries, settings.output, overwrite=args.overwrite)
    if missing:
        warn(f"{len(missing)} picked image(s) are NOT in the zip:")
        for line in missing:
            warn(f"  {line}")
    store.complete()
    if store.error:
        warn(f"Could not save the session file ({store.error}); this run was not affected.")
    kept = len({e.label for e in entries})
    say(f"Wrote {path} with {len(entries)} image{'s' if len(entries) != 1 else ''} "
        f"for {kept} keyword{'s' if kept != 1 else ''}, plus CREDITS.txt.")  # fmt: skip
    return EXIT_PARTIAL if missing else EXIT_OK


def cli() -> None:
    sys.exit(main())
