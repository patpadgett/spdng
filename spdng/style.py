"""Terminal styling.

A small, deliberate colour system:

  * honours the NO_COLOR convention (https://no-color.org)
  * honours FORCE_COLOR for people who pipe output and still want colour
  * degrades to plain text when the output is not a terminal
  * offers themes, because a cyan-and-magenta terminal is not everyone's
    taste and neither is a drab one

Colour is never the only signal. Every coloured value also carries its
text, so the output reads the same in a monochrome terminal or in a log
file. Colour is decoration, not information.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

RESET = "\033[0m"

CODES = {
    "bold": "1",
    "dim": "2",
    "italic": "3",
    "underline": "4",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
    "white": "37",
    "grey": "90",
    "bright_red": "91",
    "bright_green": "92",
    "bright_yellow": "93",
    "bright_blue": "94",
    "bright_magenta": "95",
    "bright_cyan": "96",
}


@dataclass(frozen=True)
class Theme:
    """A named set of colours."""

    name: str
    label: str
    good: str
    ok: str
    warn: str
    bad: str
    accent: str
    muted: str
    heading: str
    value: str

    def code(self, role: str) -> str:
        return CODES.get(getattr(self, role, "white"), "37")


#: The semantic roles every theme names. Anything else passed to paint()
#: is either a raw colour from CODES or a typo that should be ignored.
THEME_ROLES = ("good", "ok", "warn", "bad", "accent", "muted",
               "heading", "value")

THEMES: dict[str, Theme] = {
    "default": Theme(
        name="default", label="default",
        good="bright_green", ok="green", warn="yellow", bad="bright_red",
        accent="bright_cyan", muted="grey", heading="bold", value="bold",
    ),
    "mono": Theme(
        name="mono", label="mono",
        good="white", ok="white", warn="bold", bad="bold",
        accent="white", muted="grey", heading="bold", value="bold",
    ),
    "solarized": Theme(
        name="solarized", label="solarized",
        good="green", ok="green", warn="yellow", bad="red",
        accent="cyan", muted="grey", heading="bold", value="bold",
    ),
    "matrix": Theme(
        name="matrix", label="matrix",
        good="bright_green", ok="green", warn="green", bad="red",
        accent="green", muted="green", heading="bold", value="green",
    ),
    "vaporwave": Theme(
        name="vaporwave", label="vaporwave",
        good="bright_cyan", ok="bright_magenta", warn="bright_yellow",
        bad="bright_red", accent="bright_magenta", muted="magenta",
        heading="bold", value="bright_cyan",
    ),
    "amber": Theme(
        name="amber", label="amber",
        good="yellow", ok="yellow", warn="bright_yellow", bad="red",
        accent="yellow", muted="grey", heading="bold", value="bright_yellow",
    ),
}

_color_enabled: bool | None = None
_theme: Theme = THEMES["default"]


def _detect_color() -> bool:
    """Decide whether to emit ANSI escapes."""
    # NO_COLOR wins over everything, per the convention. Even FORCE_COLOR.
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("TERM") == "dumb":
        return False
    if os.environ.get("SPDNG_NO_COLOR") is not None:
        return False
    try:
        return sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def set_color(enabled: bool | None = None) -> None:
    """Force colour on or off, or re-detect when None."""
    global _color_enabled
    _color_enabled = enabled if enabled is not None else _detect_color()


def color_enabled() -> bool:
    global _color_enabled
    if _color_enabled is None:
        _color_enabled = _detect_color()
    return _color_enabled


def set_theme(name: str) -> Theme:
    global _theme
    _theme = THEMES.get(name.lower(), THEMES["default"])
    return _theme


def theme() -> Theme:
    return _theme


def paint(text: str, role: str) -> str:
    """Wrap text in a theme role or a raw colour name.

    Themes name semantic roles such as "good"; raw names such as "red"
    and "bold" are ANSI codes and must not be looked up as theme
    attributes. An unknown role is returned unchanged rather than being
    printed as literal text in an escape sequence.
    """
    if not color_enabled():
        return text
    if role in THEME_ROLES:
        code = _theme.code(role)
    elif role in CODES:
        code = CODES[role]
    else:
        return text
    return f"\033[{code}m{text}{RESET}"


def strip_ansi(text: str) -> str:
    """Remove ANSI escapes. Used by --no-color and by the JSON writer."""
    import re
    return re.sub(r"\033\[[0-9;]*m", "", text)


def term_width(default: int = 80) -> int:
    """The terminal width, so tables can fill the screen."""
    import shutil
    try:
        return shutil.get_terminal_size((default, 24)).columns
    except OSError:
        return default
