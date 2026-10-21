# spdng

A maintained speedtest CLI that tells you **where** your speed is going, not
just how much you have.

`spdng` is a from-scratch replacement for
[speedtest-cli](https://github.com/sivel/speedtest-cli), which has been
archived since July 2024 with its last release in **April 2021**. Homebrew
formally deprecated it as `:unmaintained` on 2026-01-18 and scheduled it for
removal on 2027-01-18.

It does everything speedtest-cli does, correctly, and adds the thing
speedtest-cli never had: an **mtr-style per-hop trace** to the test server,
so you can see which router is eating your packets.

```
$ spdng speedtest --trace

Hosted by Blue Stream Fiber (Miami, FL) [US] 5 km
Ping: 50.23 ms   jitter: 5.38 ms   loss: 0%
Download: 237.45 Mbit/s   Upload: 9.99 Mbit/s

Hop   Loss%  Snt       Last        Avg       Best      Worst   Jitter  Address
------------------------------------------------------------------------------
  1    0.0%    3   ~3.79 ms   ~4.59 ms    3.79 ms    5.07 ms     0.64  192.168.1.1
  2    0.0%    3   ~4.58 ms   ~4.42 ms    3.82 ms    4.86 ms     0.90  72.187.94.43
  3  100.0%    3        ---        ---        ---        ---      ---  *
 10    0.0%    3  ~18.49 ms  ~20.20 ms   18.49 ms   21.86 ms     1.69  66.109.6.96
 11    0.0%    3  ~27.13 ms  ~24.88 ms   21.52 ms   27.13 ms     2.80  66.109.5.127
 12    0.0%    3  ~22.80 ms  ~20.42 ms   18.51 ms   22.80 ms     2.14  208.116.217.205
 13    0.0%    3  ~19.97 ms  ~25.66 ms   19.97 ms   35.32 ms     7.67  89.149.133.226
 14    0.0%    3  ~24.40 ms  ~22.32 ms   19.87 ms   24.40 ms     3.67  74.199.177.10
 15    0.0%    3  ~28.40 ms  ~25.31 ms   20.42 ms   28.40 ms     7.33  139.138.78.174
 16    0.0%    3  ~35.98 ms  ~52.04 ms   31.66 ms   88.49 ms    30.57  139.138.78.181
 17    0.0%    2   21.00 ms   23.25 ms   21.00 ms   25.50 ms     4.50  139.138.79.141

Destination reached

Final hop: 0% loss over 2 probes
No significant latency jump across the path; end-to-end latency 23.2 ms
```

A `~` marks an RTT we estimated ourselves rather than one the kernel
reported. See [Measurement honesty](#measurement-honesty).

---

## Why speedtest-cli is broken

I installed speedtest-cli 2.1.3 and measured it. It is not a matter of taste
or age; it reports **wrong numbers**. Four independent defects, each
reproducible with `proof.py` in this repo:

### 1. It never follows HTTP redirects

`SpeedtestHTTPConnection` subclasses `http.client.HTTPConnection` and adds
no redirect handling. Ookla's current servers answer the legacy hostname with
`307 Temporary Redirect`:

```
GET http://speed.toronto.telmax.ca:8080/speedtest/latency.txt?x=1
  -> HTTP 307
  -> Location: https://server-74035.prod.hosts.ooklaserver.net:8080/speedtest/latency.txt?x=1
```

The tool's check is `if r.status == 200 and text == 'test=test'`, so a 307
falls into the failure branch on every single probe.

### 2. Failed probes become a 30-minute placeholder

On failure it appends the sentinel `3600` (seconds), then averages with
`sum(cum) / 6 * 1000.0`. With three failed probes that is exactly
**1800000.0 ms**, which the tool then reports as the latency:

```
$ speedtest-cli --csv
74035,telMAX,"Toronto, ON",2026-10-21T19:38:06.910752Z,1753.273344638221,
1800000.0,218558263.7667018,38948233.49808494,,72.187.94.43
                            ^^^^^^ km        ^^^^^^ a placeholder, not a measurement
```

`spdng` reports `unavailable` when latency cannot be measured. It never
invents a number.

### 3. Every successful latency is reported at half its true value

The divisor is 6, but only 3 probes are taken:

| true latency | speedtest-cli reports |
|---|---|
| 12.0 ms | 6.000 ms |
| 25.0 ms | 12.500 ms |
| 80.0 ms | 40.000 ms |

### 4. It uses a static, non-geo-aware server list

It fetches `speedtest-servers-static.php`, a fixed list of 10 servers. It
never asks the geo-aware API that actually knows which servers are near you.
From Tampa it probed five Canadian servers and picked one **1,753 km away**,
then measured throughput to it. The number it printed was not the user's
speed.

`spdng` uses the geo-aware `api/js/servers` endpoint, ranks by **distance
first**, and talks to the canonical hostname directly (skipping the 307
saves a round trip and roughly halves the measured latency).

---

## Install

```bash
pipx install spdng          # or: pip install spdng
```

No dependencies beyond the Python standard library. Python 3.10+.

From source:

```bash
git clone https://github.com/patpadgett/spdng
cd spdng && pip install -e .
```

---

## Usage

```bash
spdng speedtest                  # test against the nearest server
spdng speedtest --trace          # ...and trace the path to it (mtr-style)
spdng speedtest --server spectrum --trace
spdng speedtest --json           # machine-readable output
spdng servers                    # list candidate servers, nearest first
spdng trace 1.1.1.1              # mtr-style traceroute on its own
spdng doctor                     # what does this environment support?
```

### Options

| flag | meaning |
|---|---|
| `--server` | pick a server by host, name, or sponsor |
| `--duration` | seconds per direction (default 10) |
| `--threads` | parallel connections (default 4) |
| `--max-distance` | only consider servers within N km |
| `--trace` | also trace the path to the test server |
| `--trace-probes` | probes per hop (default 3) |
| `--max-hops` | maximum hops to trace (default 20) |
| `--json` | machine-readable output |
| `--no-upload` / `--no-download` | skip a direction |
| `--no-latency` | skip the standalone latency pre-check |

### JSON output

```bash
$ spdng speedtest --trace --json
```

```json
{
  "server": {
    "id": "17170",
    "host": "speedtest03.bluestreamfiber.net.prod.hosts.ooklaserver.net:8080",
    "sponsor": "Blue Stream Fiber",
    "name": "Miami, FL",
    "distance_km": 5.0,
    "latency_ms": 50.23
  },
  "download_mbps": 237.45,
  "upload_mbps": 9.99,
  "latency": {
    "avg_ms": 50.23,
    "min_ms": 44.1,
    "max_ms": 58.2,
    "jitter_ms": 5.38,
    "loss_pct": 0.0,
    "samples_ms": [50.1, 44.1, 56.4]
  },
  "trace": {
    "target": "139.138.79.141",
    "method": "ping",
    "destination_reached": true,
    "hops": [
      {
        "ttl": 1, "addr": "192.168.1.1", "sent": 3, "received": 3,
        "loss_pct": 0.0, "last_ms": 3.79, "avg_ms": 4.59,
        "rtt_approximate": true
      }
    ]
  }
}
```

---

## Measurement honesty

This is the part that matters most, and it is where the old tool is worst.

**A failed probe is never a number.** If the latency probe cannot reach the
server, `spdng` prints `unavailable` and reports the loss percentage. It
does not substitute a placeholder.

**Distance beats ping when picking a server.** A bandwidth test measures the
link to the server. A 5 km server with 50 ms latency tells you more about
your connection than a 1,753 km server with 20 ms latency. `spdng` ranks by
distance.

**Loss is judged on the final hop only.** Intermediate hops routinely drop
ICMP because routers rate-limit it. That is not packet loss on your
connection. `spdng` says so rather than crying wolf.

**The latency jump is called out explicitly.** When one hop's latency jumps
by more than 2.5x and 25 ms versus the previous hop, that is where upstream
congestion begins. `spdng` names the hop.

**Approximate measurements are marked.** A `~` in front of an RTT means we
estimated it ourselves rather than reading it from the kernel. The final hop
always carries a kernel-measured RTT and never has a tilde.

### Traceroute backends

`spdng` picks the best available method, in this order:

1. **Raw ICMP socket.** Full fidelity. Needs `CAP_NET_RAW`.
2. **The `ping` binary.** On most Linux distributions `/usr/bin/ping` ships
   with `CAP_NET_RAW`, so it can open a raw socket on our behalf even when
   our own process cannot. This is what makes per-hop tracing work in
   containers, WSL, and CI. `ping` reports an exact RTT when the echo
   reaches the destination, and no RTT when a router answers
   "Time to live exceeded" -- in that case we time the invocation and
   subtract a calibrated startup baseline, and mark the result approximate.
3. **UDP with `IP_RECVERR`.** Pure-syscall unprivileged traceroute. Linux
   delivers "Time Exceeded" errors to the socket's error queue. Degrades
   gracefully in NAT environments that swallow ICMP.

`spdng doctor` tells you which one it will use and why.

```
$ spdng doctor
spdng doctor
============================================================
  raw ICMP sockets      : no
  unprivileged ICMP     : no
  ping_group_range      : (1, 0)
    -> range is empty, unprivileged ICMP is disabled
    -> fix with: sudo sysctl -w net.ipv4.ping_group_range='0 2147483647'
  ping binary           : yes
    -> startup overhead: 1.9 ms (calibrated)
  traceroute method     : ping (via CAP_NET_RAW ping binary)

  location lookup       : ok (28.076, -82.485)
  server list           : ok (5 servers)
  nearest server        : speedtest03.bluestreamfiber.net... (5 km)
```

---

## Development

```bash
git clone https://github.com/patpadgett/spdng && cd spdng
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q          # unit tests
.venv/bin/python proof.py              # reproduce the speedtest-cli bugs
```

Live network tests are opt-in:

```bash
SPDNG_LIVE=1 .venv/bin/python -m pytest -m live -q
```

---

## License

MIT. See `LICENSE`.
