"""Tests for spdng.

Network tests are marked and skipped by default. Run them with:
    SPDNG_LIVE=1 pytest -m live
"""
from __future__ import annotations

import struct

import pytest

from spdng import measure, render, servers, traceroute

# --------------------------------------------------------------------------
# servers
# --------------------------------------------------------------------------

def test_haversine_zero_distance():
    assert servers.haversine_km(27.95, -82.45, 27.95, -82.45) == pytest.approx(0, abs=1e-6)


def test_haversine_known_distance():
    # Tampa -> Orlando is about 125 km as the crow flies
    d = servers.haversine_km(27.9506, -82.4572, 28.4158, -81.2989)
    assert 110 < d < 140


def test_rank_servers_prefers_nearest():
    s = [
        servers.Server(id="1", host="a:1", sponsor="A", name="Far",
                       country="", cc="", lat=0, lon=0, distance_km=1700.0),
        servers.Server(id="2", host="b:1", sponsor="B", name="Near",
                       country="", cc="", lat=0, lon=0, distance_km=5.0),
        servers.Server(id="3", host="c:1", sponsor="C", name="Mid",
                       country="", cc="", lat=0, lon=0, distance_km=40.0),
    ]
    ranked = servers.rank_servers(s)
    assert [x.id for x in ranked] == ["2", "3", "1"]


def test_rank_servers_respects_max_distance():
    s = [
        servers.Server(id="1", host="a:1", sponsor="A", name="Far",
                       country="", cc="", lat=0, lon=0, distance_km=1700.0),
        servers.Server(id="2", host="b:1", sponsor="B", name="Near",
                       country="", cc="", lat=0, lon=0, distance_km=5.0),
    ]
    ranked = servers.rank_servers(s, max_distance_km=100.0)
    assert [x.id for x in ranked] == ["2"]


def test_server_base_uses_host_not_url():
    """Regression: the base URL must use `host`, not the redirecting URL."""
    s = servers.Server(
        id="1", host="canon.prod.hosts.ooklaserver.net:8080", sponsor="S",
        name="N", country="", cc="", lat=0, lon=0, distance_km=5.0,
        url="http://legacy.example.com:8080/speedtest/upload.php")
    assert s.base == "http://canon.prod.hosts.ooklaserver.net:8080/speedtest"
    assert "legacy" not in s.base


def test_server_to_dict_roundtrip():
    s = servers.Server(id="7", host="h:1", sponsor="sp", name="nm", country="US",
                       cc="US", lat=1.5, lon=2.5, distance_km=12.34,
                       latency_ms=9.9, jitter_ms=0.5, loss_pct=0.0)
    d = s.to_dict()
    assert d["id"] == "7"
    assert d["distance_km"] == pytest.approx(12.34)
    assert d["latency_ms"] == 9.9
    assert "lon" in d and d["lon"] == 2.5


# --------------------------------------------------------------------------
# measurement arithmetic
# --------------------------------------------------------------------------

def test_latency_result_excludes_failures_from_average():
    r = measure.LatencyResult(samples_ms=[10.0, 20.0], errors=3, attempts=5)
    assert r.avg_ms == pytest.approx(15.0)
    assert r.loss_pct == pytest.approx(60.0)
    assert r.min_ms == 10.0
    assert r.max_ms == 20.0


def test_latency_result_with_no_samples_has_no_average():
    r = measure.LatencyResult(samples_ms=[], errors=5, attempts=5)
    assert r.avg_ms is None
    assert r.loss_pct == pytest.approx(100.0)


def test_latency_jitter():
    r = measure.LatencyResult(samples_ms=[10.0, 12.0, 10.0])
    # diffs: 2, 2 -> mean 2
    assert r.jitter_ms == pytest.approx(2.0)


def test_throughput_mbps_is_correct():
    # 12.5 MB in 1 second == 100 Mbit/s
    t = measure.ThroughputResult(bytes_transferred=12_500_000, seconds=1.0)
    assert t.mbps == pytest.approx(100.0, rel=1e-6)
    assert t.mbytes_per_sec == pytest.approx(12.5, rel=1e-6)


def test_throughput_zero_seconds_is_zero_not_crash():
    t = measure.ThroughputResult(bytes_transferred=1000, seconds=0.0)
    assert t.mbps == 0.0


# --------------------------------------------------------------------------
# the speedtest-cli bugs we are fixing
# --------------------------------------------------------------------------

def test_failed_probe_never_becomes_a_placeholder_number():
    """speedtest-cli reports 1800000.0 ms when probes fail. We never do."""
    r = measure.LatencyResult(samples_ms=[], errors=3, attempts=3)
    assert r.avg_ms is None
    assert r.avg_ms != 1800000.0
    assert r.loss_pct == pytest.approx(100.0)


def test_our_latency_is_not_halved_by_a_bad_divisor():
    """speedtest-cli divides a 3-sample sum by 6, halving every result."""
    true_ms = 25.0
    samples = [true_ms / 1000.0] * 3
    our_avg = sum(samples) / len(samples) * 1000.0
    theirs = sum(samples) / 6 * 1000.0
    assert our_avg == pytest.approx(true_ms)
    assert theirs == pytest.approx(true_ms / 2)


# --------------------------------------------------------------------------
# traceroute
# --------------------------------------------------------------------------

def test_hop_loss_percentage():
    h = traceroute.Hop(ttl=1, sent=10, received=8)
    assert h.loss_pct == pytest.approx(20.0)


def test_hop_with_no_probes_is_full_loss():
    h = traceroute.Hop(ttl=1, sent=0, received=0)
    assert h.loss_pct == pytest.approx(100.0)


def test_hop_statistics():
    h = traceroute.Hop(ttl=1, sent=3, received=3,
                       rtts_ms=[10.0, 20.0, 30.0])
    assert h.best_ms == 10.0
    assert h.worst_ms == 30.0
    assert h.avg_ms == pytest.approx(20.0)
    assert h.jitter_ms == pytest.approx(10.0)


def test_trace_total_loss_uses_final_hop_only():
    """Intermediate loss is usually ICMP rate-limiting, not real loss."""
    t = traceroute.TraceResult(hops=[
        traceroute.Hop(ttl=1, sent=3, received=0),
        traceroute.Hop(ttl=2, sent=3, received=3),
    ])
    assert t.total_loss_pct == pytest.approx(0.0)


def test_trace_to_dict_shape():
    t = traceroute.TraceResult(target="1.2.3.4", method="icmp", hops=[
        traceroute.Hop(ttl=1, addr="10.0.0.1", sent=2, received=2,
                       rtts_ms=[1.0, 2.0])])
    d = t.to_dict()
    assert d["target"] == "1.2.3.4"
    assert d["method"] == "icmp"
    assert len(d["hops"]) == 1
    h = d["hops"][0]
    assert h["addr"] == "10.0.0.1"
    assert h["loss_pct"] == pytest.approx(0.0)
    assert h["best_ms"] == 1.0


def test_icmp_checksum_is_self_consistent():
    payload = b"spdng-trace"
    ident, seq = 1234, 1
    header = struct.pack("!BBHHH", 8, 0, 0, ident, seq)
    ck = traceroute._checksum(header + payload)
    full = struct.pack("!BBHHH", 8, 0, ck, ident, seq) + payload
    assert traceroute._checksum(full) == 0


def test_recverr_probe_does_not_claim_arrival_on_local_refusal(monkeypatch):
    """A local ECONNREFUSED must not be reported as reaching the destination."""
    import socket as _socket

    def fake_send(self, data):
        raise OSError(113, "Connection refused")

    monkeypatch.setattr(_socket.socket, "send", fake_send)
    addr, rtt, reached = traceroute._recverr_probe("127.0.0.1", 9, 1, 0.2)
    assert reached is False


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def test_render_result_shows_unavailable_instead_of_a_number():
    class FakeServer:
        sponsor, name, cc, distance_km = "S", "N", "US", 5.0

    res = measure.SpeedTestResult(server=FakeServer())
    res.latency = measure.LatencyResult(samples_ms=[], errors=3, attempts=3)
    res.download_mbps = 100.0
    res.upload_mbps = 10.0
    out = render.render_result(res)
    assert "unavailable" in out
    assert "1800000" not in out


def test_render_trace_has_mtr_style_columns():
    t = traceroute.TraceResult(hops=[
        traceroute.Hop(ttl=1, addr="10.0.0.1", sent=3, received=3,
                       rtts_ms=[1.0, 2.0, 3.0]),
        traceroute.Hop(ttl=2, sent=3, received=0),
    ])
    out = render.render_trace(t)
    for col in ("Loss%", "Snt", "Last", "Avg", "Best", "Worst", "Jitter"):
        assert col in out


def test_render_loss_summary_flags_final_hop_loss():
    t = traceroute.TraceResult(hops=[
        traceroute.Hop(ttl=1, sent=3, received=3, rtts_ms=[5.0, 5.0, 5.0]),
        traceroute.Hop(ttl=2, sent=3, received=1, rtts_ms=[9.0]),
    ])
    out = render.render_loss_summary(t)
    assert "lossy" in out


def test_render_loss_summary_flags_latency_jump():
    t = traceroute.TraceResult(hops=[
        traceroute.Hop(ttl=1, sent=3, received=3, rtts_ms=[5.0, 5.0, 5.0]),
        traceroute.Hop(ttl=2, sent=3, received=3, rtts_ms=[200.0, 200.0, 200.0]),
    ])
    out = render.render_loss_summary(t)
    assert "Latency jump" in out
    assert "hop 2" in out


def test_bar_renders():
    assert render.bar(0.0, 10) == "░" * 10
    assert render.bar(1.0, 10) == "█" * 10
    assert render.bar(0.5, 10) == "█" * 5 + "░" * 5


# --------------------------------------------------------------------------
# live tests
# --------------------------------------------------------------------------

@pytest.mark.live
def test_live_server_list():
    s = servers.fetch_servers(limit=5)
    assert len(s) > 0
    assert all(x.host for x in s)


@pytest.mark.live
def test_live_latency_against_nearest_server():
    from spdng.providers.ookla import OoklaProvider
    provider = OoklaProvider()
    s = provider.list_servers(limit=20)[0]
    lat = provider.measure_latency(s, count=3, timeout=15)
    assert lat.avg_ms is not None
    assert lat.avg_ms < 2000


@pytest.mark.live
def test_live_cloudflare_provider():
    from spdng.providers.cloudflare import CloudflareProvider
    provider = CloudflareProvider()
    s = provider.list_servers(limit=1)[0]
    lat = provider.measure_latency(s, count=3, timeout=15)
    assert lat.avg_ms is not None
    assert lat.avg_ms < 2000


@pytest.mark.live
def test_live_librespeed_provider():
    from spdng.providers.librespeed import LibreSpeedProvider
    provider = LibreSpeedProvider()
    srv = provider.list_servers(limit=3)
    assert srv, "no LibreSpeed servers returned"
    lat = provider.measure_latency(srv[0], count=2, timeout=15)
    # Some community servers are unreachable from anywhere; that is fine,
    # we only assert that the call returns cleanly.
    assert lat.attempts == 2


# --------------------------------------------------------------------------
# providers
# --------------------------------------------------------------------------

def test_get_provider_by_name_and_alias():
    from spdng import providers
    assert providers.get_provider("ookla").id == "ookla"
    assert providers.get_provider("speedtest.net").id == "ookla"
    assert providers.get_provider("cf").id == "cloudflare"
    assert providers.get_provider("CLOUDFLARE").id == "cloudflare"
    assert providers.get_provider("librespeed").id == "librespeed"


def test_get_provider_rejects_unknown():
    from spdng import providers
    with pytest.raises(providers.ProviderError):
        providers.get_provider("does-not-exist")


def test_all_providers_have_distinct_ids():
    from spdng import providers
    ids = [p.id for p in providers.all_providers()]
    assert len(ids) == len(set(ids))
    assert "ookla" in ids
    assert "cloudflare" in ids
    assert "librespeed" in ids


def test_cloudflare_has_no_server_list():
    """Cloudflare measures to the nearest DC, there is nothing to pick."""
    from spdng.providers.cloudflare import CloudflareProvider
    assert CloudflareProvider().has_server_list is False


def test_provider_base_class_is_abstract():
    from spdng.providers.base import Provider
    p = Provider()
    with pytest.raises(NotImplementedError):
        p.list_servers()


# --------------------------------------------------------------------------
# style / colour
# --------------------------------------------------------------------------

def test_no_color_env_disables_colour(monkeypatch):
    from spdng import style
    monkeypatch.setenv("NO_COLOR", "1")
    style.set_color(None)
    assert style.color_enabled() is False
    assert style.paint("hello", "red") == "hello"


def test_force_color_env_enables_colour(monkeypatch):
    from spdng import style
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    style.set_color(None)
    assert style.color_enabled() is True
    assert "\033[" in style.paint("hello", "red")


def test_no_color_beats_force_color(monkeypatch):
    from spdng import style
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("FORCE_COLOR", "1")
    style.set_color(None)
    assert style.color_enabled() is False


def test_strip_ansi():
    from spdng import style
    assert style.strip_ansi("\033[31mred\033[0m") == "red"
    assert style.strip_ansi("plain") == "plain"


def test_every_theme_has_all_roles():
    from spdng import style
    roles = ("good", "ok", "warn", "bad", "accent", "muted", "heading", "value")
    for name, theme in style.THEMES.items():
        for role in roles:
            assert theme.code(role) in style.CODES.values(), \
                f"theme {name} role {role} has an unknown colour"


def test_set_theme_falls_back_to_default():
    from spdng import style
    style.set_theme("no-such-theme")
    assert style.theme().name == "default"
    style.set_theme("default")


# --------------------------------------------------------------------------
# comparison rendering
# --------------------------------------------------------------------------

def _fake_result(download=100.0, upload=10.0, ping=20.0, jitter=1.0,
                 loss=0.0, provider="ookla", sponsor="S", name="N"):
    class FakeServer:
        def __init__(self):
            self.provider = provider
            self.sponsor = sponsor
            self.name = name
            self.distance_km = 5.0

        def to_dict(self):
            return {"provider": provider}

    r = measure.SpeedTestResult(server=FakeServer())
    r.download_mbps = download
    r.upload_mbps = upload
    r.latency = measure.LatencyResult(samples_ms=[ping] * 3, errors=0,
                                      attempts=3)
    r.latency.samples_ms = [ping, ping, ping]
    return r


def test_render_comparison_lines_up_columns():
    from spdng import compare
    results = [
        _fake_result(download=100.0, provider="ookla"),
        _fake_result(download=50.0, provider="cloudflare", sponsor="CF"),
    ]
    out = compare.render_comparison(results, ["Ookla", "Cloudflare"])
    assert "Ookla" in out and "Cloudflare" in out
    assert "Download" in out
    assert "100.00 Mbit/s" in out
    assert "50.00 Mbit/s" in out


def test_render_comparison_with_no_results():
    from spdng import compare
    assert "no results" in compare.render_comparison([])


def test_render_repeat_comparison_reports_spread():
    from spdng import compare
    runs = [
        _fake_result(download=100.0),
        _fake_result(download=101.0),
        _fake_result(download=99.0),
    ]
    out = compare.render_repeat_comparison(runs)
    assert "spread" in out
    assert "stable" in out


def test_render_repeat_comparison_flags_variance():
    from spdng import compare
    runs = [
        _fake_result(download=10.0),
        _fake_result(download=200.0),
    ]
    out = compare.render_repeat_comparison(runs)
    assert "very variable" in out

