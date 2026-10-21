"""The fun bits.

A tool you use often should be a pleasure to use, and a tool that helps
you debug your network should be a little playful. Everything here is
optional, and nothing here gets in the way of a serious measurement.

The rules we held ourselves to:

  * An easter egg must never lie about a measurement.
  * An easter egg must never make the tool slower or less reliable.
  * An easter egg must be discoverable, not obscure for its own sake.
  * An easter egg must be funny to a person who does not know it exists.
"""
from __future__ import annotations

import random
import time
from datetime import datetime, timezone

from . import style

# --------------------------------------------------------------------------
# grades
# --------------------------------------------------------------------------

#: Download speed thresholds in Mbit/s, and the grade they earn.
#: The scale is deliberately generous at the bottom and steep at the top,
#: because "is my internet good" has a much lower bar than "is my internet
#: exceptional".
GRADE_THRESHOLDS = [
    (1000.0, "S+", "absurd"),
    (500.0, "S", "absurd"),
    (250.0, "A+", "excellent"),
    (150.0, "A", "very good"),
    (100.0, "B+", "good"),
    (50.0, "B", "fine"),
    (25.0, "C", "workable"),
    (10.0, "D", "rough"),
    (1.0, "E", "dial-up territory"),
    (0.0, "F", "the router is on fire"),
]

GRADE_COLORS = {
    "S+": "bright_magenta",
    "S": "bright_cyan",
    "A+": "bright_green",
    "A": "green",
    "B+": "green",
    "B": "yellow",
    "C": "yellow",
    "D": "bright_yellow",
    "E": "red",
    "F": "bright_red",
}


def grade_for(mbps: float) -> tuple[str, str]:
    """Return (grade, description) for a download speed."""
    for threshold, letter, word in GRADE_THRESHOLDS:
        if mbps >= threshold:
            return letter, word
    return "F", "the router is on fire"


def render_grade(mbps: float) -> str:
    letter, word = grade_for(mbps)
    colour = GRADE_COLORS.get(letter, "white")
    return (f"{style.paint(letter, colour)} "
            f"{style.paint(f'({word})', 'muted')}")


# --------------------------------------------------------------------------
# sparkline
# --------------------------------------------------------------------------

SPARK_CHARS = "▁▂▃▄▅▆▇█"


def sparkline(values: list[float], width: int = 40) -> str:
    """A compact text sparkline, like the ones in a git history graph."""
    if not values:
        return ""
    if len(values) > width:
        # Keep the most recent samples: the newest data is the interesting
        # data, and the tail is what the eye looks for.
        values = values[-width:]
    lo = min(values)
    hi = max(values)
    if hi == lo:
        return SPARK_CHARS[0] * len(values)
    span = hi - lo
    out = []
    for v in values:
        idx = int((v - lo) / span * (len(SPARK_CHARS) - 1))
        out.append(SPARK_CHARS[idx])
    return "".join(out)


# --------------------------------------------------------------------------
# verdict lines
# --------------------------------------------------------------------------

#: Said after a measurement. Some are conditional on the numbers, some are
#: just there because a tool that only ever says "done" is no fun.
CLEAN_QUIPS = [
    "not bad",
    "the internet is cooperating",
    "solid",
    "respectable",
    "that'll stream 4K",
    "plenty for a video call",
    "your wifi is earning its keep",
]

SLOW_QUIPS = [
    "the internet is thinking about it",
    "this is a dial-up cosplay",
    "somebody is having a bad day",
    "have you tried turning the router off and on again",
    "that is a lot of buffering",
    "this is what satellite used to feel like",
]

VERY_SLOW_QUIPS = [
    "is the ethernet cable plugged in",
    "we have reached the theoretical minimum of the internet",
    "this is a phone call at 56k",
    "the router appears to be on fire",
    "have you tried turning the router off and on again",
    "this is what a fax modem sounded like",
]


def quip_for(mbps: float) -> str:
    """A short, honest-ish remark about the speed we measured."""
    if mbps >= 100:
        pool = CLEAN_QUIPS
    elif mbps >= 25:
        pool = CLEAN_QUIPS[:4]
    elif mbps >= 5:
        pool = SLOW_QUIPS
    else:
        pool = VERY_SLOW_QUIPS
    return random.choice(pool)


# --------------------------------------------------------------------------
# the classic easter egg
# --------------------------------------------------------------------------

BANNER = r"""
  ___  ____
 / _ \/ ___|   ___  _ __   ___ _ __
| | | \___ \  / _ \| '_ \ / _ \ '_ \
| |_| |___) ||  __/| | | |  __/ |_) |
 \___/|____/  \___||_| |_|\___| .__/
                                 |_|
"""


def moo() -> str:
    """The obligatory cow."""
    return (
        "        \\   ^__^\n"
        "         \\  (oo)\\_______\n"
        "            (__)\\       )\\/\\\n"
        "                ||----w |\n"
        "                ||     ||"
    )


def cowsay(text: str) -> str:
    """Text, in a cow."""
    width = max(len(line) for line in text.split("\n"))
    top = " " * (width + 2)
    lines = [top]
    for line in text.split("\n"):
        lines.append(f"< {line.ljust(width)} >")
    lines.append(top)
    return "\n".join(lines) + "\n" + moo()


# --------------------------------------------------------------------------
# a very small time-based thing
# --------------------------------------------------------------------------

def time_of_day_greeting() -> str:
    """A greeting that depends on when you ran it."""
    hour = datetime.now(timezone.utc).astimezone().hour
    if 5 <= hour < 12:
        return "good morning"
    if 12 <= hour < 18:
        return "good afternoon"
    if 18 <= hour < 22:
        return "good evening"
    return "good night"


def it_is_friday() -> bool:
    return datetime.now().weekday() == 4


# --------------------------------------------------------------------------
# the speedometer
# --------------------------------------------------------------------------

def render_speedometer(mbps: float, width: int = 34) -> str:
    """An ASCII speedometer, for when you want to feel something."""
    # A speedometer that tops out at 1000 Mbit/s.
    fraction = min(1.0, mbps / 1000.0)
    filled = int(round(fraction * width))
    if filled == 0 and mbps > 0:
        filled = 1
    bar = "█" * filled + "░" * (width - filled)
    colour = "good" if mbps >= 100 else "ok" if mbps >= 25 else "warn"
    return f"  [{style.paint(bar, colour)}] {mbps:.1f} Mbit/s"


def sparkline_history(history: list[float]) -> str:
    """A sparkline plus its range, for showing a trend."""
    if not history:
        return ""
    line = sparkline(history)
    lo, hi = min(history), max(history)
    return (f"{style.paint(line, 'accent')} "
            f"{style.paint(f'{lo:.1f}..{hi:.1f} Mbit/s', 'muted')}")


def elapsed(seconds: float) -> str:
    """Human-readable duration."""
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m {secs:02d}s"


def countdown(seconds: float) -> str:
    """A countdown for the test duration, if we want to show progress."""
    return f"{max(0, seconds):.0f}s"


class Stopwatch:
    """A tiny stopwatch, because every speed test wants one."""

    def __init__(self) -> None:
        self.start = time.perf_counter()

    def elapsed(self) -> float:
        return time.perf_counter() - self.start

    def __str__(self) -> str:
        return elapsed(self.elapsed())
