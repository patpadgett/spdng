"""Edge-case hunting.

Every one of these is a thing that actually goes wrong. They are written
as tests because "it worked on my machine" is how the old tool ended up
reporting 1800000.0 ms.

The categories:
  * degenerate input (zero, negative, absurd)
  * network failure (timeout, refused, DNS failure, empty response)
  * environment (no ping, no raw sockets, no network at all)
  * output (JSON must always be valid, colour must never leak into it)
  * process (SIGINT, a provider that dies mid-run)
"""
from __future__ import annotations

import io
import json
import socket
from contextlib import redirect_stdout

import pytest

from spdng import compare, measure, providers, render, servers, style, traceroute

# --------------------------------------------------------------------------
# degenerate input
# --------------------------------------------------------------------------

def test_latency_of_zero_samples():
    r = measure.LatencyResult()
    assert r.avg_ms is None
    assert r.min_ms is None
    assert r.max_ms is None
    assert r.jitter_ms is None
    assert r.loss_pct == pytest.approx(100.0)


def test_throughput_of_zero_bytes():
    t = measure.ThroughputResult(bytes_transferred=0, seconds=10.0)
    assert t.mbps == 0.0


def test_throughput_of_zero_seconds_does_not_divide_by_zero():
    t = measure.ThroughputResult(bytes_transferred=1000, seconds=0.0)
    assert t.mbps == 0.0


def test_throughput_with_negative_seconds():
    """A clock that went backwards must not produce a negative speed."""
    t = measure.ThroughputResult(bytes_transferred=1000, seconds=-1.0)
    assert t.mbps == 0.0


def test_server_with_no_host():
    s = servers.Server(id="x", host="", sponsor="S", name="N", country="",
                       cc="", lat=0, lon=0, distance_km=0)
    assert s.base == ""


def test_haversine_with_identical_points():
    assert servers.haversine_km(10, 20, 10, 20) == pytest.approx(0, abs=1e-9)


def test_haversine_antipodal():
    d = servers.haversine_km(0, 0, 0, 180)
    assert 20000 < d < 20150


def test_haversine_across_the_antimeridian():
    """Two points either side of 180 degrees are close, not far apart."""
    d = servers.haversine_km(0, 179.9, 0, -179.9)
    assert d < 25


def test_rank_servers_with_empty_list():
    assert servers.rank_servers([]) == []


def test_rank_servers_max_distance_excludes_everything():
    s = [servers.Server(id="1", host="h:1", sponsor="S", name="N", country="",
                         cc="", lat=0, lon=0, distance_km=100.0)]
    assert servers.rank_servers(s, max_distance_km=10.0) == []


# --------------------------------------------------------------------------
# network failure
# --------------------------------------------------------------------------

def test_latency_against_a_dead_host_reports_loss_not_a_number():
    """The old tool's worst bug. A dead host must yield loss, not 1800000."""
    lat = measure.LatencyResult(samples_ms=[], errors=3, attempts=3)
    assert lat.avg_ms is None
    assert lat.loss_pct == pytest.approx(100.0)
    assert "1800000" not in str(lat.avg_ms)


def test_probe_loss_against_a_closed_port():
    """Port 1 on localhost should refuse; we must not hang or crash."""
    loss, rtts = traceroute.probe_loss_and_latency(
        "127.0.0.1", port=1, count=2, timeout=0.5)
    assert loss == pytest.approx(100.0)
    assert rtts == []


def test_probe_loss_against_a_listening_port():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        loss, rtts = traceroute.probe_loss_and_latency(
            "127.0.0.1", port=port, count=2, timeout=2.0)
        assert loss == pytest.approx(0.0)
        assert len(rtts) == 2
    finally:
        srv.close()


def test_traceroute_to_an_unresolvable_host():
    with pytest.raises(socket.gaierror):
        socket.gethostbyname("this-host-does-not-exist.invalid")


def test_resolved_ip_tolerates_a_bad_host():
    """_resolved_ip must never raise; it is called on a hot path."""
    assert traceroute._resolved_ip("this-host-does-not-exist.invalid")


def test_raw_icmp_probe_raises_cleanly_without_privileges():
    """On a box without CAP_NET_RAW this must fail, not crash the process."""
    if traceroute.has_raw_icmp():
        pytest.skip("we have raw sockets here")
    with pytest.raises(PermissionError):
        traceroute._raw_icmp_probe("127.0.0.1", ttl=1, seq=1, timeout=0.5)


def test_traceroute_with_zero_hops():
    t = traceroute.traceroute("127.0.0.1", max_hops=0, probes=1, timeout=0.2)
    assert t.hops == []
    assert t.destination_reached is False


def test_traceroute_with_zero_probes():
    t = traceroute.traceroute("127.0.0.1", max_hops=2, probes=0, timeout=0.2)
    for hop in t.hops:
        assert hop.sent == 0
        assert hop.loss_pct == pytest.approx(100.0)


def test_trace_to_dict_with_no_hops():
    t = traceroute.TraceResult(target="x", method="udp")
    d = t.to_dict()
    assert d["hops"] == []
    json.dumps(d)  # must be serialisable


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------

def test_json_output_never_contains_ansi():
    """Colour must never leak into JSON, or downstream parsers break."""
    from spdng.measure import LatencyResult, SpeedTestResult
    r = SpeedTestResult(server=None)
    r.download_mbps = 100.0
    r.latency = LatencyResult(samples_ms=[10.0], errors=0, attempts=1)
    payload = r.to_dict()
    text = json.dumps(payload)
    assert "\033[" not in text
    assert style.strip_ansi(text) == text


def test_json_output_is_valid_for_an_empty_result():
    r = measure.SpeedTestResult(server=None)
    payload = r.to_dict()
    assert json.loads(json.dumps(payload))["download_mbps"] == 0.0


def test_render_result_with_no_server():
    r = measure.SpeedTestResult(server=None)
    out = render.render_result(r)
    assert "Download" in out


def test_render_result_with_failed_latency_says_unavailable():
    r = measure.SpeedTestResult(server=None)
    r.latency = measure.LatencyResult(samples_ms=[], errors=3, attempts=3)
    out = render.render_result(r)
    assert "unavailable" in out
    assert "1800000" not in out


def test_render_trace_with_no_hops():
    t = traceroute.TraceResult(target="x", method="udp")
    out = render.render_trace(t)
    assert "Loss%" in out


def test_render_server_table_with_empty_list():
    """An empty list must say so, not print a header over nothing."""
    assert render.render_server_table([]) == "no servers found"


def test_render_server_table_truncates():
    s = [servers.Server(id=str(i), host=f"h{i}:1", sponsor="S" * 40,
                        name="N" * 40, country="", cc="", lat=0, lon=0,
                        distance_km=1.0) for i in range(30)]
    out = render.render_server_table(s, limit=5)
    assert len(out.split("\n")) == 7  # header + rule + 5 rows


def test_compare_with_one_result():
    r = measure.SpeedTestResult(server=None)
    r.download_mbps = 50.0
    out = compare.render_comparison([r], ["only"])
    assert "Download" in out


def test_compare_with_results_having_no_latency():
    r = measure.SpeedTestResult(server=None)
    r.download_mbps = 50.0
    out = compare.render_comparison([r], ["only"])
    assert "n/a" in out


# --------------------------------------------------------------------------
# environment
# --------------------------------------------------------------------------

def test_unprivileged_icmp_range_never_raises():
    rng = traceroute.unprivileged_icmp_range()
    assert rng is None or len(rng) == 2


def test_ping_binary_probe_without_ping(monkeypatch):
    """If ping is missing we must degrade, not crash."""
    monkeypatch.setattr("shutil.which", lambda _name: None)
    addr, samples, sent, exact = traceroute._ping_binary_probe(
        "127.0.0.1", ttl=1, timeout=0.2, count=1)
    assert addr is None
    assert samples == []
    # Nothing was sent, so nothing was sent. The caller must see that
    # as a hop with no replies, not as a hop with replies.
    assert sent == 0
    assert exact is False


def test_trace_falls_back_when_no_method_is_available(monkeypatch):
    """With no raw sockets, no ping, and no UDP, we must say so."""
    monkeypatch.setattr(traceroute, "has_raw_icmp", lambda: False)
    monkeypatch.setattr(traceroute, "has_ping_binary", lambda: False)
    monkeypatch.setattr(traceroute, "has_recverr", lambda: False)
    t = traceroute.traceroute("127.0.0.1", max_hops=1, probes=1, timeout=0.2)
    # No method at all: the trace says so rather than pretending.
    assert t.error
    assert t.method == "none"
    assert not t.hops


def test_trace_falls_back_to_udp_when_the_others_are_missing(monkeypatch):
    """With no raw sockets and no ping, UDP is the last resort."""
    monkeypatch.setattr(traceroute, "has_raw_icmp", lambda: False)
    monkeypatch.setattr(traceroute, "has_ping_binary", lambda: False)
    monkeypatch.setattr(traceroute, "has_recverr", lambda: True)
    t = traceroute.traceroute("127.0.0.1", max_hops=1, probes=1, timeout=0.2)
    assert t.method == "udp"


@pytest.mark.skipif(not traceroute.has_recverr(),
                    reason="IP_RECVERR is Linux-only")
def test_traceroute_method_udp_never_raises():
    t = traceroute.traceroute("127.0.0.1", port=1, max_hops=2, probes=1,
                              timeout=0.3, method="udp")
    assert t.hops is not None
    assert not t.error


def test_traceroute_method_udp_reports_when_it_cannot_work(monkeypatch):
    """Where IP_RECVERR does not exist, the UDP backend must not raise.

    macOS has no IP_RECVERR. Setting the socket option there raises
    ENOPROTOOPT, and that used to come straight out of the trace. The
    socket option is what fails, so that is what the test breaks.
    """
    import socket as _socket

    def no_recverr(self, level, option, value):
        raise OSError(42, "Protocol not available")

    monkeypatch.setattr(_socket.socket, "setsockopt", no_recverr)
    t = traceroute.traceroute("127.0.0.1", port=1, max_hops=2, probes=1,
                              timeout=0.3, method="udp")
    assert t.error
    assert not t.hops


# --------------------------------------------------------------------------
# process
# --------------------------------------------------------------------------

def test_moo_subcommand_exits_cleanly():
    from spdng.cli import main
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["moo", "hi"])
    assert rc == 0
    assert "hi" in buf.getvalue()


def test_unknown_provider_exits_with_an_error():
    from spdng.cli import main
    rc = main(["speedtest", "--provider", "nope"])
    assert rc == 2


def test_doctor_never_raises():
    from spdng.cli import main
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["doctor"])
    assert rc == 0
    assert "traceroute method" in buf.getvalue()


def test_color_never_leaks_when_disabled():
    style.set_color(False)
    try:
        assert style.paint("x", "red") == "x"
        out = render.render_result(measure.SpeedTestResult(server=None))
        assert "\033[" not in out
    finally:
        style.set_color(None)


def test_color_is_on_when_forced():
    style.set_color(True)
    try:
        assert "\033[" in style.paint("x", "red")
    finally:
        style.set_color(None)


# --------------------------------------------------------------------------
# provider robustness
# --------------------------------------------------------------------------

def test_provider_error_is_raised_for_an_unknown_name():
    with pytest.raises(providers.ProviderError):
        providers.get_provider("no-such-provider")


def test_librespeed_join_handles_both_path_shapes():
    from spdng.providers.librespeed import _join
    # server URL already ends in /backend
    assert _join("https://h/backend", "backend/garbage.php") == \
        "https://h/backend/garbage.php"
    # server URL does not end in /backend
    assert _join("https://h", "backend/garbage.php") == \
        "https://h/backend/garbage.php"
    # path already includes the prefix that the base has
    assert _join("https://h/backend", "garbage.php") == \
        "https://h/backend/garbage.php"
    # trailing slashes
    assert _join("https://h/", "garbage.php") == "https://h/garbage.php"


def test_librespeed_coords_lookup():
    from spdng.providers.librespeed import LibreSpeedProvider
    p = LibreSpeedProvider()
    assert p._coords_for("Amsterdam, Netherlands (Clouvider)") is not None
    assert p._coords_for("Nowhere, Atlantis (Nobody)") is None


def test_cloudflare_server_synthesises_a_host():
    from spdng.providers.cloudflare import CloudflareProvider
    s = CloudflareProvider().list_servers(limit=1)[0]
    assert s.host
    assert s.provider == "cloudflare"
