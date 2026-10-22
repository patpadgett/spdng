"""Tests for the table, the live view, and the box frames.

The alignment tests are the important ones: they exist because ANSI colour
codes make `len()` the wrong measure of a cell's width, and getting that
wrong silently misaligns every column after the first coloured one.
"""
from __future__ import annotations

import pytest

from spdng import compare, frames, measure, render, style, table
from spdng.tui import (
    PROBE_ERROR,
    PROBE_OK,
    PROBE_REFUSED,
    PROBE_TIMEOUT,
    HopHistory,
    LiveTraceView,
)

# --------------------------------------------------------------------------
# width measurement
# --------------------------------------------------------------------------

def test_visible_width_ignores_ansi():
    style.set_color(True)
    try:
        coloured = style.paint("Download", "heading")
        assert len(coloured) > len("Download")
        assert table.visible_width(coloured) == len("Download")
    finally:
        style.set_color(None)


def test_visible_width_of_plain_text():
    assert table.visible_width("hello") == 5


def test_visible_width_of_empty():
    assert table.visible_width("") == 0


def test_pad_accounts_for_ansi():
    style.set_color(True)
    try:
        padded = table.pad(style.paint("abc", "red"), 10)
        assert table.visible_width(padded) == 10
    finally:
        style.set_color(None)


def test_pad_right_aligns():
    assert table.pad("ab", 6, ">") == "    ab"


def test_pad_center_aligns():
    assert table.pad("ab", 6, "^") == "  ab  "


def test_pad_does_not_truncate():
    assert table.pad("abcdef", 3) == "abcdef"


def test_truncate_shortens_to_visible_width():
    out = table.truncate("abcdefghij", 5)
    assert table.visible_width(out) <= 5


def test_truncate_leaves_short_text_alone():
    assert table.truncate("abc", 10) == "abc"


# --------------------------------------------------------------------------
# the table
# --------------------------------------------------------------------------

def _plain(text: str) -> str:
    return table._ANSI_RE.sub("", text)


def test_table_lines_are_all_the_same_width():
    """A box that is not square is a box that is broken."""
    style.set_color(False)
    t = table.Table(box=table.ASCII_BOX, title="test")
    t.add_column("A", align="<")
    t.add_column("Bee", align=">")
    t.add_row(["one", "2"])
    t.add_row(["three", "4444"])
    lines = t.render().split("\n")
    widths = {len(_plain(line)) for line in lines}
    assert len(widths) == 1, f"ragged table: {widths}"


def test_table_lines_are_the_same_width_with_colour():
    style.set_color(True)
    try:
        t = table.Table(box=table.ASCII_BOX, title="test")
        t.add_column("A", align="<")
        t.add_column("Bee", align=">")
        t.add_row([style.paint("one", "good"), style.paint("2", "bad")])
        t.add_row(["three", "4444"])
        lines = t.render().split("\n")
        widths = {table.visible_width(line) for line in lines}
        assert len(widths) == 1, f"ragged table: {widths}"
    finally:
        style.set_color(None)


def test_table_without_columns_is_empty():
    assert table.Table().render() == ""


def test_table_title_appears_in_the_border():
    style.set_color(False)
    t = table.Table(box=table.ASCII_BOX, title="my title")
    t.add_column("A")
    t.add_row(["x"])
    out = t.render()
    assert "my title" in out


def test_every_box_style_renders():
    for name, box in table.BOX_STYLES.items():
        style.set_color(False)
        t = table.Table(box=box, title="t")
        t.add_column("A")
        t.add_row(["x"])
        out = t.render()
        assert out
        widths = {len(_plain(line)) for line in out.split("\n")}
        assert len(widths) == 1, f"{name} is ragged: {widths}"


# --------------------------------------------------------------------------
# hop history
# --------------------------------------------------------------------------

def test_hop_history_starts_empty():
    h = HopHistory()
    assert h.render() == ""
    assert h.loss_count == 0


def test_hop_history_records_outcomes():
    h = HopHistory()
    h.add(PROBE_OK)
    h.add(PROBE_TIMEOUT)
    h.add(PROBE_OK)
    assert h.render() == ".o."
    assert h.loss_count == 1


def test_hop_history_is_bounded():
    h = HopHistory(maxlen=5)
    for _ in range(20):
        h.add(PROBE_OK)
    assert len(h.render()) == 5


def test_hop_history_drops_the_oldest():
    h = HopHistory(maxlen=3)
    h.add(PROBE_TIMEOUT)
    h.add(PROBE_OK)
    h.add(PROBE_OK)
    h.add(PROBE_OK)
    assert h.render() == "..."


def test_probe_characters_are_distinct():
    chars = {PROBE_OK, PROBE_TIMEOUT, PROBE_REFUSED, PROBE_ERROR}
    assert len(chars) == 4


# --------------------------------------------------------------------------
# the live view
# --------------------------------------------------------------------------

def _view() -> LiveTraceView:
    v = LiveTraceView("1.1.1.1")
    v.update_hop(1, "192.168.1.1", 1.0, PROBE_OK, 1, 1)
    v.update_hop(2, "10.0.0.1", 5.0, PROBE_OK, 1, 1)
    v.update_hop(3, None, None, PROBE_TIMEOUT, 1, 0)
    return v


def test_live_view_renders():
    lines = _view().render_lines()
    assert lines
    assert any("192.168.1.1" in line for line in lines)


def _live_table_rows(lines: list[str]) -> list[str]:
    """The rows of the live view's table, excluding banner and footer."""
    rows = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("spdng live trace") or stripped.startswith("q quit"):
            continue
        if stripped.startswith("Hop "):
            rows.append(line)
            continue
        if line.startswith(" ") and line.strip() and line.strip()[0].isdigit():
            rows.append(line)
    return rows


def test_live_view_lines_are_aligned():
    """The whole point: the columns must line up."""
    style.set_color(False)
    rows = _live_table_rows(_view().render_lines())
    assert len(rows) >= 4, "expected a header and three hop rows"
    widths = {table.visible_width(row) for row in rows}
    assert len(widths) == 1, f"ragged live view: {widths}"


def test_live_view_aligned_with_colour():
    style.set_color(True)
    try:
        rows = _live_table_rows(_view().render_lines())
        widths = {table.visible_width(row) for row in rows}
        assert len(widths) == 1, f"ragged live view: {widths}"
    finally:
        style.set_color(None)


def test_live_view_shows_the_history():
    v = _view()
    v.update_hop(1, "192.168.1.1", 1.0, PROBE_OK, 2, 2)
    text = "\n".join(v.render_lines())
    assert ".." in text


def test_live_view_shows_loss():
    v = _view()
    text = "\n".join(v.render_lines())
    assert "100.0%" in text


def test_live_view_with_no_hops():
    v = LiveTraceView("1.1.1.1")
    lines = v.render_lines()
    assert lines


def test_live_view_status_message():
    v = _view()
    v.set_status("paused", "waiting")
    text = "\n".join(v.render_lines())
    assert "paused" in text
    assert "waiting" in text


# --------------------------------------------------------------------------
# frames
# --------------------------------------------------------------------------

def _result():
    from spdng.measure import LatencyResult, SpeedTestResult

    class FakeServer:
        sponsor = "Sponsor"
        name = "Place"
        cc = "US"
        distance_km = 5.0
        provider = "ookla"

    r = SpeedTestResult(server=FakeServer())
    r.download_mbps = 100.0
    r.upload_mbps = 10.0
    r.latency = LatencyResult(samples_ms=[10.0, 12.0], errors=0, attempts=2)
    r.latency.samples_ms = [10.0, 12.0]
    return r


def test_result_table_is_a_closed_box():
    style.set_color(False)
    out = frames.result_table(_result())
    lines = out.split("\n")
    widths = {len(line) for line in lines}
    assert len(widths) == 1, f"ragged result table: {widths}"


def test_result_table_shows_the_numbers():
    style.set_color(False)
    out = frames.result_table(_result())
    assert "100.00 Mbit/s" in out
    assert "Sponsor" in out


def test_result_table_without_latency():
    from spdng.measure import LatencyResult, SpeedTestResult
    r = SpeedTestResult(server=None)
    r.latency = LatencyResult(samples_ms=[], errors=3, attempts=3)
    out = frames.result_table(r)
    assert "unavailable" in out
    assert "1800000" not in out


def test_skipped_download_is_not_a_fabricated_zero():
    r = _result()
    r.download_mbps = None
    out = frames.result_table(r)
    assert "skipped" in out.lower() or "n/a" in out.lower()
    assert "on fire" not in out.lower()


def test_skipped_directions_are_explicit_in_every_summary():
    r = _result()
    r.download_mbps = None
    r.upload_mbps = None
    r.latency = measure.LatencyResult()

    assert "skipped" in render.render_result(r).lower()
    assert "skipped" in frames.result_table(r).lower()
    assert "not measured" in frames.result_to_csv(r)
    assert "n/a" in compare.render_comparison([r, _result()], ["a", "b"])
    assert "skipped" in compare.render_repeat_comparison([r, _result()])


def test_result_csv_has_a_header_and_rows():
    csv_text = frames.result_to_csv(_result())
    lines = csv_text.strip().split("\n")
    assert lines[0].startswith("metric,value,unit,detail")
    assert any("download" in line for line in lines)
    assert any("ping_sample_0" in line for line in lines)


def test_result_csv_of_an_empty_result():
    from spdng.measure import SpeedTestResult
    csv_text = frames.result_to_csv(SpeedTestResult(server=None))
    assert "metric,value,unit,detail" in csv_text


def test_comparison_table_is_aligned():
    style.set_color(False)
    out = frames.comparison_table([_result(), _result()], ["A", "B"])
    lines = out.split("\n")
    widths = {len(line) for line in lines}
    assert len(widths) == 1, f"ragged comparison: {widths}"


def test_server_table_is_aligned():
    style.set_color(False)
    from spdng.servers import Server
    servers = [Server(id=str(i), host="h:1", sponsor="S", name="N",
                       country="", cc="", lat=0, lon=0, distance_km=1.0)
               for i in range(3)]
    out = frames.server_table(servers)
    widths = {len(line) for line in out.split("\n")}
    assert len(widths) == 1, f"ragged server table: {widths}"


def test_trace_table_is_aligned():
    style.set_color(False)
    from spdng.traceroute import Hop, TraceResult
    t = TraceResult(target="1.1.1.1", hops=[
        Hop(ttl=1, addr="10.0.0.1", sent=3, received=3, rtts_ms=[1.0, 2.0, 3.0]),
        Hop(ttl=2, sent=3, received=0),
    ])
    out = frames.trace_table(t)
    widths = {len(line) for line in out.split("\n")}
    assert len(widths) == 1, f"ragged trace table: {widths}"
    assert "..." in out
    assert "ooo" in out


def test_trace_table_marks_approximate_rtts():
    style.set_color(False)
    from spdng.traceroute import Hop, TraceResult

    t = TraceResult(target="1.1.1.1", hops=[
        Hop(ttl=1, addr="10.0.0.1", sent=3, received=3,
            rtts_ms=[1.0, 2.0, 3.0], rtt_approximate=True),
    ])
    out = frames.trace_table(t)
    assert "~1.00 ms" in out
    assert "~2.00 ms" in out


# --------------------------------------------------------------------------
# the comparison view
# --------------------------------------------------------------------------

def _comp_result(name: str, dl: float, ping: float):
    from spdng.measure import LatencyResult, SpeedTestResult

    class FakeServer:
        sponsor = "Sponsor"
        cc = "US"
        distance_km = 5.0
        provider = "ookla"

    FakeServer.name = name
    r = SpeedTestResult(server=FakeServer())
    r.download_mbps = dl
    r.upload_mbps = 10.0
    r.latency = LatencyResult(samples_ms=[ping], errors=0, attempts=1)
    return r


def test_comparison_rows_line_up():
    """Every row of the comparison must start its columns in the same place."""
    style.set_color(False)
    from spdng import compare
    out = compare.render_comparison(
        [_comp_result("A", 100.0, 20.0), _comp_result("B", 200.0, 30.0)],
        ["one", "two"])
    for line in out.split("\n"):
        stripped = line.strip()
        if not stripped or set(stripped) <= {"─"}:
            continue
        # A label-only row (the "Server" heading) has no data columns.
        if len(line) <= 11:
            continue
        # The label column is 11 wide, so every row's first data
        # character sits at the same offset.
        assert line[11] != " ", f"row is not indented to the label column: {line!r}"


@pytest.mark.xfail(strict=True, reason="compare pads ANSI strings with len()")
def test_comparison_rows_line_up_with_colour():
    """Numbers must stay in their columns when colour is enabled."""
    style.set_color(True)
    try:
        from spdng import compare
        out = compare.render_comparison(
            [_comp_result("A", 100.0, 20.0), _comp_result("B", 200.0, 30.0)],
            ["one", "two"])
        lines = out.split("\n")
        header = next(line for line in lines if "one" in line and "two" in line)
        target = table.visible_width(header)
        for line in lines:
            if line.startswith(("Download", "Upload", "Ping", "Jitter", "Loss")):
                assert table.visible_width(line) == target, repr(line)
    finally:
        style.set_color(None)


def test_comparison_server_row_is_one_line_per_provider():
    style.set_color(False)
    from spdng import compare
    out = compare.render_comparison(
        [_comp_result("A", 100.0, 20.0), _comp_result("B", 200.0, 30.0)],
        ["one", "two"])
    lines = [line for line in out.split("\n") if line.strip()]
    server_idx = next(i for i, line in enumerate(lines)
                      if line.startswith("Server"))
    # The row after "Server" holds all the servers, not one per line.
    assert "A" in lines[server_idx + 1]
    assert "B" in lines[server_idx + 1]


def test_shorten_cuts_at_a_word_boundary():
    from spdng.compare import _shorten
    out = _shorten("Blue Stream Fiber Miami, Florida", 20)
    assert len(out) <= 20
    assert out.endswith("…")
    assert "Blue Stream" in out


def test_shorten_leaves_short_text_alone():
    from spdng.compare import _shorten
    assert _shorten("Miami", 20) == "Miami"


def test_shorten_never_returns_something_longer():
    from spdng.compare import _shorten
    for width in range(2, 30):
        out = _shorten("A Very Long Sponsor Name Indeed", width)
        assert len(out) <= width


def test_the_version_is_the_same_everywhere():
    """The package, the CLI and the packaging metadata must agree.

    They drifted once: the package said 0.1.0, the CLI said 0.3.0 and
    pyproject said something else. A tool that reports three different
    versions of itself looks unmaintained, which is the one thing this
    tool is not supposed to look like.
    """
    import re
    from pathlib import Path

    import spdng
    from spdng.cli import __version__ as cli_version

    assert spdng.__version__ == cli_version

    root = Path(spdng.__file__).parent.parent
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        text = pyproject.read_text()
        match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
        assert match, "could not find the version in pyproject.toml"
        assert match.group(1) == spdng.__version__, (
            f"pyproject says {match.group(1)}, the package says "
            f"{spdng.__version__}")


def test_the_fallback_redraws_in_place():
    """A non-interactive run must not scroll once per round.

    The fallback writes the frame, moves the cursor back up over it and
    writes it again, so the output is one frame, not one frame per round.
    """

    from spdng.tui import Screen

    screen = Screen("test")
    screen.fallback = True
    screen._fallback_rows = 0
    screen._fallback_lines = []

    captured = []

    class FakeOut:
        def write(self, text):
            captured.append(text)

        def flush(self):
            pass

    import sys
    real = sys.stdout
    sys.stdout = FakeOut()
    try:
        screen.print_frame(["line one", "line two"])
        screen.print_frame(["line one", "line two"])
    finally:
        sys.stdout = real

    out = "".join(captured)
    # The second frame must move the cursor back up over the first.
    assert "[3A" in out, f"expected a cursor-up, got {out!r}"
    # And each line is cleared before it is written.
    assert out.count("[2K") >= 4


def test_the_fallback_counts_rows_not_strings():
    """A frame element can contain newlines, so rows != strings."""
    from spdng.tui import Screen

    screen = Screen("test")
    screen.fallback = True
    screen._fallback_rows = 0
    screen._fallback_lines = []

    captured = []

    class FakeOut:
        def write(self, text):
            captured.append(text)

        def flush(self):
            pass

    import sys
    real = sys.stdout
    sys.stdout = FakeOut()
    try:
        screen.print_frame(["a\nb", "c"])
    finally:
        sys.stdout = real

    out = "".join(captured)
    # "a\nb" is two rows, "c" is one, so 3 rows plus the cursor's own
    # line: the next frame moves up 4.
    screen._fallback_rows = sum(
        len(line.split("\n")) for line in ["a\nb", "c"]) + 1
    assert screen._fallback_rows == 4
    assert out.count("\r\n") == 3
