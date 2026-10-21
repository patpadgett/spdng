"""The watch view: a live bar graph.

A speed test is one number. The interesting question is the shape of the
number over time, so `spdng watch` draws a graph rather than a line of
text.

The graph is a bar per sample, scaled against the best sample seen so
far in the session, so the bars show relative change rather than absolute
speed. A download graph and a ping graph sit side by side, because they
move in opposite directions when something is wrong: speed falls and
latency rises.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import style
from .tui import Screen

BLOCKS = " ▁▂▃▄▅▆▇█"


@dataclass(slots=True)
class WatchSample:
    """One measurement in the watch session."""

    timestamp: float
    download_mbps: float
    upload_mbps: float
    ping_ms: float | None
    provider: str = ""
    server: str = ""


@dataclass(slots=True)
class WatchView:
    """The live watch screen."""

    target: str = ""
    samples: list[WatchSample] = field(default_factory=list)
    status: str = "starting"
    message: str = ""
    max_samples: int = 0
    _started: float = field(default_factory=time.time)

    def add(self, sample: WatchSample) -> None:
        self.samples.append(sample)

    def set_status(self, status: str, message: str = "") -> None:
        self.status = status
        self.message = message

    # -- graph ------------------------------------------------------------

    def _bar(self, value: float, peak: float, width: int) -> str:
        """A horizontal bar of `value` against `peak`."""
        if peak <= 0:
            return " " * width
        filled = int(round((value / peak) * width))
        filled = max(0, min(width, filled))
        return "█" * filled + "░" * (width - filled)

    def _spark(self, values: list[float], height: int = 6) -> list[str]:
        """A vertical bar chart, one column per sample."""
        if not values:
            return []
        peak = max(values) or 1
        rows = []
        for level in range(height, 0, -1):
            line = []
            for v in values:
                filled = (v / peak) * height
                if filled >= level:
                    line.append("█")
                elif filled >= level - 0.5:
                    line.append("▄")
                else:
                    line.append(" ")
            rows.append("".join(line))
        return rows

    def _stat_line(self, label: str, unit: str, peak: float, last: float,
                   avg: float) -> str:
        """A padded summary line, so the numbers sit under each other."""
        from .table import pad
        return (f"    {pad('peak', 10)}{peak:>8.1f} {unit}   "
                f"{pad('last', 6)}{last:>8.1f}   "
                f"{pad('avg', 5)}{avg:>8.1f}")

    def render_lines(self, width: int = 100) -> list[str]:
        """The frame as a list of strings."""
        out: list[str] = []
        elapsed = time.time() - self._started

        out.append(style.paint(
            f" spdng watch -> {self.target}   "
            f"{len(self.samples)} samples   {elapsed:.0f}s   {self.status} ",
            "heading"))
        if self.message:
            out.append(style.paint(f" {self.message}", "muted"))
        out.append("")

        if not self.samples:
            out.append(style.paint("  waiting for the first sample...", "muted"))
            out.append("")
            out.append(self._footer())
            return out

        dls = [s.download_mbps for s in self.samples]
        pings = [s.ping_ms for s in self.samples if s.ping_ms is not None]
        ups = [s.upload_mbps for s in self.samples]
        peak_dl = max(dls) or 1
        peak_up = max(ups) or 1
        peak_ping = max(pings) if pings else 1

        graph_width = max(20, min(64, width - 34))

        # Download bar graph
        out.append(style.paint("  Download", "heading"))
        out.append(self._stat_line("Download", "Mbit/s", peak_dl, dls[-1],
                                   sum(dls) / len(dls)))
        bar = self._bar(dls[-1], peak_dl, graph_width)
        out.append("    " + style.paint(bar, "accent")
                   + f" {dls[-1]:>7.1f} Mbit/s")
        out.append("")

        # Upload bar graph
        out.append(style.paint("  Upload", "heading"))
        out.append(self._stat_line("Upload", "Mbit/s", peak_up, ups[-1],
                                   sum(ups) / len(ups)))
        bar = self._bar(ups[-1], peak_up, graph_width)
        out.append("    " + style.paint(bar, "ok")
                   + f" {ups[-1]:>7.1f} Mbit/s")
        out.append("")

        # Ping bar graph, inverted: lower is better, so we show "how bad is
        # it" and a tall bar always means a problem.
        out.append(style.paint("  Ping", "heading"))
        if pings:
            out.append(self._stat_line("Ping", "ms", peak_ping, pings[-1],
                                       sum(pings) / len(pings)))
            inv = [(peak_ping - p) / peak_ping for p in pings]
            bar = self._bar(inv[-1], 1.0, graph_width)
            out.append("    " + style.paint(bar, "warn")
                       + f" {pings[-1]:>7.1f} ms")
        else:
            out.append(style.paint("    no ping samples yet", "muted"))

        # The vertical chart, which shows the shape over time.
        out.append("")
        out.append(style.paint("  Download over time", "heading"))
        window = dls[-graph_width:]
        chart = self._spark(window, height=8)
        scale = (max(window) if window else 0) / len(chart) if chart else 0
        for i, row in enumerate(chart):
            level = len(chart) - i
            value = scale * level
            out.append(f"  {style.paint(row, 'accent')} {value:>8.1f}")

        out.append("")
        out.append(self._footer())
        return out

    def _footer(self) -> str:
        return style.paint(
            " q quit   s save csv   j save json   r save report   "
            "space pause   +/- interval", "muted")

    def render(self, screen: Screen) -> list[str]:
        lines = self.render_lines(screen.size()[1])
        screen.print_frame(lines)
        return lines


def save_csv(samples: list[WatchSample], path: str) -> str:
    """Write the samples to a CSV file."""
    import csv
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["timestamp", "iso_time", "provider", "server",
                         "download_mbps", "upload_mbps", "ping_ms"])
        for s in samples:
            writer.writerow([
                f"{s.timestamp:.3f}",
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(s.timestamp)),
                s.provider, s.server,
                f"{s.download_mbps:.3f}", f"{s.upload_mbps:.3f}",
                f"{s.ping_ms:.3f}" if s.ping_ms is not None else "",
            ])
    return path


def save_json(samples: list[WatchSample], path: str) -> str:
    import json
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({
            "samples": [
                {
                    "timestamp": s.timestamp,
                    "iso_time": time.strftime("%Y-%m-%dT%H:%M:%S",
                                             time.localtime(s.timestamp)),
                    "provider": s.provider,
                    "server": s.server,
                    "download_mbps": s.download_mbps,
                    "upload_mbps": s.upload_mbps,
                    "ping_ms": s.ping_ms,
                }
                for s in samples
            ]
        }, fh, indent=2)
    return path


def save_report(samples: list[WatchSample], path: str, target: str) -> str:
    dls = [s.download_mbps for s in samples]
    pings = [s.ping_ms for s in samples if s.ping_ms is not None]
    lines = [
        "spdng watch report",
        f"target: {target}",
        f"samples: {len(samples)}",
        "",
    ]
    if dls:
        lines += [
            f"download: min {min(dls):.2f}  max {max(dls):.2f}  "
            f"avg {sum(dls) / len(dls):.2f} Mbit/s",
        ]
    if pings:
        lines += [
            f"ping    : min {min(pings):.2f}  max {max(pings):.2f}  "
            f"avg {sum(pings) / len(pings):.2f} ms",
        ]
    lines += ["", "timestamp              download   upload   ping"]
    for s in samples:
        lines.append(
            f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(s.timestamp))}"
            f"  {s.download_mbps:>8.2f}  {s.upload_mbps:>6.2f}  "
            f"{s.ping_ms if s.ping_ms is not None else 0:>6.2f}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path
