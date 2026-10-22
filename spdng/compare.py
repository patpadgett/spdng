"""Comparison views: run several measurements and line them up.

The question this answers is "which network is my connection actually
good at?", and the second question, "is my connection getting worse?".
Both need more than one number side by side.
"""
from __future__ import annotations

from collections.abc import Sequence

from . import style
from .measure import SpeedTestResult
from .table import pad


def _shorten(text: str, width: int) -> str:
    """Shorten a label to fit a column, at a word boundary.

    Cutting mid-word produces something that looks like a real name, so
    the last word is dropped instead and an ellipsis marks the cut.
    """
    from .table import visible_width
    if visible_width(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    cut = text[:width - 1]
    space = cut.rfind(" ")
    if space > width // 2:
        cut = cut[:space]
    return cut.rstrip() + "…"


def _fmt(value: float | None, unit: str, decimals: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{decimals}f} {unit}"


def _delta(new: float | None, old: float | None, *,
           higher_is_better: bool) -> str:
    """Render the change between two measurements."""
    if new is None or old is None or old == 0:
        return ""
    pct = (new - old) / old * 100
    if abs(pct) < 0.5:
        return style.paint("~same", "muted")
    arrow = "▲" if pct > 0 else "▼"
    good = (pct > 0) == higher_is_better
    role = "good" if good else "bad"
    return style.paint(f"{arrow}{abs(pct):.0f}%", role)


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
    titles = list(titles) if titles else [f"#{i + 1}" for i in range(n)]
    if len(titles) < n:
        titles.extend(f"#{i + 1}" for i in range(len(titles), n))
    elif len(titles) > n:
        titles = titles[:n]

    # Layout: the numeric rows live in strict columns so they line up and
    # can be compared at a glance. The server details get their own lines,
    # because forcing a long name like "Blue Stream Fiber" into a 20
    # character column truncates it to uselessness.
    total = max(76, style.term_width())
    col_w = max(14, min(24, (total - 12) // n))
    label_w = 11

    def row(label: str, values: list[str]) -> str:
        # Visible-width padding, not len(): a coloured value would
        # otherwise lose its trailing spaces and shift every later
        # column left by the length of the escape sequence.
        cells = "".join(pad(v, col_w, "<") for v in values)
        return f"{label:<{label_w}}{cells}"

    header = "".join(f"{t[:col_w - 1]:<{col_w}}" for t in titles)
    out = [style.paint(f"{'':<{label_w}}{header}", "heading")]
    out.append(style.paint("─" * min(total, label_w + col_w * n), "dim"))

    # The server details go in their own row, one column per provider, so
    # they line up with the numbers below. A long sponsor name is
    # shortened at a word boundary rather than cut mid-word, because a
    # truncated name reads as a real name.
    out.append(style.paint(f"{'Server':<{label_w}}", "muted"))
    server_cells = []
    for r in results:
        srv = r.server
        detail = f"{srv.sponsor} {srv.name}" if srv is not None else "?"
        server_cells.append(style.paint(_shorten(detail, col_w), "muted"))
    out.append(row("", server_cells))

    out.append(style.paint(f"{'Provider':<{label_w}}", "muted"))
    prov_cells = []
    for r in results:
        srv = r.server
        prov = getattr(srv, "provider", "?") if srv else "?"
        dist = f"{srv.distance_km:.0f} km" if srv and srv.distance_km else "-"
        prov_cells.append(f"{style.paint(prov, 'accent')} {dist}")
    out.append(row("", prov_cells))

    out.append("")
    downloads = [r.download_mbps for r in results]
    uploads = [r.upload_mbps for r in results]
    pings = [r.latency.avg_ms for r in results]
    jitters = [r.latency.jitter_ms for r in results]
    losses = [r.latency.loss_pct if r.latency.attempts else None
              for r in results]

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

    # With more than one column, show how each differs from the first.
    if n > 1:
        out.append("")
        out.append(style.paint(f"{'':<{label_w}}vs first column", "dim"))
        for label, values, better in (
            ("Download", downloads, True),
            ("Upload", uploads, True),
            ("Ping", pings, False),
        ):
            base = values[0]
            deltas = [_delta(v, base, higher_is_better=better) for v in values]
            out.append(row(label, deltas))

    known_downloads = [v for v in downloads if v is not None]
    if show_bars and known_downloads and max(known_downloads) > 0:
        best = max(known_downloads)
        out.append("")
        out.append(style.paint(f"{'':<{label_w}}Download, relative to best",
                                "dim"))
        for t, v in zip(titles, downloads, strict=False):
            if v is None:
                continue
            frac = v / best
            bar = "█" * max(1, int(round(frac * 20)))
            out.append(f"{'':<{label_w}}{t[:col_w - 1]:<{col_w}}"
                       f"{style.paint(bar, 'accent')} {v:.1f} Mbit/s")

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
            if v is None:
                out.append(f"    run {i + 1}: {'skipped':>10} {unit:<8}")
                continue
            bar_val = ""
            if hi > lo:
                bar_val = " " * int(round((v - lo) / (hi - lo) * 24))
            out.append(f"    run {i + 1}: {v:>10.2f} {unit:<8} "
                       f"{style.paint(bar_val, 'accent')}")
        out.append(f"    {style.paint('spread', 'dim')}: {spread:.1f}% "
                   f"({stability})")
        out.append("")
    return "\n".join(out)
