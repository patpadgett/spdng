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

from . import measure, render, servers, traceroute

__version__ = "0.1.0"

USER_AGENT = measure.DEFAULT_UA


def _resolve_server(args) -> tuple[servers.Server | None, str | None]:
    """Pick the server to test against."""
    all_servers = servers.fetch_servers(limit=args.limit * 3, ua=USER_AGENT)
    if not all_servers:
        return None, "server list was empty"

    if args.server:
        needle = args.server.lower()
        for s in all_servers:
            if needle in s.host.lower() or needle in s.name.lower() \
                    or needle in s.sponsor.lower():
                return s, None
        return None, f"no server matched {args.server!r}"

    ranked = servers.rank_servers(all_servers, max_distance_km=args.max_distance)
    if not ranked:
        return None, (f"no servers within {args.max_distance} km; "
                      f"raise --max-distance")
    return ranked[0], None


def cmd_speedtest(args) -> int:
    server, err = _resolve_server(args)
    if err:
        print(f"spdng: {err}", file=sys.stderr)
        return 2

    if args.verbose:
        print(f"Selected {server.host} ({server.name}) "
              f"{server.distance_km:.0f} km away", file=sys.stderr)

    # Latency first, so we can report a real number before the long test.
    if not args.no_latency:
        lat = measure.measure_latency(server.base, count=args.latency_samples,
                                      ua=USER_AGENT)
        server.latency_ms = lat.avg_ms
        server.jitter_ms = lat.jitter_ms
        if args.verbose:
            print(f"Latency: {lat.avg_ms} ms "
                  f"(min {lat.min_ms}, max {lat.max_ms}, "
                  f"loss {lat.loss_pct:.0f}%)", file=sys.stderr)

    result = measure.run_speedtest(
        server,
        duration=args.duration,
        threads=args.threads,
        latency_samples=args.latency_samples,
        do_upload=not args.no_upload,
        do_download=not args.no_download,
        ua=USER_AGENT,
    )

    if args.trace:
        # Trace to the resolved IP, not the hostname: ping would apply
        # reverse DNS to the responder and we would lose the comparison
        # against the target address.
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

    if args.json:
        payload = result.to_dict()
        if args.trace:
            payload["trace"] = trace.to_dict()
        print(json.dumps(payload, indent=2))
        return 0

    print()
    print(render.render_result(result))
    if args.trace:
        print()
        print(render.render_trace(trace))
        print()
        print(render.render_loss_summary(trace))
    print()
    return 0


def _trace_notes(trace) -> list[str]:
    notes = []
    if trace.error:
        notes.append(f"traceroute: {trace.error}")
    if not trace.hops:
        notes.append("traceroute: no hops probed")
    return notes


def cmd_servers(args) -> int:
    try:
        lat, lon, attrs = servers.get_client_location(ua=USER_AGENT)
    except Exception as exc:  # noqa: BLE001
        print(f"spdng: could not determine location: {exc}", file=sys.stderr)
        lat = lon = 0.0
        attrs = {}

    all_servers = servers.fetch_servers(limit=args.limit, ua=USER_AGENT)
    ranked = servers.rank_servers(all_servers, max_distance_km=args.max_distance)

    if args.json:
        print(json.dumps({
            "client": attrs,
            "servers": [s.to_dict() for s in ranked],
        }, indent=2))
        return 0

    print(f"Your location: {lat:.4f}, {lon:.4f} "
          f"({attrs.get('isp', 'unknown ISP')})")
    print()
    print(render.render_server_table(ranked, limit=args.limit))
    print()
    return 0


def cmd_trace(args) -> int:
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
                with socket.create_connection((ip, 443 if proto == "https" else 80),
                                              timeout=5):
                    port = 443 if proto == "https" else 80
                    break
            except OSError:
                continue
        port = port or 80

    trace = traceroute.traceroute(
        ip, port=port, max_hops=args.max_hops, probes=args.probes,
        timeout=args.timeout, method=args.method)

    if args.json:
        print(json.dumps(trace.to_dict(), indent=2))
        return 0

    print()
    print(render.render_trace(trace))
    print()
    print(render.render_loss_summary(trace))
    print()
    return 0


def cmd_doctor(args) -> int:
    """Report what this environment can and cannot do."""
    print("spdng doctor")
    print("=" * 60)
    raw = traceroute.has_raw_icmp()
    unpriv = traceroute.has_unprivileged_icmp()
    print(f"  raw ICMP sockets      : {'yes' if raw else 'no'}")
    print(f"  unprivileged ICMP     : {'yes' if unpriv else 'no'}")
    rng = traceroute.unprivileged_icmp_range()
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
    print()
    try:
        lat, lon, attrs = servers.get_client_location(ua=USER_AGENT)
        print(f"  location lookup       : ok ({lat:.3f}, {lon:.3f})")
    except Exception as exc:  # noqa: BLE001
        print(f"  location lookup       : FAILED ({exc})")
    try:
        srv = servers.fetch_servers(limit=5, ua=USER_AGENT)
        print(f"  server list           : ok ({len(srv)} servers)")
        if srv:
            best = servers.rank_servers(srv)[0]
            print(f"  nearest server        : {best.host} "
                  f"({best.distance_km:.0f} km)")
    except Exception as exc:  # noqa: BLE001
        print(f"  server list           : FAILED ({exc})")
    print()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="spdng",
        description="A maintained speedtest CLI with mtr-style path analysis.",
    )
    p.add_argument("--version", action="version",
                   version=f"spdng {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp):
        sp.add_argument("--json", action="store_true",
                        help="machine-readable JSON output")
        sp.add_argument("--limit", type=int, default=20,
                        help="how many candidate servers to fetch")
        sp.add_argument("--max-distance", type=float, default=0.0,
                        help="only consider servers within N km (0 = no limit)")

    sp = sub.add_parser("speedtest", help="run a bandwidth test")
    add_common(sp)
    sp.add_argument("--server", help="pick a server by host, name, or sponsor")
    sp.add_argument("--duration", type=float, default=10.0,
                    help="seconds per direction")
    sp.add_argument("--threads", type=int, default=4,
                    help="parallel connections")
    sp.add_argument("--latency-samples", type=int, default=5)
    sp.add_argument("--no-latency", action="store_true",
                    help="skip the standalone latency pre-check")
    sp.add_argument("--no-upload", action="store_true")
    sp.add_argument("--no-download", action="store_true")
    sp.add_argument("--trace", action="store_true",
                    help="also trace the path to the test server (mtr-style)")
    sp.add_argument("--trace-probes", type=int, default=3)
    sp.add_argument("--trace-timeout", type=float, default=2.0)
    sp.add_argument("--max-hops", type=int, default=20)
    sp.add_argument("-v", "--verbose", action="store_true")
    sp.set_defaults(func=cmd_speedtest)

    sp = sub.add_parser("servers", help="list candidate servers, nearest first")
    add_common(sp)
    sp.set_defaults(func=cmd_servers)

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

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
