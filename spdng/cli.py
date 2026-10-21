"""spdng - a maintained speedtest CLI with mtr-style path analysis.

A drop-in-ish replacement for the archived speedtest-cli (v2.1.3, 2021),
plus the thing speedtest-cli never had: a traceroute to the server so you
can see WHERE your speed is being lost, not just how much.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time

from . import (
    compare,
    frames,
    fun,
    history,
    live,
    measure,
    providers,
    render,
    servers,
    style,
    traceroute,
    watch,
)
from .version import __version__

USER_AGENT = measure.DEFAULT_UA


def _apply_style(args) -> None:
    """Set up colour and theme from the parsed arguments."""
    if getattr(args, "no_color", False):
        style.set_color(False)
    elif getattr(args, "color", False):
        style.set_color(True)
    if getattr(args, "theme", None):
        style.set_theme(args.theme)


def _set_origin(provider, args) -> None:
    """Tell the provider where the user is, so it can rank by distance.

    Only some providers need it (Ookla and Cloudflare know your location
    server-side; LibreSpeed's public list is just a flat list), but it
    costs one lookup and makes the ranking consistent.
    """
    setter = getattr(provider, "set_origin", None)
    if setter is None:
        return
    try:
        lat, lon, _attrs = servers.get_client_location(ua=USER_AGENT)
    except Exception:  # noqa: BLE001
        return
    setter(lat, lon)


def _pick_server(provider, args) -> tuple[servers.Server | None, str | None]:
    """Pick the server to test against."""
    _set_origin(provider, args)
    candidates = provider.list_servers(limit=max(10, args.limit))

    if not candidates:
        return None, f"{provider.label} returned no servers"

    if args.server:
        needle = args.server.lower()
        for s in candidates:
            if (needle in s.host.lower() or needle in s.name.lower()
                    or needle in s.sponsor.lower()):
                return s, None
        # A URL means "use this exact server", which is how you point at a
        # LibreSpeed instance you run yourself.
        if args.server.lower().startswith(("http://", "https://")):
            url = args.server.rstrip("/")
            host = url.split("//", 1)[-1].split("/")[0]
            return servers.Server(
                id="custom", host=host, sponsor="Custom", name=url,
                country="", cc="", lat=0.0, lon=0.0, distance_km=0.0,
                url=url, provider=provider.id,
            ), None
        return None, f"no server matched {args.server!r}"

    if args.max_distance > 0:
        candidates = [s for s in candidates if s.distance_km <= args.max_distance]
        if not candidates:
            return None, (f"no servers within {args.max_distance} km; "
                          f"raise --max-distance")
    return candidates[0], None


def _run_one(provider, server, args, *, do_trace: bool):
    """Run a full measurement against one server with one provider."""
    result = measure.SpeedTestResult(server=server)
    result.provider = provider.id
    result.provider_label = provider.label

    result.latency = provider.measure_latency(
        server, count=args.latency_samples)
    server.latency_ms = result.latency.avg_ms
    server.jitter_ms = result.latency.jitter_ms

    if result.latency.avg_ms is None:
        result.errors.append(
            "latency probe failed against every server; "
            "the server may require a browser session")

    if not args.no_download:
        result.download_detail = provider.measure_download(
            server, duration=args.duration, threads=args.threads)
        result.download_mbps = result.download_detail.mbps

    if not args.no_upload:
        result.upload_detail = provider.measure_upload(
            server, duration=args.duration, threads=max(1, args.threads // 2))
        result.upload_mbps = result.upload_detail.mbps

    trace = None
    if do_trace:
        host = server.host.split(":")[0]
        port = int(server.host.split(":")[1]) if ":" in server.host else 8080
        try:
            target_ip = socket.gethostbyname(host)
        except OSError:
            target_ip = host
        trace = traceroute.traceroute(
            target_ip, port=port, max_hops=args.max_hops,
            probes=args.trace_probes, timeout=args.trace_timeout)
        result.errors.extend(_trace_notes(trace))

    return result, trace


def _trace_notes(trace) -> list[str]:
    notes = []
    if trace.error:
        notes.append(f"traceroute: {trace.error}")
    if not trace.hops:
        notes.append("traceroute: no hops probed")
    return notes


def cmd_speedtest(args) -> int:
    _apply_style(args)
    try:
        provider = providers.get_provider(args.provider)
    except providers.ProviderError as exc:
        print(f"spdng: {exc}", file=sys.stderr)
        return 2
    server, err = _pick_server(provider, args)
    if err:
        print(f"spdng: {err}", file=sys.stderr)
        return 2

    if args.verbose:
        print(f"Provider: {provider.describe()}", file=sys.stderr)
        print(f"Selected {server.host} ({server.name}) "
              f"{server.distance_km:.0f} km away", file=sys.stderr)

    result, trace = _run_one(provider, server, args, do_trace=args.trace)

    if not args.no_history:
        history.append(history.entry_from_result(result, provider.id))

    if args.json:
        payload = result.to_dict()
        payload["provider"] = provider.id
        payload["provider_label"] = provider.label
        if trace is not None:
            payload["trace"] = trace.to_dict()
        print(json.dumps(payload, indent=2))
        return 0

    if args.csv:
        sys.stdout.write(frames.result_to_csv(result))
        return 0

    if args.save:
        path = _write_report(result, args, provider)
        if args.verbose:
            print(f"saved {path}", file=sys.stderr)

    print()
    print(frames.result_table(result, fun_on=not args.no_fun))
    if trace is not None:
        print()
        print(frames.trace_table(trace))
        print()
        print(render.render_loss_summary(trace))
    print()
    return 0


def _write_report(result, args, provider) -> str:
    """Write a result to a file, in the format the extension asks for."""
    import os
    if args.save_dir:
        os.makedirs(args.save_dir, exist_ok=True)
        base = args.save_dir
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        root = xdg if xdg else os.path.join(os.path.expanduser("~"),
                                           ".local", "share")
        base = os.path.join(root, "spdng", "reports")
        os.makedirs(base, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    ext = os.path.splitext(args.save)[1].lstrip(".") or "txt"
    path = os.path.join(base, f"result-{stamp}.{ext}")
    if ext == "csv":
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(frames.result_to_csv(result))
    elif ext == "json":
        payload = result.to_dict()
        payload["provider"] = provider.id
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
    else:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(frames.result_table(result, fun_on=not args.no_fun) + "\n")
    return path


def cmd_compare(args) -> int:
    """Run the same test against several providers and line them up."""
    _apply_style(args)
    results = []
    titles = []
    traces = {}

    for name in args.providers:
        try:
            provider = providers.get_provider(name)
        except providers.ProviderError as exc:
            print(f"spdng: {exc}", file=sys.stderr)
            return 2
        server, err = _pick_server(provider, args)
        if err:
            print(f"spdng: {provider.label}: {err}", file=sys.stderr)
            results.append(None)
            titles.append(provider.label)
            continue
        if args.verbose:
            print(f"Testing {provider.label} -> {server.host} "
                  f"({server.name})", file=sys.stderr)
        result, trace = _run_one(provider, server, args, do_trace=args.trace)
        results.append(result)
        titles.append(provider.label)
        if trace is not None:
            traces[provider.id] = trace

    good = [r for r in results if r is not None]
    if not good:
        print("spdng: every provider failed", file=sys.stderr)
        return 1

    if args.json:
        payload = {
            "results": [r.to_dict() for r in good],
            "providers": titles,
        }
        if traces:
            payload["traces"] = {k: v.to_dict() for k, v in traces.items()}
        print(json.dumps(payload, indent=2))
        return 0

    print()
    print(compare.render_comparison(good, titles))
    print()
    for pid, trace in traces.items():
        print(style.paint(f"Path to {pid}", "heading"))
        print(render.render_trace(trace))
        print()
    return 0


def cmd_repeat(args) -> int:
    """Run the same test several times to see the spread."""
    _apply_style(args)
    try:
        provider = providers.get_provider(args.provider)
    except providers.ProviderError as exc:
        print(f"spdng: {exc}", file=sys.stderr)
        return 2
    server, err = _pick_server(provider, args)
    if err:
        print(f"spdng: {err}", file=sys.stderr)
        return 2

    runs = []
    for i in range(args.count):
        if args.verbose:
            print(f"Run {i + 1} of {args.count}...", file=sys.stderr)
        result, _ = _run_one(provider, server, args, do_trace=False)
        runs.append(result)

    if args.json:
        print(json.dumps([r.to_dict() for r in runs], indent=2))
        return 0

    print()
    print(compare.render_repeat_comparison(runs))
    return 0


def cmd_servers(args) -> int:
    _apply_style(args)
    try:
        provider = providers.get_provider(args.provider)
    except providers.ProviderError as exc:
        print(f"spdng: {exc}", file=sys.stderr)
        return 2
    try:
        lat, lon, attrs = servers.get_client_location(ua=USER_AGENT)
    except Exception as exc:  # noqa: BLE001
        print(f"spdng: could not determine location: {exc}", file=sys.stderr)
        lat = lon = 0.0
        attrs = {}

    ranked = provider.list_servers(limit=args.limit)

    if args.json:
        print(json.dumps({
            "provider": provider.id,
            "provider_label": provider.label,
            "infrastructure": provider.infrastructure,
            "client": attrs,
            "servers": [s.to_dict() for s in ranked],
        }, indent=2))
        return 0

    print(f"Provider: {style.paint(provider.describe(), 'accent')}")
    print(f"Your location: {lat:.4f}, {lon:.4f} "
          f"({attrs.get('isp', 'unknown ISP')})")
    print()
    print(frames.server_table(ranked, limit=args.limit))
    print()
    return 0


def cmd_trace(args) -> int:
    _apply_style(args)
    host = args.target
    try:
        ip = socket.gethostbyname(host)
    except OSError as exc:
        print(f"spdng: cannot resolve {host}: {exc}", file=sys.stderr)
        return 2

    port = args.port
    if port is None:
        for proto in ("https", "http"):
            try:
                with socket.create_connection(
                        (ip, 443 if proto == "https" else 80), timeout=5):
                    port = 443 if proto == "https" else 80
                    break
            except OSError:
                continue
        port = port or 80

    # mtr shows each hop the moment it is measured, not after the whole
    # path is done. So the traceroute hands us each hop as it lands and
    # we print it right away. On a terminal the rows are redrawn in
    # place, so it looks like mtr's updating table; piped to a file,
    # each row is simply written as it arrives.
    if args.json:
        trace = traceroute.traceroute(
            ip, port=port, max_hops=args.max_hops, probes=args.probes,
            timeout=args.timeout, method=args.method)
        print(json.dumps(trace.to_dict(), indent=2))
        return 0

    return _stream_trace(ip, port, args)


def _stream_trace(ip: str, port: int, args) -> int:
    """Trace the path, showing each hop as soon as it is measured."""
    from .tui import supports_curses

    header = _trace_header()
    if supports_curses():
        return _stream_trace_curses(ip, port, args, header)
    return _stream_trace_plain(ip, port, args, header)


def _trace_header() -> list[tuple[str, int, str]]:
    """The column layout of the streaming trace."""
    return [
        ("Hop", 4, ">"),
        ("Loss%", 7, ">"),
        ("Snt", 4, ">"),
        ("Last", 9, ">"),
        ("Avg", 9, ">"),
        ("Best", 9, ">"),
        ("Worst", 9, ">"),
        ("Jitter", 8, ">"),
        ("Address", 30, "<"),
    ]


def _trace_row(hop, columns) -> str:
    """One hop as a row of cells."""
    from .table import pad
    loss = hop.loss_pct
    if loss >= 50:
        role = "bad"
    elif loss > 0:
        role = "warn"
    else:
        role = "good"

    def ms(value):
        return f"{value:.2f} ms" if value is not None else "---"

    values = {
        "Hop": f"{hop.ttl}",
        "Loss%": style.paint(f"{loss:.1f}%", role),
        "Snt": f"{hop.sent}",
        "Last": ms(hop.last_ms),
        "Avg": ms(hop.avg_ms),
        "Best": ms(hop.best_ms),
        "Worst": ms(hop.worst_ms),
        "Jitter": ms(hop.jitter_ms),
        "Address": hop.addr or "*",
    }
    # Built exactly like the header: a leading space, then the cells
    # joined by a space. If the two are built differently they are a
    # character apart, and every column after the first is off.
    return " " + " ".join(
        pad(values.get(title, ""), width, align)
        for title, width, align in columns)


def _stream_trace_plain(ip: str, port: int, args, columns) -> int:
    """Print each hop as it is measured. No curses needed."""
    from .table import pad, visible_width

    title = " " + " ".join(
        pad(t, w, a) for t, w, a in columns)
    print(title)
    print(" " + "-" * (visible_width(title) - 1))
    sys.stdout.flush()

    rows: list[str] = []

    def on_hop(hop):
        row = _trace_row(hop, columns)
        rows.append(row)
        print(row)
        sys.stdout.flush()

    trace = traceroute.traceroute(
        ip, port=port, max_hops=args.max_hops, probes=args.probes,
        timeout=args.timeout, method=args.method, on_hop=on_hop)

    print()
    print(render.render_loss_summary(trace))
    print()
    return 0


def _stream_trace_curses(ip: str, port: int, args, columns) -> int:
    """The mtr view: a table that fills in as the path is measured."""
    from .table import pad, visible_width
    from .tui import Screen

    title = " " + " ".join(pad(t, w, a) for t, w, a in columns)

    # `keep_output` is what makes the results survive: curses.endwin()
    # restores the terminal and wipes the screen, so without this the
    # trace would leave nothing behind.
    with Screen("spdng trace", keep_output=True) as screen:
        lines = [title, " " + "-" * (visible_width(title) - 1)]

        def on_hop(hop):
            lines.append(_trace_row(hop, columns))
            screen.print_frame(lines)

        trace = traceroute.traceroute(
            ip, port=port, max_hops=args.max_hops, probes=args.probes,
            timeout=args.timeout, method=args.method, on_hop=on_hop)

    print()
    print(render.render_loss_summary(trace))
    print()
    return 0


def cmd_doctor(args) -> int:
    """Report what this environment can and cannot do."""
    _apply_style(args)
    print("spdng doctor")
    print("=" * 60)
    raw = traceroute.has_raw_icmp()
    unpriv = traceroute.has_unprivileged_icmp()
    rng = traceroute.unprivileged_icmp_range()
    print(f"  raw ICMP sockets      : {'yes' if raw else 'no'}")
    print(f"  unprivileged ICMP     : {'yes' if unpriv else 'no'}")
    print(f"  ping_group_range      : {rng if rng else 'unknown'}")
    if rng and rng[0] > rng[1]:
        print("    -> range is empty, unprivileged ICMP is disabled")
        print("    -> fix with: sudo sysctl -w "
              "net.ipv4.ping_group_range='0 2147483647'")
    has_ping = traceroute.has_ping_binary()
    print(f"  ping binary           : {'yes' if has_ping else 'no'}")
    if has_ping:
        print(f"    -> startup overhead: "
              f"{traceroute._ping_startup_overhead():.1f} ms (calibrated)")
    if raw:
        method = "icmp (raw socket)"
    elif has_ping:
        method = "ping (via CAP_NET_RAW ping binary)"
    else:
        method = "udp (IP_RECVERR)"
    print(f"  traceroute method     : {method}")
    print(f"  colour                : "
          f"{'on' if style.color_enabled() else 'off'}"
          f"  (theme: {style.theme().name})")
    print()
    try:
        lat, lon, attrs = servers.get_client_location(ua=USER_AGENT)
        print(f"  location lookup       : ok ({lat:.3f}, {lon:.3f})")
    except Exception as exc:  # noqa: BLE001
        print(f"  location lookup       : FAILED ({exc})")

    for p in providers.all_providers():
        try:
            srv = p.list_servers(limit=3)
            nearest = srv[0] if srv else None
            detail = (f"{nearest.host} ({nearest.distance_km:.0f} km)"
                      if nearest else "none")
            print(f"  provider {p.id:<12}: ok ({len(srv)} servers) {detail}")
        except Exception as exc:  # noqa: BLE001
            print(f"  provider {p.id:<12}: FAILED ({exc})")
    print()
    return 0


def cmd_watch(args) -> int:
    """Re-run the test on a timer and watch it move."""
    _apply_style(args)
    try:
        provider = providers.get_provider(args.provider)
    except providers.ProviderError as exc:
        print(f"spdng: {exc}", file=sys.stderr)
        return 2
    server, err = _pick_server(provider, args)
    if err:
        print(f"spdng: {err}", file=sys.stderr)
        return 2

    def run_once():
        result, _ = _run_one(provider, server, args, do_trace=False)
        return result

    if args.plain:
        results = watch.watch(run_once, interval=args.interval,
                               duration=args.watch_duration)
        if not results:
            return 1
        if args.json:
            print(json.dumps([r.to_dict() for r in results], indent=2))
            return 0
        print()
        print(watch.render_watch_summary(results))
        return 0

    return _watch_curses(run_once, provider, server, args)


def _watch_curses(run_once, provider, server, args) -> int:
    """The curses watch view, with a live bar graph and hotkeys.

    Two things run at once here. The measurement runs on a worker
    thread, because a speed test takes seconds and the user must not
    have to wait for one to finish before the keyboard is read. The
    keyboard is read on a short timeout in a loop, so pressing "q"
    quits immediately instead of after the current sample completes.
    """
    import queue
    import threading

    from .tui import Screen, key_name
    from .watchview import WatchSample, WatchView, save_csv, save_json, save_report

    view = WatchView(target=f"{provider.label} {server.name}")
    interval = args.interval
    paused = False
    samples: list[WatchSample] = []
    started = time.time()

    # Results come back on a queue, so the worker never touches the
    # screen and the screen never waits on the worker.
    done: queue.Queue = queue.Queue()
    stop = threading.Event()
    busy = False

    def worker():
        """Run ONE sample and hand the result back.

        The main loop starts the next one when it is time, so the
        interval between samples is honoured and the server is not
        hammered.

        The measurement runs with the terminal's own stdout and stderr
        detached. A thread that inherits them keeps the pty slave open
        for as long as it lives, and that stops the terminal from
        closing cleanly when curses ends.
        """
        try:
            result = run_once()
        except Exception as exc:  # noqa: BLE001
            done.put(("error", exc))
            return
        done.put(("sample", result))

    with Screen("spdng watch", keep_output=True) as screen:
        next_run = time.time()
        while True:
            # A resize changes the viewport, so the layout is rebuilt
            # from the new size on the next frame.
            screen.resized()

            # Collect whatever the worker has finished.
            while True:
                try:
                    kind, payload = done.get_nowait()
                except queue.Empty:
                    break
                if kind == "error":
                    view.set_status("error", str(payload))
                    busy = False
                    break
                result = payload
                busy = False
                sample = WatchSample(
                    timestamp=time.time(),
                    download_mbps=result.download_mbps,
                    upload_mbps=result.upload_mbps,
                    ping_ms=result.latency.avg_ms,
                    provider=provider.id,
                    server=f"{server.sponsor} {server.name}",
                )
                samples.append(sample)
                view.add(sample)
                view.set_status("running")

            if not paused:
                view.set_status(view.status or "running")
            view.render(screen)

            # Read the keyboard with a SHORT timeout, so the loop comes
            # back often and a keypress is acted on at once. A long
            # timeout here is what made "q" feel broken.
            code = screen.getch(50)
            key = key_name(code)
            if key in ("Q", "ESC"):
                break
            elif key == "S":
                path = live._report_path(args.save_dir, "watch", "csv")
                save_csv(samples, path)
                view.set_status(view.status, f"saved {path}")
            elif key == "J":
                path = live._report_path(args.save_dir, "watch", "json")
                save_json(samples, path)
                view.set_status(view.status, f"saved {path}")
            elif key == "R":
                path = live._report_path(args.save_dir, "watch", "txt")
                save_report(samples, path, view.target)
                view.set_status(view.status, f"saved {path}")
            elif key == "SPACE":
                paused = not paused
                view.set_status("paused" if paused else "running")
            elif key == "+":
                interval = max(2.0, interval - 1.0)
                view.set_status(view.status, f"interval {interval:.0f}s")
            elif key == "-":
                interval = min(600.0, interval + 1.0)
                view.set_status(view.status, f"interval {interval:.0f}s")
            elif key == "U":
                view.show_upload = not view.show_upload
                view.set_status(view.status,
                                f"upload chart "
                                f"{'shown' if view.show_upload else 'hidden'}")

            if args.watch_duration and (time.time() - started) >= args.watch_duration:
                break
            if view.status == "error":
                break

            # Start the next sample when it is due, and only if one is
            # not already running. The sample runs on its own thread,
            # so the keyboard stays live while it works.
            if not paused and not busy and time.time() >= next_run:
                busy = True
                next_run = time.time() + interval
                threading.Thread(target=worker, daemon=True).start()

            time.sleep(0.02)

    stop.set()
    print()
    if samples:
        print(render.render_watch_summary(
            [r for r in [_result_from_sample(s) for s in samples]]))
    return 0


def _result_from_sample(sample):
    """Rebuild a minimal result from a watch sample, for the summary."""
    r = measure.SpeedTestResult(server=None)
    r.download_mbps = sample.download_mbps
    r.upload_mbps = sample.upload_mbps
    r.latency = measure.LatencyResult(
        samples_ms=[sample.ping_ms] if sample.ping_ms else [], errors=0,
        attempts=1)
    return r


def cmd_history(args) -> int:
    """Show past measurements and the trend."""
    _apply_style(args)
    entries = history.load(limit=args.limit)

    if args.clear:
        history.clear()
        print("history cleared")
        return 0

    if not entries:
        print("no history yet; run a speedtest first")
        return 0

    if args.json:
        print(json.dumps({
            "entries": [e.to_dict() for e in entries],
            "trend": _trend_json(entries),
        }, indent=2))
        return 0

    print()
    print(style.paint(f"{len(entries)} past measurements", "heading"))
    print()
    for e in entries[-args.show:]:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(e.timestamp))
        ping = f"{e.ping_ms:6.1f} ms" if e.ping_ms is not None else "     n/a"
        print(f"  {when}  {e.provider:<12} "
              f"{style.paint(f'{e.download_mbps:>8.2f}', 'good')} Mbit/s down  "
              f"{style.paint(f'{e.upload_mbps:>6.2f}', 'ok')} Mbit/s up  "
              f"ping {ping}")

    t = history.trend(entries)
    print()
    downloads = [e.download_mbps for e in entries]
    print(f"  {style.paint('range', 'dim')}: "
          f"{t['download_min']:.1f} .. {t['download_max']:.1f} Mbit/s "
          f"(avg {t['download_avg']:.1f})")
    print(f"  {fun.sparkline_history(downloads)}")
    if "change_pct" in t:
        change = t["change_pct"]
        role = "good" if change >= 0 else "bad"
        arrow = "▲" if change >= 0 else "▼"
        print(f"  {style.paint('trend', 'dim')}: {arrow} {abs(change):.1f}% "
              f"{style.paint('vs the first half of the history', role)}")
    print()
    print(style.paint(f"  history file: {history.history_path()}", "muted"))
    print()
    return 0


def _trend_json(entries) -> dict:
    t = history.trend(entries)
    return {k: (v.to_dict() if hasattr(v, "to_dict") else v)
            for k, v in t.items()}


def cmd_live(args) -> int:
    """The live mtr-style trace."""
    _apply_style(args)
    return live.run_live_trace(
        args.target,
        port=args.port or 33434,
        max_hops=args.max_hops,
        interval=args.interval,
        rounds=args.rounds,
        timeout=args.timeout,
        method=args.method,
        save_dir=args.save_dir,
    )


def cmd_moo(args) -> int:
    """The obligatory easter egg."""
    _apply_style(args)
    text = " ".join(args.text) if args.text else (
        f"{fun.time_of_day_greeting()}, "
        f"{'it is friday' if fun.it_is_friday() else 'have a nice day'}")
    print(fun.cowsay(text))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="spdng",
        description="A maintained speedtest CLI with mtr-style path analysis.",
    )
    p.add_argument("--version", action="version",
                   version=f"spdng {__version__}")
    p.add_argument("--no-color", action="store_true",
                   help="disable colour output (also honours NO_COLOR)")
    p.add_argument("--color", action="store_true",
                   help="force colour output even when piped")
    p.add_argument("--theme", choices=sorted(style.THEMES), default="default",
                   help="colour theme")
    p.add_argument("--no-fun", action="store_true",
                   help="no grades, no speedometer, no wisecracks")
    p.add_argument("--no-history", action="store_true",
                   help="do not record this result to the local history")
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp):
        sp.add_argument("--json", action="store_true",
                        help="machine-readable JSON output")
        sp.add_argument("--limit", type=int, default=20,
                        help="how many candidate servers to fetch")
        sp.add_argument("--max-distance", type=float, default=0.0,
                        help="only consider servers within N km (0 = no limit)")
        sp.add_argument("--server", help="pick a server by host, name, sponsor, "
                                         "or a full URL for your own instance")
        sp.add_argument("--provider", default="ookla",
                        help="which network to test against "
                             "(ookla, cloudflare, librespeed)")
        sp.add_argument("--duration", type=float, default=10.0,
                        help="seconds per direction")
        sp.add_argument("--threads", type=int, default=4,
                        help="parallel connections")
        sp.add_argument("--latency-samples", type=int, default=5)
        sp.add_argument("--no-upload", action="store_true")
        sp.add_argument("--no-download", action="store_true")
        sp.add_argument("--no-latency", action="store_true",
                        help="skip the standalone latency pre-check")
        sp.add_argument("--csv", action="store_true",
                        help="write the result as CSV to stdout")
        sp.add_argument("--save", metavar="PATH",
                        help="save the result to a file (.txt, .csv, .json)")
        sp.add_argument("--save-dir", default=None,
                        help="default directory for saved reports")
        sp.add_argument("--trace", action="store_true",
                        help="also trace the path to the test server (mtr-style)")
        sp.add_argument("--trace-probes", type=int, default=3)
        sp.add_argument("--trace-timeout", type=float, default=2.0)
        sp.add_argument("--max-hops", type=int, default=20)
        sp.add_argument("-v", "--verbose", action="store_true")

    sp = sub.add_parser("speedtest", help="run a bandwidth test")
    add_common(sp)
    sp.set_defaults(func=cmd_speedtest)

    sp = sub.add_parser("compare",
                         help="test against several providers and line them up")
    add_common(sp)
    sp.add_argument("--providers", nargs="+",
                    default=["ookla", "cloudflare", "librespeed"],
                    help="which providers to compare")
    sp.set_defaults(func=cmd_compare)

    sp = sub.add_parser("repeat",
                         help="run the same test several times to see the spread")
    add_common(sp)
    sp.add_argument("--count", type=int, default=3,
                    help="how many times to run the test")
    sp.set_defaults(func=cmd_repeat)

    sp = sub.add_parser("servers", help="list candidate servers, nearest first")
    add_common(sp)
    sp.set_defaults(func=cmd_servers)

    sp = sub.add_parser("live", help="live mtr-style traceroute, updating in place")
    sp.add_argument("target", nargs="?", default="1.1.1.1")
    sp.add_argument("--port", type=int, default=None)
    sp.add_argument("--max-hops", type=int, default=20)
    sp.add_argument("--interval", type=float, default=1.0,
                    help="seconds between rounds")
    sp.add_argument("--rounds", type=int, default=0,
                    help="stop after N rounds (0 = until you quit)")
    sp.add_argument("--timeout", type=float, default=2.0)
    sp.add_argument("--method", choices=["auto", "icmp", "udp"], default="auto")
    sp.add_argument("--save-dir", default=None,
                    help="where to save reports (default: the data dir)")
    sp.set_defaults(func=cmd_live)

    sp = sub.add_parser("trace", help="mtr-style traceroute to a host")
    sp.add_argument("target")
    sp.add_argument("--port", type=int, default=None)
    sp.add_argument("--max-hops", type=int, default=20)
    sp.add_argument("--probes", type=int, default=3)
    sp.add_argument("--timeout", type=float, default=2.0)
    sp.add_argument("--method", choices=["auto", "icmp", "udp"], default="auto")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_trace)

    sp = sub.add_parser("doctor", help="check what this environment supports")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("watch", help="re-test on a timer and show a trend")
    add_common(sp)
    sp.add_argument("--interval", type=float, default=30.0,
                    help="seconds between tests")
    sp.add_argument("--watch-duration", type=float, default=0.0,
                    help="stop after this many seconds (0 = until ctrl-c)")
    sp.add_argument("--plain", action="store_true",
                    help="plain text output instead of the curses view")
    sp.set_defaults(func=cmd_watch)

    sp = sub.add_parser("history", help="show past measurements and the trend")
    sp.add_argument("--limit", type=int, default=200,
                    help="how many history entries to load")
    sp.add_argument("--show", type=int, default=10,
                    help="how many to print")
    sp.add_argument("--clear", action="store_true",
                    help="delete the history file and exit")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_history)

    sp = sub.add_parser("moo", help="moo")
    sp.add_argument("text", nargs="*", help="what the cow should say")
    sp.set_defaults(func=cmd_moo)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except providers.ProviderError as exc:
        print(f"spdng: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
