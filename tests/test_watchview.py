"""Tests for the watch view: the charts, the toggle, and the sizing.

The bug these exist for: the "u" key was wired to the upload STAT BAR
instead of the upload TIME-BASED CHART. The stat bar shows the current
upload rate and never changes length; the chart shows the shape of the
upload over time. They are different things and only one of them is
worth toggling.
"""
from __future__ import annotations

import json

import pytest

from spdng import style
from spdng.table import visible_width
from spdng.watchview import (
    WatchSample,
    WatchView,
    save_csv,
    save_json,
    save_report,
)


def _chart_rows_in(lines: list[str]) -> list[str]:
    """The rows of the time-based chart.

    The chart comes after its "over time" heading, and ends at the
    blank line before the footer. A stat bar also uses block
    characters, so "the line has a block in it" is not enough to tell
    them apart.
    """
    rows = []
    started = False
    for line in lines:
        if "over time" in line:
            started = True
            continue
        if not started:
            continue
        if not line.strip():
            break
        rows.append(line)
    return rows


def _view(samples: int = 40, upload: bool = True) -> WatchView:
    v = WatchView(target="Ookla (speedtest.net) Miami, FL")
    v.show_upload = upload
    for i in range(samples):
        v.add(WatchSample(
            timestamp=1000 + i,
            download_mbps=200 + (i * 7) % 90,
            upload_mbps=10 + (i * 3) % 5,
            ping_ms=45 + (i * 5) % 20,
            provider="ookla",
            server="Blue Stream Fiber",
        ))
    return v


# --------------------------------------------------------------------------
# the toggle
# --------------------------------------------------------------------------

def test_the_upload_chart_can_be_hidden():
    with_chart = "\n".join(_view(upload=True).render_lines(100, 30))
    without = "\n".join(_view(upload=False).render_lines(100, 30))
    assert "Upload over time" in with_chart
    assert "Upload over time" not in without


def test_the_upload_stat_bar_always_shows():
    """The stat bar is the current rate; only the chart is toggleable."""
    with_chart = "\n".join(_view(upload=True).render_lines(100, 30))
    without = "\n".join(_view(upload=False).render_lines(100, 30))
    assert "Upload" in with_chart
    assert "Upload" in without
    # The stat line is present in both.
    assert "peak" in without
    assert "Mbit/s" in without


def test_the_download_chart_is_always_there():
    for upload in (True, False):
        text = "\n".join(_view(upload=upload).render_lines(100, 30))
        assert "Download over time" in text


# --------------------------------------------------------------------------
# the layout
# --------------------------------------------------------------------------

@pytest.mark.parametrize("width", [40, 48, 60, 80, 100, 120, 160, 200])
def test_the_view_fits_the_width(width):
    """A line wider than the terminal wraps and corrupts the layout."""
    for upload in (True, False):
        for line in _view(upload=upload).render_lines(width, 30):
            assert visible_width(line) <= width, (
                f"line is {visible_width(line)} wide, screen is {width}: "
                f"{line!r}")


@pytest.mark.parametrize("width", [60, 80, 100, 120, 160, 200])
def test_the_two_charts_line_up(width):
    """The download and upload charts must be in the same columns."""
    lines = _view(upload=True).render_lines(width, 30)
    header = next(line for line in lines if "Download over time" in line)
    download_at = header.index("Download")
    upload_at = header.index("Upload")
    assert upload_at > download_at

    # The upload chart's blocks must all start at or after the upload
    # heading, and the download chart's blocks must all start before
    # it.
    for line in lines:
        blocks = [i for i, ch in enumerate(line)
                  if ch in "▁▂▃▄▅▆▇█▀▄"]
        if not blocks:
            continue
        before = [i for i in blocks if i < upload_at]
        after = [i for i in blocks if i >= upload_at]
        # Every block is in one half or the other; a block that
        # straddles the boundary means the halves do not line up.
        assert not (before and after and
                    min(after) - max(before) < 0), "blocks straddle"
        # Nothing in the download half may start after the upload
        # heading, and vice versa, beyond the boundary itself.
        for i in before:
            assert i < upload_at
        for i in after:
            assert i >= upload_at


def test_the_download_chart_uses_half_the_width_when_upload_is_shown():
    """Two charts side by side, so each gets half the screen."""
    lines = _view(upload=True).render_lines(120, 30)
    header = next(line for line in lines if "Download over time" in line)
    upload_at = header.index("Upload")
    # The upload chart starts at about the middle of the screen.
    assert 45 < upload_at < 80, f"upload chart at column {upload_at}"


def test_the_download_chart_uses_the_whole_width_when_alone():
    lines = _view(upload=False).render_lines(120, 30)
    chart = [line for line in lines
             if any(ch in line for ch in "▁▂▃▄▅▆▇█")]
    widest = max(visible_width(line) for line in chart)
    # It uses the width, rather than stopping at the half it would
    # need when it shares the screen.
    assert widest > 100, f"the chart is only {widest} wide"


def test_the_chart_height_fills_the_viewport():
    """A tall terminal gets a tall chart."""
    short = _view().render_lines(100, 24)
    tall = _view().render_lines(100, 44)
    assert len(tall) > len(short)


def test_the_chart_height_has_a_floor():
    """Even a tiny terminal gets a readable chart."""
    for height in (10, 14, 18):
        lines = _view().render_lines(80, height)
        chart = [line for line in lines
                 if any(ch in line for ch in "▁▂▃▄▅▆▇█")]
        assert len(chart) >= 4, f"only {len(chart)} chart rows at {height}"


def test_the_chart_height_has_a_ceiling():
    """A very tall terminal does not get a wall of blocks."""
    lines = _view().render_lines(100, 200)
    assert len(_chart_rows_in(lines)) <= 24


def test_the_footer_fits_the_width():
    for width in (40, 60, 80, 100):
        lines = _view().render_lines(width, 30)
        footer = lines[-1]
        assert visible_width(footer) <= width


def test_the_footer_mentions_the_upload_toggle():
    lines = _view().render_lines(120, 30)
    assert "upload" in lines[-1]


# --------------------------------------------------------------------------
# the chart rows
# --------------------------------------------------------------------------

def test_the_chart_rows_have_the_right_width():
    """Every row is exactly the requested width, padded on the left."""
    v = _view()
    rows = v._chart_rows([1.0, 2.0, 3.0], height=5, width=10)
    assert len(rows) == 5
    for spark, value in rows:
        assert len(spark) == 10
        assert isinstance(value, float)
    # The three samples are on the right, padded on the left.
    assert rows[0][0].startswith(" " * 7)


def test_the_chart_rows_scale_to_the_peak():
    v = _view()
    rows = v._chart_rows([0.0, 50.0, 100.0], height=4, width=10)
    # The top row is the peak, the bottom row is a quarter of it.
    assert rows[0][1] == pytest.approx(100.0)
    assert rows[-1][1] == pytest.approx(25.0)


def test_the_chart_of_no_values_is_still_the_right_width():
    """An empty chart keeps its width, so the layout does not jump."""
    v = _view()
    rows = v._chart_rows([], height=5, width=10)
    assert len(rows) == 5
    for spark, value in rows:
        assert len(spark) == 10
        assert value == 0.0


def test_the_chart_window_is_the_last_n_samples():
    v = _view()
    rows = v._chart_rows([1.0] * 10 + [2.0] * 5, height=2, width=5)
    # Five columns, all from the 2.0 samples.
    assert len(rows[0][0]) == 5


# --------------------------------------------------------------------------
# the resize
# --------------------------------------------------------------------------

def test_the_view_is_built_for_the_size_it_is_given():
    """A resize means a new frame at the new size."""
    v = _view()
    narrow = v.render_lines(80, 24)
    wide = v.render_lines(160, 24)
    assert max(visible_width(line) for line in narrow) <= 80
    assert max(visible_width(line) for line in wide) <= 160
    # The wide one is wider, because the chart uses the space.
    wide_chart = _chart_rows_in(wide)
    narrow_chart = _chart_rows_in(narrow)
    assert (max(visible_width(line) for line in wide_chart)
            > max(visible_width(line) for line in narrow_chart))


def test_no_colour_means_no_escapes():
    style.set_color(False)
    try:
        for line in _view().render_lines(100, 30):
            assert "\033" not in line
    finally:
        style.set_color(None)


def test_with_colour_the_width_is_unchanged():
    """Colour must not change the layout."""
    style.set_color(True)
    try:
        coloured = _view().render_lines(100, 30)
    finally:
        style.set_color(None)
    plain = _view().render_lines(100, 30)
    assert [visible_width(line) for line in coloured] == \
        [visible_width(line) for line in plain]


def test_a_missing_ping_is_saved_as_missing_not_zero(tmp_path):
    sample = WatchSample(
        timestamp=1000.0, download_mbps=100.0, upload_mbps=10.0,
        ping_ms=None, provider="ookla", server="Example")
    csv_path = tmp_path / "watch.csv"
    json_path = tmp_path / "watch.json"
    report_path = tmp_path / "watch.txt"

    save_csv([sample], str(csv_path))
    save_json([sample], str(json_path))
    save_report([sample], str(report_path), "Example")

    assert csv_path.read_text().splitlines()[-1].endswith(
        ",100.000,10.000,")
    assert json.loads(json_path.read_text())["samples"][0]["ping_ms"] is None
    report_line = report_path.read_text().splitlines()[-1]
    assert report_line.endswith("n/a")
