"""Run configuration: where the API key comes from, and every per-run setting.

The API key is read from the `PIXABAY_API_KEY` environment variable or a gitignored
`.env` file, and deliberately *not* from a command-line flag. A flag lands in shell
history and in the process list every user on the machine can read; an environment
variable and a `.env` do neither. The environment wins over `.env`, so a one-off
override does not mean editing a file. Rejected: a config file in the home directory,
which would be a second place a key can hide from someone auditing where theirs went.

Settings are validated when they are built, so a bad `--size` or `--colors` fails
before the first request is spent on it. Pixabay's rate limit is 100 requests per
minute per key; a run that dies on the fortieth keyword because of a typo in a filter
has cost the user forty of them.

The allowed values below are Pixabay's own (https://pixabay.com/api/docs/). They are
listed here rather than fetched because the API has no endpoint that returns them; if
Pixabay adds a category, this is the one place to add it.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

API_KEY_ENV = "PIXABAY_API_KEY"
API_KEY_PLACEHOLDER = "your_key_here"  # what .env.example ships

IMAGE_TYPES = ("all", "photo", "illustration", "vector")
ORIENTATIONS = ("all", "horizontal", "vertical")
CATEGORIES = (
    "backgrounds", "fashion", "nature", "science", "education", "feelings", "health",
    "people", "religion", "places", "animals", "industry", "computer", "food", "sports",
    "transportation", "travel", "buildings", "business", "music",
)  # fmt: skip
COLORS = (
    "grayscale", "transparent", "red", "orange", "yellow", "green", "turquoise", "blue",
    "lilac", "pink", "white", "gray", "black", "brown",
)  # fmt: skip
ORDERS = ("popular", "latest")
LANGUAGES = (
    "cs", "da", "de", "en", "es", "fr", "id", "it", "hu", "nl", "no", "pl", "pt", "ro",
    "sk", "fi", "sv", "tr", "vi", "th", "bg", "ru", "el", "ja", "ko", "zh",
)  # fmt: skip

RESIZE_MODES = ("crop", "pad", "fit")
OUTPUT_FORMATS = ("png", "jpeg", "webp")

# Pixabay allows 3-200 results per page, and a search fetches exactly one page, so these
# are also the floor and ceiling on candidates per keyword. The spec's "1-10" is not
# reachable: `per_page=1` is refused by the API, and asking for 3 and showing 1 would
# make "next page" skip two results. The ceiling stays inside the 500-result cap.
MIN_CANDIDATES = 3
MAX_CANDIDATES = 200
# A guard against `--size 999999x999999` allocating gigabytes in Pillow.
MAX_SIDE = 8192


class ConfigError(ValueError):
    """A setting is missing or invalid. The message says how to fix it."""


def _one_of(name: str, value: str, allowed: tuple[str, ...]) -> None:
    if value not in allowed:
        raise ConfigError(f"{name} must be one of {', '.join(allowed)}; got {value!r}")


@dataclass(frozen=True)
class SearchOptions:
    """The filters Pixabay's image search exposes (FR2)."""

    image_type: str = "all"
    orientation: str = "all"
    category: str | None = None
    colors: tuple[str, ...] = ()
    min_width: int = 0
    min_height: int = 0
    safesearch: bool = False
    order: str = "popular"
    lang: str = "en"
    editors_choice: bool = False

    def __post_init__(self) -> None:
        _one_of("image_type", self.image_type, IMAGE_TYPES)
        _one_of("orientation", self.orientation, ORIENTATIONS)
        _one_of("order", self.order, ORDERS)
        _one_of("lang", self.lang, LANGUAGES)
        if self.category is not None:
            _one_of("category", self.category, CATEGORIES)
        for color in self.colors:
            _one_of("colors", color, COLORS)
        if self.min_width < 0 or self.min_height < 0:
            raise ConfigError("min_width and min_height cannot be negative")


@dataclass(frozen=True)
class Settings:
    # repr=False: a Settings that ends up in a traceback or a log line must not
    # carry the key with it.
    api_key: str = field(default="", repr=False)
    candidates: int = 5
    search: SearchOptions = field(default_factory=SearchOptions)
    # Global search-term override (FR2): `{term}` is replaced by each keyword's own
    # search term, so "{term} icon" turns every search into a search for an icon.
    term_template: str = "{term}"
    size: tuple[int, int] = (512, 512)
    resize_mode: str = "crop"
    output_format: str = "png"
    # Fill for `pad`: "#rrggbb", or None for transparent (png/webp) and white (jpeg).
    background: str | None = None
    # Download imageURL instead of largeImageURL; only present in responses for
    # accounts Pixabay has approved for full API access.
    full_size: bool = False
    work_dir: Path = Path(".winnower")
    output: Path = Path("winnower.zip")

    def __post_init__(self) -> None:
        if not MIN_CANDIDATES <= self.candidates <= MAX_CANDIDATES:
            raise ConfigError(
                f"candidates must be between {MIN_CANDIDATES} and {MAX_CANDIDATES} "
                "(the page sizes Pixabay accepts)"
            )
        if "{term}" not in self.term_template:
            raise ConfigError("term_template must contain {term}, e.g. '{term} icon'")
        width, height = self.size
        if not (1 <= width <= MAX_SIDE and 1 <= height <= MAX_SIDE):
            raise ConfigError(f"size must be between 1x1 and {MAX_SIDE}x{MAX_SIDE}")
        _one_of("resize mode", self.resize_mode, RESIZE_MODES)
        _one_of("output format", self.output_format, OUTPUT_FORMATS)
        if self.background is not None and not re.fullmatch(r"#[0-9a-fA-F]{6}", self.background):
            raise ConfigError(f"background must look like #rrggbb; got {self.background!r}")

    def search_term(self, term: str) -> str:
        return self.term_template.replace("{term}", term)


def parse_size(text: str) -> tuple[int, int]:
    """`512x512`, `512X256` or just `512` (a square)."""
    match = re.fullmatch(r"\s*(\d+)\s*(?:[xX]\s*(\d+))?\s*", text)
    if not match:
        raise ConfigError(f"size must look like 512x512 or 512; got {text!r}")
    width = int(match[1])
    return width, int(match[2] or width)


def parse_dotenv(text: str) -> dict[str, str]:
    """The small subset of .env syntax people actually write.

    `KEY=value`, `export KEY=value`, single or double quotes, `#` comments. Rejected:
    python-dotenv, which is a fine library but a third runtime dependency for forty
    lines, and variable interpolation, which nobody puts in a file that holds one key.
    """
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        value = value.strip()
        if value and value[0] in "\"'":
            end = value.find(value[0], 1)
            value = value[1:end] if end != -1 else value[1:]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        values[key.strip()] = value
    return values


def find_api_key(env: Mapping[str, str] | None = None, dotenv_path: Path = Path(".env")) -> str:
    env = os.environ if env is None else env
    key = env.get(API_KEY_ENV, "").strip()
    if not key and dotenv_path.is_file():
        # utf-8-sig: Notepad on Windows saves a BOM that would otherwise become part
        # of the first key name and make the variable silently "missing".
        key = parse_dotenv(dotenv_path.read_text(encoding="utf-8-sig")).get(API_KEY_ENV, "").strip()
    if not key:
        raise ConfigError(
            f"No Pixabay API key found. Set {API_KEY_ENV} in the environment or in a .env "
            "file (copy .env.example). Each user needs their own free key: "
            "https://pixabay.com/api/docs/"
        )
    if key == API_KEY_PLACEHOLDER:
        raise ConfigError(
            f"{API_KEY_ENV} is still the placeholder from .env.example; paste your own key."
        )
    return key


def load_settings(
    overrides: Mapping[str, Any] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    dotenv_path: Path = Path(".env"),
) -> Settings:
    """Defaults, then the API key from the environment or .env, then `overrides`.

    `overrides` is what the command line supplied; a value of None means "not given"
    and is skipped, so argparse defaults of None do not clobber a real default.
    """
    given = {k: v for k, v in (overrides or {}).items() if v is not None}
    if "api_key" in given:
        raise ConfigError("the API key is read from the environment or .env, never passed in")
    return replace(Settings(), api_key=find_api_key(env, dotenv_path), **given)
