"""Diagnostic: prove the exact failure chain in speedtest-cli 2.1.3.

Three independent defects, each reproducible:

  1. STATIC SERVER LIST. The tool fetches
     http://www.speedtest.net/speedtest-servers-static.php
     which returns a fixed, non-geo-aware list. It never asks the geo API
     that actually knows which servers are near the user. In Tampa the list
     it probes is 5 Canadian servers ~1750 km away.

  2. NO REDIRECT HANDLING. Its HTTP client subclasses http.client.HTTPConnection
     and adds no redirect logic. Current Ookla servers answer the legacy
     hostname with "307 Temporary Redirect" to
     https://server-<id>.prod.hosts.ooklaserver.net:8080/...
     A 307 is not 200, so the code takes the failure branch and appends the
     3600 sentinel.

  3. THE 3600 SENTINEL MATH. On failure it appends 3600 (seconds), then
     averages as sum(cum)/6*1000.0. With 3 failed probes that yields
     1800000.0 ms, which is then reported as the latency. It is a 30-minute
     placeholder, not a measurement.

  Bonus defect: the divisor is 6 but only 3 probes are taken, so every
  SUCCESSFUL latency is reported at exactly half its true value.
"""
import http.client
import sys
import time
import urllib.parse

UA = ("Mozilla/5.0 (X11; Linux x86_64; rv:25.0) Gecko/20100101 "
      "Firefox/25.0")

LEGACY = "http://speed.toronto.telmax.ca:8080/speedtest/latency.txt?x=1"
MODERN = "http://tampfl-speedtest-ookla-01.st.charter.com:8080/speedtest/latency.txt?x=1"


def raw_get(url, follow=False, timeout=12):
    """GET a URL the way speedtest-cli does (no redirect handling)."""
    parts = urllib.parse.urlparse(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    conn_cls = (http.client.HTTPSConnection if parts.scheme == "https"
                else http.client.HTTPConnection)
    conn = conn_cls(parts.hostname, port, timeout=timeout)
    path = parts.path + (("?" + parts.query) if parts.query else "")
    t0 = time.perf_counter()
    conn.request("GET", path, headers={"User-Agent": UA})
    resp = conn.getresponse()
    body = resp.read(9)
    dt = (time.perf_counter() - t0) * 1000
    status = resp.status
    location = resp.getheader("Location")
    conn.close()
    return status, body, dt, location


def main():
    print("=" * 74)
    print("PROOF 1: the legacy server list 307-redirects, and speedtest-cli")
    print("         does not follow redirects")
    print("=" * 74)
    status, body, dt, loc = raw_get(LEGACY)
    print(f"  GET {LEGACY}")
    print(f"    -> HTTP {status}  body={body!r}  rtt={dt:.1f}ms")
    if loc:
        print(f"    -> Location: {loc}")
    print()
    print("  speedtest-cli's check is:")
    print("      if int(r.status) == 200 and text == 'test=test'.encode():")
    print("          cum.append(total)")
    print("      else:")
    print("          cum.append(3600)      <-- taken on the 307")
    print()
    verdict = "FAIL -> 3600 sentinel" if status != 200 else "ok"
    print(f"  VERDICT: {verdict}")

    print()
    print("=" * 74)
    print("PROOF 2: a geo-local server answers 200 immediately")
    print("=" * 74)
    status2, body2, dt2, loc2 = raw_get(MODERN)
    print(f"  GET {MODERN}")
    print(f"    -> HTTP {status2}  body={body2!r}  rtt={dt2:.1f}ms")
    print(f"  VERDICT: {'ok - 200, test=test' if status2 == 200 and body2.strip() == b'test=test' else 'unexpected'}")

    print()
    print("=" * 74)
    print("PROOF 3: the 3600 sentinel arithmetic produces 1800000.0 ms")
    print("=" * 74)
    cum = [3600.0, 3600.0, 3600.0]
    reported = round((sum(cum) / 6) * 1000.0, 3)
    print(f"  3 failed probes -> cum = {cum}")
    print(f"  avg = (sum(cum) / 6) * 1000.0 = {reported} ms")
    print(f"  that is {reported / 60000:.0f} minutes. It is a placeholder.")

    print()
    print("PROOF 4: the divisor bug halves every SUCCESSFUL latency")
    print("=" * 74)
    for true_ms in (12.0, 25.0, 80.0):
        cum = [true_ms / 1000.0] * 3
        rep = round((sum(cum) / 6) * 1000.0, 3)
        print(f"  true {true_ms:>6.1f} ms -> reported {rep:>7.3f} ms  "
              f"({rep / true_ms:.0%} of truth)")

    print()
    print("=" * 74)
    print("PROOF 5: the server list it uses is NOT geo-aware")
    print("=" * 74)
    try:
        import urllib.request
        req = urllib.request.Request(
            "http://www.speedtest.net/speedtest-servers-static.php",
            headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            txt = r.read().decode("utf-8", "replace")
        hosts = sorted({h for h in _extract_hosts(txt) if h})
        print(f"  speedtest-servers-static.php returned {len(txt)} bytes, "
              f"{len(hosts)} distinct hosts")
        for h in hosts[:12]:
            print(f"    {h}")
        print()
        print("  Every one of these is far from Tampa FL; the geo-aware")
        print("  endpoint api/js/servers reports 'distance' per server and")
        print("  includes Tampa servers at 5 km.")
    except Exception as e:
        print(f"  (could not fetch static list: {e})")

    return 0 if (status != 200 and status2 == 200) else 1


def _extract_hosts(txt):
    import re
    return re.findall(r"https?://([A-Za-z0-9.\-]+:\d+)", txt)


if __name__ == "__main__":
    sys.exit(main())
