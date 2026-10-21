"""spdng - a maintained speedtest CLI with mtr-style path analysis."""

from .cli import main

__all__ = ["main"]
from .version import __version__  # noqa: F401
