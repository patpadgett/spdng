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

import http.client
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
    #: The cost of establishing the connection, when the provider can
    #: measure it. This is NOT the latency of the link: it is a
    #: one-off setup cost, and averaging it into the latency would
    #: inflate every number by it.
    handshake_ms: float | None = None

    @property
    def loss_pct(self) -> float:
        if not self.attempts:
            return 100.0
        # Keep the percentage meaningful even if a provider reports a
        # setup error separately from its probe attempts.
        return min(max(self.errors, 0), self.attempts) * 100.0 / self.attempts

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
    #: None means the direction was not measured (for example because the
    #: user passed --no-download). It is deliberately different from 0.0,
    #: which is a real transfer that moved no bytes.
    download_mbps: float | None = None
    upload_mbps: float | None = None
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
            "download_mbps": (round(self.download_mbps, 2)
                              if self.download_mbps is not None else None),
            "upload_mbps": (round(self.upload_mbps, 2)
                            if self.upload_mbps is not None else None),
            "latency": {
                "avg_ms": self.latency.avg_ms,
                "min_ms": self.latency.min_ms,
                "max_ms": self.latency.max_ms,
                "jitter_ms": self.latency.jitter_ms,
                # No attempts means the latency probe was skipped, not
                # that every probe failed. Do not turn that into 100% loss.
                "loss_pct": (round(self.latency.loss_pct, 1)
                             if self.latency.attempts else None),
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



_WORKER_LOCK = threading.Lock()


def _count(counter: list[int], byte_count: int) -> None:
    """Add a worker's bytes under the lock shared by all transfer threads."""
    with _WORKER_LOCK:
        counter[0] += byte_count


def _error(errors: list[int]) -> None:
    with _WORKER_LOCK:
        errors[0] += 1


def _cache_busted(url: str, sequence: int) -> str:
    """Add a fresh query parameter so a repeated URL is not served from cache."""
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query.append(("spdng", str(sequence)))
    return urllib.parse.urlunsplit((
        parts.scheme, parts.netloc, parts.path,
        urllib.parse.urlencode(query), parts.fragment,
    ))


def download_worker(url: str, ua: str, stop_at: float,
                    counter: list[int], errors: list[int],
                    chunk_size: int = 65536) -> None:
    """Download from `url` repeatedly until `stop_at`, counting bytes.

    A single request is not a throughput test: a small file ends in
    milliseconds and a large one can end before the window does. Keep
    issuing requests (with a fresh cache-buster) until the deadline, and
    count every byte read before it. A response that ends early is
    followed by another request; an error ends this worker.
    """
    opener = _opener(ua)
    sequence = 0
    while time.perf_counter() < stop_at:
        target = _cache_busted(url, sequence)
        sequence += 1
        received = 0
        try:
            req = urllib.request.Request(target, headers={
                "User-Agent": ua, "Accept": "*/*"})
            with opener.open(req, timeout=30) as resp:
                while time.perf_counter() < stop_at:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    received += len(chunk)
                    _count(counter, len(chunk))
        except (urllib.error.URLError, OSError, ssl.SSLError, ValueError):
            _error(errors)
            return
        if received == 0:
            # A request that was still in flight when the window closed
            # has simply run out of time; that is not a transfer error.
            if time.perf_counter() >= stop_at:
                return
            # An empty 200 response would otherwise spin as fast as the
            # CPU allows without measuring anything.
            _error(errors)
            return


def _upload_connection(url: str, timeout: float = 30.0):
    """A persistent HTTP(S) connection for the upload worker."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https":
        return http.client.HTTPSConnection(
            parts.hostname, parts.port or 443, timeout=timeout,
            context=ssl.create_default_context())
    if parts.scheme == "http":
        return http.client.HTTPConnection(
            parts.hostname, parts.port or 80, timeout=timeout)
    raise ValueError(f"unsupported upload scheme: {parts.scheme!r}")


def _upload_path(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    path = parts.path or "/"
    return f"{path}?{parts.query}" if parts.query else path


def upload_worker(url: str, ua: str, stop_at: float,
                  counter: list[int], errors: list[int],
                  chunk_size: int = 1 << 16) -> None:
    """POST random bytes until `stop_at`, reusing one connection.

    Opening a fresh connection for every 64 KiB chunk measures the TCP
    and TLS handshake, not the upload. Keep the connection open and send
    the chunks back-to-back; if the server closes it between requests,
    reconnect once and continue.
    """
    path = _upload_path(url)
    conn = None
    try:
        while time.perf_counter() < stop_at:
            payload = os.urandom(chunk_size)
            for attempt in (0, 1):
                try:
                    if conn is None:
                        conn = _upload_connection(url)
                    conn.request("POST", path, body=payload, headers={
                        "User-Agent": ua,
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(len(payload)),
                    })
                    resp = conn.getresponse()
                    resp.read()
                    if not 200 <= resp.status < 300:
                        raise OSError(
                            f"upload returned HTTP {resp.status}")
                    _count(counter, len(payload))
                    break
                except (http.client.HTTPException, OSError, ssl.SSLError,
                        ValueError):
                    if conn is not None:
                        conn.close()
                    conn = None
                    if attempt == 1:
                        _error(errors)
                        return
    finally:
        if conn is not None:
            conn.close()


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
