"""Terminal rendering: the speedtest readout and the mtr-style path view."""
from __future__ import annotations

import shutil
import sys
from collections.abc import Iterable

from .measure import SpeedTestResult
from .traceroute import Hop, TraceResult

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"


def _supports_color() -> bool:
    if not sys.stdout.isatty():
        return False
    import os
    return os.environ.get("TERM", "") not in ("", "dumb")


_COLOR = _supports_color()


def c(text: str, code: str) -> str:
    if not _COLOR:
        return text
    return f"{code}{text}{_RESET}"


def bar(fraction: float, width: int = 30, fill: str = "█", empty: str = "░") -> str:
    filled = max(0, min(width, int(round(fraction * width))))
    return fill * filled + empty * (width - filled)


def format_speed(download_mbps: float, upload_mbps: float) -> str:
    return (f"Download: {c(f'{download_mbps:.2f}', _BOLD)} Mbit/s   "
            f"Upload: {c(f'{upload_mbps:.2f}', _BOLD)} Mbit/s")


def render_result(result: SpeedTestResult, show_server: bool = True) -> str:
    """Render a speed test the way speedtest-cli does, but correctly."""
    out: list[str] = []
    srv = result.server
    if show_server and srv is not None:
        out.append(f"Hosted by {c(srv.sponsor, _BOLD)} ({srv.name}) "
                   f"[{srv.cc}] {c(f'{srv.distance_km:.0f} km', _DIM)}")
    if result.latency.avg_ms is not None:
        out.append(f"Ping: {c(f'{result.latency.avg_ms:.2f}', _BOLD)} ms   "
                   f"jitter: {result.latency.jitter_ms:.2f} ms   "
                   f"loss: {result.latency.loss_pct:.0f}%")
    else:
        out.append("Ping: " + c("unavailable", _BOLD) +
                   "   (latency probe failed; not reporting a placeholder)")
    out.append(format_speed(result.download_mbps, result.upload_mbps))
    if result.share_url:
        out.append(f"Share: {result.share_url}")
    for err in result.errors:
        out.append(c(f"  warning: {err}", _DIM))
    return "\n".join(out)


def _hop_loss_color(hop: Hop) -> str:
    if hop.received == 0:
        return c("100.0%", "\033[31m")
    if hop.loss_pct >= 20:
        return c(f"{hop.loss_pct:5.1f}%", "\033[31m")
    if hop.loss_pct > 0:
        return c(f"{hop.loss_pct:5.1f}%", "\033[33m")
    return c(f"{hop.loss_pct:5.1f}%", "\033[32m")


def _hop_ms_color(hop: Hop) -> str:
    avg = hop.avg_ms
    if avg is None:
        return c("     ---   ", _DIM)
    if avg > 150:
        return c(f"{avg:8.2f} ms", "\033[31m")
    if avg > 60:
        return c(f"{avg:8.2f} ms", "\033[33m")
    return c(f"{avg:8.2f} ms", "\033[32m")


def render_trace(trace: TraceResult, width: int = 30) -> str:
    """Render a trace mtr-style: one line per hop, loss, sent, last/avg/best/worst.

    mtr shows:  hostname  loss%  sent  last  avg  best  worst  stdev
    We show the same columns so the output is immediately familiar, plus
    jitter, which is the number that actually predicts trouble.
    """
    out: list[str] = []
    header = (f"{'Hop':>3}  {'Loss%':>6}  {'Snt':>3}  {'Last':>9}  "
              f"{'Avg':>9}  {'Best':>9}  {'Worst':>9}  {'Jitter':>7}  Address")
    out.append(c(header, _BOLD))
    out.append(c("-" * len(header), _DIM))
    for hop in trace.hops:
        label = hop.host or hop.addr or "*"
        if not hop.host and hop.addr:
            label = hop.addr
        if hop.addr is None and hop.received == 0:
            label = "*"
        last = f"{hop.last_ms:.2f} ms" if hop.last_ms is not None else "---"
        avg = f"{hop.avg_ms:.2f} ms" if hop.avg_ms is not None else "---"
        best = f"{hop.best_ms:.2f} ms" if hop.best_ms is not None else "---"
        worst = f"{hop.worst_ms:.2f} ms" if hop.worst_ms is not None else "---"
        jitter = f"{hop.jitter_ms:.2f}" if hop.jitter_ms is not None else "---"
        # A tilde marks an RTT we measured ourselves rather than one the
        # kernel reported, so the reader is never misled about precision.
        if hop.rtt_approximate:
            last = f"~{hop.last_ms:.2f} ms" if hop.last_ms is not None else "---"
            avg = f"~{hop.avg_ms:.2f} ms" if hop.avg_ms is not None else "---"
        out.append(
            f"{hop.ttl:>3}  {_hop_loss_color(hop)}  {hop.sent:>3}  "
            f"{last:>9}  {avg:>9}  {best:>9}  {worst:>9}  {jitter:>7}  {label}"
        )
    out.append("")
    if trace.destination_reached:
        out.append(c("Destination reached", "\033[32m"))
    else:
        out.append(c("Destination NOT reached", "\033[31m"))
    if trace.error:
        out.append(c(f"  {trace.error}", _DIM))
    return "\n".join(out)


def render_loss_summary(trace: TraceResult) -> str:
    """Point at the first hop where loss or latency starts to climb.

    This is the question mtr exists to answer.
    """
    lines: list[str] = []
    prev_latency = None
    culprit = None
    for hop in trace.hops:
        if hop.avg_ms is None:
            continue
        if prev_latency is not None and hop.avg_ms > prev_latency * 2.5 \
                and hop.avg_ms - prev_latency > 25:
            culprit = hop
            break
        prev_latency = hop.avg_ms

    final = trace.hops[-1] if trace.hops else None
    if final is not None:
        if final.loss_pct > 0:
            lines.append(c(
                f"Final hop shows {final.loss_pct:.0f}% loss "
                f"({final.received}/{final.sent} replies) "
                f"- path to the server is lossy", "\033[31m"))
        else:
            lines.append(c(
                f"Final hop: 0% loss over {final.sent} probes", "\033[32m"))

    if culprit is not None:
        lines.append(c(
            f"Latency jump at hop {culprit.ttl} "
            f"({culprit.addr or 'unknown'}): "
            f"{culprit.avg_ms:.1f} ms - upstream congestion begins here",
            "\033[33m"))
    elif final is not None and final.avg_ms is not None:
        lines.append(c(
            f"No significant latency jump across the path; "
            f"end-to-end latency {final.avg_ms:.1f} ms", "\033[32m"))
    return "\n".join(lines)


def render_server_table(servers: Iterable, limit: int = 10) -> str:
    """Render the candidate server list, nearest first."""
    out: list[str] = []
    out.append(c(f"{'#':>3}  {'Distance':>9}  {'Latency':>9}  "
                 f"{'Spons':<24} Location", _BOLD))
    out.append(c("-" * 78, _DIM))
    for i, s in enumerate(servers):
        if i >= limit:
            break
        lat = (f"{s.latency_ms:.2f} ms" if s.latency_ms is not None
               else c("     ---   ", _DIM))
        out.append(f"{i + 1:>3}  {s.distance_km:>7.0f} km  {lat:>9}  "
                   f"{s.sponsor[:24]:<24} {s.name}")
    return "\n".join(out)


def term_width(default: int = 80) -> int:
    try:
        return shutil.get_terminal_size((default, 24)).columns
    except OSError:
        return default
