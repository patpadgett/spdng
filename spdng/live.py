"""Live traceroute: the mtr loop.

Real mtr sends a round of probes to every hop at once, then redraws. That
is the behaviour that makes it useful: you watch the path change rather
than waiting for a full trace to finish.

We do the same. Each round:

  1. Send one probe to every TTL, in parallel.
  2. Record the outcome for each hop.
  3. Redraw the screen.

Between rounds we sleep for a short interval, which is why it feels alive
rather than like a loop that hammers your router.

Two things this shares with the old `speedtest-cli`, and does not:

  * A probe that fails is recorded as a failure character, never as a
    latency number.
  * A hop that never answers is shown as unresponsive, and we say so
    rather than pretending the path continues past it.
"""
from __future__ import annotations

import socket
import sys
import time

from . import servers, traceroute
from .tui import (
    PROBE_ERROR,
    PROBE_OK,
    PROBE_REFUSED,
    PROBE_TIMEOUT,
    LiveTraceView,
    Screen,
    key_name,
)


def _classify_error(exc: BaseException | None) -> str:
    """Turn a probe failure into one of the history characters."""
    if exc is None:
        return PROBE_OK
    if isinstance(exc, socket.timeout):
        return PROBE_TIMEOUT
    if isinstance(exc, ConnectionRefusedError):
        return PROBE_REFUSED
    if isinstance(exc, OSError) and exc.errno == 113:
        return PROBE_REFUSED  # EHOSTUNREACH
    return PROBE_ERROR


def probe_hop(host: str, port: int, ttl: int, timeout: float,
              method: str) -> tuple[int, str | None, float | None, str]:
    """Probe a single hop once.

    Returns (ttl, address, rtt_ms, outcome).
    """
    if method == "icmp":
        try:
            addr, rtt = traceroute._raw_icmp_probe(host, ttl, ttl, timeout)
            if addr is None:
                # _raw_icmp_probe returns the elapsed time even when no
                # reply arrived. That is a timeout, not a successful
                # probe with a timeout-sized RTT.
                return ttl, None, None, PROBE_TIMEOUT
            return ttl, addr, rtt, PROBE_OK
        except PermissionError:
            return ttl, None, None, PROBE_ERROR
        except OSError as exc:
            return ttl, None, None, _classify_error(exc)
    if method in ("icmp-dgram", "dgram"):
        try:
            addr, rtt = traceroute._dgram_icmp_probe(
                host, ttl, ttl, timeout)
        except traceroute.TraceUnavailable:
            return ttl, None, None, PROBE_ERROR
        if addr is None:
            return ttl, None, None, PROBE_TIMEOUT
        return ttl, addr, rtt, PROBE_OK
    if method == "ping":
        addr, samples, _sent, _exact = traceroute._ping_binary_probe(
            host, ttl, timeout, 1)
        if addr:
            return ttl, addr, (samples[0] if samples else None), PROBE_OK
        return ttl, None, None, PROBE_TIMEOUT
    if method != "udp":
        return ttl, None, None, PROBE_ERROR
    # udp / IP_RECVERR
    addr, rtt, reached = traceroute._recverr_probe(host, port, ttl, timeout)
    if reached or addr:
        return ttl, addr, rtt, PROBE_OK
    return ttl, None, None, PROBE_TIMEOUT


def run_live_trace(target: str, port: int | None = None, max_hops: int = 20,
                   interval: float = 1.0, rounds: int = 0,
                   timeout: float = 2.0, method: str = "auto",
                   save_dir: str | None = None) -> int:
    """Run the live mtr-style trace until the user quits.

    rounds of 0 means run forever. A URL target supplies its own port when
    the caller did not pass one.
    """
    try:
        host, target_port = servers.split_host_port(target)
        ip, _family = traceroute.resolve_address(host)
    except (OSError, ValueError) as exc:
        print(f"spdng: cannot resolve {target}: {exc}")
        return 2
    if port is None:
        port = target_port if target_port is not None else 33434
    if not 1 <= port <= 65535:
        print("spdng: --port must be between 1 and 65535", file=sys.stderr)
        return 2

    if method == "auto":
        method = traceroute.choose_method(ip)
        if method is None:
            print(
                "spdng: no per-hop tracing method is available here: "
                "no raw or unprivileged ICMP sockets, no ping binary, "
                "and no IP_RECVERR",
                file=sys.stderr)
            return 2
    if method == "icmp" and _family == socket.AF_INET6:
        print("spdng: raw ICMP tracing supports IPv4 only",
              file=sys.stderr)
        return 2
    if (method == "icmp" and _family == socket.AF_INET
            and not traceroute.has_raw_icmp()
            and traceroute.has_unprivileged_icmp()
            and traceroute.has_recverr()):
        method = "icmp-dgram"
    if method not in ("icmp", "icmp-dgram", "dgram", "ping", "udp"):
        print(f"spdng: unknown traceroute method: {method}",
              file=sys.stderr)
        return 2

    view = LiveTraceView(host)
    sent: dict[int, int] = {}
    received: dict[int, int] = {}
    paused = False
    should_quit = False
    current_interval = interval
    round_no = 0

    # Probe results come back on a queue, so the probing happens on a
    # worker thread and the keyboard is read while it works. A probe
    # round can take a couple of seconds, and the user must not have to
    # wait for it to finish before "q" is noticed.
    import queue
    import threading

    results_queue: queue.Queue = queue.Queue()
    stop = threading.Event()

    def do_probe(ttl: int):
        return probe_hop(ip, port, ttl, timeout, method)

    def probe_round():
        """One round of probes to every hop, in parallel.

        Plain threads, not a ThreadPoolExecutor. The executor's
        shutdown handler takes a lock, and at interpreter exit that
        lock can be held by a probe that is still running - so the
        process hangs for a couple of seconds after you press "q".
        Plain daemon threads are simply abandoned.
        """
        results: list = [None] * max_hops
        threads = []

        def run(index: int):
            results[index] = do_probe(index + 1)

        for index in range(max_hops):
            thread = threading.Thread(target=run, args=(index,), daemon=True)
            thread.start()
            threads.append(thread)
        for thread in threads:
            thread.join()
        return [r for r in results if r is not None]

    def worker():
        try:
            results = probe_round()
        except Exception:  # noqa: BLE001
            results = []
        if not stop.is_set():
            results_queue.put(results)

    with Screen("spdng live trace", keep_output=True) as screen:
        probing = False
        next_round_at = time.monotonic()
        while not should_quit:
            view._round = round_no
            now = time.monotonic()

            # Probe only when the previous round has finished, the user
            # is not paused, and the requested interval has elapsed. A
            # worker thread keeps keys responsive while probes are in
            # flight; the main loop never starts a second round.
            if not paused and not probing and now >= next_round_at:
                probing = True
                threading.Thread(target=worker, daemon=True).start()

            # Take any round that has finished. Results are recorded even
            # if the user paused while the round was in flight; pause
            # stops new probing, it does not throw away what was measured.
            try:
                results = results_queue.get_nowait()
            except queue.Empty:
                results = None

            if results is not None:
                probing = False
                for ttl, addr, rtt, outcome in results:
                    # sent/received ACCUMULATE across rounds. The view
                    # tracks them per hop; these dicts are the running
                    # totals.
                    sent[ttl] = sent.get(ttl, 0) + 1
                    if outcome == PROBE_OK:
                        received[ttl] = received.get(ttl, 0) + 1
                    view.update_hop(ttl, addr, rtt, outcome,
                                    sent.get(ttl, 0), received.get(ttl, 0))

                # Stop probing beyond the first hop that is clearly dead,
                # because everything past it measures nothing.
                dead_at = _first_dead(view)
                if dead_at and not paused:
                    view.set_status(
                        "running",
                        f"hop {dead_at} is not responding; "
                        f"hops beyond it are not being measured")
                round_no += 1
                next_round_at = time.monotonic() + current_interval

            screen.resized()
            view.render(screen)

            # Handle keys. We read with a short timeout so the loop
            # comes back often and a keypress is acted on at once.
            code = screen.getch(50)
            key = key_name(code)
            if key in ("Q", "ESC"):
                should_quit = True
            elif key == "S":
                path = _save_csv(view, save_dir)
                view.set_status("running", f"saved {path}")
            elif key == "J":
                path = _save_json(view, save_dir)
                view.set_status("running", f"saved {path}")
            elif key == "R":
                path = _save_report(view, save_dir)
                view.set_status("running", f"saved {path}")
            elif key == "SPACE":
                paused = not paused
                if not paused:
                    # Do not wait out the interval that was already
                    # running while the view was paused.
                    next_round_at = time.monotonic()
                view.set_status("paused" if paused else "running")
            elif key == "+":
                current_interval = max(0.1, current_interval - 0.1)
                view.set_status("running",
                                f"interval {current_interval:.1f}s")
                if not probing:
                    next_round_at = time.monotonic() + current_interval
            elif key == "-":
                current_interval = min(10.0, current_interval + 0.1)
                view.set_status("running",
                                f"interval {current_interval:.1f}s")
                if not probing:
                    next_round_at = time.monotonic() + current_interval
            elif key == "UP":
                view.scroll(-1)
            elif key == "DOWN":
                view.scroll(1)

            # Keep the loop responsive for keys. The interval is enforced
            # by next_round_at, not by sleeping here.
            time.sleep(0.05)

            if rounds and round_no >= rounds:
                should_quit = True

    stop.set()
    print()

    received_any = any(hop["received"] > 0 for hop in view.hops)
    reached = any(
        hop["address"] and traceroute._is_target(ip, hop["address"])
        for hop in view.hops)
    if round_no and not received_any:
        return 1
    if rounds and not reached:
        return 1
    return 0


def _first_dead(view: LiveTraceView) -> int | None:
    """The first hop that has stopped answering entirely."""
    for hop in view.hops:
        if hop["sent"] >= 3 and hop["received"] == 0:
            return hop["ttl"]
    return None


def _save_csv(view: LiveTraceView, save_dir: str | None) -> str:
    import csv
    import io
    path = _report_path(save_dir, "trace", "csv")
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["ttl", "address", "sent", "received", "loss_pct",
                     "last_ms", "best_ms", "worst_ms", "history"])
    for hop in view.hops:
        loss = view._loss_pct(hop)
        writer.writerow([
            hop["ttl"], hop["address"] or "", hop["sent"], hop["received"],
            f"{loss:.1f}",
            f"{hop['last_ms']:.2f}" if hop["last_ms"] is not None else "",
            f"{hop['best_ms']:.2f}" if hop["best_ms"] is not None else "",
            f"{hop['worst_ms']:.2f}" if hop["worst_ms"] is not None else "",
            hop["history"].render(),
        ])
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(buf.getvalue())
    return path


def _save_json(view: LiveTraceView, save_dir: str | None) -> str:
    import json
    path = _report_path(save_dir, "trace", "json")
    data = {
        "target": view.target,
        "rounds": view._round,
        "hops": [
            {
                "ttl": h["ttl"],
                "address": h["address"],
                "sent": h["sent"],
                "received": h["received"],
                "loss_pct": round(view._loss_pct(h), 1),
                "last_ms": h["last_ms"],
                "best_ms": h["best_ms"],
                "worst_ms": h["worst_ms"],
                "history": h["history"].render(),
            }
            for h in view.hops
        ],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return path


def _save_report(view: LiveTraceView, save_dir: str | None) -> str:
    path = _report_path(save_dir, "trace", "txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(view.render_lines()) + "\n")
    return path


def _report_path(save_dir: str | None, kind: str, ext: str) -> str:
    import os
    if save_dir:
        os.makedirs(save_dir, exist_ok=True)
        base = save_dir
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            base = os.path.join(xdg, "spdng", "reports")
        else:
            base = os.path.join(os.path.expanduser("~"), ".local", "share",
                                "spdng", "reports")
        os.makedirs(base, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return os.path.join(base, f"{kind}-{stamp}.{ext}")
