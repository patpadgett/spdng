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
    download_mbps: float | None
    upload_mbps: float | None
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
    #: Whether the upload TIME-BASED chart is shown, on the right of
    #: the download chart. The user toggles it with "u". The upload
    #: STAT bar above is always shown - that is a different thing, the
    #: current upload rate, and it is worth seeing.
    show_upload: bool = True
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

    def _stat_line(self, label: str, unit: str, peak: float | None,
                   last: float | None, avg: float | None,
                   width: int = 0) -> str:
        """A padded summary line, so the numbers sit under each other.

        The line is trimmed to fit the terminal: the "avg" field goes
        first, because peak and last are the numbers you read.
        """
        from .table import pad

        def value(number: float | None) -> str:
            return f"{number:>8.1f}" if number is not None else "     n/a"

        parts = [
            f"    {pad('peak', 10)}{value(peak)} {unit}",
            f"   {pad('last', 6)}{value(last)}",
            f"   {pad('avg', 5)}{value(avg)}",
        ]
        line = parts[0]
        for extra in parts[1:]:
            if width and len(line) + len(extra) > width:
                break
            line += extra
        return line[:width] if width else line

    def render_lines(self, width: int = 100, height: int = 0) -> list[str]:
        """The frame as a list of strings.

        `width` and `height` are the viewport size. The layout uses
        the full width of the terminal, and the history chart grows to
        fill whatever height is left over, so the view fills the screen
        it was given instead of sitting in a 100x24 box in the corner.
        """
        out: list[str] = []
        elapsed = time.time() - self._started

        # The header carries the target, which can be a long server
        # name. It is shortened to fit, because a header that wraps
        # pushes the whole layout down.
        from .table import truncate
        stats = (f"{len(self.samples)} samples   {elapsed:.0f}s   "
                 f"{self.status} ")
        # Build the header, then shorten the target until the WHOLE
        # line fits. Working out the room by hand is how this ended
        # up two characters too wide: the prefix, the separator and
        # the ellipsis all count.
        prefix = " spdng watch -> "
        room = max(4, width - len(prefix) - len(stats) - 3)
        target = truncate(self.target, room)
        # Cut the target down until the whole line fits. The cut is on
        # the character count, and it always makes the string SHORTER,
        # so the loop terminates. (Truncating with an ellipsis does
        # not: the ellipsis puts the length back, so a loop that
        # truncates by one character each time never ends.)
        while len(f"{prefix}{target}   {stats}") > width and target:
            target = target[:-1]
        header = f"{prefix}{target}   {stats}"
        if len(header) > width:
            header = header[:width]
        out.append(style.paint(header, "heading"))
        if self.message:
            out.append(style.paint(f" {self.message}"[:width], "muted"))
        out.append("")

        if not self.samples:
            out.append(style.paint("  waiting for the first sample...", "muted"))
            out.append("")
            out.append(self._footer(width))
            return out

        dls = [s.download_mbps for s in self.samples
               if s.download_mbps is not None]
        pings = [s.ping_ms for s in self.samples if s.ping_ms is not None]
        ups = [s.upload_mbps for s in self.samples
               if s.upload_mbps is not None]
        peak_dl = max(dls) if dls else None
        peak_up = max(ups) if ups else None
        peak_ping = max(pings) if pings else 1

        # The graph leaves room for the indent, the current value, and
        # the unit. Never force a 20-column bar onto a narrower terminal:
        # that is how the stat bar used to overflow at 40 columns.
        graph_width = max(1, min(60, width - 19))

        # Download bar graph
        out.append(style.paint("  Download", "heading"))
        if dls:
            out.append(self._stat_line(
                "Download", "Mbit/s", peak_dl, dls[-1],
                sum(dls) / len(dls), width))
            bar = self._bar(dls[-1], peak_dl or 1, graph_width)
            out.append("    " + style.paint(bar, "accent")
                       + f" {dls[-1]:>7.1f} Mbit/s")
        else:
            out.append(style.paint("    skipped", "muted"))
        out.append("")

        # Upload stat bar. This always shows: it is the current upload
        # rate, which is worth seeing. The "u" key toggles the
        # TIME-BASED upload chart at the bottom, not this.
        out.append(style.paint("  Upload", "heading"))
        if ups:
            out.append(self._stat_line(
                "Upload", "Mbit/s", peak_up, ups[-1],
                sum(ups) / len(ups), width))
            bar = self._bar(ups[-1], peak_up or 1, graph_width)
            out.append("    " + style.paint(bar, "ok")
                       + f" {ups[-1]:>7.1f} Mbit/s")
        else:
            out.append(style.paint("    skipped", "muted"))
        out.append("")

        # Ping bar graph, inverted: lower is better, so we show "how bad is
        # it" and a tall bar always means a problem.
        out.append(style.paint("  Ping", "heading"))
        if pings:
            out.append(self._stat_line("Ping", "ms", peak_ping, pings[-1],
                                       sum(pings) / len(pings), width))
            inv = [(peak_ping - p) / peak_ping for p in pings]
            bar = self._bar(inv[-1], 1.0, graph_width)
            out.append("    " + style.paint(bar, "warn")
                       + f" {pings[-1]:>7.1f} ms")
        else:
            out.append(style.paint("    no ping samples yet", "muted"))

        # The time-based charts at the bottom. These are the graphs that
        # show the SHAPE of the measurement over time, as opposed to the
        # stat bars above, which only show the current value.
        #
        # The download chart is on the left. The upload chart is on the
        # right and can be switched on and off with "u", because the
        # upload number is the least interesting of the three on most
        # connections.
        out.append("")
        chart_height = self._chart_height(height, len(out) + 2)

        if self.show_upload and width >= 50:
            # Side by side. Each half gets an equal share of the width.
            # A half is: 2 spaces of indent, the spark, a space, and an
            # 8-character value, plus a 4-space gap between the halves.
            # Below 50 columns that minimum does not fit, so the charts
            # are stacked instead of being drawn past the right edge.
            half = max(24, (width - 4) // 2)
            spark_width = max(10, half - 11)
            dl_rows = self._chart_rows(dls, chart_height, spark_width)
            up_rows = self._chart_rows(ups, chart_height, spark_width)
            # Both charts must be the same width, or their columns do
            # not line up. The sample windows are the same length, so
            # the spark rows are the same length too.
            assert all(len(r[0]) == spark_width for r in dl_rows), \
                "the download chart is not the right width"
            assert all(len(r[0]) == spark_width for r in up_rows), \
                "the upload chart is not the right width"

            # A row is: 2 indent + spark + 1 space + 7 for the value,
            # then a 4-space gap before the second half.
            second_start = spark_width + 16
            out.append(
                "  " + style.paint("Download over time", "heading")
                + " " * max(1, second_start - 2 - len("Download over time"))
                + style.paint("Upload over time", "heading"))
            for i in range(chart_height):
                dl_spark, dl_value = dl_rows[i]
                up_spark, up_value = up_rows[i]
                # Each half is built the same way and is the same
                # width, so the two sparks line up column for column.
                left = ("  " + dl_spark.ljust(spark_width)
                        + f" {dl_value:>7.1f}")
                right = ("  " + up_spark.ljust(spark_width)
                         + f" {up_value:>7.1f}")
                out.append(style.paint(left, "accent")
                           + "    " + style.paint(right, "ok"))
        else:
            # A full-width chart, either because upload is hidden or
            # because two charts cannot fit side by side.
            spark_width = max(1, width - 10)

            def full_chart(values: list[float], title: str, role: str) -> None:
                rows = self._chart_rows(values, chart_height, spark_width)
                out.append(style.paint(f"  {title}"[:width], "heading"))
                for spark, value in rows:
                    line = (("  " + spark.ljust(spark_width))
                            + f" {value:>7.1f}")
                    out.append(style.paint(line[:width], role))

            full_chart(dls, "Download over time", "accent")
            if self.show_upload:
                out.append("")
                full_chart(ups, "Upload over time", "ok")


        out.append("")
        out.append(self._footer(width))
        return out

    def _chart_rows(self, values: list[float], height: int,
                    width: int) -> list[tuple[str, float]]:
        """A time-based chart, as (spark_row, scale_value) pairs.

        One column per sample, so the chart shows the shape of the
        measurement rather than a single bar of the current value. The
        scale value is what the top row of that row represents, so the
        numbers down the side read as an axis.

        Every row is exactly `width` columns wide. A short history is
        padded on the LEFT, because the newest sample belongs on the
        right and the chart scrolls left as samples arrive. If the rows
        were not all the same width, the two charts side by side would
        not line up.
        """
        if width <= 0:
            return []
        window = values[-width:]
        chart = self._spark(window, height=height)
        if not chart:
            # No samples yet: the chart is still `width` wide, so the
            # layout does not jump when the first sample lands.
            return [(" " * width, 0.0) for _ in range(height)]
        padding = width - len(chart[0])
        if padding > 0:
            chart = [" " * padding + row for row in chart]
        peak = max(window) if window else 0
        rows = []
        for i, row in enumerate(chart):
            level = len(chart) - i
            rows.append((row, peak * level / len(chart)))
        return rows

    def _chart_height(self, viewport_height: int, used: int) -> int:
        """How many rows the history chart gets.

        The chart takes whatever height is left over, with a floor so it
        is still readable and a ceiling so it does not become a wall of
        blocks on a tall terminal.
        """
        if viewport_height <= 0:
            return 8
        left = viewport_height - used
        return max(4, min(24, left))

    def _footer(self, width: int = 100) -> str:
        """The hotkey line, trimmed to fit the terminal.

        The hints that do not fit are dropped from the end, so the ones
        that survive are the ones you need most. "q quit" is always
        kept, because a way out matters more than any other hint.
        """
        hints = [
            "q quit", "s save csv", "j save json", "r save report",
            "space pause", "+/- interval", "u upload",
        ]
        if not width:
            return " " + "   ".join(hints)
        chosen: list[str] = []
        for hint in hints:
            candidate = chosen + [hint]
            if 1 + len("   ".join(candidate)) > width - 1:
                break
            chosen = candidate
        if not chosen:
            chosen = ["q quit"]
        line = " " + "   ".join(chosen)
        if len(line) > width:
            # Even "q quit" does not fit, which means a very narrow
            # terminal. Cut it to fit rather than wrapping.
            line = line[:width]
        return line

    def render(self, screen: Screen) -> list[str]:
        height, width = screen.size()
        lines = self.render_lines(width, height)
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
                f"{s.download_mbps:.3f}" if s.download_mbps is not None else "",
                f"{s.upload_mbps:.3f}" if s.upload_mbps is not None else "",
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
    dls = [s.download_mbps for s in samples
           if s.download_mbps is not None]
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
        down = f"{s.download_mbps:>8.2f}" if s.download_mbps is not None \
            else f"{'n/a':>8}"
        up = f"{s.upload_mbps:>6.2f}" if s.upload_mbps is not None \
            else f"{'n/a':>6}"
        ping = f"{s.ping_ms:>6.2f}" if s.ping_ms is not None \
            else f"{'n/a':>6}"
        lines.append(
            f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(s.timestamp))}"
            f"  {down}  {up}  {ping}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path
