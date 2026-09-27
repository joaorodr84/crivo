"""Crivo: pick one Pixabay image per keyword, resized and zipped."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Derived from the git tag by setuptools-scm; see CLAUDE.md -> Versions.
    __version__ = version("crivo")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0+unknown"
