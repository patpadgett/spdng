"""Box-framed output for the summary views.

A table of numbers is easier to read inside a frame, and a frame makes a
misalignment obvious at a glance rather than leaving you to squint at
ragged whitespace. Everything here uses the ANSI-aware padding from
`table.py`, so a coloured cell lines up exactly like a plain one.
"""
from __future__ import annotations

from . import style
from .table import ASCII_BOX, Table


def result_table(result, fun_on: bool = True) -> str:
    """The main speed test result, as a framed table."""
    t = Table(box=ASCII_BOX, title="spdng result")
    t.add_column("Metric", align="<")
    t.add_column("Value", align=">")
    t.add_column("Detail", align="<")

    srv = result.server
    if srv is not None:
        t.add_row([
            "Server",
            f"{srv.sponsor} {srv.name}",
            f"[{srv.cc}] {srv.distance_km:.0f} km" if srv.distance_km else "",
        ])
        t.add_row(["Provider", getattr(srv, "provider", "?"), ""])

    lat = result.latency
    if lat.avg_ms is not None:
        detail = f"jitter {lat.jitter_ms:.2f} ms   loss {lat.loss_pct:.0f}%"
        if lat.handshake_ms is not None:
            detail += f"   setup {lat.handshake_ms:.0f} ms"
        t.add_row(["Ping", f"{lat.avg_ms:.2f} ms", detail])
    elif lat.attempts == 0 and lat.errors == 0:
        t.add_row(["Ping", style.paint("skipped", "muted"),
                   "not measured"])
    else:
        t.add_row([
            "Ping",
            style.paint("unavailable", "warn"),
            style.paint("latency probe failed; no placeholder reported",
                        "muted"),
        ])

    for label, value, detail, role in (
        ("Download", result.download_mbps, result.download_detail, "good"),
        ("Upload", result.upload_mbps, result.upload_detail, "ok"),
    ):
        if value is None:
            t.add_row([label, style.paint("skipped", "muted"),
                       "not measured"])
            continue
        t.add_row([
            label,
            style.paint(f"{value:.2f} Mbit/s", role),
            f"{detail.bytes_transferred / 1e6:.1f} MB in "
            f"{detail.seconds:.1f}s",
        ])

    if fun_on and result.download_mbps is not None:
        from . import fun as funmod
        letter, word = funmod.grade_for(result.download_mbps)
        t.add_row(["Grade", style.paint(letter, "good"),
                   style.paint(word, "muted")])

    for err in result.errors:
        t.add_row([style.paint("warning", "warn"), err, ""])

    return t.render()


def comparison_table(results, titles) -> str:
    """A framed version of the provider comparison."""
    t = Table(box=ASCII_BOX, title="spdng comparison")
    for title in titles:
        t.add_column(title, align=">")

    rows: list[list[str]] = []

    servers = []
    for r in results:
        srv = r.server
        servers.append(f"{srv.sponsor} {srv.name}" if srv else "?")
    rows.append(servers)

    provs = []
    dists = []
    for r in results:
        srv = r.server
        provs.append(getattr(srv, "provider", "?") if srv else "?")
        dists.append(f"{srv.distance_km:.0f} km"
                     if srv and srv.distance_km else "-")
    rows.append(provs)
    rows.append(dists)

    rows.append([
        f"{r.download_mbps:.2f} Mbit/s" if r.download_mbps is not None
        else "n/a" for r in results])
    rows.append([
        f"{r.upload_mbps:.2f} Mbit/s" if r.upload_mbps is not None
        else "n/a" for r in results])
    rows.append([
        f"{r.latency.avg_ms:.2f} ms" if r.latency.avg_ms is not None else "n/a"
        for r in results])
    rows.append([
        f"{r.latency.jitter_ms:.2f} ms" if r.latency.jitter_ms is not None
        else "n/a" for r in results])
    rows.append([
        f"{r.latency.loss_pct:.1f}%" if r.latency.loss_pct is not None
        else "n/a" for r in results])

    for row in rows:
        t.add_row(row)
    return t.render()


def server_table(servers, limit: int = 10) -> str:
    """A framed list of candidate servers."""
    t = Table(box=ASCII_BOX, title="candidate servers")
    t.add_column("#", align=">")
    t.add_column("Distance", align=">")
    t.add_column("Latency", align=">")
    t.add_column("Sponsor", align="<")
    t.add_column("Location", align="<")

    for i, s in enumerate(servers):
        if i >= limit:
            break
        lat = (f"{s.latency_ms:.2f} ms" if s.latency_ms is not None
               else style.paint("---", "muted"))
        t.add_row([str(i + 1), f"{s.distance_km:.0f} km", lat,
                    s.sponsor[:24], s.name])
    return t.render()


def trace_table(trace) -> str:
    """A framed version of the mtr-style trace."""
    t = Table(box=ASCII_BOX, title=f"path to {trace.target}")
    t.add_column("Hop", align=">")
    t.add_column("Loss%", align=">")
    t.add_column("Snt", align=">")
    t.add_column("Last", align=">")
    t.add_column("Avg", align=">")
    t.add_column("Best", align=">")
    t.add_column("Worst", align=">")
    t.add_column("Jitter", align=">")
    t.add_column("History", align="<")
    t.add_column("Address", align="<")

    for hop in trace.hops:
        loss = hop.loss_pct
        if loss >= 50:
            role = "bad"
        elif loss > 0:
            role = "warn"
        else:
            role = "good"

        def ms(value, approximate=hop.rtt_approximate):
            if value is None:
                return "---"
            prefix = "~" if approximate else ""
            return f"{prefix}{value:.2f} ms"

        t.add_row([
            str(hop.ttl),
            style.paint(f"{loss:.1f}%", role),
            str(hop.sent),
            ms(hop.last_ms),
            ms(hop.avg_ms),
            ms(hop.best_ms),
            ms(hop.worst_ms),
            ms(hop.jitter_ms),
            _history_cell(hop),
            hop.addr or "*",
        ])
    return t.render()


def _history_cell(hop) -> str:
    """The probe history for a hop, as dots and letters.

    A static trace has no history to speak of, so we synthesise one from
    the outcome of the probes we did run: a hop that answered every time
    gets dots, a hop that timed out gets 'o's. It is a summary of what
    happened, not a record of a live session.
    """
    from .tui import PROBE_OK, PROBE_TIMEOUT, _colour_history
    answered = hop.received
    lost = max(0, hop.sent - hop.received)
    chars = (PROBE_OK * answered + PROBE_TIMEOUT * lost)
    if not chars:
        return style.paint("---", "muted")
    return _colour_history(chars)


def result_to_csv(result) -> str:
    """The result as CSV, for people who want to do their own analysis."""
    import csv
    import io
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["metric", "value", "unit", "detail"])
    srv = result.server
    if srv is not None:
        writer.writerow(["server", f"{srv.sponsor} {srv.name}", "",
                         f"provider={getattr(srv, 'provider', '?')}"])
        writer.writerow(["distance", f"{srv.distance_km:.2f}", "km", ""])
    lat = result.latency
    writer.writerow(["ping", f"{lat.avg_ms:.4f}" if lat.avg_ms is not None else "",
                     "ms", f"jitter={lat.jitter_ms}"])
    writer.writerow(["jitter", f"{lat.jitter_ms:.4f}"
                     if lat.jitter_ms is not None else "", "ms", ""])
    writer.writerow(["loss",
                     f"{lat.loss_pct:.2f}" if lat.attempts else "",
                     "%", "" if lat.attempts else "not measured"])
    writer.writerow([
        "download",
        f"{result.download_mbps:.4f}" if result.download_mbps is not None
        else "",
        "Mbit/s",
        (f"{result.download_detail.bytes_transferred} bytes in "
         f"{result.download_detail.seconds:.3f}s")
        if result.download_mbps is not None else "not measured",
    ])
    writer.writerow([
        "upload",
        f"{result.upload_mbps:.4f}" if result.upload_mbps is not None
        else "",
        "Mbit/s",
        (f"{result.upload_detail.bytes_transferred} bytes in "
         f"{result.upload_detail.seconds:.3f}s")
        if result.upload_mbps is not None else "not measured",
    ])
    for i, sample in enumerate(lat.samples_ms):
        writer.writerow([f"ping_sample_{i}", f"{sample:.4f}", "ms", ""])
    return buf.getvalue()
