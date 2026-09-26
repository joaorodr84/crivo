"""Name the results, write CREDITS.txt, and zip it all (FR7).

File names come from keyword labels, and a label is whatever a person typed, so a name is
made safe for the strictest filesystem the zip might be unpacked on, which is Windows:

- `<>:"/\\|?*` and control characters become `_`. `/` and `\\` are what would turn a label
  like `../../x` into a path; with them gone, no member name can leave the folder it is
  unpacked into (the "zip slip" shape), and every name is flat.
- Leading dots and spaces are dropped (no hidden files, no `..`), and trailing dots and
  spaces too, which Windows silently strips and so would make two names one.
- `CON`, `PRN`, `AUX`, `NUL`, `COM1`-`COM9` and `LPT1`-`LPT9` are reserved on Windows
  whatever the extension (`con.png` is the console), so they get a `_` prefix.
- The stem is cut at 100 characters, leaving room for a suffix and an extension well under
  the 255 that most filesystems allow.
- Unicode is normalised to NFC, so `é` typed as one character and as `e` plus a combining
  accent do not become two files on Linux and one on macOS.

Uniqueness is decided case-insensitively, after cleaning: `Cat` and `cat` are one file on
Windows and macOS, and so are `a/b` and `a_b` once the slash is gone. The first keeps the
plain name and later ones get `-2`, `-3`, and so on, in keyword order, so the result does
not depend on which sanitising step caused the clash. Several picks for one keyword
(FR4 allows them) are numbered the same way.

The zip is written to a temporary file next to its destination and renamed into place, so
a full disk or a Ctrl-C leaves no half-written archive under the name the person is
waiting for. Overwriting an existing file is refused unless asked for: it may be a
previous run's result.

CREDITS.txt is plain UTF-8 text, not JSON: the spec allows either, it is meant to be read by
a person who opens the archive, and contributor names are not ASCII.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
import time
import unicodedata
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .pixabay_client import Candidate

CREDITS_NAME = "CREDITS.txt"
LICENSE_URL = "https://pixabay.com/service/license/"
MAX_STEM = 100
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_RESERVED = re.compile(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?")


class PackageError(Exception):
    """The archive could not be written. `str()` says why, for the person."""


@dataclass(frozen=True)
class Entry:
    """One finished image and where it came from."""

    label: str  # the keyword, which the file is named after
    candidate: Candidate
    data: bytes  # the resized image, already encoded
    extension: str  # "png", "jpg" or "webp"
    term: str = ""  # what was actually searched, if different from the label


def safe_stem(label: str) -> str:
    text = unicodedata.normalize("NFC", label)
    text = _ILLEGAL.sub("_", text)
    text = " ".join(text.split()).lstrip(". ").rstrip(". ")
    text = text[:MAX_STEM].rstrip(". ")
    if not text:
        return "image"
    if _RESERVED.fullmatch(text):
        text = "_" + text
    return text


def plan_names(entries: Sequence[Entry]) -> list[str]:
    """One unique, safe file name per entry, in order."""
    taken = {CREDITS_NAME.casefold()}
    names = []
    for entry in entries:
        stem = safe_stem(entry.label)
        name = f"{stem}.{entry.extension}"
        number = 1
        while name.casefold() in taken:
            number += 1
            name = f"{stem}-{number}.{entry.extension}"
        taken.add(name.casefold())
        names.append(name)
    return names


def build_credits(entries: Sequence[Entry], names: Sequence[str], generated: str) -> str:
    lines = [
        "Images from Pixabay (https://pixabay.com), used under the Pixabay Content License:",
        LICENSE_URL,
        "",
        "Attribution is not required by that license. It is recorded here because Pixabay",
        "asks that users be shown where images come from, and so that there is a paper",
        "trail if a question about one of these images comes up later.",
        "",
        f"Prepared with Winnower {__version__} on {generated}.",
        "",
    ]
    for entry, name in zip(entries, names, strict=True):
        lines += [
            name,
            f"  Keyword:      {entry.label}",
        ]
        if entry.term and entry.term != entry.label:
            lines.append(f"  Searched for: {entry.term}")
        lines += [
            f"  Pixabay page: {entry.candidate.page_url}",
            f"  Contributor:  {entry.candidate.user}",
            "",
        ]
    return "\n".join(lines)


def write_zip(
    entries: Sequence[Entry],
    path: Path,
    *,
    overwrite: bool = False,
    now: Callable[[], float] = time.time,
) -> Path:
    """Write the images and CREDITS.txt to `path` as one zip and return it."""
    if not entries:
        raise PackageError("There is nothing to package: no image was picked.")
    path = Path(path)
    if path.exists() and not overwrite:
        raise PackageError(f"{path} already exists. Choose another name, or allow overwriting.")
    moment = time.localtime(now())
    names = plan_names(entries)
    credits_text = build_credits(entries, names, time.strftime("%Y-%m-%d", moment))
    stamp = moment[:6]

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle, zipfile.ZipFile(handle, "w") as archive:
                for entry, name in zip(entries, names, strict=True):
                    _add(archive, name, entry.data, stamp)
                _add(archive, CREDITS_NAME, credits_text.encode("utf-8"), stamp)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
    except OSError as err:
        raise PackageError(f"Could not write {path}: {err.strerror or err}") from None
    return path


def _add(archive: zipfile.ZipFile, name: str, data: bytes, stamp: tuple[int, ...]) -> None:
    info = zipfile.ZipInfo(name, date_time=stamp)  # type: ignore[arg-type]
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16  # so Unix unzip does not produce mode-000 files
    archive.writestr(info, data)
