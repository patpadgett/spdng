"""LibreSpeed provider.

LibreSpeed is the open-source speedtest server. It matters for two
reasons: the public server list is community-run and self-hostable, and
because a LibreSpeed instance you control is the only way to measure
against a *specific* vantage point (a VPS in another country, say) rather
than than the nearest one.

The protocol is simple and stable:

    GET  {server}/empty.php?   -> latency probe
    GET  {server}/garbage.php?ckSize=N -> download (N megabytes of junk)
    POST {server}/empty.php    -> upload

The public server list lives at
https://librespeed.org/backend-servers/servers.php
and each entry names its own download and upload paths, which we honour
rather than assuming.

`--provider librespeed --server https://my-own-box.example/backend` lets
you point at your own instance.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from ..measure import (
    DEFAULT_UA,
    LatencyResult,
    ThroughputResult,
    download_worker,
    upload_worker,
)
from ..servers import Server, haversine_km
from .base import Provider, ProviderError

PUBLIC_SERVER_LIST = "https://librespeed.org/backend-servers/servers.php"


class LibreSpeedProvider(Provider):
    id = "librespeed"
    label = "LibreSpeed"
    infrastructure = "community-run open-source servers"
    has_server_list = True

    def __init__(self, ua: str = DEFAULT_UA) -> None:
        self.ua = ua
        #: Where the user is, as (lat, lon). Set by the CLI so we can rank
        #: the public server list by distance.
        self._origin: tuple[float, float] | None = None

    def set_origin(self, lat: float, lon: float) -> None:
        self._origin = (lat, lon)

    #: Approximate coordinates for the cities that appear in the public
    #: LibreSpeed server list, so we can rank by distance the way we do for
    #: Ookla. A city we do not know gets distance 0 and sorts first, which
    #: is why we only apply this ranking when we actually recognised the
    #: city.
    CITY_COORDS = {
        "amsterdam": (52.3676, 4.9041),
        "atlanta": (33.7490, -84.3880),
        "athens": (37.9838, 23.7275),
        "auckland": (-36.8485, 174.7633),
        "barcelona": (41.3851, 2.1734),
        "berlin": (52.5200, 13.4050),
        "brisbane": (-27.4698, 153.0251),
        "brussels": (50.8503, 4.3517),
        "bucharest": (44.4268, 26.1025),
        "budapest": (47.4979, 19.0402),
        "buenos aires": (-34.6037, -58.3816),
        "cape town": (-33.9249, 18.4241),
        "chicago": (41.8781, -87.6298),
        "cologne": (50.9375, 6.9603),
        "copenhagen": (55.6761, 12.5683),
        "dallas": (32.7767, -96.7970),
        "denver": (39.7392, -104.9903),
        "dublin": (53.3498, -6.2603),
        "frankfurt": (50.1109, 8.6821),
        "helsinki": (60.1699, 24.9384),
        "hong kong": (22.3193, 114.1694),
        "istanbul": (41.0082, 28.9784),
        "johannesburg": (-26.2041, 28.0473),
        "lisbon": (38.7223, -9.1393),
        "london": (51.5074, -0.1278),
        "los angeles": (34.0522, -118.2437),
        "lyon": (45.7640, 4.8357),
        "madrid": (40.4168, -3.7038),
        "melbourne": (-37.8136, 144.9631),
        "milan": (45.4642, 9.1900),
        "montreal": (45.5017, -73.5673),
        "munich": (48.1351, 11.5820),
        "new york": (40.7128, -74.0060),
        "oslo": (59.9139, 10.7522),
        "paris": (48.8566, 2.3522),
        "phoenix": (33.4484, -112.0740),
        "portland": (45.5152, -122.6784),
        "prague": (50.0755, 14.4378),
        "rome": (41.9028, 12.4964),
        "san francisco": (37.7749, -122.4194),
        "sao paulo": (-23.5505, -46.6333),
        "seattle": (47.6062, -122.3321),
        "singapore": (1.3521, 103.8198),
        "stockholm": (59.3293, 18.0686),
        "sydney": (-33.8688, 151.2093),
        "taipei": (25.0330, 121.5654),
        "tokyo": (35.6762, 139.6503),
        "toronto": (43.6532, -79.3832),
        "vienna": (48.2082, 16.3738),
        "warsaw": (52.2297, 21.0122),
        "zurich": (47.3769, 8.5417),
    }

    def _coords_for(self, name: str) -> tuple[float, float] | None:
        """Coordinates for a LibreSpeed server name, if we know the city."""
        city = name.split("(")[0].strip().rstrip(",")
        if "," in city:
            city = city.rsplit(",", 1)[0].strip()
        key = city.lower()
        if key in self.CITY_COORDS:
            return self.CITY_COORDS[key]
        for known in self.CITY_COORDS:
            if key.startswith(known):
                return self.CITY_COORDS[known]
        return None

    def list_servers(self, limit: int = 10) -> list[Server]:
        try:
            req = urllib.request.Request(PUBLIC_SERVER_LIST, headers={
                "User-Agent": self.ua, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = json.load(resp)
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(
                f"could not fetch LibreSpeed server list: {exc}") from exc
        if not isinstance(raw, list):
            raise ProviderError("unexpected LibreSpeed server list payload")

        servers: list[Server] = []
        for item in raw:
            server_url = item.get("server", "")
            if not server_url:
                continue
            name = item.get("name", "")
            sponsor = item.get("sponsorName", "") or name.split("(")[0].strip()
            # Each entry names its own endpoint paths, relative to the
            # server root. Some include the "backend/" prefix and some do
            # not, and the server URL may or may not already end in
            # "/backend". Rather than guess, we resolve each path against
            # the server URL and let the HTTP layer sort it out.
            base = server_url.rstrip("/")
            dl = item.get("dlURL") or "backend/garbage.php"
            ul = item.get("ulURL") or "backend/empty.php"
            ping = item.get("pingURL") or "backend/empty.php"
            servers.append(Server(
                id=str(item.get("id", "")),
                host=_host_from_url(base),
                sponsor=sponsor[:24],
                name=name[:32],
                country=_country_from_name(name),
                cc="",
                lat=0.0,
                lon=0.0,
                distance_km=0.0,
                url=base,
                provider=self.id,
                endpoints={
                    "download": _join(base, dl),
                    "upload": _join(base, ul),
                    "ping": _join(base, ping),
                },
            ))

        # Rank by distance from the user when we can locate them. The
        # LibreSpeed list has no coordinates, so we match the city in the
        # server name against a small table.
        if self._origin is not None:
            o_lat, o_lon = self._origin
            for srv in servers:
                coords = self._coords_for(srv.name)
                if coords:
                    srv.lat, srv.lon = coords
                    srv.distance_km = haversine_km(o_lat, o_lon, *coords)
            # Servers we could not locate keep distance 0 and would sort
            # first, which would be wrong. Push them to the back.
            located = [s_ for s_ in servers if s_.distance_km > 0]
            unknown = [s_ for s_ in servers if s_.distance_km == 0]
            servers = sorted(located, key=lambda s_: s_.distance_km) + unknown

        return servers[:limit]

    def _endpoints(self, server: Server) -> dict[str, str]:
        if server.endpoints:
            return server.endpoints
        base = server.url.rstrip("/")
        return {
            "download": f"{base}/garbage.php",
            "upload": f"{base}/empty.php",
            "ping": f"{base}/empty.php",
        }

    def measure_latency(self, server: Server, count: int = 5,
                        timeout: float = 10.0) -> LatencyResult:
        """Measure latency over a warm, persistent connection.

        The first exchange pays the TCP and TLS handshake, so it is
        discarded rather than reported as a latency sample.
        """
        from ..httpconn import LatencyConnection, ProbeError, ok

        result = LatencyResult()
        url = self._endpoints(server)["ping"]
        parts = urllib.parse.urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        endpoint = parts.path or "/empty.php"
        conn = LatencyConnection(origin, ua=self.ua, timeout=timeout,
                                 validator=ok)
        try:
            if conn.warm_up(endpoint):
                result.attempts += 1
            for i in range(count):
                result.attempts += 1
                stamp = int(time.time() * 1000)
                try:
                    rtt = conn.probe(endpoint, f"{stamp}.{i}")
                    result.samples_ms.append(rtt)
                except ProbeError:
                    result.errors += 1
        finally:
            conn.close()
        return result

    def measure_download(self, server: Server, duration: float = 10.0,
                         threads: int = 4) -> ThroughputResult:
        result = ThroughputResult(threads=threads)
        stop_at = time.perf_counter() + duration
        counter = [0]
        errors = [0]
        url = self._endpoints(server)["download"]
        workers = []
        for i in range(threads):
            # ckSize is the payload size in megabytes.
            size = 5 + (i * 5)
            target = f"{url}{'&' if '?' in url else '?'}ckSize={size}"
            t = threading.Thread(target=download_worker,
                                 args=(target, self.ua, stop_at, counter,
                                       errors),
                                 daemon=True)
            t.start()
            workers.append(t)
        start = time.perf_counter()
        for t in workers:
            t.join(timeout=duration + 15)
        result.seconds = time.perf_counter() - start
        result.bytes_transferred = counter[0]
        result.errors = errors[0]
        return result

    def measure_upload(self, server: Server, duration: float = 10.0,
                       threads: int = 2) -> ThroughputResult:
        result = ThroughputResult(threads=threads)
        stop_at = time.perf_counter() + duration
        counter = [0]
        errors = [0]
        url = self._endpoints(server)["upload"]
        workers = []
        for _ in range(threads):
            t = threading.Thread(target=upload_worker,
                                 args=(url, self.ua, stop_at, counter, errors),
                                 daemon=True)
            t.start()
            workers.append(t)
        start = time.perf_counter()
        for t in workers:
            t.join(timeout=duration + 15)
        result.seconds = time.perf_counter() - start
        result.bytes_transferred = counter[0]
        result.errors = errors[0]
        return result



def _host_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return parsed.netloc or url


def _join(base: str, path: str) -> str:
    """Resolve an endpoint path against a server URL.

    The LibreSpeed server list is inconsistent: some entries give
    "backend/garbage.php" and some give "garbage.php", and the server URL
    may or may not already include the "/backend" suffix. Joining naively
    produces URLs like https://host/garbage.php, which 404s because the
    file actually lives at https://host/backend/garbage.php.

    So: if the server URL already ends in the path's first segment, do not
    add it again.
    """
    base = base.rstrip("/")
    path = path.lstrip("/")
    if not path:
        return base
    first = path.split("/", 1)[0]
    if base.endswith("/" + first):
        return f"{base}/{path.split('/', 1)[1]}"
    return f"{base}/{path}"


def _country_from_name(name: str) -> str:
    """LibreSpeed names look like "Amsterdam, Netherlands (Sponsor)"."""
    if "(" in name:
        name = name.split("(")[0]
    parts = [p.strip() for p in name.split(",")]
    return parts[-1] if len(parts) > 1 else ""
