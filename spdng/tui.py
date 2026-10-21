"""Live terminal UI.

The mtr-style view is the centrepiece: a screen that updates in place,
showing every hop with its loss percentage and a rolling history of how
each probe went.

The history characters are the mtr convention, and they are worth keeping
because they are genuinely readable at a glance:

    .    the probe came back
    o    the probe timed out
    O    the probe was refused (host unreachable)
    X    the probe hit an error

A column of `.........o..` tells you the link is fine with an occasional
blip. A column of `oooOOXXXXX` tells you the hop is down and you should
stop reading further hops, because everything past it is measuring
nothing.

Everything degrades: if the terminal is not interactive, or curses fails
to initialise, we fall back to printing frames one after another. The tool
never refuses to run because it could not draw a box.
"""
from __future__ import annotations

import curses
import os
import sys
import time
from dataclasses import dataclass, field

from . import style
from .table import ASCII_BOX, BoxStyle

#: The characters mtr uses for a probe's history.
PROBE_OK = "."
PROBE_TIMEOUT = "o"
PROBE_REFUSED = "O"
PROBE_ERROR = "X"


@dataclass(slots=True)
class HopHistory:
    """Rolling record of how each probe to one hop went."""

    maxlen: int = 20
    entries: list[str] = field(default_factory=list)

    def add(self, outcome: str) -> None:
        self.entries.append(outcome)
        if len(self.entries) > self.maxlen:
            del self.entries[:-self.maxlen]

    def render(self) -> str:
        """The history as a string, oldest on the left."""
        return "".join(self.entries)

    @property
    def loss_count(self) -> int:
        return sum(1 for e in self.entries if e != PROBE_OK)


def supports_curses() -> bool:
    """Can we reasonably draw a curses UI here?"""
    if not sys.stdout.isatty():
        return False
    if os.environ.get("TERM", "") in ("", "dumb"):
        return False
    if os.environ.get("SPDNG_NO_CURSES"):
        return False
    return True


class Screen:
    """A curses screen, with a graceful fallback.

    `with Screen() as scr:` gives you a screen. If curses could not be
    started, `scr` is a fallback that just prints. Either way the code
    inside the with block is identical.
    """

    def __init__(self, title: str = "spdng") -> None:
        self.title = title
        self._stdscr = None
        self.fallback = not supports_curses()
        self._fallback_lines: list[str] = []
        self._fallback_rows = 0
        self._prev_size = (0, 0)

    def __enter__(self) -> Screen:
        if self.fallback:
            return self
        try:
            self._stdscr = curses.initscr()
            curses.noecho()
            curses.cbreak()
            self._stdscr.keypad(True)
            curses.curs_set(0)
            try:
                curses.start_color()
                curses.use_default_colors()
                self._init_colors()
            except curses.error:
                pass
        except Exception:  # noqa: BLE001
            # Any failure at all and we fall back to plain printing.
            self.fallback = True
            self._stdscr = None
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._stdscr is not None:
            try:
                curses.nocbreak()
                self._stdscr.keypad(False)
                curses.echo()
                curses.endwin()
            except curses.error:
                pass
            self._stdscr = None

    def _init_colors(self) -> None:
        """Map our theme roles onto curses colour pairs."""
        pairs = {
            "good": curses.COLOR_GREEN,
            "ok": curses.COLOR_GREEN,
            "warn": curses.COLOR_YELLOW,
            "bad": curses.COLOR_RED,
            "accent": curses.COLOR_CYAN,
            "muted": curses.COLOR_BLUE,
            "heading": curses.COLOR_WHITE,
            "value": curses.COLOR_WHITE,
        }
        self._pairs: dict[str, int] = {}
        for i, (role, colour) in enumerate(pairs.items(), start=1):
            try:
                curses.init_pair(i, colour, -1)
                self._pairs[role] = i
            except curses.error:
                continue

    # -- drawing ---------------------------------------------------------

    def size(self) -> tuple[int, int]:
        if self.fallback or self._stdscr is None:
            return (24, 80)
        try:
            return self._stdscr.getmaxyx()
        except curses.error:
            return (24, 80)

    def clear(self) -> None:
        if self.fallback or self._stdscr is None:
            return
        self._stdscr.clear()

    def refresh(self) -> None:
        if self.fallback or self._stdscr is None:
            return
        self._stdscr.refresh()

    def addstr(self, y: int, x: int, text: str, role: str | None = None) -> None:
        """Draw a string at a position, clipping to the screen."""
        if self.fallback or self._stdscr is None:
            return
        height, width = self.size()
        if y < 0 or y >= height:
            return
        room = width - x
        if room <= 0:
            return
        text = text[:room]
        try:
            if role and hasattr(self, "_pairs"):
                pair = self._pairs.get(role)
                if pair:
                    self._stdscr.addstr(y, x, text, curses.color_pair(pair))
                    return
            self._stdscr.addstr(y, x, text)
        except curses.error:
            # Writing to the bottom-right corner always raises; that is
            # normal and not worth reporting.
            pass

    def getch(self, timeout_ms: int = 250) -> int:
        """Read a key, or -1 on timeout."""
        if self.fallback or self._stdscr is None:
            time.sleep(timeout_ms / 1000)
            return -1
        self._stdscr.timeout(timeout_ms)
        try:
            return self._stdscr.getch()
        except curses.error:
            return -1

    # -- fallback drawing -------------------------------------------------

    def print_frame(self, lines: list[str]) -> None:
        """Draw a frame.

        In curses mode this clears the screen and redraws in place. In
        fallback mode there is no screen to clear, so we move the cursor
        back up over the previous frame and redraw it there. That way a
        non-interactive run scrolls once, not once per round.
        """
        if self.fallback or self._stdscr is None:
            self._print_fallback_frame(lines)
            return
        self.clear()
        for i, line in enumerate(lines):
            self.addstr(i, 0, line)
        self.refresh()

    def _print_fallback_frame(self, lines: list[str]) -> None:
        """Redraw in place, without a terminal that can clear itself.

        A "line" here may itself contain newlines, so the number of
        terminal ROWS a frame occupies is not the number of strings in
        it. Count the rows properly, or the cursor-up lands in the wrong
        place and the frames run into each other.
        """
        out = sys.stdout
        if self._fallback_rows:
            # The cursor sits on the row after the last one, so we move up
            # that many rows to get back to the top of the frame.
            out.write(f"\r\033[{self._fallback_rows}A")
        for line in lines:
            for i, part in enumerate(line.split("\n")):
                if i:
                    out.write("\r\n")
                out.write("\033[2K" + part)
            out.write("\r\n")
        out.flush()
        self._fallback_lines = list(lines)
        self._fallback_rows = sum(
            len(line.split("\n")) for line in lines) + 1

    def finish_fallback(self) -> None:
        """Leave the fallback in a state where the next print starts fresh."""
        self._fallback_lines = []
        self._fallback_rows = 0


def key_name(code: int) -> str:
    """A readable name for a key code."""
    if code == -1:
        return ""
    if code == 27:
        return "ESC"
    if code == ord(" "):
        return "SPACE"
    if code in (curses.KEY_UP,):
        return "UP"
    if code in (curses.KEY_DOWN,):
        return "DOWN"
    if code in (curses.KEY_LEFT,):
        return "LEFT"
    if code in (curses.KEY_RIGHT,):
        return "RIGHT"
    if code in (curses.KEY_RESIZE,):
        return "RESIZE"
    if 32 <= code < 127:
        return chr(code).upper()
    return f"KEY{code}"


# --------------------------------------------------------------------------
# the live trace view
# --------------------------------------------------------------------------

class LiveTraceView:
    """The mtr-style screen.

    Shows one row per hop, updating in place, with the probe history as a
    column of dots. The header carries the summary, the footer carries the
    hotkeys.
    """

    def __init__(self, target: str, box: BoxStyle = ASCII_BOX) -> None:
        self.target = target
        self.box = box
        self.hops: list[dict] = []
        self.status = "starting"
        self.message = ""
        self._started = time.time()
        self._round = 0

    def update_hop(self, ttl: int, address: str | None, rtt_ms: float | None,
                   outcome: str, sent: int, received: int) -> None:
        """Record one probe result for a hop."""
        while len(self.hops) <= ttl - 1:
            self.hops.append({
                "ttl": len(self.hops) + 1,
                "address": None,
                "history": HopHistory(),
                "sent": 0,
                "received": 0,
                "last_ms": None,
                "best_ms": None,
                "worst_ms": None,
                "samples": [],
            })
        hop = self.hops[ttl - 1]
        hop["history"].add(outcome)
        hop["sent"] = sent
        hop["received"] = received
        if address:
            hop["address"] = address
        if rtt_ms is not None:
            hop["last_ms"] = rtt_ms
            hop["samples"].append(rtt_ms)
            hop["samples"] = hop["samples"][-50:]
            hop["best_ms"] = min(hop["samples"])
            hop["worst_ms"] = max(hop["samples"])

    def set_status(self, status: str, message: str = "") -> None:
        self.status = status
        self.message = message

    def _loss_pct(self, hop: dict) -> float:
        sent = hop["sent"]
        if not sent:
            return 0.0
        return (sent - hop["received"]) * 100.0 / sent

    def render(self, screen: Screen) -> list[str]:
        """Build the frame and draw it."""
        lines = self.render_lines()
        screen.print_frame(lines)
        return lines

    #: Column layout: (title, width, alignment). The widths are the
    #: *visible* widths; padding is done with the ANSI-aware helper, so
    #: coloured cells line up exactly like plain ones.
    COLUMNS = [
        ("Hop", 4, ">"),
        ("Loss%", 7, ">"),
        ("Snt", 4, ">"),
        ("Last", 9, ">"),
        ("Avg", 9, ">"),
        ("Best", 9, ">"),
        ("Worst", 9, ">"),
        ("StDev", 8, ">"),
        ("History", 22, "<"),
        ("Host", 32, "<"),
    ]

    def render_lines(self) -> list[str]:
        """The frame as a list of strings, for the fallback and for saving."""
        from .table import pad

        out: list[str] = []

        elapsed = time.time() - self._started
        out.append(style.paint(
            f" spdng live trace -> {self.target}   "
            f"round {self._round}   {elapsed:.0f}s   {self.status} ",
            "heading"))
        if self.message:
            out.append(style.paint(f" {self.message}", "muted"))
        out.append("")

        # Header, using the same padding as the body so they line up.
        header = " ".join(
            pad(style.paint(title, "heading"), width, align)
            for title, width, align in self.COLUMNS)
        out.append(" " + header)
        out.append(style.paint(
            " " + "-" * (len(header) + 8), "muted"))

        for hop in self.hops:
            loss = self._loss_pct(hop)
            samples = hop["samples"]
            avg = sum(samples) / len(samples) if samples else None
            stdev = _stdev(samples) if len(samples) > 1 else None

            if loss >= 50:
                loss_role = "bad"
            elif loss > 0:
                loss_role = "warn"
            else:
                loss_role = "good"

            history = _colour_history(hop["history"].render())
            addr = hop["address"] or "*"

            cells = [
                pad(f"{hop['ttl']}", 4, ">"),
                pad(style.paint(f"{loss:.1f}%", loss_role), 7, ">"),
                pad(f"{hop['sent']}", 4, ">"),
                pad(_fmt_ms(hop["last_ms"]), 9, ">"),
                pad(_fmt_ms(avg), 9, ">"),
                pad(_fmt_ms(hop["best_ms"]), 9, ">"),
                pad(_fmt_ms(hop["worst_ms"]), 9, ">"),
                pad(_fmt_ms(stdev), 8, ">"),
                pad(history, 22, "<"),
                pad(style.paint(addr, "muted"), 32, "<"),
            ]
            out.append(" " + " ".join(cells))

        out.append("")
        out.append(style.paint(
            " q quit   s save csv   j save json   r save report   "
            "space pause   +/- interval   up/down scroll", "muted"))
        return out


def _fmt_ms(value: float | None) -> str:
    if value is None:
        return "---"
    return f"{value:.2f}ms"


def _stdev(samples: list[float]) -> float | None:
    if len(samples) < 2:
        return None
    mean = sum(samples) / len(samples)
    var = sum((x - mean) ** 2 for x in samples) / (len(samples) - 1)
    return var ** 0.5


def _colour_history(history: str) -> str:
    """Colour the history characters by what they mean."""
    out = []
    for ch in history:
        if ch == PROBE_OK:
            out.append(style.paint(ch, "good"))
        elif ch == PROBE_TIMEOUT:
            out.append(style.paint(ch, "warn"))
        elif ch == PROBE_REFUSED:
            out.append(style.paint(ch, "bad"))
        else:
            out.append(style.paint(ch, "bad"))
    return "".join(out)
