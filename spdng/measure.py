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
import time
import urllib.error
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
    #: Which provider produced this result.
    provider: str = "ookla"
    provider_label: str = ""

    def to_dict(self) -> dict:
        srv = self.server.to_dict() if self.server is not None else None
        return {
            "server": srv,
            "provider": self.provider,
            "provider_label": self.provider_label,
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



def download_worker(url: str, ua: str, stop_at: float,
                    counter: list[int], errors: list[int],
                    chunk_size: int = 65536) -> None:
    """Stream a URL until `stop_at`, counting bytes.

    Shared by every provider: they differ in the URL, not in how the
    transfer is counted.
    """
    opener = _opener(ua)
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": ua, "Accept": "*/*", "Connection": "close"})
        with opener.open(req, timeout=30) as resp:
            while time.perf_counter() < stop_at:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                counter[0] += len(chunk)
    except (urllib.error.URLError, OSError, ssl.SSLError, ValueError):
        errors[0] += 1


def upload_worker(url: str, ua: str, stop_at: float,
                  counter: list[int], errors: list[int],
                  chunk_size: int = 1 << 16) -> None:
    """POST random bytes to a URL until `stop_at`, counting bytes."""
    opener = _opener(ua)
    try:
        while time.perf_counter() < stop_at:
            payload = os.urandom(chunk_size)
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


# ---------------------------------------------------------------------------
# Provider-specific measurement lives in spdng/providers/
# ---------------------------------------------------------------------------
# The functions below used to live here. They now belong to the provider
# that owns the protocol, because each network has a different way of
# measuring latency and throughput. What remains here is the shared
# vocabulary: LatencyResult, ThroughputResult, SpeedTestResult, and the
# HTTP opener that follows redirects (the bug that broke the old tool).


def run_speedtest(server, duration: float = 10.0, threads: int = 4,
                  latency_samples: int = 5, do_upload: bool = True,
                  do_download: bool = True,
                  ua: str = DEFAULT_UA) -> SpeedTestResult:
    """Run a full speed test against a Server using the Ookla provider.

    Kept as a convenience wrapper; the provider is the real interface.
    """
    from .providers.ookla import OoklaProvider

    provider = OoklaProvider(ua=ua)
    result = SpeedTestResult(server=server)
    result.provider = provider.id
    result.provider_label = provider.label
    result.latency = provider.measure_latency(
        server, count=latency_samples, ua=ua)
    server.latency_ms = result.latency.avg_ms
    server.jitter_ms = result.latency.jitter_ms
    if do_download:
        result.download_detail = provider.measure_download(
            server, duration=duration, threads=threads)
        result.download_mbps = result.download_detail.mbps
    if do_upload:
        result.upload_detail = provider.measure_upload(
            server, duration=duration, threads=max(1, threads // 2))
        result.upload_mbps = result.upload_detail.mbps
    return result
