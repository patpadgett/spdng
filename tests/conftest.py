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

import pytest


def pytest_collection_modifyitems(config, items):
    """Skip the live tests unless SPDNG_LIVE=1 is set."""
    if os.environ.get("SPDNG_LIVE"):
        return
    skip_live = pytest.mark.skip(
        reason="hits the network; set SPDNG_LIVE=1 to run it")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
