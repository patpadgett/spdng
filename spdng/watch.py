"""Live monitoring: run a test on a timer and watch it move.

`speedtest --watch` is the thing you want when you are debugging a
connection rather than measuring it once. It re-runs the measurement on an
interval and draws a sparkline, so you can see the shape of your
connection over time: is it steady, is it dropping in the evening, does it
collapse when the neighbours start streaming?
"""
from __future__ import annotations

import sys
import time
from collections.abc import Callable

from . import fun, style
from .measure import SpeedTestResult


def _clear_line() -> None:
    sys.stdout.write("\r\033[K")
    sys.stdout.flush()


def render_watch_frame(results: list[SpeedTestResult], interval: float,
                       next_in: float) -> str:
    """One frame of the live display."""
    parts: list[str] = []
    if results:
        latest = results[-1]
        dl = latest.download_mbps
        up = latest.upload_mbps
        ping = latest.latency.avg_ms
        dl_text = f"{dl:>7.1f}" if dl is not None else f"{'n/a':>7}"
        up_text = f"{up:>6.1f}" if up is not None else f"{'n/a':>6}"
        parts.append(
            f"{style.paint(dl_text, 'good')} Mbit/s down  "
            f"{style.paint(up_text, 'ok')} Mbit/s up  "
            f"ping {style.paint(f'{ping:>6.1f}', 'value') if ping else '   n/a'} ms"
        )
        if len(results) > 1:
            history = [r.download_mbps for r in results
                       if r.download_mbps is not None]
            if history:
                parts.append(f"  {fun.sparkline_history(history)}")
    parts.append(style.paint(f"next in {next_in:.0f}s", "muted"))
    parts.append(style.paint("ctrl-c to stop", "muted"))
    return "  ".join(parts)


def watch(run_once: Callable[[], SpeedTestResult], interval: float = 30.0,
          duration: float = 0.0, clear: bool = True) -> list[SpeedTestResult]:
    """Run `run_once` on a loop, printing a live frame each time.

    duration of 0 means run until interrupted.
    """
    results: list[SpeedTestResult] = []
    started = time.perf_counter()
    try:
        while True:
            if duration and (time.perf_counter() - started) >= duration:
                break
            cycle_start = time.perf_counter()
            result = run_once()
            results.append(result)
            elapsed_in_cycle = time.perf_counter() - cycle_start
            if clear:
                _clear_line()
            next_in = max(0.0, interval - elapsed_in_cycle)
            frame = render_watch_frame(results, interval, next_in)
            sys.stdout.write(frame + "\n")
            sys.stdout.flush()

            if clear:
                _clear_line()
            sleep_for = max(0.0, interval - elapsed_in_cycle)
            if duration:
                sleep_for = min(sleep_for,
                                duration - (time.perf_counter() - started))
            if sleep_for <= 0:
                continue
            time.sleep(sleep_for)
    except KeyboardInterrupt:
        if clear:
            _clear_line()
            sys.stdout.write("\n")
    return results


def render_watch_summary(results: list[SpeedTestResult]) -> str:
    """A summary of a watch session, once it ends."""
    if not results:
        return "no samples collected"
    dls = [r.download_mbps for r in results
           if r.download_mbps is not None]
    pings = [r.latency.avg_ms for r in results if r.latency.avg_ms is not None]
    out: list[str] = []
    out.append(style.paint(f"{len(results)} samples", "heading"))
    if dls:
        out.append(f"  download: min {min(dls):.1f}  max {max(dls):.1f}  "
                   f"avg {sum(dls) / len(dls):.1f} Mbit/s")
    else:
        out.append("  download: skipped")
    if pings:
        out.append(f"  ping    : min {min(pings):.1f}  max {max(pings):.1f}  "
                   f"avg {sum(pings) / len(pings):.1f} ms")
    if dls:
        out.append(f"  {fun.sparkline_history(dls)}")
    if len(dls) > 1:
        spread = (max(dls) - min(dls)) / max(min(dls), 0.1) * 100
        verdict = ("steady" if spread < 15 else
                   "somewhat variable" if spread < 40 else "very variable")
        out.append(f"  {style.paint('spread', 'dim')}: {spread:.0f}% "
                   f"({verdict})")
    return "\n".join(out)
