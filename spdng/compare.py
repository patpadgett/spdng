"""Comparison views: run several measurements and line them up.

The question this answers is "which network is my connection actually
good at?", and the second question, "is my connection getting worse?".
Both need more than one number side by side.
"""
from __future__ import annotations

from collections.abc import Sequence

from . import style
from .measure import SpeedTestResult


def _fmt(value: float | None, unit: str, decimals: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{decimals}f} {unit}"


def _delta(new: float | None, old: float | None, *, higher_is_better: bool,
           unit: str) -> str:
    """Render the change between two measurements."""
    if new is None or old is None or old == 0:
        return ""
    pct = (new - old) / old * 100
    if abs(pct) < 0.5:
        return style.paint("  ~same", "muted")
    arrow = "▲" if pct > 0 else "▼"
    good = (pct > 0) == higher_is_better
    role = "good" if good else "bad"
    return style.paint(f"{arrow} {abs(pct):+.1f}%", role)


def render_comparison(results: Sequence[SpeedTestResult],
                      titles: Sequence[str] | None = None,
                      show_bars: bool = True) -> str:
    """Render several results as columns, one per measurement.

    Each column is a separate provider (or a separate run), so you can see
    at a glance that your link is fast to Cloudflare and slow to a
    particular speedtest server, which is a routing problem rather than a
    bandwidth problem.
    """
    if not results:
        return "no results to compare"

    n = len(results)
    titles = list(titles) if titles else [
        f"#{i + 1}" for i in range(n)]

    # Work out the widest label we need.
    labels = ["Provider", "Server", "Distance", "Download", "Upload",
              "Ping", "Jitter", "Loss"]
    label_w = max(len(x) for x in labels) + 2

    # Column widths: give each column at least 15 chars, and let them grow
    # to fill the terminal, but not so much that the table becomes a wall
    # of whitespace.
    total = max(78, style.term_width())
    col_w = max(15, min(26, (total - label_w) // n))

    header = "".join(f"{t[:col_w - 1]:<{col_w}}" for t in titles)
    out = [style.paint(f"{'':<{label_w}}{header}", "heading")]
    out.append(style.paint("─" * min(78, label_w + col_w * n), "dim"))

    def row(label: str, values: list[str]) -> str:
        cells = "".join(f"{v[:col_w - 1]:<{col_w}}" for v in values)
        return f"{label:<{label_w}}{cells}"

    providers = []
    servers = []
    distances = []
    for r in results:
        srv = r.server
        providers.append(getattr(srv, "provider", "ookla") if srv else "?")
        servers.append(f"{srv.sponsor} {srv.name}" if srv else "?")
        distances.append(f"{srv.distance_km:.0f} km" if srv else "?")

    out.append(row("Provider", [style.paint(p, "accent") for p in providers]))
    out.append(row("Server", [style.paint(v[:col_w - 1], "muted")
                               for v in servers]))
    out.append(row("Distance", distances))

    downloads = [r.download_mbps for r in results]
    uploads = [r.upload_mbps for r in results]
    pings = [r.latency.avg_ms for r in results]
    jitters = [r.latency.jitter_ms for r in results]
    losses = [r.latency.loss_pct for r in results]

    out.append(row("Download", [
        style.paint(_fmt(v, "Mbit/s"), "good" if v and v > 10 else "value")
        for v in downloads]))
    out.append(row("Upload", [
        style.paint(_fmt(v, "Mbit/s"), "ok" if v and v > 1 else "value")
        for v in uploads]))
    out.append(row("Ping", [
        style.paint(_fmt(v, "ms"), "value" if v else "muted") for v in pings]))
    out.append(row("Jitter", [
        style.paint(_fmt(v, "ms"), "value" if v else "muted") for v in jitters]))
    out.append(row("Loss", [
        style.paint(_fmt(v, "%", 1), "good" if v == 0 else "bad")
        if v is not None else "n/a" for v in losses]))

    if show_bars and downloads:
        best = max(downloads) or 1
        out.append("")
        out.append(style.paint(f"{'':<{label_w}}Download, relative to best", "dim"))
        for t, v in zip(titles, downloads, strict=False):
            frac = (v / best) if best else 0
            bar = "█" * int(round(frac * 18)) or "·"
            out.append(f"{'':<{label_w}}{t[:col_w - 1]:<{col_w}}"
                       f"{style.paint(bar, 'accent')} "
                       f"{v:.1f} Mbit/s")

    return "\n".join(out)


def render_repeat_comparison(runs: Sequence[SpeedTestResult],
                             label: str = "Run") -> str:
    """Compare repeated runs of the same test.

    A single speed test is noisy. Two runs that differ by 30% tell you
    nothing; three runs that agree tell you a lot. This shows the spread.
    """
    if len(runs) < 2:
        return render_comparison(runs, [f"{label} {i + 1}"
                                        for i in range(len(runs))])

    out = [style.paint(f"{len(runs)} runs of the same test", "heading"), ""]
    rows = [
        ("Download", [r.download_mbps for r in runs], "Mbit/s", True),
        ("Upload", [r.upload_mbps for r in runs], "Mbit/s", True),
        ("Ping", [r.latency.avg_ms for r in runs], "ms", False),
    ]
    for name, values, unit, _higher_better in rows:
        clean = [v for v in values if v is not None]
        if not clean:
            continue
        lo, hi = min(clean), max(clean)
        spread = ((hi - lo) / lo * 100) if lo else 0.0
        stability = ("stable" if spread < 10 else
                     "somewhat variable" if spread < 25 else "very variable")
        out.append(f"  {name}:")
        for i, v in enumerate(values):
            bar_val = ""
            if v is not None and hi > lo:
                bar_val = " " * int(round((v - lo) / (hi - lo) * 24))
            out.append(f"    run {i + 1}: {v:>10.2f} {unit:<8} "
                       f"{style.paint(bar_val, 'accent')}")
        out.append(f"    {style.paint('spread', 'dim')}: {spread:.1f}% "
                   f"({stability})")
        out.append("")
    return "\n".join(out)
