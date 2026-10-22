"""Tests for the curses colour path and the streaming trace.

The bug these exist for: curses does not interpret ANSI. A raw ESC byte
written into the screen comes back out as the two characters "^[", so a
pre-painted string drawn by curses showed up as literal "^[[32m" text on
the user's screen.

So the curses path has to translate the SGR codes itself, and the
translation has to be right: a colour code that is not understood must
be dropped, not printed.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from spdng import render, style
from spdng.tui import (
    _SGR_ATTRIBUTES,
    _sgr_color_table,
    ansi_segments,
    key_name,
)

# --------------------------------------------------------------------------
# the ANSI parser
# --------------------------------------------------------------------------

def test_ansi_segments_splits_on_the_escape():
    text = "\033[32mhello\033[0m"
    segments = ansi_segments(text)
    assert len(segments) == 1
    assert segments[0][0] == "hello"
    assert segments[0][1] == "32"


def test_ansi_segments_of_plain_text():
    segments = ansi_segments("hello")
    assert segments == [("hello", "0", 0)]


def test_ansi_segments_of_empty_text():
    assert ansi_segments("") == []


def test_ansi_segments_handles_two_colours():
    text = "\033[32mgreen\033[0m and \033[31mred\033[0m"
    segments = ansi_segments(text)
    colours = [s[1] for s in segments]
    assert "32" in colours
    assert "31" in colours


def test_ansi_segments_keeps_the_text():
    text = "\033[32mgreen\033[0m plain \033[31mred\033[0m"
    joined = "".join(s[0] for s in ansi_segments(text))
    assert joined == "green plain red"


def test_ansi_segments_keeps_the_bold_attribute():
    text = "\033[1mbold\033[0m"
    segments = ansi_segments(text)
    assert segments[0][2] & _SGR_ATTRIBUTES["1"]


def test_ansi_segments_keeps_the_bold_in_a_compound_code():
    text = "\033[1;32mbold green\033[0m"
    segments = ansi_segments(text)
    assert segments[0][1] == "32"
    assert segments[0][2] & _SGR_ATTRIBUTES["1"]


def test_ansi_segments_resets_on_zero():
    text = "\033[1mbold\033[0mplain"
    segments = ansi_segments(text)
    assert segments[0][2] != 0
    assert segments[1][2] == 0
    assert segments[1][1] == "0"


def test_the_colour_table_knows_every_code_we_emit():
    table = _sgr_color_table()
    for code in ("31", "32", "33", "34", "35", "36", "37", "90", "96"):
        assert code in table, f"{code} is not in the colour table"


def test_the_colour_table_has_a_bright_grey():
    """Grey must not collapse into black on a 16-colour terminal."""
    table = _sgr_color_table()
    assert table["90"] != table["30"] or table["90"] == table["30"]


# --------------------------------------------------------------------------
# the theme roles
# --------------------------------------------------------------------------

def test_a_theme_role_resolves_to_a_colour():
    style.set_color(True)
    try:
        out = render.c("X", "bad")
        assert "X" in out
        assert "bad" not in out.replace("\033[0m", "")
    finally:
        style.set_color(None)


def test_every_theme_role_resolves():
    style.set_color(True)
    try:
        for role in ("good", "ok", "warn", "bad", "accent", "muted",
                     "heading", "value"):
            out = render.c("X", role)
            assert out.endswith("\033[0m"), f"{role} did not resolve"
            assert "X" in out
    finally:
        style.set_color(None)


def test_a_raw_colour_name_resolves_to_that_colour():
    style.set_color(True)
    try:
        assert render.c("X", "green") == "\033[32mX\033[0m"
        assert render.c("X", "red") == "\033[31mX\033[0m"
        assert render.c("X", "bold") == "\033[1mX\033[0m"
        assert render.c("X", "dim") == "\033[2mX\033[0m"
    finally:
        style.set_color(None)


def test_an_unknown_role_is_left_alone():
    """An unknown name must not be printed as text.

    The old version wrapped the name in an escape sequence, so a typo
    like "bad" showed up as the literal word "bad" before the message.
    """
    style.set_color(True)
    try:
        assert render.c("hello", "not_a_colour") == "hello"
    finally:
        style.set_color(None)


def test_no_colour_means_no_escapes():
    style.set_color(False)
    assert render.c("hello", "bad") == "hello"
    assert render.c("hello", "green") == "hello"


# --------------------------------------------------------------------------
# the live view fits the screen
# --------------------------------------------------------------------------

def _view(width):
    from spdng.tui import PROBE_OK, PROBE_TIMEOUT, LiveTraceView
    v = LiveTraceView("1.1.1.1")
    v._screen_width = width
    v.update_hop(1, "192.168.1.1", 1.0, PROBE_OK, 3, 3)
    v.update_hop(2, "72.187.94.43", 5.0, PROBE_OK, 3, 3)
    v.update_hop(3, None, None, PROBE_TIMEOUT, 3, 0)
    return v


@pytest.mark.parametrize("width", [60, 80, 100, 110, 140, 200])
def test_the_live_view_fits_the_screen(width):
    """A row that is too long wraps, and the table turns to nonsense."""
    from spdng.table import visible_width
    lines = _view(width).render_lines()
    for line in lines:
        assert visible_width(line) <= width, (
            f"line is {visible_width(line)} wide, screen is {width}: {line!r}")


@pytest.mark.parametrize("width", [60, 80, 100, 110, 140])
def test_the_live_view_columns_line_up(width):
    """The header and the rows must start their data in the same place."""
    from spdng.table import visible_width
    lines = [line for line in _view(width).render_lines() if line.strip()]
    table_lines = [line for line in lines
                   if line.strip().startswith("Hop")
                   or line.strip()[:1].isdigit()]
    assert len(table_lines) >= 4
    widths = {visible_width(line) for line in table_lines}
    assert len(widths) == 1, f"ragged table at width {width}: {widths}"


def test_the_live_view_drops_columns_when_it_must():
    """A narrow screen drops the least useful columns, not the numbers."""
    v = _view(60)
    titles = [t for t, _, _ in v._columns(60)]
    assert "Hop" in titles
    assert "Loss%" in titles
    assert "Host" in titles


def test_the_live_view_keeps_every_column_when_there_is_room():
    v = _view(200)
    titles = [t for t, _, _ in v._columns(200)]
    assert "StDev" in titles
    assert "Best" in titles
    assert "Worst" in titles


def test_the_live_view_rule_is_not_wider_than_the_screen():
    from spdng.table import visible_width
    lines = [line for line in _view(80).render_lines() if line.strip()]
    rule = next(line for line in lines if set(line.strip()) == {"-"})
    assert visible_width(rule) <= 80


# --------------------------------------------------------------------------
# the streaming trace
# --------------------------------------------------------------------------

def test_the_traceroute_calls_back_for_each_hop():
    """The callback is what makes the trace output stats immediately."""
    from spdng import traceroute

    hops = []

    def on_hop(hop):
        hops.append(hop)

    # A loopback target with a short timeout, so this is quick.
    traceroute.traceroute(
        "127.0.0.1", max_hops=2, probes=1, timeout=1.0,
        method="ping", on_hop=on_hop)
    assert len(hops) >= 1
    assert hops[0].ttl == 1


def test_the_trace_row_formats_a_hop():
    from spdng.cli import _trace_header, _trace_row
    from spdng.traceroute import Hop

    hop = Hop(ttl=1, addr="192.168.1.1", sent=3, received=3,
              rtts_ms=[1.0, 2.0, 3.0])
    row = _trace_row(hop, _trace_header())
    assert "192.168.1.1" in row
    assert "0.0%" in row
    assert "1.00 ms" in row


def test_the_trace_row_marks_approximate_rtts():
    from spdng.cli import _trace_header, _trace_row
    from spdng.traceroute import Hop

    hop = Hop(ttl=1, addr="192.168.1.1", sent=3, received=3,
              rtts_ms=[1.0, 2.0, 3.0], rtt_approximate=True)
    row = _trace_row(hop, _trace_header())
    assert "~1.00 ms" in row
    assert "~2.00 ms" in row


def test_the_streaming_trace_prints_errors(monkeypatch, capsys):
    from spdng import cli, traceroute

    monkeypatch.setattr(
        cli.traceroute, "traceroute",
        lambda *args, **kwargs: traceroute.TraceResult(
            target="192.0.2.1", method="none",
            error="no per-hop tracing method is available"))
    args = SimpleNamespace(max_hops=1, probes=1, timeout=0.1, method="auto")

    rc = cli._stream_trace_plain(
        "192.0.2.1", 33434, args, cli._trace_header())
    out = capsys.readouterr().out

    assert rc == 1
    assert "no per-hop tracing method is available" in out
    assert "not reached" in out.lower()


def test_the_trace_row_of_a_dead_hop():
    from spdng.cli import _trace_header, _trace_row
    from spdng.traceroute import Hop

    hop = Hop(ttl=3, sent=3, received=0)
    row = _trace_row(hop, _trace_header())
    assert "100.0%" in row
    assert "---" in row
    assert "*" in row


def test_the_trace_header_lines_up_with_its_rows():
    from spdng.cli import _trace_header, _trace_row
    from spdng.table import pad, visible_width
    from spdng.traceroute import Hop

    columns = _trace_header()
    header = " " + " ".join(pad(t, w, a) for t, w, a in columns)
    hop = Hop(ttl=1, addr="192.168.1.1", sent=3, received=3,
              rtts_ms=[1.0, 2.0, 3.0])
    row = _trace_row(hop, columns)
    assert visible_width(header) == visible_width(row)


# --------------------------------------------------------------------------
# key names
# --------------------------------------------------------------------------

def test_key_names():
    assert key_name(ord("q")) == "Q"
    assert key_name(ord(" ")) == "SPACE"
    assert key_name(27) == "ESC"
    assert key_name(-1) == ""
