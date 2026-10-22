"""A persistent HTTP connection for latency probes.

Measuring latency over a fresh connection measures the TCP and TLS
handshake, not the network. A cold probe to an Ookla server comes back
around 100 ms; the same probe on a warm connection comes back around
25 ms. Worse, the handshake is the slowest part of the exchange, so one
slow handshake drags the reported average up by hundreds of milliseconds
and makes the tool look like it is reporting a bad link when the link is
fine.

So: open the connection once, throw away the first exchange, and report
the rest. The discarded probe is still counted as an attempt, because it
was attempted - but it is not a latency sample, because it is not a
measurement of the link.

The connection is also retried, because a keep-alive socket that the
server closed between probes is a normal thing to hit and not an error
worth reporting to the user.
"""
from __future__ import annotations

import http.client
import ssl
import time
import urllib.parse

DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:25.0) Gecko/20100101 Firefox/25.0"
)


class ProbeError(Exception):
    """A latency probe failed."""


class LatencyConnection:
    """A keep-alive connection to a speedtest server."""

    def __init__(self, base_url: str, ua: str = DEFAULT_UA,
                 timeout: float = 10.0, verify_tls: bool = True,
                 validator: callable | None = None,
                 max_body: int = 65536) -> None:
        parts = urllib.parse.urlsplit(base_url)
        if parts.scheme not in ("http", "https"):
            raise ProbeError(f"unsupported scheme: {parts.scheme!r}")
        self.base = base_url
        self.ua = ua
        self.timeout = timeout
        self._parts = parts
        self._base_path = parts.path.rstrip("/")
        self._conn: http.client.HTTPConnection | None = None
        self._verify_tls = verify_tls
        self.max_body = max_body
        #: Checks the response body. Returns False for a body that is
        #: not the expected one, which counts as a failed probe.
        self.validator = validator or _default_validator

    # -- connection handling ------------------------------------------------

    def _connect(self) -> http.client.HTTPConnection:
        parts = self._parts
        if parts.scheme == "https":
            ctx = ssl.create_default_context()
            if not self._verify_tls:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(
                parts.hostname, parts.port or 443, timeout=self.timeout,
                context=ctx)
        else:
            conn = http.client.HTTPConnection(
                parts.hostname, parts.port or 80, timeout=self.timeout)
        return conn

    def _ensure(self) -> http.client.HTTPConnection:
        if self._conn is None:
            self._conn = self._connect()
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None

    def __enter__(self) -> LatencyConnection:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- probing ------------------------------------------------------------

    def _target(self, path: str, query: str) -> str:
        """Resolve a probe path against the base URL without duplicating it.

        A base URL may be an origin (``https://host``), a directory
        (``https://host/backend``), or the full endpoint
        (``https://host/backend/empty.php``). The providers use all three
        shapes. If the caller's path already includes the base path, keep
        it as-is; otherwise join it to the base path.
        """
        if path.startswith(("http://", "https://")):
            absolute = urllib.parse.urlsplit(path)
            path = absolute.path or "/"
            if absolute.query:
                query = absolute.query
        path = "/" + path.lstrip("/")
        base = self._base_path
        if base and path != base and not path.startswith(base + "/"):
            path = base + path
        return f"{path}?{query}" if query else path

    def probe(self, path: str, stamp: str, query: str = "") -> float:
        """Run one request on the persistent connection, return the RTT.

        The returned RTT is the time from sending the request to reading
        the whole response, on a connection that is already open. If the
        server closed the connection, we reconnect and retry once - the
        retry is not counted as a failed probe, because the probe itself
        succeeded.
        """
        qs = query if query else f"x={stamp}"
        for attempt in (0, 1):
            conn = self._ensure()
            start = time.perf_counter()
            try:
                conn.request("GET", self._target(path, qs),
                             headers={"User-Agent": self.ua,
                                      "Accept": "*/*"})
                resp = conn.getresponse()
                body = resp.read(self.max_body)
                if len(body) == self.max_body:
                    # The body is too large to be one of the tiny latency
                    # endpoints. Leave it unread rather than letting it
                    # desynchronise the next keep-alive request.
                    self.close()
                rtt = (time.perf_counter() - start) * 1000
                if resp.status != 200 or not self.validator(body):
                    raise ProbeError(
                        f"unexpected response: {resp.status} {body!r}")
                return rtt
            except (http.client.HTTPException, OSError, ssl.SSLError) as exc:
                # The socket went away. Reconnect and try once more.
                self.close()
                if attempt == 0:
                    continue
                raise ProbeError(f"probe failed: {exc}") from exc
        raise ProbeError("probe failed")

    def warm_up(self, path: str = "/latency.txt", query: str = "") -> bool:
        """Open the connection and discard the first exchange.

        Returns True if the warm-up succeeded. A failed warm-up is not
        an error: the first real probe will reconnect and try again.
        """
        try:
            self.probe(path, f"warm{int(time.time() * 1000)}", query=query)
            return True
        except ProbeError:
            self.close()
            return False


def _default_validator(body: bytes) -> bool:
    """Ookla's latency endpoint answers with exactly "test=test"."""
    return body.strip() == b"test=test"


def ok(body: bytes) -> bool:
    """A validator for endpoints that answer 200 with any body."""
    return True
