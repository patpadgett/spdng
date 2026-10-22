"""Per-hop path analysis: the mtr-style part.

This is the differentiator. speedtest-cli tells you a number; mtr tells you
*where* the number comes from. We do both:

  * a traceroute-style hop-by-hop path to the test server, and
  * per-hop loss and latency so you can see exactly which router starts
    dropping packets or adding latency.

ICMP strategy, in order of preference:

  1. Raw ICMP sockets (ICMP_ECHO / ICMP_ECHOREPLY). Needs CAP_NET_RAW.
     Full fidelity: we see the TTL-exceeded reply and its source address.
  2. The `ping` binary. On most Linux distributions /usr/bin/ping ships
     with CAP_NET_RAW, so it can open a raw socket on our behalf even when
     our own process cannot. This is what makes per-hop tracing work in
     containers, WSL, and CI, where raw sockets are denied to the user.
  3. UDP sockets with IP_RECVERR. Needs the socket's own error queue, which
     Linux delivers for unprivileged UDP sends. This is how modern
     unprivileged traceroute implementations work. Requires the TTL to
     expire in the path so the kernel queues a "Time Exceeded" error back
     to us.

When nothing is available we say so plainly rather than faking a path.
"""
from __future__ import annotations

import errno
import os
import select
import shutil
import socket
import struct
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field

# Cached measurement of `ping`'s process startup overhead in ms.
_PING_OVERHEAD_MS: float | None = None

# The IP socket option numbers are PLATFORM-SPECIFIC. IP_TTL is 2 on
# Linux and 4 on BSD and macOS, so a hardcoded number sets the wrong
# option off Linux - IP_RECVTTL on macOS, which does not exist, so the
# call fails with ENOPROTOOPT.
#
# The socket module knows the right numbers for the platform it runs
# on, so use them and only fall back where it does not expose one.
IP_RECVERR = getattr(socket, "IP_RECVERR", 11)
IP_TTL = getattr(socket, "IP_TTL", 2)
IP_MTU_DISCOVER = getattr(socket, "IP_MTU_DISCOVER", 10)
SOL_IPV6 = getattr(socket, "IPV6_SOL_SOCKET", 41)
IPV6_RECVERR = getattr(socket, "IPV6_RECVERR", 26)
MSG_ERRQUEUE = 0x2000
IPPROTO_ICMP = 1
ICMP_ECHO = 8
ICMP_ECHOREPLY = 0

#: Does this platform have IP_RECVERR? It is Linux-only: on macOS the
#: socket option does not exist, and setting it raises ENOPROTOOPT
#: ("Protocol not available"). The UDP traceroute backend depends on
#: the socket's error queue, so it cannot work there.
_HAS_RECVERR = hasattr(socket, "IP_RECVERR")


def _has_pmtu_discover() -> bool:
    """Does this platform have the PMTU discovery socket option?

    It is Linux-only. Python does not expose it, so the only way to
    find out is to try it: on a platform where the number means
    something else, the call either fails or would set the wrong
    option. We check by trying to set it on a throwaway socket.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return False
    try:
        sock.setsockopt(socket.IPPROTO_IP, IP_MTU_DISCOVER, 2)
    except OSError:
        return False
    finally:
        sock.close()
    return True


_HAS_PMTU_DISCOVER = _has_pmtu_discover()


def has_recverr() -> bool:
    """Can this platform deliver ICMP errors to an unprivileged UDP socket?

    This checks that the option is ACCEPTED, which is not the same as
    it working. On macOS the `IP_RECVERR` constant exists in the
    socket module, so `hasattr` says yes, but the option does not do
    anything: there is no error queue, so the probes would never see
    a router reply and every hop would come back empty. That is
    silently wrong, which is worse than not having the backend.

    So the check is: can we set the option, and does the platform
    actually have the error-queue machinery? The MSG_ERRQUEUE flag is
    the giveaway - it is Linux-only.
    """
    if not _HAS_RECVERR:
        return False
    if not hasattr(socket, "MSG_ERRQUEUE"):
        return False
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return False
    try:
        sock.setsockopt(socket.IPPROTO_IP, IP_RECVERR, 1)
    except OSError:
        return False
    finally:
        sock.close()
    return True


class TraceUnavailable(RuntimeError):
    """Raised when no usable probing mechanism exists in this environment."""


@dataclass(slots=True)
class Hop:
    """One router (or the destination) along the path."""

    ttl: int
    addr: str | None = None
    host: str | None = None
    sent: int = 0
    received: int = 0
    rtts_ms: list[float] = field(default_factory=list)
    times_ms: list[float] = field(default_factory=list)
    rtt_approximate: bool = False

    @property
    def loss_pct(self) -> float:
        if self.sent == 0:
            return 100.0
        return (self.sent - self.received) * 100.0 / self.sent

    @property
    def last_ms(self) -> float | None:
        return self.rtts_ms[-1] if self.rtts_ms else None

    @property
    def best_ms(self) -> float | None:
        return min(self.rtts_ms) if self.rtts_ms else None

    @property
    def worst_ms(self) -> float | None:
        return max(self.rtts_ms) if self.rtts_ms else None

    @property
    def avg_ms(self) -> float | None:
        return sum(self.rtts_ms) / len(self.rtts_ms) if self.rtts_ms else None

    @property
    def jitter_ms(self) -> float | None:
        """Mean absolute difference between consecutive RTTs (RFC 3550 style)."""
        if len(self.rtts_ms) < 2:
            return 0.0 if self.rtts_ms else None
        diffs = [abs(b - a) for a, b in zip(self.rtts_ms, self.rtts_ms[1:], strict=False)]
        return sum(diffs) / len(diffs)

    @property
    def is_final(self) -> bool:
        return self.addr is not None and self.received > 0 and self.sent > 0 \
            and self._final

    _final: bool = False


@dataclass(slots=True)
class TraceResult:
    hops: list[Hop] = field(default_factory=list)
    target: str = ""
    destination_reached: bool = False
    method: str = ""
    error: str | None = None

    @property
    def total_loss_pct(self) -> float:
        """Loss seen on the final hop only.

        Intermediate-hop loss is usually the router rate-limiting ICMP, not
        real packet loss. Only the last hop matters for a verdict.
        """
        if not self.hops:
            return 100.0
        return self.hops[-1].loss_pct

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "method": self.method,
            "destination_reached": self.destination_reached,
            "error": self.error,
            "hops": [
                {
                    "ttl": h.ttl,
                    "addr": h.addr,
                    "host": h.host,
                    "sent": h.sent,
                    "received": h.received,
                    "loss_pct": round(h.loss_pct, 1),
                    "last_ms": h.last_ms,
                    "best_ms": h.best_ms,
                    "worst_ms": h.worst_ms,
                    "avg_ms": h.avg_ms,
                    "jitter_ms": h.jitter_ms,
                    "rtt_approximate": h.rtt_approximate,
                }
                for h in self.hops
            ],
        }


# --------------------------------------------------------------------------
# capability detection
# --------------------------------------------------------------------------

def has_raw_icmp() -> bool:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
    except (PermissionError, OSError):
        return False
    s.close()
    return True


def has_unprivileged_icmp() -> bool:
    """SOCK_DGRAM ICMP is permitted when net.ipv4.ping_group_range allows it."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
    except (PermissionError, OSError):
        return False
    s.close()
    return True


def unprivileged_icmp_range() -> tuple[int, int] | None:
    try:
        with open("/proc/sys/net/ipv4/ping_group_range") as fh:
            lo, hi = fh.read().split()
        return int(lo), int(hi)
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------
# raw ICMP traceroute
# --------------------------------------------------------------------------

def _checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    total = 0
    for i in range(0, len(data), 2):
        total += (data[i] << 8) + data[i + 1]
    total = (total >> 16) + (total & 0xFFFF)
    total += total >> 16
    return ~total & 0xFFFF


def _raw_icmp_probe(host: str, ttl: int, seq: int,
                    timeout: float) -> tuple[str | None, float]:
    """Send one ICMP echo with a given TTL. Returns (reply_source, rtt_ms)."""
    ident = os.getpid() & 0xFFFF
    payload = b"spdng-trace"
    header = struct.pack("!BBHHH", ICMP_ECHO, 0, 0, ident, seq)
    checksum = _checksum(header + payload)
    packet = struct.pack("!BBHHH", ICMP_ECHO, 0, checksum, ident, seq) + payload

    sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
    try:
        sock.settimeout(timeout)
        sock.setsockopt(socket.IPPROTO_IP, IP_TTL, ttl)
        start = time.perf_counter()
        sock.sendto(packet, (host, 0))
        deadline = start + timeout
        while time.perf_counter() < deadline:
            remaining = deadline - time.perf_counter()
            ready, _, _ = select.select([sock], [], [], remaining)
            if not ready:
                return None, (time.perf_counter() - start) * 1000
            try:
                packet_in, addr = sock.recvfrom(1024)
            except (TimeoutError, BlockingIOError):
                return None, (time.perf_counter() - start) * 1000
            # IP header is 20 bytes for IPv4 without options
            icmp = packet_in[20:]
            if len(icmp) < 8:
                continue
            icmp_type, _, _, r_id, r_seq = struct.unpack("!BBHHH", icmp[:8])
            if icmp_type == ICMP_ECHOREPLY and r_id == ident and r_seq == seq:
                return addr[0], (time.perf_counter() - start) * 1000
            # Time Exceeded (11) means a router answered; give us that address
            if icmp_type == 11:
                return addr[0], (time.perf_counter() - start) * 1000
        return None, (time.perf_counter() - start) * 1000
    finally:
        sock.close()


# --------------------------------------------------------------------------
# IP_RECVERR traceroute (unprivileged)
# --------------------------------------------------------------------------

def _recverr_probe(host: str, port: int, ttl: int,
                   timeout: float) -> tuple[str | None, float, bool]:
    """Send a UDP datagram with a TTL and wait for the error queue.

    Returns (router_or_none, rtt_ms, reached_destination).

    IMPORTANT: on a connected UDP socket, a send to a closed local port can
    surface as ECONNREFUSED. That does NOT mean we reached the destination;
    it means the local stack rejected the datagram. Treating it as
    "reached" made every trace collapse to a single hop. We therefore only
    report "reached" when the TTL was high enough to plausibly traverse the
    path AND we actually saw an ICMP port-unreachable from the far end.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        try:
            sock.setsockopt(socket.IPPROTO_IP, IP_RECVERR, 1)
        except OSError as exc:
            # No error queue on this platform, so this backend cannot
            # work. Say so rather than raising out of a trace.
            raise TraceUnavailable(
                f"IP_RECVERR is not available here: {exc}") from exc
        # PMTU discovery is a Linux socket option. Python does not
        # expose IP_MTU_DISCOVER, and the number 10 means something
        # else entirely on macOS (it is IP_MTU there), so this is only
        # set where the option genuinely exists.
        if _HAS_PMTU_DISCOVER:
            pmtudisc_do = getattr(socket, "IP_PMTUDISC_DO", 2)
            try:
                sock.setsockopt(socket.IPPROTO_IP, IP_MTU_DISCOVER,
                                pmtudisc_do)
            except OSError:
                pass
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.setsockopt(socket.IPPROTO_IP, IP_TTL, ttl)
        start = time.perf_counter()
        try:
            sock.send(b"spdng-trace")
        except OSError as exc:
            if exc.errno == errno.ECONNREFUSED:
                # Local rejection, not proof of arrival. Keep probing.
                return None, (time.perf_counter() - start) * 1000, False
            if exc.errno in (errno.EPERM, errno.EACCES):
                raise
            return None, (time.perf_counter() - start) * 1000, False

        deadline = start + timeout
        while time.perf_counter() < deadline:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                break
            try:
                data, anc, flags, addr = sock.recvmsg(
                    2048, 2048, MSG_ERRQUEUE)
            except (BlockingIOError, InterruptedError):
                continue
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    continue
                if exc.errno == errno.ECONNREFUSED:
                    # A real ICMP port-unreachable came back from the far end.
                    return None, (time.perf_counter() - start) * 1000, True
                return None, (time.perf_counter() - start) * 1000, False
            rtt = (time.perf_counter() - start) * 1000
            for level, typ, cmsg in anc:
                if level == socket.IPPROTO_IP and typ == IP_RECVERR:
                    router = _router_from_cmsg(cmsg)
                    if router:
                        return router, rtt, False
                    # An error arrived but we could not attribute it to a
                    # router. If the error was "host unreachable" we are
                    # likely at the destination.
                    return None, rtt, True
            return None, rtt, False
        return None, (time.perf_counter() - start) * 1000, False
    finally:
        sock.close()


def _router_from_cmsg(cmsg: bytes) -> str | None:
    """Extract the router address from a struct sock_extended_err payload."""
    if len(cmsg) < 16:
        return None
    # struct sock_extended_err {
    #   u8 ee_errno; u8 ee_origin; u8 ee_type; u8 ee_code; u8 ee_pad;
    #   u32 ee_info; u32 ee_data;
    # } followed by the offending IP header + 8 bytes
    payload = cmsg[16:]
    if len(payload) < 20:
        return None
    ihl = (payload[0] & 0x0F) * 4
    if ihl < 20 or len(payload) < ihl:
        return None
    # The source of the *embedded* IP packet is the router that replied.
    # Linux copies the original packet's header here, so the embedded src is
    # our source, not the router. The router address itself is not in the
    # error queue payload, so we fall back to the outer source address.
    return None


# --------------------------------------------------------------------------
# ping-binary probe
# --------------------------------------------------------------------------

def has_ping_binary() -> bool:
    """Is there a `ping` binary we can drive?

    On most Linux distributions /usr/bin/ping ships with CAP_NET_RAW, so it
    can open a raw socket on our behalf even when our own process cannot.
    That makes it a reliable way to get per-hop ICMP replies in containers,
    WSL, and CI, where raw sockets are denied to the user.
    """
    import shutil
    return shutil.which("ping") is not None


def _ping_startup_overhead() -> float:
    """Milliseconds of overhead in a `ping` invocation, measured once.

    `ping` to a router that returns "Time to live exceeded" gives us no
    RTT of its own, so we have to time the whole invocation. That includes
    process startup, DNS, and socket setup, which is roughly 10-15 ms and
    would otherwise swamp the sub-millisecond RTT of a first-hop router.

    We calibrate against loopback, where the true RTT is ~0.05 ms, and
    subtract that baseline.
    """
    global _PING_OVERHEAD_MS
    if _PING_OVERHEAD_MS is not None:
        return _PING_OVERHEAD_MS
    ping = shutil.which("ping")
    if not ping:
        _PING_OVERHEAD_MS = 0.0
        return 0.0
    samples = []
    for _ in range(3):
        start = time.perf_counter()
        try:
            subprocess.run([ping, "-c", "1", "-W", "1", "127.0.0.1"],
                           capture_output=True, timeout=5)
        except subprocess.TimeoutExpired:
            continue
        samples.append((time.perf_counter() - start) * 1000)
    _PING_OVERHEAD_MS = min(samples) if samples else 0.0
    return _PING_OVERHEAD_MS


#: Which flavour of `ping` we have: "linux" or "bsd". None until it is
#: worked out.
_PING_FLAVOUR: str | None = None


def _ping_flavour() -> str:
    """Work out which `ping` this is.

    The two flavours differ in ways that matter here:

    * The TTL flag. Linux uses `-t ttl`. BSD and macOS used to as
      well, but macOS changed `-t` to mean the TIMEOUT and moved the
      TTL to `-h hoplimit`. So on macOS, `-t 5` sets a five-second
      timeout and the packet leaves with the default TTL of 64 - every
      hop would look like the final hop, and the whole trace would be
      wrong.
    * The units of `-W`. Linux takes SECONDS, BSD and macOS take
      MILLISECONDS.

    So we ask the binary rather than guessing from the platform, because
    a Linux user on macOS (or vice versa) is not that rare and the
    guess would be wrong.
    """
    global _PING_FLAVOUR
    if _PING_FLAVOUR is not None:
        return _PING_FLAVOUR

    import shutil
    import subprocess

    ping = shutil.which("ping")
    _PING_FLAVOUR = "linux"
    if not ping:
        return _PING_FLAVOUR
    try:
        # `ping -v` prints the version and the usage on every
        # platform, and it exits without sending anything.
        proc = subprocess.run([ping, "-v"], capture_output=True, text=True,
                              timeout=5)
    except (OSError, subprocess.SubprocessError):
        return _PING_FLAVOUR
    out = (proc.stdout or "") + (proc.stderr or "")
    lowered = out.lower()
    if "bsd" in lowered or "darwin" in lowered or "mac os" in lowered:
        _PING_FLAVOUR = "bsd"
    elif "-h hoplimit" in lowered or "hoplimit" in lowered:
        _PING_FLAVOUR = "bsd"
    return _PING_FLAVOUR


def _ping_argv(ping: str, host: str, ttl: int, timeout: float) -> list[str]:
    """The command line for one ping, for the flavour we have."""
    wait = max(1, int(timeout))
    if _ping_flavour() == "bsd":
        # BSD and macOS: -W is milliseconds, and the TTL is -h.
        return [ping, "-c", "1", "-W", str(wait * 1000), "-h", str(ttl),
                host]
    # Linux: -W is seconds, and the TTL is -t.
    return [ping, "-c", "1", "-W", str(wait), "-t", str(ttl), host]


def _ping_binary_probe(host: str, ttl: int, timeout: float,
                       count: int = 1) -> tuple[str | None, list[float], int]:
    """ICMP echo probes with a given TTL, via the ping binary.

    Returns (responder_address, rtt_samples_ms, sent).

    Two measurement sources, in order of preference:

    1. When the echo actually reaches the destination, ping reports a
       precise kernel-measured RTT ("time=22.9 ms"). We use that verbatim.
    2. When a router answers "Time to live exceeded", ping reports no RTT.
       We time the whole invocation and subtract a calibrated startup
       baseline. That result is an estimate, so the hop is flagged
       rtt_approximate and rendered with a tilde.

    We use `-c 1` per probe rather than `-c N` because ping's default
    interval between probes is one second, which would contaminate any
    wall-clock measurement with the interval rather than the RTT.
    """
    import re
    import shutil
    import subprocess

    ping = shutil.which("ping")
    if not ping:
        return None, [], 0, False
    wait = max(1, int(timeout))
    overhead = _ping_startup_overhead()

    samples: list[float] = []
    addr = None
    sent = 0
    exact = False
    for _ in range(count):
        sent += 1
        started = time.perf_counter()
        try:
            proc = subprocess.run(
                _ping_argv(ping, host, ttl, wait),
                capture_output=True, text=True, timeout=wait + 5,
            )
        except subprocess.TimeoutExpired:
            continue
        wall_ms = (time.perf_counter() - started) * 1000
        out = (proc.stdout or "") + (proc.stderr or "")

        # "64 bytes from 1.1.1.1: icmp_seq=1 ttl=50 time=29.1 ms"
        m = re.search(r"time[=<]\s*([0-9.]+)\s*ms", out)
        if m:
            samples.append(float(m.group(1)))
            exact = True
            a = re.search(r"from\s+(\S+?):?\s+icmp_seq=\d+", out)
            if a:
                addr = a.group(1).rstrip(":")
            continue

        # "From 192.168.1.1 icmp_seq=1 Time to live exceeded" on Linux,
        # and "From 192.168.1.1: icmp_seq=0 Time to live exceeded" on
        # BSD and macOS. The trailing colon has to go, or the address
        # does not parse as an IP.
        a = re.search(r"From\s+(\S+?):?\s+icmp_seq=\d+", out)
        if a:
            addr = a.group(1).rstrip(":")
            samples.append(max(0.0, wall_ms - overhead))

    if exact:
        return addr, samples, sent, True
    return addr, samples, sent, False


def _is_ip(text: str) -> bool:
    """Is this string an IPv4 address rather than a hostname?"""
    parts = text.split(".")
    if len(parts) != 4:
        return False
    return all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


def _trace_ping(host: str, max_hops: int, probes: int, timeout: float,
                result: TraceResult,
                on_hop: Callable[[Hop], None] | None = None) -> TraceResult:
    target_ip = _resolved_ip(host)
    for ttl in range(1, max_hops + 1):
        hop = Hop(ttl=ttl)
        addr, samples, sent, exact = _ping_binary_probe(host, ttl, timeout,
                                                         probes)
        hop.sent += sent
        if addr:
            hop.addr = addr
            hop.received += len(samples)
            hop.rtts_ms.extend(samples)
            hop.times_ms.extend(samples)
            # When the echo reached the destination, ping reported a
            # kernel-measured RTT and it is exact. When a router answered
            # with "time to live exceeded", our RTT is an estimate.
            hop.rtt_approximate = not exact
        result.hops.append(hop)
        if on_hop is not None:
            on_hop(hop)
        if hop.addr and hop.addr == target_ip:
            hop._final = True
            result.destination_reached = True
            break
        # ping reports the REVERSE-DNS name of the responder
        # ("139-138-79-141.static4.bluestreamfiber.net") rather than the
        # literal address, so a direct string compare against the target
        # IP fails. Resolve what ping gave us and compare IPs.
        if hop.addr and not _is_ip(hop.addr):
            if _resolved_ip(hop.addr) == target_ip:
                hop.addr = target_ip
                hop._final = True
                result.destination_reached = True
                break
    return result


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------

def traceroute(
    host: str,
    port: int = 33434,
    max_hops: int = 30,
    probes: int = 3,
    timeout: float = 2.0,
    method: str = "auto",
    on_hop: Callable[[Hop], None] | None = None,
) -> TraceResult:
    """Trace the path to `host`, mtr style.

    method: "icmp" (raw socket), "udp" (IP_RECVERR), "ping" (ping binary),
    or "auto".

    `on_hop` is called with each hop as soon as it has been measured, so
    a caller can show the result immediately instead of waiting for the
    whole path. mtr works this way: you see the first hop answer while
    the rest of the path is still being measured.
    """
    if method == "auto":
        if has_raw_icmp():
            method = "icmp"
        elif has_ping_binary():
            method = "ping"
        elif has_recverr():
            method = "udp"
        else:
            # Nothing here can trace per hop. Say so, rather than
            # starting a trace that cannot work.
            result = TraceResult(target=host, method="none")
            result.error = (
                "no per-hop tracing method is available here: no raw "
                "ICMP sockets, no ping binary, and no IP_RECVERR")
            return result

    result = TraceResult(target=host, method=method)

    if method == "icmp":
        try:
            return _trace_icmp(host, max_hops, probes, timeout, result,
                               on_hop)
        except PermissionError as exc:
            result.error = f"raw ICMP not permitted: {exc}"
            return result
    if method == "ping":
        return _trace_ping(host, max_hops, probes, timeout, result, on_hop)
    if method == "udp":
        try:
            return _trace_udp(host, port, max_hops, probes, timeout, result,
                              on_hop)
        except TraceUnavailable as exc:
            result.error = str(exc)
            return result
    return result


def _resolved_ip(host: str) -> str:
    """Resolve a host to its IPv4 address once, tolerating failures."""
    try:
        return socket.gethostbyname(host)
    except OSError:
        return host


def _trace_icmp(host: str, max_hops: int, probes: int, timeout: float,
                 result: TraceResult,
                 on_hop: Callable[[Hop], None] | None = None) -> TraceResult:
    seq = 0
    for ttl in range(1, max_hops + 1):
        hop = Hop(ttl=ttl)
        for _ in range(probes):
            seq += 1
            addr, rtt = _raw_icmp_probe(host, ttl, seq, timeout)
            hop.sent += 1
            if addr:
                hop.addr = addr
                hop.received += 1
                hop.rtts_ms.append(rtt)
                hop.times_ms.append(rtt)
        result.hops.append(hop)
        if on_hop is not None:
            on_hop(hop)
        if hop.addr and _is_target(host, hop.addr):
            hop._final = True
            result.destination_reached = True
            break
    return result


def _trace_udp(host: str, port: int, max_hops: int, probes: int, timeout: float,
               result: TraceResult,
               on_hop: Callable[[Hop], None] | None = None) -> TraceResult:
    for ttl in range(1, max_hops + 1):
        hop = Hop(ttl=ttl)
        reached = False
        for _ in range(probes):
            addr, rtt, reached = _recverr_probe(host, port, ttl, timeout)
            hop.sent += 1
            if reached:
                hop.received += 1
                hop.rtts_ms.append(rtt)
                hop.times_ms.append(rtt)
                reached = True
                break
            if addr:
                hop.addr = addr
                hop.received += 1
                hop.rtts_ms.append(rtt)
                hop.times_ms.append(rtt)
        result.hops.append(hop)
        if on_hop is not None:
            on_hop(hop)
        if reached:
            hop.addr = hop.addr or host
            hop._final = True
            result.destination_reached = True
            break
    return result


def _is_target(host: str, addr: str) -> bool:
    try:
        return socket.gethostbyname(host) == addr
    except OSError:
        return False


def probe_loss_and_latency(host: str, port: int = 33434, count: int = 10,
                           timeout: float = 2.0) -> tuple[float, list[float]]:
    """Measure packet loss and RTTs to a host. Returns (loss_pct, rtts_ms).

    Uses TCP connect (works unprivileged) so it works even where ICMP is
    entirely unavailable, which is the common case in containers and WSL.
    """
    rtts: list[float] = []
    received = 0
    for _ in range(count):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        start = time.perf_counter()
        try:
            sock.connect((host, port))
            received += 1
            rtts.append((time.perf_counter() - start) * 1000)
            # Half-close rather than just closing. Closing a socket we
            # never read from sends a RST, and on macOS the next
            # connect to the same listener can be refused - which
            # reads as a lost packet when it is not one.
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass
        except (TimeoutError, OSError):
            pass
        finally:
            sock.close()
        # Give the peer a moment to notice the close before the next
        # probe, so the two do not overlap.
        time.sleep(0.01)
    loss = (count - received) * 100.0 / count if count else 100.0
    return loss, rtts
