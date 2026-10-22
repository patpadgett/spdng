"""Shared test configuration.

The live tests hit the network: they fetch the real server list and
measure the real latency to a real server. They are marked `live` and
skipped unless SPDNG_LIVE=1 is set, so the default test run is
offline, fast and deterministic.

    pytest              # offline, no network
    SPDNG_LIVE=1 pytest  # also run the network tests
    pytest -m live      # only the network tests
"""
from __future__ import annotations

import os
import threading
from http.server import ThreadingHTTPServer

import pytest


@pytest.fixture
def http_server():
    """Start a threaded local HTTP server for a test.

    Returns a callable that takes a request-handler class and returns the
    server's base URL. Every server started by the fixture is shut down
    when the test finishes.
    """
    running: list[tuple[ThreadingHTTPServer, threading.Thread]] = []

    def start(handler_cls) -> str:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        httpd.daemon_threads = True
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        running.append((httpd, thread))
        return f"http://127.0.0.1:{httpd.server_address[1]}"

    yield start

    for httpd, thread in running:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def pytest_collection_modifyitems(config, items):
    """Skip the live tests unless SPDNG_LIVE=1 is set."""
    if os.environ.get("SPDNG_LIVE"):
        return
    skip_live = pytest.mark.skip(
        reason="hits the network; set SPDNG_LIVE=1 to run it")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
