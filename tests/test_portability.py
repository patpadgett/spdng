"""Tests for the things that differ between platforms.

These exist because the first CI run on macOS failed, and the bugs it
found are the kind that only show up on the other platform:

  * `curses.A_ITALIC` does not exist on macOS, so importing the module
    raised AttributeError and nothing ran at all.
  * macOS `ping` uses `-h` for the TTL and `-t` for the timeout, while
    Linux uses `-t` for the TTL. So the Linux command line on macOS set
    a five-second timeout and sent the packet with the default TTL of
    64 - every hop looked like the final hop, and the whole trace was
    wrong.
  * macOS `ping` reports the TTL-exceeded reply as
    "From 192.168.1.1: icmp_seq=0" with a colon, and Linux reports it
    without one. The colon made the address unparseable.
"""
from __future__ import annotations

import re

import pytest

# --------------------------------------------------------------------------
# the curses attributes
# --------------------------------------------------------------------------

def test_the_curses_attributes_survive_a_missing_one():
    """A platform without A_ITALIC must still import."""
    import importlib

    import spdng.tui as tui

    real_italic = getattr(tui.curses, "A_ITALIC", None)
    try:
        if hasattr(tui.curses, "A_ITALIC"):
            del tui.curses.A_ITALIC
        reloaded = importlib.reload(tui)
        # The import worked, and the attribute is simply zero.
        assert reloaded._SGR_ATTRIBUTES["3"] == 0
        # The others are still there.
        assert reloaded._SGR_ATTRIBUTES["1"] != 0
    finally:
        if real_italic is not None:
            tui.curses.A_ITALIC = real_italic
            importlib.reload(tui)


def test_every_sgr_attribute_is_an_int():
    from spdng.tui import _SGR_ATTRIBUTES

    for code, value in _SGR_ATTRIBUTES.items():
        assert isinstance(value, int), f"{code} is not an int"


# --------------------------------------------------------------------------
# the ping command line
# --------------------------------------------------------------------------

def test_the_linux_ping_uses_t_for_the_ttl():
    from spdng import traceroute

    traceroute._PING_FLAVOUR = "linux"
    argv = traceroute._ping_argv("ping", "1.1.1.1", ttl=5, timeout=2.0)
    assert "-t" in argv
    assert argv[argv.index("-t") + 1] == "5"
    # Linux takes the wait in SECONDS.
    assert argv[argv.index("-W") + 1] == "2"


def test_the_bsd_ping_uses_h_for_the_ttl():
    from spdng import traceroute

    traceroute._PING_FLAVOUR = "bsd"
    argv = traceroute._ping_argv("ping", "1.1.1.1", ttl=5, timeout=2.0)
    assert "-h" in argv
    assert argv[argv.index("-h") + 1] == "5"
    # BSD takes the wait in MILLISECONDS.
    assert argv[argv.index("-W") + 1] == "2000"


def test_the_bsd_ping_does_not_use_t_for_the_ttl():
    """-t on macOS is the timeout, so using it for the TTL is wrong."""
    from spdng import traceroute

    traceroute._PING_FLAVOUR = "bsd"
    argv = traceroute._ping_argv("ping", "1.1.1.1", ttl=5, timeout=2.0)
    assert "-t" not in argv


def test_the_ping_argv_always_has_the_count_and_the_host():
    from spdng import traceroute

    for flavour in ("linux", "bsd"):
        traceroute._PING_FLAVOUR = flavour
        argv = traceroute._ping_argv("ping", "example.com", 3, 1.0)
        assert argv[0] == "ping"
        assert argv[-1] == "example.com"
        assert argv[argv.index("-c") + 1] == "1"


def test_the_ping_flavour_is_cached():
    from spdng import traceroute

    traceroute._PING_FLAVOUR = None
    first = traceroute._ping_flavour()
    second = traceroute._ping_flavour()
    assert first == second


# --------------------------------------------------------------------------
# the ping output parsers
# --------------------------------------------------------------------------

@pytest.mark.parametrize("output,expected", [
    ("64 bytes from 1.1.1.1: icmp_seq=1 ttl=50 time=29.1 ms", "1.1.1.1"),
    ("64 bytes from 1.1.1.1: icmp_seq=0 ttl=50 time=29.166 ms", "1.1.1.1"),
])
def test_the_reply_address_parses_on_both_platforms(output, expected):
    m = re.search(r"from\s+(\S+?):?\s+icmp_seq=\d+", output)
    assert m.group(1).rstrip(":") == expected


@pytest.mark.parametrize("output,expected", [
    ("From 192.168.1.1 icmp_seq=1 Time to live exceeded", "192.168.1.1"),
    # macOS puts a colon after the address.
    ("From 192.168.1.1: icmp_seq=0 Time to live exceeded", "192.168.1.1"),
])
def test_the_ttl_exceeded_address_parses_on_both_platforms(output, expected):
    m = re.search(r"From\s+(\S+?):?\s+icmp_seq=\d+", output)
    assert m.group(1).rstrip(":") == expected


def test_the_address_has_no_trailing_colon():
    """A trailing colon makes the address unparseable as an IP."""
    from spdng.traceroute import _is_ip

    m = re.search(r"From\s+(\S+?):?\s+icmp_seq=\d+",
                  "From 192.168.1.1: icmp_seq=0 Time to live exceeded")
    assert _is_ip(m.group(1).rstrip(":"))


@pytest.mark.parametrize("output,expected", [
    ("64 bytes from 1.1.1.1: icmp_seq=1 ttl=50 time=29.1 ms", 29.1),
    ("64 bytes from 1.1.1.1: icmp_seq=0 ttl=50 time=29.166 ms", 29.166),
    ("64 bytes from 1.1.1.1: icmp_seq=0 ttl=50 time=29.166 ms (1 ms)", 29.166),
])
def test_the_rtt_parses_on_both_platforms(output, expected):
    m = re.search(r"time[=<]\s*([0-9.]+)\s*ms", output)
    assert float(m.group(1)) == pytest.approx(expected)
