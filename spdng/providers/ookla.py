"""Ookla / speedtest.net provider.

The classic server network. The server list comes from the geo-aware
`api/js/servers` endpoint, which reports the distance in km from the
caller, so we can rank by proximity.

A note on the `host` vs `url` fields: `host` is the canonical hostname and
answers directly. The hostname inside `url` is the legacy one and replies
with a 307 redirect to the canonical name. We use `host` and skip the
redirect entirely, which is one full round trip saved.
"""
from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request

from ..measure import (
    DEFAULT_UA,
    LatencyResult,
    ThroughputResult,
    _opener,
    download_worker,
    upload_worker,
)
from ..servers import Server, fetch_servers, rank_servers
from .base import Provider, ProviderError

SERVERS_API = "https://www.speedtest.net/api/js/servers"
CONFIG_API = "https://www.speedtest.net/speedtest-config.php"

DOWNLOAD_SIZES = [350, 500, 1000, 2000, 4000]


class OoklaProvider(Provider):
    id = "ookla"
    label = "Ookla (speedtest.net)"
    infrastructure = "Ookla's server network"
    has_server_list = True

    def __init__(self, ua: str = DEFAULT_UA) -> None:
        self.ua = ua

    def list_servers(self, limit: int = 10) -> list[Server]:
        try:
            servers = fetch_servers(limit=limit * 3, ua=self.ua)
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"could not fetch Ookla server list: {exc}") from exc
        ranked = rank_servers(servers)
        for s in ranked:
            s.provider = self.id
        return ranked[:limit]

    def measure_latency(self, server: Server, count: int = 5,
                        timeout: float = 10.0) -> LatencyResult:
        result = LatencyResult()
        opener = _opener(self.ua)
        for i in range(count):
            result.attempts += 1
            stamp = int(time.time() * 1000)
            url = f"{server.base}/latency.txt?x={stamp}.{i}"
            start = time.perf_counter()
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": self.ua, "Accept": "*/*",
                    "Connection": "close"})
                with opener.open(req, timeout=timeout) as resp:
                    body = resp.read(16)
                    rtt = (time.perf_counter() - start) * 1000
                    if resp.status == 200 and body.strip() == b"test=test":
                        result.samples_ms.append(rtt)
                    else:
                        result.errors += 1
            except (urllib.error.URLError, OSError):
                result.errors += 1
        return result

    def measure_download(self, server: Server, duration: float = 10.0,
                         threads: int = 4) -> ThroughputResult:
        import threading
        result = ThroughputResult(threads=threads)
        stop_at = time.perf_counter() + duration
        counter = [0]
        errors = [0]
        stamp = int(time.time() * 1000)
        workers = []
        for i in range(threads):
            size = DOWNLOAD_SIZES[i % len(DOWNLOAD_SIZES)]
            url = f"{server.base}/random{size}x{size}.jpg?x={stamp}.{i}"
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
        import threading
        result = ThroughputResult(threads=threads)
        stop_at = time.perf_counter() + duration
        counter = [0]
        errors = [0]
        url = f"{server.base}/upload.php?x={int(time.time() * 1000)}"
        workers = []
        for _ in range(threads):
            t = threading.Thread(target=upload_worker,
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
