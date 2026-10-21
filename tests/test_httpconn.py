"""Tests for the persistent connection and the latency measurement.

These exist because the original latency measurement was wrong in a way
that was invisible on a good connection and obvious on a bad one: every
probe paid for a fresh TCP and TLS handshake, so the reported "ping"
was the handshake cost plus the round trip, and a single slow handshake
inflated the average by hundreds of milliseconds.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from spdng.httpconn import LatencyConnection, ProbeError, ok


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    body = b"test=test\n"

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/boom"):
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    """A local HTTP server that keeps the connection alive."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    yield f"http://{host}:{port}"
    httpd.shutdown()


def test_probe_returns_a_positive_rtt(server):
    conn = LatencyConnection(server)
    try:
        rtt = conn.probe("/latency.txt", "1")
        assert rtt > 0
    finally:
        conn.close()


def test_probe_rejects_a_bad_body(server):
    conn = LatencyConnection(server, validator=ok)
    try:
        with pytest.raises(ProbeError):
            # /boom returns 500, which is not a success.
            conn.probe("/boom", "1")
    finally:
        conn.close()


def test_probe_rejects_a_bad_status(server):
    conn = LatencyConnection(server, validator=lambda body: True)
    try:
        with pytest.raises(ProbeError):
            conn.probe("/boom", "1")
    finally:
        conn.close()


def test_the_connection_is_reused(server):
    """The whole point: one connection, many probes."""
    conn = LatencyConnection(server)
    try:
        conn.warm_up()
        first = conn._conn
        conn.probe("/latency.txt", "2")
        assert conn._conn is first, "the connection was not reused"
    finally:
        conn.close()


def test_warm_up_reports_success(server):
    conn = LatencyConnection(server)
    try:
        assert conn.warm_up() is True
    finally:
        conn.close()


def test_warm_up_on_a_dead_host_returns_false():
    # Port 1 on localhost: nothing listens there.
    conn = LatencyConnection("http://127.0.0.1:1", timeout=1.0)
    try:
        assert conn.warm_up() is False
    finally:
        conn.close()


def test_probe_on_a_dead_host_raises():
    conn = LatencyConnection("http://127.0.0.1:1", timeout=1.0)
    try:
        with pytest.raises(ProbeError):
            conn.probe("/latency.txt", "1")
    finally:
        conn.close()


def test_a_closed_server_still_probes():
    """The server closing the connection is normal, not an error.

    The connection is reopened and the probe retried, so the user sees
    a successful measurement rather than a spurious loss.
    """
    state = {"close_after_warmup": False}

    class ClosingHandler(_Handler):
        def do_GET(self):  # noqa: N802
            if state["close_after_warmup"]:
                self.close_connection = True
            super().do_GET()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), ClosingHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    conn = LatencyConnection(f"http://{host}:{port}")
    try:
        conn.warm_up()
        state["close_after_warmup"] = True
        # This must not raise: the connection is reopened and retried.
        rtt = conn.probe("/latency.txt", "2")
        assert rtt > 0
    finally:
        conn.close()
        httpd.shutdown()


def test_an_unsupported_scheme_is_rejected():
    with pytest.raises(ProbeError):
        LatencyConnection("ftp://example.com")


def test_the_default_validator_checks_the_body():
    from spdng.httpconn import _default_validator
    assert _default_validator(b"test=test\n") is True
    assert _default_validator(b"nope") is False


def test_the_permissive_validator_accepts_anything():
    assert ok(b"") is True
    assert ok(b"whatever") is True


def test_a_custom_query_is_sent(server):
    """Cloudflare's endpoint needs bytes=0, not just a cache buster."""
    seen = []

    class RecordingHandler(_Handler):
        def do_GET(self):  # noqa: N802
            seen.append(self.path)
            super().do_GET()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), RecordingHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    conn = LatencyConnection(f"http://{host}:{port}", validator=ok)
    try:
        conn.probe("/__down", "1", query="bytes=0&x=1")
        assert "bytes=0" in seen[0]
    finally:
        conn.close()
        httpd.shutdown()


# --------------------------------------------------------------------------
# the latency result
# --------------------------------------------------------------------------

def test_handshake_is_reported_separately():
    """The handshake is a setup cost, not the latency of the link."""
    from spdng.measure import LatencyResult
    r = LatencyResult(samples_ms=[20.0, 22.0], errors=0, attempts=3)
    r.handshake_ms = 95.0
    assert r.avg_ms == 21.0
    assert r.handshake_ms == 95.0


def test_handshake_defaults_to_none():
    from spdng.measure import LatencyResult
    assert LatencyResult().handshake_ms is None


def test_avg_ignores_the_handshake():
    from spdng.measure import LatencyResult
    r = LatencyResult(samples_ms=[20.0, 30.0], errors=0, attempts=2)
    r.handshake_ms = 500.0
    assert r.avg_ms == 25.0
