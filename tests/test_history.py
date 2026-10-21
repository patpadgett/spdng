"""Tests for the local history file."""
from __future__ import annotations

import json

import pytest

from spdng import history


def test_history_path_uses_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    p = history.history_path()
    assert str(p).startswith(str(tmp_path))
    assert p.name == "history.jsonl"


def test_history_path_falls_back_to_local_share(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    p = history.history_path()
    assert ".local" in str(p)


def test_append_and_load_roundtrip(tmp_path):
    f = tmp_path / "h.jsonl"
    e = history.HistoryEntry(timestamp=1000.0, provider="ookla",
                              server="Test 1", download_mbps=100.0,
                              upload_mbps=10.0, ping_ms=20.0)
    assert history.append(e, f) is True
    loaded = history.load(f)
    assert len(loaded) == 1
    assert loaded[0].download_mbps == 100.0
    assert loaded[0].server == "Test 1"


def test_append_creates_the_directory(tmp_path):
    f = tmp_path / "nested" / "deeper" / "h.jsonl"
    e = history.HistoryEntry(timestamp=1.0)
    assert history.append(e, f) is True
    assert f.exists()


def test_load_of_a_missing_file_is_empty(tmp_path):
    assert history.load(tmp_path / "nope.jsonl") == []


def test_load_skips_a_corrupt_line(tmp_path):
    """One bad line must not lose the rest of the file."""
    f = tmp_path / "h.jsonl"
    good = history.HistoryEntry(timestamp=1.0, download_mbps=50.0)
    with f.open("w") as fh:
        fh.write(json.dumps(good.to_dict()) + "\n")
        fh.write("{not json at all\n")
        fh.write("\n")
        fh.write(json.dumps(good.to_dict()) + "\n")
    loaded = history.load(f)
    assert len(loaded) == 2
    assert all(x.download_mbps == 50.0 for x in loaded)


def test_load_respects_the_limit(tmp_path):
    f = tmp_path / "h.jsonl"
    for i in range(10):
        history.append(history.HistoryEntry(timestamp=float(i),
                                            download_mbps=float(i)), f)
    loaded = history.load(f, limit=3)
    assert len(loaded) == 3
    # The most recent are kept.
    assert loaded[-1].timestamp == 9.0


def test_clear_removes_the_file(tmp_path):
    f = tmp_path / "h.jsonl"
    history.append(history.HistoryEntry(timestamp=1.0), f)
    assert history.clear(f) is True
    assert not f.exists()


def test_clear_of_a_missing_file_is_fine(tmp_path):
    assert history.clear(tmp_path / "nope.jsonl") is True


def test_entry_from_result():
    from spdng.measure import LatencyResult, SpeedTestResult

    class FakeServer:
        sponsor = "Sponsor"
        name = "Place"

    r = SpeedTestResult(server=FakeServer())
    r.download_mbps = 123.0
    r.upload_mbps = 12.0
    r.latency = LatencyResult(samples_ms=[9.0], errors=0, attempts=1)
    e = history.entry_from_result(r, "cloudflare")
    assert e.provider == "cloudflare"
    assert e.server == "Sponsor Place"
    assert e.download_mbps == 123.0
    assert e.ping_ms == 9.0


def test_entry_from_result_with_no_server():
    from spdng.measure import SpeedTestResult
    e = history.entry_from_result(SpeedTestResult(server=None))
    assert e.server == ""


def test_trend_of_nothing():
    t = history.trend([])
    assert t["count"] == 0


def test_trend_of_one_entry():
    e = history.HistoryEntry(timestamp=1.0, download_mbps=100.0)
    t = history.trend([e])
    assert t["count"] == 1
    assert t["download_min"] == 100.0
    assert t["download_max"] == 100.0
    assert "change_pct" not in t


def test_trend_reports_a_change():
    """The trend splits the history in half and compares the halves.

    With 5 entries [100, 100, 100, 200, 200] the split is at index 2, so
    the early half is [100, 100] (avg 100) and the late half is
    [100, 200, 200] (avg 166.67).
    """
    entries = [history.HistoryEntry(timestamp=float(i),
                                    download_mbps=float(v))
               for i, v in enumerate([100, 100, 100, 200, 200])]
    t = history.trend(entries)
    assert t["change_pct"] > 0
    assert t["early_avg"] == pytest.approx(100.0)
    assert t["late_avg"] == pytest.approx(500.0 / 3.0)


def test_trend_handles_a_zero_average_without_dividing_by_zero():
    entries = [history.HistoryEntry(timestamp=float(i), download_mbps=0.0)
               for i in range(4)]
    t = history.trend(entries)
    assert t["change_pct"] == 0.0
