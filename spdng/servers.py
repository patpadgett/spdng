"""Server discovery and selection.

The whole point of this module is to NOT do what speedtest-cli 2.1.3 does:
fetch a static, non-geo-aware list of servers and pick by a latency number
that was computed wrong. We use the geo-aware API, we measure latency
ourselves, and we rank by distance first.
"""
from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass, field

DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:25.0) Gecko/20100101 Firefox/25.0"
)

SERVERS_API = "https://www.speedtest.net/api/js/servers"
CONFIG_API = "https://www.speedtest.net/speedtest-config.php"


@dataclass(slots=True)
class Server:
    """One candidate speedtest server."""

    id: str
    host: str
    sponsor: str
    name: str
    country: str
    cc: str
    lat: float
    lon: float
    distance_km: float
    url: str = ""
    latency_ms: float | None = None
    jitter_ms: float | None = None
    loss_pct: float | None = None
    https_functional: bool = True
    #: Which provider produced this server.
    provider: str = "ookla"
    #: Provider-specific endpoint paths, when they differ from the default.
    endpoints: dict[str, str] = field(default_factory=dict)

    @property
    def base(self) -> str:
        """Base URL of the server's /speedtest/ directory.

        We deliberately build this from `host`, NOT from the hostname in
        `url`. Ookla's `url` field points at the *legacy* hostname, which
        answers with a 307 redirect to the canonical
        `<host>.prod.hosts.ooklaserver.net`. Following that redirect costs
        an extra round trip and, measured on a live server, roughly doubled
        the latency (46 ms direct vs 128 ms via the redirect).

        `host` is already the canonical name, so we talk to it directly.
        """
        if not self.host:
            return ""
        return f"http://{self.host}/speedtest"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "host": self.host,
            "sponsor": self.sponsor,
            "name": self.name,
            "country": self.country,
            "cc": self.cc,
            "lat": self.lat,
            "lon": self.lon,
            "distance_km": round(self.distance_km, 2),
            "latency_ms": self.latency_ms,
            "jitter_ms": self.jitter_ms,
            "loss_pct": self.loss_pct,
        }


def _get(url: str, ua: str = DEFAULT_UA, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": ua,
        "Accept": "*/*",
        "Connection": "close",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km."""
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def get_client_location(timeout: int = 20, ua: str = DEFAULT_UA) -> tuple[float, float, dict]:
    """Return (lat, lon, raw_client_dict) for the calling IP."""
    raw = _get(f"{CONFIG_API}?x=0", ua=ua, timeout=timeout).decode("utf-8", "replace")
    # <client ip="..." lat="..." lon="..." isp="..." isprating="..." ... />
    import re
    m = re.search(r"<client\s+([^>]*?)/>", raw)
    if not m:
        raise RuntimeError("could not parse client location from speedtest-config.php")
    attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
    try:
        return float(attrs["lat"]), float(attrs["lon"]), attrs
    except (KeyError, ValueError) as exc:
        raise RuntimeError(f"client location missing lat/lon: {attrs}") from exc


def fetch_servers(
    limit: int = 100,
    timeout: int = 20,
    ua: str = DEFAULT_UA,
) -> list[Server]:
    """Fetch the geo-aware server list.

    Uses the JS API which returns distance in km from the caller, so we can
    rank by proximity without doing our own geolocation.
    """
    params = urllib.parse.urlencode({
        "engine": "js",
        "https_functional": "true",
        "limit": str(limit),
        "x": "0",
    })
    try:
        data = json.loads(_get(f"{SERVERS_API}?{params}", ua=ua, timeout=timeout))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"speedtest server list returned HTTP {exc.code} "
            f"(a browser User-Agent is required)"
        ) from exc
    if not isinstance(data, list):
        raise RuntimeError("unexpected server list payload")

    servers: list[Server] = []
    for item in data:
        try:
            servers.append(Server(
                id=str(item.get("id", "")),
                host=item.get("host", ""),
                sponsor=item.get("sponsor", ""),
                name=item.get("name", ""),
                country=item.get("country", ""),
                cc=item.get("cc", ""),
                lat=float(item.get("lat") or 0.0),
                lon=float(item.get("lon") or 0.0),
                distance_km=float(item.get("distance") or 0.0),
                url=item.get("url", ""),
                https_functional=bool(item.get("https_functional", True)),
            ))
        except (TypeError, ValueError):
            continue
    return servers


def rank_servers(
    servers: Iterable[Server],
    max_distance_km: float = 0.0,
) -> list[Server]:
    """Order servers: nearest first, then by name for stability.

    Distance dominates on purpose. A bandwidth test measures the link to
    the server, so a 40 km server with 30 ms latency beats a 1750 km server
    with 20 ms latency for the purposes of measuring your own throughput.
    """
    servers = list(servers)
    if max_distance_km > 0:
        servers = [s for s in servers if s.distance_km <= max_distance_km]
    return sorted(servers, key=lambda s: (s.distance_km, s.name))
