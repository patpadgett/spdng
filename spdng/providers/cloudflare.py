"""Cloudflare provider.

Uses Cloudflare's public measurement endpoint, the same one behind
speed.cloudflare.com. It is a genuinely different measurement from the
others: the test server is the nearest Cloudflare data centre, which is
the network almost all of your internet traffic crosses. If your ISP has a
problem with a specific third party but not with Cloudflare, only this
provider will show it.

The API is two endpoints and no key:

    GET  https://speed.cloudflare.com/__down?bytes=N
    POST https://speed.cloudflare.com/__up

There is no server list to speak of: there is one implicit server, the
nearest data centre. We synthesise a Server entry for it so it flows
through the same code path as the others.
"""
from __future__ import annotations

import socket
import threading
import time

from ..measure import (
    DEFAULT_UA,
    LatencyResult,
    ThroughputResult,
    download_worker,
    upload_worker,
)
from ..servers import Server
from .base import Provider

BASE = "https://speed.cloudflare.com"
DOWN_ENDPOINT = f"{BASE}/__down"
UP_ENDPOINT = f"{BASE}/__up"

# Cloudflare lets you ask for a specific number of bytes. Larger sizes
# amortise the connection setup better on fast links.
DOWNLOAD_CHUNKS = [1_000_000, 10_000_000, 25_000_000, 50_000_000]


class CloudflareProvider(Provider):
    id = "cloudflare"
    label = "Cloudflare"
    infrastructure = "Cloudflare's nearest data centre"
    has_server_list = False

    def __init__(self, ua: str = DEFAULT_UA) -> None:
        self.ua = ua

    def _server(self) -> Server:
        host = "speed.cloudflare.com"
        return Server(
            id="cloudflare",
            host=f"{host}:443",
            sponsor="Cloudflare",
            name="Nearest Cloudflare data centre",
            country="",
            cc="",
            lat=0.0,
            lon=0.0,
            distance_km=0.0,
            url=f"{BASE}/",
            provider=self.id,
        )

    def list_servers(self, limit: int = 10) -> list[Server]:
        return [self._server()]

    def measure_latency(self, server: Server, count: int = 5,
                        timeout: float = 10.0) -> LatencyResult:
        """Measure the steady-state RTT to speed.cloudflare.com.

        Cloudflare has no "latency.txt" endpoint, but it does have
        `__down?bytes=0`, which answers 200 with an empty body. On a warm
        connection that measures the round trip on the link, which is
        what we want and what makes the number comparable to the other
        providers.

        The TCP and TLS handshake is a separate thing: a one-off setup
        cost, not the latency of the link. We measure it once and report
        it separately, because averaging it into the latency would
        inflate every sample.
        """
        import ssl

        from ..httpconn import LatencyConnection, ProbeError, ok

        result = LatencyResult()

        # The handshake, measured once, on its own.
        start = time.perf_counter()
        try:
            sock = socket.create_connection(("speed.cloudflare.com", 443),
                                            timeout=timeout)
            try:
                sock.settimeout(timeout)
                ctx = ssl.create_default_context()
                with ctx.wrap_socket(sock,
                                     server_hostname="speed.cloudflare.com"):
                    result.handshake_ms = (time.perf_counter() - start) * 1000
            finally:
                sock.close()
        except (TimeoutError, OSError):
            result.errors += 1

        conn = LatencyConnection(BASE, ua=self.ua, timeout=timeout,
                                 validator=ok)
        try:
            if conn.warm_up("/__down", "bytes=0"):
                result.attempts += 1
            for i in range(count):
                result.attempts += 1
                stamp = int(time.time() * 1000)
                try:
                    rtt = conn.probe("/__down", f"{stamp}.{i}",
                                     query=f"bytes=0&x={stamp}.{i}")
                    result.samples_ms.append(rtt)
                except ProbeError:
                    result.errors += 1
        finally:
            conn.close()
        return result

    def measure_download(self, server: Server, duration: float = 10.0,
                         threads: int = 4) -> ThroughputResult:
        result = ThroughputResult(threads=threads)
        stop_at = time.perf_counter() + duration
        counter = [0]
        errors = [0]
        workers = []
        for i in range(threads):
            size = DOWNLOAD_CHUNKS[i % len(DOWNLOAD_CHUNKS)]
            url = f"{DOWN_ENDPOINT}?bytes={size}"
            t = threading.Thread(target=download_worker,
                                 args=(url, self.ua, stop_at, counter, errors),
                                 daemon=True)
            t.start()
            workers.append(t)
        start = time.perf_counter()
        for t in workers:
            t.join(timeout=duration + 15)
        result.seconds = time.perf_counter() - start
        result.bytes_transferred = counter[0]
        result.errors = errors[0]
        return result

    def measure_upload(self, server: Server, duration: float = 10.0,
                       threads: int = 2) -> ThroughputResult:
        result = ThroughputResult(threads=threads)
        stop_at = time.perf_counter() + duration
        counter = [0]
        errors = [0]
        workers = []
        for _ in range(threads):
            t = threading.Thread(target=upload_worker,
                                 args=(UP_ENDPOINT, self.ua, stop_at, counter,
                                       errors),
                                 daemon=True)
            t.start()
            workers.append(t)
        start = time.perf_counter()
        for t in workers:
            t.join(timeout=duration + 15)
        result.seconds = time.perf_counter() - start
        result.bytes_transferred = counter[0]
        result.errors = errors[0]
        return result

