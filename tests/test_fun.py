"""Tests for the fun module and the watch display.

The rule these enforce: nothing in here may alter a measurement, and
nothing may raise on edge-case input.
"""
from __future__ import annotations

import pytest

from spdng import fun, watch

# --------------------------------------------------------------------------
# grades
# --------------------------------------------------------------------------

@pytest.mark.parametrize("mbps,expected", [
    (2000.0, "S+"),
    (600.0, "S"),
    (300.0, "A+"),
    (175.0, "A"),
    (120.0, "B+"),
    (75.0, "B"),
    (30.0, "C"),
    (12.0, "D"),
    (3.0, "E"),
    (0.0, "F"),
])
def test_grade_thresholds(mbps, expected):
    assert fun.grade_for(mbps)[0] == expected


def test_grade_is_monotonic():
    """Faster is never a worse grade."""
    speeds = [0, 1, 5, 10, 25, 50, 100, 150, 250, 500, 1000, 5000]
    grades = [fun.grade_for(s)[0] for s in speeds]
    order = ["F", "E", "D", "C", "B", "B+", "A", "A+", "S", "S+"]
    index = [order.index(g) for g in grades]
    assert index == sorted(index)


def test_render_grade_never_raises():
    for mbps in (-1.0, 0.0, 1e9):
        out = fun.render_grade(mbps)
        assert out


# --------------------------------------------------------------------------
# sparkline
# --------------------------------------------------------------------------

def test_sparkline_empty():
    assert fun.sparkline([]) == ""


def test_sparkline_single_value():
    assert len(fun.sparkline([5.0])) == 1


def test_sparkline_length_matches_input():
    values = [1.0, 5.0, 3.0, 9.0, 2.0]
    assert len(fun.sparkline(values)) == len(values)


def test_sparkline_keeps_most_recent():
    values = list(range(100))
    out = fun.sparkline(values, width=10)
    assert len(out) == 10


def test_sparkline_flat_values():
    out = fun.sparkline([3.0, 3.0, 3.0])
    assert len(out) == 3
    assert set(out) == {fun.SPARK_CHARS[0]}


def test_sparkline_min_is_lowest_char():
    values = [0.0, 100.0, 50.0]
    out = fun.sparkline(values)
    assert out[0] == fun.SPARK_CHARS[0]
    assert out[1] == fun.SPARK_CHARS[-1]


# --------------------------------------------------------------------------
# quips
# --------------------------------------------------------------------------

def test_quip_never_raises():
    for mbps in (-5.0, 0.0, 0.5, 10.0, 100.0, 10000.0):
        assert fun.quip_for(mbps)


def test_quip_fast_is_not_a_slow_quip():
    for _ in range(20):
        assert fun.quip_for(500.0) in fun.CLEAN_QUIPS


def test_quip_very_slow_is_slow_humour():
    for _ in range(20):
        assert fun.quip_for(0.5) in fun.VERY_SLOW_QUIPS


# --------------------------------------------------------------------------
# the cow
# --------------------------------------------------------------------------

def test_cowsay_wraps_the_text():
    out = fun.cowsay("hello")
    assert "< hello >" in out
    assert "(oo)" in out


def test_cowsay_handles_multiline():
    out = fun.cowsay("one\ntwo")
    assert "< one" in out
    assert "two" in out


def test_moo_is_a_cow():
    assert "(oo)" in fun.moo()


# --------------------------------------------------------------------------
# formatting helpers
# --------------------------------------------------------------------------

def test_elapsed_formats():
    assert fun.elapsed(0.5).endswith("ms")
    assert fun.elapsed(30).endswith("s")
    assert "m" in fun.elapsed(125)


def test_countdown_never_negative():
    assert fun.countdown(-5) == "0s"


def test_speedometer_shows_the_value():
    out = fun.render_speedometer(123.4)
    assert "123.4" in out
    assert "Mbit/s" in out


def test_speedometer_handles_zero():
    out = fun.render_speedometer(0.0)
    assert "0.0" in out


def test_speedometer_clamps_a_huge_value():
    """A 10 Gbit link must not overflow the bar."""
    out = fun.render_speedometer(10000.0)
    assert "10000.0" in out


def test_stopwatch_measures_time():
    w = fun.Stopwatch()
    assert w.elapsed() >= 0.0


# --------------------------------------------------------------------------
# watch rendering
# --------------------------------------------------------------------------

def _result(download=100.0, ping=20.0):
    from spdng.measure import LatencyResult, SpeedTestResult
    r = SpeedTestResult(server=None)
    r.download_mbps = download
    r.latency = LatencyResult(samples_ms=[ping], errors=0, attempts=1)
    return r


def test_watch_frame_with_no_results():
    frame = watch.render_watch_frame([], 30.0, 5.0)
    assert "next in" in frame


def test_watch_frame_shows_the_latest():
    frame = watch.render_watch_frame([_result(150.0, 12.0)], 30.0, 5.0)
    assert "150.0" in frame
    assert "12.0" in frame


def test_watch_frame_shows_a_sparkline_after_two_samples():
    frame = watch.render_watch_frame(
        [_result(100.0), _result(200.0)], 30.0, 5.0)
    assert any(ch in frame for ch in fun.SPARK_CHARS)


def test_watch_frame_shows_a_real_zero_ping():
    frame = watch.render_watch_frame([_result(100.0, 0.0)], 30.0, 5.0)
    assert "ping    0.0 ms" in frame


def test_watch_does_not_clear_a_redirected_stream(capsys):
    watch.style.set_color(False)
    try:
        results = watch.watch(
            lambda: _result(), interval=0.01, duration=0.03, clear=True)
    finally:
        watch.style.set_color(None)

    out = capsys.readouterr().out
    assert results
    assert "\r" not in out
    assert "\033[K" not in out


def test_watch_retries_after_a_failed_sample(capsys):
    calls = []

    def run_once():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("temporary network failure")
        return _result()

    results = watch.watch(
        run_once, interval=0.01, duration=0.05, clear=False,
        show_frames=False)

    captured = capsys.readouterr()
    assert len(calls) >= 2
    assert results
    assert "temporary network failure" in captured.err


def test_watch_summary_of_no_samples():
    assert "no samples" in watch.render_watch_summary([])


def test_watch_summary_reports_range_and_spread():
    out = watch.render_watch_summary(
        [_result(100.0), _result(120.0), _result(110.0)])
    assert "3 samples" in out
    assert "min 100.0" in out
    assert "max 120.0" in out
    assert "spread" in out


def test_watch_summary_marks_a_steady_link():
    out = watch.render_watch_summary(
        [_result(100.0), _result(101.0), _result(99.0)])
    assert "steady" in out


def test_watch_summary_marks_a_variable_link():
    out = watch.render_watch_summary(
        [_result(10.0), _result(200.0)])
    assert "variable" in out
