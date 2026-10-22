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
import re
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

#: The SGR colour codes we understand, and the base curses colour each
#: one means. The bright variants (90-97) map to the same base colour
#: here; on a terminal with 16 or more colours they get their own
#: colour number instead (see `_sgr_color_table`).
_SGR_BASE_COLORS: dict[str, int] = {
    "30": curses.COLOR_BLACK,
    "31": curses.COLOR_RED,
    "32": curses.COLOR_GREEN,
    "33": curses.COLOR_YELLOW,
    "34": curses.COLOR_BLUE,
    "35": curses.COLOR_MAGENTA,
    "36": curses.COLOR_CYAN,
    "37": curses.COLOR_WHITE,
    "90": curses.COLOR_BLACK,
    "91": curses.COLOR_RED,
    "92": curses.COLOR_GREEN,
    "93": curses.COLOR_YELLOW,
    "94": curses.COLOR_BLUE,
    "95": curses.COLOR_MAGENTA,
    "96": curses.COLOR_CYAN,
    "97": curses.COLOR_WHITE,
}

#: SGR codes that are text attributes rather than colours.
#:
#: Not every attribute exists on every platform: `curses.A_ITALIC` is
#: missing on macOS, and `A_UNDERLINE` is missing on some builds. So
#: each one is looked up defensively and the ones that are not there
#: simply do not apply.
def _attr(name: str) -> int:
    """A curses attribute, or 0 if this platform does not have it."""
    return getattr(curses, name, 0)


_SGR_ATTRIBUTES: dict[str, int] = {
    "1": _attr("A_BOLD"),
    "2": _attr("A_DIM"),
    "3": _attr("A_ITALIC"),
    "4": _attr("A_UNDERLINE"),
}

#: The bright SGR codes, in the order they appear in 90-97.
_BRIGHT_CODES = ["90", "91", "92", "93", "94", "95", "96", "97"]

_ANSI_SGR = re.compile(r"\033\[([0-9;]*)m")


def _sgr_color_table() -> dict[str, int]:
    """The SGR code -> curses colour number map for this terminal.

    On a terminal with 16 colours or more, the bright codes (90-97) get
    their own colour numbers (8-15) so that grey is grey and not black.
    On an 8-colour terminal they fall back to the base colour, and the
    bright look is approximated with the bold attribute.
    """
    table = dict(_SGR_BASE_COLORS)
    try:
        colours = curses.COLORS
    except AttributeError:
        colours = 8
    if colours >= 16:
        for offset, code in enumerate(_BRIGHT_CODES):
            table[code] = 8 + offset
    return table


def ansi_segments(text: str) -> list[tuple[str, str, int]]:
    """Split a coloured string into (text, colour_code, attributes).

    The colour code is the SGR colour in force for that segment, or "0"
    for the default. The attributes are a bitmask of curses attributes
    (bold, dim, underline) in force at that point.
    """
    colour_table = _sgr_color_table()
    segments: list[tuple[str, str, int]] = []
    colour = "0"
    attributes = 0
    pos = 0
    for match in _ANSI_SGR.finditer(text):
        chunk = text[pos:match.start()]
        if chunk:
            segments.append((chunk, colour, attributes))
        for part in (match.group(1) or "0").split(";"):
            if part == "" or part == "0":
                colour = "0"
                attributes = 0
            elif part in _SGR_ATTRIBUTES:
                attributes |= _SGR_ATTRIBUTES[part]
            elif part in colour_table:
                colour = part
        pos = match.end()
    tail = text[pos:]
    if tail:
        segments.append((tail, colour, attributes))
    return segments


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

    def __init__(self, title: str = "spdng",
                 keep_output: bool = False) -> None:
        self.title = title
        self._stdscr = None
        self.fallback = not supports_curses()
        self._fallback_lines: list[str] = []
        self._fallback_rows = 0
        self._prev_size = (0, 0)
        #: The last frame drawn, so it can be re-printed when curses
        #: ends.
        self._last_frame: list[str] = []
        #: Keep the final frame on the screen after curses exits.
        #:
        #: `curses.endwin()` restores the terminal to the state it was
        #: in before curses started, which WIPES the screen. So a
        #: curses run that ends leaves nothing behind: the results are
        #: only visible while the program is running. With this set,
        #: the final frame is printed again after endwin, so it stays.
        self.keep_output = keep_output

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
        if self.keep_output and self._last_frame:
            # The screen was just restored, so the frame that was on it
            # is gone. Put it back.
            print()
            for line in self._last_frame:
                print(line)

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

        # A pair for every SGR colour code we emit, so a pre-painted
        # string can be re-coloured through curses.
        self._sgr_pairs: dict[str, int] = {}
        n = max(self._pairs.values(), default=0) + 1
        for code, fg in _sgr_color_table().items():
            if n >= curses.COLOR_PAIRS:
                break
            try:
                curses.init_pair(n, fg, -1)
            except curses.error:
                continue
            self._sgr_pairs[code] = n
            n += 1

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
        """Draw a string at a position, clipping to the screen.

        IMPORTANT: `text` must NOT contain ANSI escape sequences. Curses
        has its own colour model, and a raw ESC byte drawn into the
        screen appears as the two literal characters "^[" - so a
        pre-painted string shows up as "^[[32m" garbage on screen.

        So the curses path draws plain text and applies the colour
        through a curses colour pair instead. The fallback path is the
        other way around: it prints the string, ANSI and all, because a
        normal terminal interprets those.
        """
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

    def addstr_styled(self, y: int, x: int, text: str,
                      role: str | None = None) -> None:
        """Draw a string, translating any ANSI it carries into curses.

        Curses has its own colour model. A raw ESC byte written into the
        screen comes out as the two literal characters "^[", so a
        pre-painted string renders as "^[[32m" garbage.

        So the ANSI is parsed out and the colour is re-applied through a
        curses colour pair. The colour survives and the screen never
        shows an escape character.
        """
        if self.fallback or self._stdscr is None:
            return
        if "\033[" not in text:
            self.addstr(y, x, text, role)
            return

        width = self.size()[1]
        col = x
        for chunk, code, attributes in ansi_segments(text):
            if col >= width:
                break
            if not chunk:
                continue
            room = width - col
            if len(chunk) > room:
                chunk = chunk[:room]
            attr = self._attr_for(code, attributes)
            try:
                if attr:
                    self._stdscr.addstr(y, col, chunk, attr)
                else:
                    self._stdscr.addstr(y, col, chunk)
            except curses.error:
                pass
            col += len(chunk)

    def _attr_for(self, sgr_code: str, attributes: int = 0) -> int:
        """The curses attribute for an SGR colour code and attributes."""
        attr = attributes
        if sgr_code and sgr_code != "0":
            pair = self._sgr_pairs.get(sgr_code)
            if pair:
                attr |= curses.color_pair(pair)
            # On an 8-colour terminal a bright colour is approximated
            # with the bold attribute.
            elif sgr_code in _BRIGHT_CODES:
                attr |= curses.A_BOLD
        return attr

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

    def resized(self) -> bool:
        """Has the terminal changed size since the last check?

        The size is re-read every time, so a view that asks for it
        always gets the current one. This just says whether it CHANGED,
        so a caller knows to re-lay-out its columns.
        """
        if self.fallback or self._stdscr is None:
            return False
        current = self.size()
        if current == self._prev_size:
            return False
        self._prev_size = current
        # Tell curses the terminal changed, so it re-reads the size and
        # stops trying to use the old one.
        try:
            curses.update_lines_cols()
        except (curses.error, AttributeError):
            pass
        try:
            self._stdscr.clear()
        except curses.error:
            pass
        return True

    # -- fallback drawing -------------------------------------------------

    def print_frame(self, lines: list[str]) -> None:
        """Draw a frame.

        In curses mode this redraws in place. The screen is NOT fully
        cleared first: `clear()` marks every cell as changed, so the
        terminal repaints the whole screen and the view flickers. So we
        write over the top and then blank whatever is left below.

        In fallback mode there is no screen to clear, so we move the
        cursor back up over the previous frame and redraw it there. That
        way a non-interactive run scrolls once, not once per round.
        """
        self._last_frame = list(lines)
        if self.fallback or self._stdscr is None:
            self._print_fallback_frame(lines)
            return

        height, width = self.size()
        try:
            self._stdscr.move(0, 0)
        except curses.error:
            pass
        for i, line in enumerate(lines):
            if i >= height:
                break
            self.addstr_styled(i, 0, line)
        # Blank whatever the previous, longer frame left behind.
        for y in range(len(lines), height):
            try:
                self._stdscr.move(y, 0)
                self._stdscr.clrtoeol()
            except curses.error:
                pass
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
        #: The width of the screen, set when the view is drawn. The
        #: separator rule is capped to it so it never wraps.
        self._screen_width = 0
        #: The height of the screen, set when the view is drawn. It limits
        #: the hop window so a long path does not push the footer off.
        self._screen_height = 0
        #: First hop shown when the path is taller than the window.
        self._scroll = 0

    def scroll(self, delta: int) -> None:
        """Move the visible hop window up or down."""
        self._scroll = max(0, self._scroll + delta)

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
        height, width = screen.size()
        self._screen_height = height
        self._screen_width = width
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

    #: The columns that never shrink, because their numbers need room.
    FIXED_COLUMNS = 8

    def _columns(self, screen_width: int) -> list[tuple[str, int, str]]:
        """The column layout, fitted to the screen.

        The full layout is 123 columns wide, which is wider than most
        terminals. A row that is too long wraps, and a wrapped row
        interleaves with the next one, so the table turns into
        nonsense. So the history and host columns give way first: the
        history shrinks to a readable minimum, and whatever is left goes
        to the host name.
        """
        if not screen_width:
            return list(self.COLUMNS)

        fixed = self.COLUMNS[:self.FIXED_COLUMNS]
        # A row is " " + cell + " " + cell + ... , so the leading space
        # and the n-1 separating spaces come out of the budget too.
        # The rule sits under the row and needs a column of its own.
        used = sum(w for _, w, _ in fixed) + len(fixed)
        spare = screen_width - used - 2

        history_min, host_min = 10, 16
        if spare < history_min + host_min:
            # Even the minimums do not fit. Drop the optional columns
            # that cost the least: StDev, then Best and Worst.
            for droppable in ("StDev", "Best", "Worst", "Avg"):
                names = [t for t, _, _ in fixed]
                if droppable not in names:
                    continue
                if spare >= history_min + host_min:
                    break
                index = names.index(droppable)
                fixed.pop(index)
                used = sum(w for _, w, _ in fixed) + len(fixed)
                # The same budget as above: the leading space, the
                # separators, and a column for the rule.
                spare = screen_width - used - 2
            if spare < history_min + host_min:
                return fixed

        # Give the history a little more than the minimum if we can.
        history = max(history_min, min(22, spare - host_min))
        host = max(host_min, spare - history)
        return fixed + [("History", history, "<"), ("Host", host, "<")]

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
        columns = self._columns(self._screen_width)
        header = " ".join(
            pad(style.paint(title, "heading"), width, align)
            for title, width, align in columns)
        out.append(" " + header)
        # The rule is as long as the header, measured VISIBLY. Using
        # len() on a coloured string counts the escape sequences and
        # makes the rule far too long, which wraps and draws a second
        # line under it.
        from .table import visible_width
        # The rule is exactly as wide as the row it sits under: the
        # leading space plus the visible width of the header.
        rule_len = visible_width(header) + 1
        # Never wider than the screen. A line that reaches the last
        # column makes curses raise when it writes there, and a line
        # that is one too long wraps and draws a second line under
        # itself.
        screen_width = self._screen_width
        if screen_width and rule_len > screen_width - 1:
            rule_len = screen_width - 1
        out.append(style.paint(" " + "-" * rule_len, "muted"))

        start = 0
        end = len(self.hops)
        if self._screen_height:
            # Title, optional message, blank, header, rule, blank, footer.
            reserved = 6 + (1 if self.message else 0)
            visible = max(1, self._screen_height - reserved)
            max_scroll = max(0, len(self.hops) - visible)
            self._scroll = min(self._scroll, max_scroll)
            start = self._scroll
            end = start + visible

        for hop in self.hops[start:end]:
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

            # The values are looked up BY COLUMN TITLE, so a column that
            # was dropped for width takes its value with it. Pairing by
            # position instead shifts every value one column left.
            by_title = {
                "Hop": f"{hop['ttl']}",
                "Loss%": style.paint(f"{loss:.1f}%", loss_role),
                "Snt": f"{hop['sent']}",
                "Last": _fmt_ms(hop["last_ms"]),
                "Avg": _fmt_ms(avg),
                "Best": _fmt_ms(hop["best_ms"]),
                "Worst": _fmt_ms(hop["worst_ms"]),
                "StDev": _fmt_ms(stdev),
                "History": history,
                "Host": style.paint(addr, "muted"),
            }
            cells = [
                pad(by_title.get(title, ""), width, align)
                for title, width, align in columns
            ]
            out.append(" " + " ".join(cells))

        out.append("")
        out.append(style.paint(self._footer(), "muted"))
        return out

    def _footer(self) -> str:
        """The hotkey line, trimmed to fit the screen.

        The full line is 95 characters, which is wider than most
        terminals. The least important hints go first, so the ones that
        survive on a narrow screen are the ones you actually need.
        """
        hints = [
            "q quit", "s save csv", "j save json", "r save report",
            "space pause", "+/- interval", "up/down scroll",
        ]
        width = self._screen_width or 0
        if not width:
            return " " + "   ".join(hints)
        chosen: list[str] = []
        for hint in hints:
            candidate = chosen + [hint]
            if 1 + len("   ".join(candidate)) > width - 1:
                break
            chosen = candidate
        if not chosen:
            chosen = ["q quit"]
        line = " " + "   ".join(chosen)
        if len(line) > width:
            # Even "q quit" does not fit, which means a very narrow
            # terminal. Cut it to fit rather than wrapping.
            line = line[:width]
        return line


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
