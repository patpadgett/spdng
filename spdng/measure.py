"""Bandwidth measurement.

Measures against an Ookla speedtest server using its documented endpoints:

    GET  {base}/latency.txt?x=N        -> "test=test"   (latency probe)
    GET  {base}/random{N}x{N}.jpg      -> random bytes (download)
    POST {base}/upload.php             -> random bytes (upload)

Two things this module gets right that the old tool does not:

  * Latency is measured as a real RTT. A failed probe contributes NOTHING to
    the average and is reported as loss, never as a placeholder number.
  * Throughput is computed from bytes actually transferred over the measured
    window, not from a fixed assumption about the payload size.
"""
from __future__ import annotations

import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:25.0) Gecko/20100101 Firefox/25.0"
)

# Ookla payload sizes. dl1..dl3 are the phase durations in ms; the sizes
# grow so a fast link is not saturated by a tiny buffer.
DOWNLOAD_SIZES = [350, 500, 1000, 2000, 4000]
UPLOAD_CHUNK = 1 << 20  # 1 MiB


@dataclass(slots=True)
class LatencyResult:
    samples_ms: list[float] = field(default_factory=list)
    errors: int = 0
    attempts: int = 0

    @property
    def loss_pct(self) -> float:
        if not self.attempts:
            return 100.0
        return self.errors * 100.0 / self.attempts

    @property
    def avg_ms(self) -> float | None:
        if not self.samples_ms:
            return None
        return sum(self.samples_ms) / len(self.samples_ms)

    @property
    def min_ms(self) -> float | None:
        return min(self.samples_ms) if self.samples_ms else None

    @property
    def max_ms(self) -> float | None:
        return max(self.samples_ms) if self.samples_ms else None

    @property
    def jitter_ms(self) -> float | None:
        if len(self.samples_ms) < 2:
            return 0.0 if self.samples_ms else None
        diffs = [abs(b - a) for a, b in
                 zip(self.samples_ms, self.samples_ms[1:], strict=False)]
        return sum(diffs) / len(diffs)


@dataclass(slots=True)
class ThroughputResult:
    bytes_transferred: int = 0
    seconds: float = 0.0
    threads: int = 1
    errors: int = 0

    @property
    def mbps(self) -> float:
        if self.seconds <= 0:
            return 0.0
        return (self.bytes_transferred * 8) / self.seconds / 1_000_000

    @property
    def mbytes_per_sec(self) -> float:
        if self.seconds <= 0:
            return 0.0
        return self.bytes_transferred / self.seconds / 1_000_000


@dataclass(slots=True)
class SpeedTestResult:
    server: object = None
    download_mbps: float = 0.0
    upload_mbps: float = 0.0
    latency: LatencyResult = field(default_factory=LatencyResult)
    download_detail: ThroughputResult = field(default_factory=ThroughputResult)
    upload_detail: ThroughputResult = field(default_factory=ThroughputResult)
    share_url: str | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        srv = self.server.to_dict() if self.server is not None else None
        return {
            "server": srv,
            "download_mbps": round(self.download_mbps, 2),
            "upload_mbps": round(self.upload_mbps, 2),
            "latency": {
                "avg_ms": self.latency.avg_ms,
                "min_ms": self.latency.min_ms,
                "max_ms": self.latency.max_ms,
                "jitter_ms": self.latency.jitter_ms,
                "loss_pct": round(self.latency.loss_pct, 1),
                "samples_ms": [round(s, 2) for s in self.latency.samples_ms],
            },
            "download": {
                "bytes": self.download_detail.bytes_transferred,
                "seconds": round(self.download_detail.seconds, 3),
                "threads": self.download_detail.threads,
                "errors": self.download_detail.errors,
            },
            "upload": {
                "bytes": self.upload_detail.bytes_transferred,
                "seconds": round(self.upload_detail.seconds, 3),
                "threads": self.upload_detail.threads,
                "errors": self.upload_detail.errors,
            },
            "share_url": self.share_url,
            "errors": self.errors,
        }


def _opener(ua: str) -> urllib.request.OpenerDirector:
    """An opener that follows redirects, unlike speedtest-cli's client."""
    return urllib.request.build_opener(
        urllib.request.HTTPRedirectHandler(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )


def measure_latency(base_url: str, count: int = 5, timeout: float = 10.0,
                    ua: str = DEFAULT_UA) -> LatencyResult:
    """Measure latency by timing the latency.txt probe.

    A probe that fails (timeout, non-200, wrong body) is recorded as an
    error and excluded from the average. It is NEVER substituted with a
    placeholder.
    """
    result = LatencyResult()
    opener = _opener(ua)
    for i in range(count):
        result.attempts += 1
        stamp = int(time.time() * 1000)
        url = f"{base_url}/latency.txt?x={stamp}.{i}"
        start = time.perf_counter()
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": ua, "Accept": "*/*", "Connection": "close"})
            with opener.open(req, timeout=timeout) as resp:
                body = resp.read(16)
                rtt = (time.perf_counter() - start) * 1000
                if resp.status == 200 and body.strip() == b"test=test":
                    result.samples_ms.append(rtt)
                else:
                    result.errors += 1
        except (urllib.error.URLError, OSError, ssl.SSLError):
            result.errors += 1
    return result


def _download_worker(url: str, ua: str, stop_at: float,
                     counter: list[int], errors: list[int]) -> None:
    opener = _opener(ua)
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": ua, "Accept": "*/*", "Connection": "close"})
        with opener.open(req, timeout=30) as resp:
            while time.perf_counter() < stop_at:
                chunk = resp.read(65536)
                if not chunk:
                    break
                counter[0] += len(chunk)
    except (urllib.error.URLError, OSError, ssl.SSLError, ValueError):
        errors[0] += 1


def measure_download(base_url: str, duration: float = 10.0, threads: int = 4,
                     ua: str = DEFAULT_UA) -> ThroughputResult:
    """Measure download throughput with N parallel connections."""
    result = ThroughputResult(threads=threads)
    stop_at = time.perf_counter() + duration
    counter = [0]
    errors = [0]
    stamp = int(time.time() * 1000)
    workers: list[threading.Thread] = []
    for i in range(threads):
        size = DOWNLOAD_SIZES[i % len(DOWNLOAD_SIZES)]
        url = f"{base_url}/random{size}x{size}.jpg?x={stamp}.{i}"
        t = threading.Thread(target=_download_worker,
                             args=(url, ua, stop_at, counter, errors),
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


def _upload_worker(url: str, ua: str, stop_at: float,
                   counter: list[int], errors: list[int]) -> None:
    opener = _opener(ua)
    try:
        while time.perf_counter() < stop_at:
            payload = os.urandom(1 << 16)
            req = urllib.request.Request(
                url, data=payload, headers={
                    "User-Agent": ua,
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(len(payload)),
                })
            with opener.open(req, timeout=30) as resp:
                resp.read()
                counter[0] += len(payload)
    except (urllib.error.URLError, OSError, ssl.SSLError, ValueError):
        errors[0] += 1


def measure_upload(base_url: str, duration: float = 10.0, threads: int = 2,
                   ua: str = DEFAULT_UA) -> ThroughputResult:
    """Measure upload throughput with N parallel connections."""
    result = ThroughputResult(threads=threads)
    stop_at = time.perf_counter() + duration
    counter = [0]
    errors = [0]
    url = f"{base_url}/upload.php?x={int(time.time() * 1000)}"
    workers: list[threading.Thread] = []
    for _ in range(threads):
        t = threading.Thread(target=_upload_worker,
                             args=(url, ua, stop_at, counter, errors),
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


def run_speedtest(server, duration: float = 10.0, threads: int = 4,
                  latency_samples: int = 5, do_upload: bool = True,
                  do_download: bool = True,
                  ua: str = DEFAULT_UA) -> SpeedTestResult:
    """Run a full speed test against a Server."""
    result = SpeedTestResult(server=server)
    base = server.base

    result.latency = measure_latency(base, count=latency_samples, ua=ua)
    if result.latency.avg_ms is None:
        result.errors.append(
            "latency probe failed against every server; "
            "the server may require a browser session")

    if do_download:
        result.download_detail = measure_download(
            base, duration=duration, threads=threads, ua=ua)
        result.download_mbps = result.download_detail.mbps

    if do_upload:
        result.upload_detail = measure_upload(
            base, duration=duration, threads=max(1, threads // 2), ua=ua)
        result.upload_mbps = result.upload_detail.mbps

    server.latency_ms = result.latency.avg_ms
    server.jitter_ms = result.latency.jitter_ms
    return result
