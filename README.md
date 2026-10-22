# spdng

[![CI](https://github.com/patpadgett/spdng/actions/workflows/test.yml/badge.svg)](https://github.com/patpadgett/spdng/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/spdng.svg)](https://pypi.org/project/spdng/)
[![Python](https://img.shields.io/pypi/pyversions/spdng.svg)](https://pypi.org/project/spdng/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Dependencies](https://img.shields.io/badge/dependencies-zero-success.svg)](pyproject.toml)

A maintained speedtest CLI that tells you **where** your speed is going, not
just how much you have.

`spdng` is a from-scratch replacement for
[speedtest-cli](https://github.com/sivel/speedtest-cli), which has been
archived since July 2024 with its last release in **April 2021**. Homebrew
formally deprecated it as `:unmaintained` on 2026-01-18 and scheduled it for
removal on 2027-01-18.

It does everything speedtest-cli does, correctly, and adds three things
speedtest-cli never had:

- an **mtr-style per-hop trace** to the test server, so you can see which
  router is eating your packets
- **multiple providers**, so you can measure against more than one network
  and find out whether your link is slow or just slow to one destination
- a **local history**, so you can see whether your connection is getting
  worse

### What is different

| | speedtest-cli | spdng |
|---|---|---|
| Latency | measured on a **cold** connection, so it included the TCP and TLS handshake. A slow handshake dragged the average up by hundreds of milliseconds. | measured on a **warm** connection, so it is the round trip and not the setup cost. The handshake is reported separately. |
| Failed probes | became a **30-minute placeholder** (exactly `1800000.0` ms) and were averaged in with the real ones. | reported as **loss**, and never as a number. |
| HTTP redirects | not followed, so every latency probe against a current server **failed**. | followed, using the canonical host so there is no extra round trip. |
| Server choice | a static, non-geo-aware list, so it measured against a server **1,753 km away**. | the nearest server, ranked by distance. |
| Path analysis | none | an mtr-style per-hop trace, with loss, latency and a probe history per hop. |
| Providers | Ookla only | Ookla, Cloudflare and LibreSpeed, side by side, so you can tell a slow link from a slow route. |
| Dependencies | none | none, still. |

```
$ spdng speedtest --trace

+- spdng result ----------------------------------------------------+
| Metric   |                       Value | Detail                   |
+-------------------------------------------------------------------+
| Server   | Blue Stream Fiber Miami, FL | [US] 5 km                |
| Provider |                       ookla |                          |
| Ping     |                    47.82 ms | jitter 6.75 ms   loss 0% |
| Download |               232.34 Mbit/s | 10.6 MB in 0.4s          |
| Upload   |                 3.36 Mbit/s | 2.5 MB in 5.9s           |
| Grade    |                           A | very good                |
+-------------------------------------------------------------------+

+- path to 139.138.79.141 -------------------------------------------------------------------------------------+
| Hop |  Loss% | Snt |       Last |       Avg |      Best |      Worst |    Jitter | History | Address         |
+--------------------------------------------------------------------------------------------------------------+
|   1 |   0.0% |   3 |  ~19.43 ms |  ~9.69 ms |  ~3.96 ms |  ~19.43 ms |  ~7.73 ms | ...     | 192.168.1.1     |
|   2 |   0.0% |   3 |   ~3.70 ms |  ~4.14 ms |  ~3.70 ms |   ~4.45 ms |  ~0.38 ms | ...     | 72.187.94.43    |
|   3 | 100.0% |   3 |        --- |       --- |       --- |        --- |       --- | ooo     | *               |
|   9 |  33.3% |   3 |  ~43.89 ms | ~29.17 ms | ~14.45 ms |  ~43.89 ms | ~14.72 ms | ..o     | 71.44.3.118     |
|  10 |   0.0% |   3 |  ~64.72 ms | ~49.04 ms | ~33.32 ms |  ~64.72 ms | ~15.70 ms | ...     | 66.109.6.96     |
|  11 |   0.0% |   3 |  ~27.78 ms | ~24.21 ms | ~20.34 ms |  ~27.78 ms |  ~3.72 ms | ...     | 66.109.5.127    |
|  12 |   0.0% |   3 |  ~54.12 ms | ~48.26 ms | ~43.53 ms |  ~54.12 ms |  ~5.29 ms | ...     | 208.116.217.205 |
|  13 |   0.0% |   3 |  ~44.00 ms | ~38.82 ms | ~34.25 ms |  ~44.00 ms |  ~4.88 ms | ...     | 89.149.133.226  |
|  16 |   0.0% |   3 | ~119.81 ms | ~94.15 ms | ~68.40 ms | ~119.81 ms | ~25.70 ms | ...     | 139.138.78.181  |
|  17 |   0.0% |   3 |   53.40 ms |  41.74 ms |  32.30 ms |   53.40 ms |  10.55 ms | ...     | 139.138.79.141  |
+--------------------------------------------------------------------------------------------------------------+

Final hop: 0% loss over 3 probes
Latency jump at hop 9 (71.44.3.118): 29.2 ms - upstream congestion begins here
```

The `History` column is the mtr convention: `.` is a probe that came
back, `o` is one that timed out, `O` is one that was refused and `X`
is one that hit an error. A column of `.........o..` says the link is
fine with an occasional blip. A column of `oooOOXXXXX` says the hop is
down, and the tool says so in words rather than making you read rows of
`*`.

The `~` marks an RTT we estimated ourselves rather than one the kernel
reported. See [Measurement honesty](#measurement-honesty).

---

## Why speedtest-cli is broken

I installed speedtest-cli 2.1.3 and measured it. It is not a matter of
taste or age; it reports **wrong numbers**. Four independent defects, each
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
saves a round trip and roughly halves the measured latency: 46 ms direct
versus 128 ms via the redirect).

---

## Whose infrastructure is this?

Good question, and one the old tool never answered. `spdng` supports three
networks, and you can compare them:

| provider | what it measures | whose infrastructure |
|---|---|---|
| `ookla` | the nearest speedtest server, chosen by distance | Ookla's server network (a commercial company; this API is unofficial) |
| `cloudflare` | the nearest Cloudflare data centre | Cloudflare's own. This is the network almost all of your internet traffic crosses, so it is a different and often more relevant question |
| `librespeed` | a community-run LibreSpeed server | open source, and self-hostable: `--server https://your-own-box/backend` (a real example URL, replaced with your own host) tests against a machine you control, which is the only way to measure a specific vantage point |

Netflix's **fast.com** is deliberately *not* supported. Its API sits behind
Akamai bot protection that rejects non-browser clients with a 403, and
working around that would mean impersonating a browser we are not. If they
ever open it up, it drops in as a fourth provider.

Comparing providers is often more informative than any single test:

```
$ spdng compare --providers ookla cloudflare

           Ookla (speedtest.net)   Cloudflare
───────────────────────────────────────────────────────────
Server
           Blue Stream Fiber…      Cloudflare Nearest…
Provider
           ookla 5 km              cloudflare -

Download   224.75 Mbit/s           317.02 Mbit/s
Upload     10.51 Mbit/s            7.42 Mbit/s
Ping       47.82 ms                57.94 ms
Jitter     6.75 ms                 2.83 ms
Loss       0.0 %                   0.0 %

           vs first column
Download   ~same                   ▲41%
Upload     ~same                   ▼29%
Ping       ~same                   ▲21%

           Download, relative to best
           Ookla (speedtest.net)   ██████████████ 224.8 Mbit/s
           Cloudflare              ████████████████████ 317.0 Mbit/s
```

Cloudflare is 41% faster than Ookla from the same machine at the same
moment. That is a routing fact you would never see from a single-provider
test, and it is usually the interesting one.

---

## Install

```bash
pipx install spdng          # or: pip install spdng
```

Or with Homebrew:

```bash
brew install patpadgett/spdng/spdng
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
spdng speedtest --provider cf    # measure to Cloudflare instead
spdng speedtest --no-upload      # measure download only
spdng speedtest --no-download    # measure upload only
spdng speedtest --no-latency     # skip the standalone latency probe
spdng speedtest --save report.json  # save an exact file (.txt/.csv/.json)
spdng compare                    # test all three providers, side by side
spdng repeat --count 5           # run the same test 5 times, show the spread
spdng watch --interval 30        # keep testing, show a live trend
spdng watch --plain              # ...without the full-screen view
spdng history                    # past measurements and the trend
spdng servers                    # list candidate servers, nearest first
spdng live 1.1.1.1               # live mtr-style trace, updating in place
spdng trace 1.1.1.1              # mtr-style traceroute on its own
spdng doctor                     # what does this environment support?
spdng moo                        # moo
```

`--no-upload` and `--no-download` leave that direction unmeasured. The
result says `skipped` (or `n/a` in a comparison or the history), not
`0.00 Mbit/s`: a skipped direction is not the same as a transfer that moved
no bytes.

### Options

| flag | meaning |
|---|---|
| `--provider` | `ookla`, `cloudflare`, or `librespeed` |
| `--server` | pick a server by host, name, sponsor, or a full URL for your own instance |
| `--duration` | seconds per direction (default 10) |
| `--threads` | parallel connections (default 4) |
| `--latency-samples` | latency probes per test (default 5) |
| `--max-distance` | only consider servers within N km |
| `--limit` | candidate servers to fetch, or history entries to load |
| `--trace` | also trace the path to the test server |
| `--trace-probes` | probes per hop (default 3) |
| `--trace-timeout` | seconds to wait for each trace reply (default 2) |
| `--max-hops` | maximum hops to trace (default 20) |
| `--method` | `trace`/`live`: force `icmp`, `icmp-dgram`, `ping`, or `udp` |
| `--port` | `trace`/`live`: destination port (1-65535) |
| `--json` | machine-readable output |
| `--csv` | write the result as CSV to stdout |
| `--save PATH` | save to an exact file; the extension picks `.txt`, `.csv`, or `.json` |
| `--save-dir DIR` | directory for a relative `--save` path |
| `--no-upload` / `--no-download` | skip a direction |
| `--no-latency` | skip the standalone latency pre-check |
| `--no-fun` | no grades, no speedometer, no wisecracks |
| `--no-history` | do not record this result |
| `--providers` | `compare` only: providers to test, e.g. `ookla cloudflare` |
| `--count` | `repeat` only: number of runs (default 3) |
| `--interval` | `watch`/`live` only: seconds between rounds |
| `--plain` | `watch` only: line-by-line output instead of the full-screen view |
| `--watch-duration` | `watch` only: stop after this many seconds |
| `--show` / `--clear` | `history` only: rows to print, or delete the history file |
| `--color` / `--no-color` | force colour on or off |
| `--theme` | `default`, `mono`, `solarized`, `matrix`, `vaporwave`, `amber` |

### Boxed output

Tables are drawn inside an ASCII frame, with the title in the top border:

```
+- spdng result ----------------------------------------------------+
| Metric   |                       Value | Detail                   |
+-------------------------------------------------------------------+
| Server   | Blue Stream Fiber Miami, FL | [US] 5 km                |
| Provider |                       ookla |                          |
| Ping     |                    47.82 ms | jitter 6.75 ms   loss 0% |
| Download |               232.34 Mbit/s | 10.6 MB in 0.4s          |
| Upload   |                     skipped | not measured             |
| Grade    |                           A | very good                |
+-------------------------------------------------------------------+
```

That `skipped` row is what `--no-upload` produces. It is deliberately not
`0.00 Mbit/s`; a direction that was not measured is different from a
transfer that moved no bytes.

Columns line up exactly, and they line up with colour on too. That last part
is not automatic: `len()` on a coloured string counts the ANSI escape
sequences, so a coloured cell measures nine characters wider than a plain
one and every column after it drifts. `spdng` measures the *visible* width
instead, and there is a test that asserts every line of every table has the
same visible width, with and without colour.

The default frame is plain ASCII so it renders identically in a log file or
a CI transcript. Unicode, rounded and double styles are available in
`spdng/table.py`.

### The live mtr view

`spdng live <target>` is the mtr loop: a round of probes to every hop in
parallel, a redraw, and repeat.

```
 spdng live trace -> 1.1.1.1   round 4   15s   running

  Hop   Loss%  Snt      Last       Avg      Best     Worst    StDev History         Host
 ---------------------------------------------------------------------------------------------------
    1    0.0%    5    3.51ms    2.33ms    1.30ms    3.51ms   0.79ms .....           192.168.1.1
    2   20.0%    5    3.11ms    3.20ms    1.32ms    5.06ms   1.53ms ....o           72.187.94.43
    3   80.0%    5   12.35ms   12.35ms   12.35ms   12.35ms      --- .oooo           72.187.94.43

 q quit   s save csv   j save json   r save report   space pause   +/- interval   up/down scroll
```

The history column uses the mtr convention:

| character | meaning |
|---|---|
| `.` | the probe came back |
| `o` | the probe timed out |
| `O` | the probe was refused |
| `X` | the probe hit an error |

A column of `.........o..` says the link is fine with an occasional blip.
A column of `oooOOXXXXX` says the hop is down, and the view says so
explicitly rather than making you read rows of `*`.

### Hotkeys

Both the live trace and the watch view respond to keys:

| key | action |
|---|---|
| `q` | quit |
| `s` | save the current data as CSV |
| `j` | save it as JSON |
| `r` | save a human-readable report |
| `space` | pause and resume |
| `+` / `-` | change the interval between rounds |
| `u` | show or hide the upload chart (watch view) |

The keyboard is answered while the work is running, so `q` quits
immediately instead of after the current sample finishes.

The `s`/`j`/`r` hotkeys save to `~/.local/share/spdng/reports` by default,
or to the directory named by `--save-dir`.

`spdng speedtest` can also save non-interactively:

```bash
spdng speedtest --save ~/reports/speedtest.json   # exact path
spdng speedtest --save speedtest.csv --save-dir ~/reports
spdng speedtest --csv > speedtest.csv              # CSV to stdout
```

The extension picks the format: `.txt` (the boxed table), `.csv`, or
`.json`. A path without an extension gets `.txt`. If `PATH` is a
directory, `spdng` writes a timestamped `result-*.txt` file inside it
rather than silently discarding the name. A relative `--save` path is
resolved inside `--save-dir` when one is given.

### The watch graph

`spdng watch` draws a live bar graph per metric, scaled against the best
sample in the session, plus a time-based chart of download over time. Ping
is drawn inverted, so a tall bar always means a problem rather than always
meaning a good number.

The view fills the terminal it was given: the chart takes the full width
and its height grows to fill whatever is left over. Resize the terminal
and the layout follows.

Press `u` to show or hide the upload chart. With it on, the two charts
sit side by side and the download chart takes half the width; with it off,
the download chart takes the whole width. The upload *stat bar* at the top
is always shown - that is the current upload rate, which is worth seeing.

```
 spdng watch -> Ookla Blue Stream Fiber Miami, FL   4 samples   75s   running

  Download
    peak         236.7 Mbit/s   last     177.0   avg     218.7
    █████████████████████████████████████████████░░░░░░░░░░░░░░░   177.0 Mbit/s

  Upload
    peak          12.9 Mbit/s   last       9.7   avg      11.6
    █████████████████████████████████████████████░░░░░░░░░░░░░░░     9.7 Mbit/s

  Ping
    peak          51.6 ms   last      47.9   avg      48.7
    ████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░    47.9 ms

  Download over time                                 Upload over time
                                   █▄▄    236.7                                       █ ▄     12.9
                                   ███    207.1                                       ███     11.3
                                   ███▄   177.5                                       ████     9.7
                                   ████   147.9                                       ████     8.1
                                   ████   118.3                                       ████     6.5
                                   ████    88.8                                       ████     4.8
                                   ████    59.2                                       ████     3.2
                                   ████    29.6                                       ████     1.6

 q quit   s save csv   j save json   r save report   space pause   +/- interval   u upload
```

### Colour

`spdng` honours the [NO_COLOR](https://no-color.org) convention, which takes
precedence over `FORCE_COLOR`. It also honours `FORCE_COLOR` for people who
pipe output and want it anyway, and detects non-terminals correctly.

Colour is **never the only signal**. Every coloured value also carries its
text, so the output reads identically in a monochrome terminal or in a log
file. Colour is decoration, not information.

### The fun bits

An easter egg must never lie about a measurement, never slow the tool down,
never be obscure for its own sake, and must be funny to someone who does not
know it exists. Within those rules:

- a **grade** (S+ down to F) with a plain-language description
- an **ASCII speedometer**
- short remarks that depend on the measured speed, so a 3 Mbit/s result
  gets a different remark than a 300 Mbit/s one
- `spdng moo`, the obligatory cow
- **sparklines**, which are also the basis of the watch and history views

`--no-fun` turns all of it off.

### Live monitoring

`spdng watch` re-runs the measurement on a timer and draws a live
sparkline, because the useful question is rarely "how fast is my internet
right now" but "why was it slow at 8pm":

```
$ spdng watch --interval 30 --plain
  236.7 Mbit/s down     n/a Mbit/s up  ping   46.3 ms  next in 11s  ctrl-c to stop
  226.9 Mbit/s down     n/a Mbit/s up  ping   49.0 ms    █▁ 226.9..236.7 Mbit/s  next in 11s  ctrl-c to stop
  234.1 Mbit/s down     n/a Mbit/s up  ping   51.6 ms    █▁▆ 226.9..236.7 Mbit/s  next in 11s  ctrl-c to stop
  177.0 Mbit/s down     n/a Mbit/s up  ping   47.9 ms    █▆▇▁ 177.0..236.7 Mbit/s  next in 11s  ctrl-c to stop

4 samples
  download: min 177.0  max 236.7  avg 218.7 Mbit/s
  ping    : min 46.3  max 51.6  avg 48.7 ms
  █▆▇▁ 177.0..236.7 Mbit/s
  spread: 34% (somewhat variable)
```

### History

Every result is recorded to a JSON-lines file under `XDG_DATA_HOME`
(`~/.local/share/spdng/history.jsonl` by default). Plain text, local,
nothing uploaded, `--clear` deletes it.

```
$ spdng history

2 past measurements

  2026-10-21 16:50  ookla          266.92 Mbit/s down     n/a Mbit/s up  ping   45.6 ms
  2026-10-21 16:50  cloudflare     276.67 Mbit/s down     n/a Mbit/s up  ping   58.6 ms

  range: 266.9 .. 276.7 Mbit/s (avg 271.8)
  ▁█ 266.9..276.7 Mbit/s
  trend: ▲ 3.7% vs the first half of the history
```

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
    "country": "United States",
    "cc": "US",
    "lat": 25.8,
    "lon": -80.2,
    "distance_km": 5.0,
    "latency_ms": 50.0,
    "jitter_ms": 6.0,
    "loss_pct": 0.0
  },
  "provider": "ookla",
  "provider_label": "Ookla (speedtest.net)",
  "download_mbps": 237.45,
  "upload_mbps": 9.99,
  "latency": {
    "avg_ms": 50.0,
    "min_ms": 44.0,
    "max_ms": 56.0,
    "jitter_ms": 6.0,
    "loss_pct": 0.0,
    "samples_ms": [44.0, 50.0, 56.0]
  },
  "download": {
    "bytes": 23745000,
    "seconds": 0.8,
    "threads": 4,
    "errors": 0
  },
  "upload": {
    "bytes": 9990000,
    "seconds": 8.0,
    "threads": 2,
    "errors": 0
  },
  "share_url": null,
  "errors": [],
  "trace": {
    "target": "139.138.79.141",
    "method": "ping",
    "destination_reached": true,
    "error": null,
    "hops": [
      {
        "ttl": 1,
        "addr": "192.168.1.1",
        "host": null,
        "sent": 3,
        "received": 3,
        "loss_pct": 0.0,
        "last_ms": 5.1,
        "best_ms": 3.8,
        "worst_ms": 5.1,
        "avg_ms": 4.5,
        "jitter_ms": 0.65,
        "rtt_approximate": true
      },
      {
        "ttl": 17,
        "addr": "139.138.79.141",
        "host": null,
        "sent": 3,
        "received": 3,
        "loss_pct": 0.0,
        "last_ms": 48.4,
        "best_ms": 48.4,
        "worst_ms": 50.2,
        "avg_ms": 49.2,
        "jitter_ms": 0.9,
        "rtt_approximate": false
      }
    ]
  }
}
```

JSON output never contains ANSI escapes, so it is always safe to parse.

---

## Measurement honesty

This is the part that matters most, and it is where the old tool is worst.

**A failed probe is never a number.** If the latency probe cannot reach the
server, `spdng` prints `unavailable` and reports the loss percentage. It
does not substitute a placeholder.

**Skipping is not failing.** `--no-latency` leaves the latency unmeasured;
the result says `Ping skipped`, and JSON reports `null` for the latency
values and loss instead of inventing a failure.

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

**One speed test is noisy.** `spdng repeat` runs the same test N times and
reports the spread, distinguishing steady (<10%), somewhat variable, and
very variable.

### Traceroute backends

`spdng` picks the best available method, in this order:

1. **Raw ICMP socket.** Full fidelity. Needs `CAP_NET_RAW`. IPv4 only.
2. **Unprivileged ICMP.** Linux allows a `SOCK_DGRAM` ICMP socket when
   `net.ipv4.ping_group_range` includes your group, and `IP_RECVERR`
   delivers router errors to the socket. No `CAP_NET_RAW` and no setuid
   binary are needed. IPv4 only.
3. **The `ping` binary.** On most Linux distributions `/usr/bin/ping`
   ships with `CAP_NET_RAW`, so it can open a raw socket on our behalf even
   when our own process cannot. This is what makes per-hop tracing work in
   containers, WSL, and CI. `ping` reports an exact RTT when the echo
   reaches the destination, and no RTT when a router answers
   "Time to live exceeded" -- in that case we time the invocation and
   subtract a calibrated startup baseline, and mark the result approximate.
   Works for IPv4 and IPv6.
4. **UDP with `IP_RECVERR`.** Pure-syscall unprivileged traceroute. Linux
   delivers "Time Exceeded" errors to the socket's error queue. Degrades
   gracefully in NAT environments that swallow ICMP. Works for IPv4 and
   IPv6.

Force a backend with `spdng trace --method icmp|icmp-dgram|ping|udp`
(the live view accepts the same flag), or leave it at the default `auto`.
`--port` sets the UDP port and accepts a host, `host:port`, URL, or IPv6
literal as the target; a URL also supplies its own port when one is not
given. `spdng doctor` tells you which method `auto` will choose and why.

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
  colour                : on  (theme: default)

  location lookup       : ok (28.076, -82.485)
  provider ookla       : ok (3 servers) speedtest03.bluestreamfiber.net... (5 km)
  provider cloudflare  : ok (1 servers) speed.cloudflare.com:443 (0 km)
  provider librespeed  : ok (3 servers) ams.speedtest.clouvider.net (656 km)
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

The test suite covers degenerate input, network failure, environment
limits, output contracts, and the process itself:

| file | what it covers |
|---|---|
| `test_spdng.py` | the core: measurement, providers, the CLI |
| `test_edge_cases.py` | the things that actually go wrong |
| `test_ui.py` | the tables and the live view |
| `test_curses_colour.py` | the curses colour path and the streaming trace |
| `test_watchview.py` | the watch charts and the layout at any width |
| `test_httpconn.py` | the persistent connection and warm latency |
| `test_fun.py` | the extras |
| `test_history.py` | the local history file |

`test_ui.py` and `test_curses_colour.py` have the alignment tests, which
are the ones worth keeping: every line of every table must have the same
visible width, with and without colour, in every box style. That is the
test that would have caught the original misalignment.

## License

MIT. See `LICENSE`.
