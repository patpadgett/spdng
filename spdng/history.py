"""Local history.

A speed test is a snapshot. The useful question is usually about the
history: is my connection getting worse, did that change after I moved
the router, is this the same as last Tuesday?

We keep a small JSON-lines file of past results and can show a trend.
The file is local, plain text, and easy to delete. Nothing is uploaded.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path


def history_path() -> Path:
    """Where we keep the history file.

    XDG_DATA_HOME if set, otherwise ~/.local/share/spdng/history.jsonl
    """
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        root = Path(base)
    else:
        root = Path.home() / ".local" / "share"
    return root / "spdng" / "history.jsonl"


@dataclass(slots=True)
class HistoryEntry:
    """One past measurement."""

    timestamp: float
    provider: str = "ookla"
    server: str = ""
    download_mbps: float | None = None
    upload_mbps: float | None = None
    ping_ms: float | None = None
    jitter_ms: float | None = None
    loss_pct: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> HistoryEntry:
        known = {f for f in cls.__slots__}
        return cls(**{k: v for k, v in d.items() if k in known})


def append(entry: HistoryEntry, path: Path | None = None) -> bool:
    """Add an entry to the history file. Returns False if it could not."""
    p = path or history_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry.to_dict()) + "\n")
        return True
    except OSError:
        return False


def load(path: Path | None = None, limit: int = 0) -> list[HistoryEntry]:
    """Read the history, oldest first. A corrupt line is skipped, not fatal."""
    p = path or history_path()
    if not p.exists():
        return []
    entries: list[HistoryEntry] = []
    try:
        with p.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(HistoryEntry.from_dict(json.loads(line)))
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue  # a corrupt line must not lose the whole file
    except OSError:
        return []
    if limit and len(entries) > limit:
        entries = entries[-limit:]
    return entries


def clear(path: Path | None = None) -> bool:
    p = path or history_path()
    try:
        if p.exists():
            p.unlink()
        return True
    except OSError:
        return False


def entry_from_result(result, provider_id: str = "ookla") -> HistoryEntry:
    """Build a history entry from a SpeedTestResult."""
    srv = getattr(result, "server", None)
    server = ""
    if srv is not None:
        server = f"{getattr(srv, 'sponsor', '')} {getattr(srv, 'name', '')}".strip()
    return HistoryEntry(
        timestamp=time.time(),
        provider=provider_id,
        server=server,
        download_mbps=result.download_mbps,
        upload_mbps=result.upload_mbps,
        ping_ms=result.latency.avg_ms,
        jitter_ms=result.latency.jitter_ms,
        loss_pct=result.latency.loss_pct,
    )


def trend(entries: list[HistoryEntry]) -> dict:
    """Summarise a set of entries: latest, average, and the direction."""
    if not entries:
        return {"count": 0}
    dls = [e.download_mbps for e in entries if e.download_mbps is not None]
    latest = entries[-1]
    result = {
        "count": len(entries),
        "latest": latest,
        "first": entries[0],
    }
    if not dls:
        return result
    result.update({
        "download_min": min(dls),
        "download_max": max(dls),
        "download_avg": sum(dls) / len(dls),
    })
    if len(dls) >= 2:
        half = max(1, len(dls) // 2)
        early = sum(dls[:half]) / half
        late = sum(dls[half:]) / (len(dls) - half)
        result["early_avg"] = early
        result["late_avg"] = late
        result["change_pct"] = ((late - early) / early * 100) if early else 0.0
    return result
