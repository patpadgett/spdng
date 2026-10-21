"""Table rendering with box drawing.

Two problems this solves.

**Alignment.** `len()` on a coloured string returns the length including the
ANSI escape sequences, so a coloured cell is 9 characters "longer" than a
plain one and every column after it is off by that much. The width of a
cell is its *visible* width, so we measure that, not `len()`.

**Framing.** Numbers in columns are much easier to read when they are
inside a frame, and a frame makes a misaligned table obvious at a glance
instead of leaving you to squint at ragged whitespace.

The box characters are configurable. The default is plain ASCII, because
ASCII is the point of "ascii art boxes" and it renders identically
everywhere, including in a log file, a CI transcript, or a terminal with a
missing font. Unicode is available for people who want it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import style

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

# --------------------------------------------------------------------------
# box styles
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BoxStyle:
    """The characters that draw a box."""

    name: str
    top_left: str
    top_right: str
    bottom_left: str
    bottom_right: str
    horizontal: str
    vertical: str
    tee_down: str
    tee_up: str
    tee_right: str
    tee_left: str
    cross: str

    def top(self, width: int) -> str:
        """The top border, for an inner width of `width`."""
        return self.top_left + self.horizontal * width + self.top_right

    def bottom(self, width: int) -> str:
        return self.bottom_left + self.horizontal * width + self.bottom_right

    def separator(self, width: int) -> str:
        return self.tee_left + self.horizontal * width + self.tee_right

    def rule(self, width: int) -> str:
        return self.tee_down + self.horizontal * width + self.tee_up


ASCII_BOX = BoxStyle(
    name="ascii",
    top_left="+", top_right="+", bottom_left="+", bottom_right="+",
    horizontal="-", vertical="|",
    tee_down="+", tee_up="+", tee_right="+", tee_left="+", cross="+",
)

UNICODE_BOX = BoxStyle(
    name="unicode",
    top_left="┌", top_right="┐", bottom_left="└", bottom_right="┘",
    horizontal="─", vertical="│",
    tee_down="┬", tee_up="┴", tee_right="├", tee_left="┤", cross="┼",
)

ROUNDED_BOX = BoxStyle(
    name="rounded",
    top_left="╭", top_right="╮", bottom_left="╰", bottom_right="╯",
    horizontal="─", vertical="│",
    tee_down="┬", tee_up="┴", tee_right="├", tee_left="┤", cross="┼",
)

DOUBLE_BOX = BoxStyle(
    name="double",
    top_left="╔", top_right="╗", bottom_left="╚", bottom_right="╝",
    horizontal="═", vertical="║",
    tee_down="╦", tee_up="╩", tee_right="╠", tee_left="╣", cross="╬",
)

BOX_STYLES = {
    "ascii": ASCII_BOX,
    "unicode": UNICODE_BOX,
    "rounded": ROUNDED_BOX,
    "double": DOUBLE_BOX,
}


def visible_width(text: str) -> int:
    """The width of a string as it appears on screen.

    ANSI escape sequences take up no space. A wide character (CJK, emoji)
    takes two columns, so we count those too, because a table with a CJK
    label in it should still line up.
    """
    plain = _ANSI_RE.sub("", text)
    width = 0
    for ch in plain:
        if unicodedata_east_asian(ch):
            width += 2
        elif unicodedata_combining(ch):
            continue
        else:
            width += 1
    return width


def unicodedata_east_asian(ch: str) -> bool:
    import unicodedata
    return unicodedata.east_asian_width(ch) in ("W", "F")


def unicodedata_combining(ch: str) -> bool:
    import unicodedata
    return unicodedata.combining(ch) != 0


def pad(text: str, width: int, align: str = "<") -> str:
    """Pad a (possibly coloured) string to a visible width."""
    gap = width - visible_width(text)
    if gap <= 0:
        return text
    if align == ">":
        return " " * gap + text
    if align == "^":
        left = gap // 2
        return " " * left + text + " " * (gap - left)
    return text + " " * gap


def truncate(text: str, width: int, ellipsis: str = "…") -> str:
    """Shorten a string to a visible width.

    We keep the ANSI sequences intact where we can, but the common case
    is a plain cell, so the simple path handles that and the styled path
    falls back to stripping colour if the styled text is too long.
    """
    if visible_width(text) <= width:
        return text
    plain = _ANSI_RE.sub("", text)
    if width <= 1:
        return plain[:width]
    return plain[: width - 1] + ellipsis


# --------------------------------------------------------------------------
# the table
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Column:
    """One column of a table."""

    title: str
    width: int = 0
    align: str = "<"
    #: Minimum width, used when the content is narrower than the title.
    min_width: int = 0


@dataclass(slots=True)
class Table:
    """A framed, column-aligned table."""

    columns: list[Column] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    box: BoxStyle = ASCII_BOX
    title: str = ""
    #: A line of text under the title, inside the frame.
    subtitle: str = ""

    def add_column(self, title: str, align: str = "<",
                    width: int = 0) -> Column:
        col = Column(title=title, align=align, width=width)
        self.columns.append(col)
        return col

    def add_row(self, cells: list[str]) -> None:
        self.rows.append(cells)

    def _compute_widths(self) -> None:
        """Size each column to fit its widest cell, or its title."""
        for i, col in enumerate(self.columns):
            longest = visible_width(col.title)
            for row in self.rows:
                if i < len(row):
                    longest = max(longest, visible_width(row[i]))
            col.width = max(longest, col.min_width, col.width)

        # If the table is too narrow to hold its title, widen the last
        # column rather than truncating the title to nothing.
        if self.title:
            needed = visible_width(self.title) + 4
            if self._inner_width() < needed:
                spare = needed - self._inner_width()
                self.columns[-1].width += spare

    def _inner_width(self) -> int:
        """The width of the box's inside, in characters.

        Each cell is rendered as " content ", so each column costs its
        width plus two spaces, and the columns are separated by one more
        space each.
        """
        return (sum(c.width + 2 for c in self.columns)
                + len(self.columns) - 1)

    def _outer_width(self) -> int:
        """The total width of a rendered line, including the edges."""
        return self._inner_width() + 2

    def _rule(self, style_role: str = "muted") -> str:
        width = self._inner_width()
        bar = self.box.separator(width)
        return style.paint(bar, style_role)

    def render(self) -> str:
        """Render the table as a string."""
        if not self.columns:
            return ""
        self._compute_widths()
        inner = self._inner_width()
        v = self.box.vertical

        out: list[str] = []

        # Top border, with the title embedded in it. The whole line is
        # exactly the outer width, so the box closes.
        outer = self._outer_width()
        if self.title:
            label = f" {self.title} "
            # Leave at least one dash on each side of the label.
            if visible_width(label) > inner - 2:
                label = truncate(label, max(1, inner - 2))
            # outer = 1 (corner) + dashes_left + label + dashes_right + 1 (corner)
            dashes_left = 1
            dashes_right = outer - 2 - dashes_left - visible_width(label)
            top = (self.box.top_left
                   + self.box.horizontal * dashes_left
                   + label
                   + self.box.horizontal * max(0, dashes_right)
                   + self.box.top_right)
            out.append(style.paint(top, "heading"))
        else:
            out.append(style.paint(self.box.top(inner), "muted"))

        if self.subtitle:
            out.append(style.paint(f"{v} {self.subtitle}", "muted"))

        # Header row
        header = v
        for col in self.columns:
            header += " " + pad(col.title, col.width, col.align) + " " + v
        out.append(style.paint(header, "heading"))

        out.append(self._rule())

        # Body
        for row in self.rows:
            line = v
            for i, col in enumerate(self.columns):
                cell = row[i] if i < len(row) else ""
                cell = truncate(cell, col.width)
                line += " " + pad(cell, col.width, col.align) + " " + v
            out.append(line)

        out.append(style.paint(self.box.bottom(inner), "muted"))
        return "\n".join(out)
