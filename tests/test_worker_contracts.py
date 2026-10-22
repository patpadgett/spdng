"""Contract tests for the shared transfer workers.

These run against a real local HTTP server rather than mocking urllib, so
they exercise the request/deadline/error paths the providers rely on.
"""
from __future__ import annotations

import time
from http.server import BaseHTTPRequestHandler


class _DownloadHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    body = b"x" * 1024
    gets = 0

    def do_GET(self):  # noqa: N802
        type(self).gets += 1
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):
        pass


class _ErrorHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802
        self.send_response(500)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


class _UploadHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    posts = 0
    payload_bytes = 0

    def do_POST(self):  # noqa: N802
        type(self).posts += 1
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).payload_bytes += len(body)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


def test_download_worker_counts_the_body_it_reads(http_server):
    from spdng.measure import download_worker

    _DownloadHandler.gets = 0
    base = http_server(_DownloadHandler)
    counter = [0]
    errors = [0]

    started = time.perf_counter()
    download_worker(f"{base}/payload", "spdng-test", started + 1.0,
                    counter, errors)

    assert errors[0] == 0
    assert counter[0] >= len(_DownloadHandler.body)
    assert _DownloadHandler.gets >= 1


def test_download_worker_records_an_http_error(http_server):
    from spdng.measure import download_worker

    base = http_server(_ErrorHandler)
    counter = [0]
    errors = [0]

    download_worker(f"{base}/fail", "spdng-test",
                    time.perf_counter() + 0.5, counter, errors)

    assert counter[0] == 0
    assert errors[0] == 1


def test_upload_worker_counts_bytes_it_posts(http_server):
    from spdng.measure import upload_worker

    _UploadHandler.posts = 0
    _UploadHandler.payload_bytes = 0
    base = http_server(_UploadHandler)
    counter = [0]
    errors = [0]

    upload_worker(f"{base}/upload", "spdng-test",
                  time.perf_counter() + 0.3, counter, errors)

    assert errors[0] == 0
    assert counter[0] > 0
    assert counter[0] == _UploadHandler.payload_bytes
    assert _UploadHandler.posts >= 1
