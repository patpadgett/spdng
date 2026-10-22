# spdng launch kit

## 1. The upstream issue (sivel/speedtest-cli)

The repo is archived and has issues disabled, so this cannot be filed
there. It is a draft for wherever a human can actually post it, and it
doubles as the README's "why" section.

**Subject:** `speedtest-cli is archived - a maintained replacement, and the bugs I found on the way`

**Body:**

`speedtest-cli` has been archived since July 2024, with its last release in
April 2021. Homebrew deprecated it as `:unmaintained` on 2026-01-18 and
scheduled it for removal on 2027-01-18.

I wanted to understand what was actually broken rather than just start
something new, so I read the source and wrote a script that reproduces each
defect. Four of them, all of them live:

**1. It never follows HTTP redirects.**
`SpeedtestHTTPConnection` subclasses a bare `http.client.HTTPConnection`
and adds no redirect handling. Current Ookla servers answer the legacy
hostname with a `307`, so every latency probe takes the failure branch.

**2. A failed probe becomes a 30-minute placeholder.**
On failure it appends the sentinel `3600` and averages it in as
`sum(cum)/6*1000.0`, which produces exactly `1800000.0` ms - reported to
the user as a measurement.

**3. Every successful latency is half its true value.**
It takes three probes and divides the sum by six.

**4. It measures against a server 1,753 km away.**
It uses a static, non-geo-aware server list, so from Tampa it picked a
server in Toronto.

Here is the script, with no network access required to read it:
https://github.com/patpadgett/spdng/blob/main/proof.py

I built a replacement, `spdng`, with the same CLI shape:

- Latency is measured on a **warm** connection. A cold probe measures the
  TCP and TLS handshake, not the round trip - one slow handshake can drag
  the reported average up by hundreds of milliseconds.
- A failed probe is **loss**, never a number.
- The **nearest** server is used, ranked by distance.
- An **mtr-style per-hop trace**, live in curses, showing where loss and
  latency actually begin.
- **Three providers** - Ookla, Cloudflare, LibreSpeed - measured side by
  side, so you can tell a slow link from a slow route.
- **Zero dependencies.** The standard library covers HTTP, TLS, concurrency,
  curses and CSV.

    pip install spdng

It is a different name on purpose. This is a different tool, and the
original author owns the `speedtest-cli` name.

https://github.com/patpadgett/spdng

---

## 2. The Homebrew pull request (homebrew-core)

The formula for `speedtest-cli` has `replacement_formula: None`, and
Homebrew will remove it on 2027-01-18. A formula for `spdng` plus a
`replacement_formula` on the old one is the highest-leverage thing here:
it is the registry itself telling every Mac user where to go.

**Title:** `spdng 0.7.0`

**Body:**

```
Adds a formula for spdng, a maintained speedtest CLI with mtr-style path
analysis.

spdng is a drop-in replacement for speedtest-cli, which has been archived
since July 2024 with its last release in April 2021. Homebrew deprecated
it as :unmaintained on 2026-01-18 and scheduled it for removal on
2027-01-18.

What it does differently:

- Latency is measured on a warm connection. A cold probe measures the TCP
  and TLS handshake rather than the round trip, and one slow handshake can
  drag the reported average up by hundreds of milliseconds. The handshake
  time is reported separately.
- A failed probe is reported as loss, never as a number. The upstream tool
  appends a 3600 second sentinel and averages it in, which yields exactly
  1800000.0 ms as a measurement.
- Redirects are followed. Current Ookla servers answer the legacy hostname
  with a 307.
- The nearest server is used, ranked by distance, rather than a static list
  that put a server 1,753 km away.
- An mtr-style per-hop trace, live in curses, with loss, latency and probe
  history per hop.
- Three providers - Ookla, Cloudflare and LibreSpeed - measured side by
  side, so a slow link can be told apart from a slow route.
- Zero runtime dependencies. The Python standard library covers HTTP, TLS,
  concurrency, curses and CSV.

    brew install spdng

Python 3.10 or later, Linux and macOS. MIT licensed.

Upstream: https://github.com/patpadgett/spdng
PyPI: https://pypi.org/project/spdng/
```

A personal tap is live now, for anyone who wants it before the core
formula lands:

    brew install patpadgett/spdng/spdng

---

## 3. r/networking, r/linux, r/commandline

Three communities, three angles. Post to each only, not to all three.

### r/networking - the diagnostic angle

**Title:** `I built a speedtest CLI that shows you which hop is eating your packet loss`

**Body:**

Looking at a speed test result and then an mtr result is two separate
workflows, and the interesting failure is usually in the second one. Your
ISP says 300 Mbps, and every packet vanishes somewhere around the third
hop, and no dashboard is going to tell you that.

So I built `spdng`, an mtr-style speed test where the path analysis is the
main event rather than an afterthought:

```
$ spdng live 1.1.1.1
```

It probes every TTL concurrently, so you get the whole path in about a
second instead of thirty, and each hop keeps a history of what happened to
it - `.` for a reply, `o` for a timeout, `O` for a refusal, `X` for an
error. The point is watching where the loss starts.

It also does the boring part properly, which turned out to matter more than
I expected:

- **Latency is measured on a warm connection.** The obvious implementation
  opens a fresh TCP+TLS connection for every probe, which means you are
  mostly measuring handshakes. On a healthy link that reported a 460 ms
  average with 519 ms of jitter. The same code, keeping the connection
  open and discarding the warm-up exchange, reports 20-25 ms with 1-4 ms of
  jitter.
- **A failed probe is loss, never a number.** I found the tool I was
  replacing averaging a `3600` second sentinel into its results, which
  surfaces as a very confident `1800000.0 ms`.
- **The nearest server is used**, ranked by distance. The one I was
  replacing picked a server 1,753 km away.

It also compares Ookla, Cloudflare and LibreSpeed side by side, because
"my internet is slow" and "my route to this one host is bad" are different
problems with different fixes.

Python, no dependencies, Linux and macOS:

    pip install spdng

    spdng speedtest --trace    # measure, and show the path
    spdng live 1.1.1.1         # the mtr loop
    spdng compare              # three providers, side by side
    spdng doctor               # what does this environment support?

Source: https://github.com/patpadgett/spdng

Happy to answer questions about the traceroute implementation. Getting
ICMP error queue delivery working on an unprivileged UDP socket across
Linux and macOS was the fiddly part.

### r/linux - the implementation angle

**Title:** `spdng: a dependency-free speedtest CLI with an mtr-style traceroute in curses`

**Body:**

I wanted a speedtest tool and ended up writing one, mostly because the
obvious one (`speedtest-cli`) has been archived since 2024 and has some
interesting bugs.

`spdng` is pure standard library - no requests, no rich, no texttable. Just
`http.client`, `socket`, `concurrent.futures` and `curses`.

The part I enjoyed most was the traceroute. Getting per-hop results fast
needs every TTL probed concurrently rather than one after another, which
means you need ICMP error delivery to an unprivileged socket. There are
three ways to get it and they have different tradeoffs:

- **Raw ICMP sockets** work but need `CAP_NET_RAW`.
- **The `ping` binary** works unprivileged wherever it has
  `cap_net_raw=ep`, which is most modern Linux distros. You parse its
  output instead of opening a socket.
- **UDP with `IP_RECVERR`** is the clean one, but it is Linux-only.

So it probes in parallel with all three, uses whichever is available, and
degrades gracefully. `spdng doctor` tells you which one it picked and why.

Two portability traps I hit, both of which are now tests:

- `ping` means different things on Linux and BSD. On macOS `-t` is the
  *timeout* and the TTL is `-h`; on Linux it is the other way around. The
  timeout unit differs too - Linux takes seconds, BSD takes milliseconds.
  Get the command line wrong and every hop looks like the final hop, with
  no error message at all.
- The IP socket option numbers are platform-specific. `IP_TTL` is 2 on
  Linux and 4 on BSD and macOS, so a hardcoded number silently sets the
  wrong option off Linux.

    pip install spdng

    spdng live 1.1.1.1
    spdng speedtest --trace
    spdng doctor

Source: https://github.com/patpadgett/spdng

The CI matrix is Linux and macOS across Python 3.10-3.13, and every
platform bug above came out of it. A Linux-only test run found none of
them.

### r/commandline - the interface angle

**Title:** `spdng: speedtest with an mtr-style live path view, in curses`

**Body:**

`speedtest-cli` is archived and Homebrew is removing it on 2027-01-18, so
I built a replacement. It is a curses app:

    spdng live 1.1.1.1

Live per-hop path view in the style of mtr, with a history column per hop
(`.` reply, `o` timeout, `O` refusal, `X` error) so you can watch loss
appear at a specific hop and stay there.

    spdng watch

A live dashboard with current values as bars, plus a time-series chart of
download (and upload, toggled with `u`) along the bottom. Hotkeys save the
raw data as CSV or JSON, or a human-readable report, from inside the
running app.

The output is boxed with the columns lined up exactly, because a table
that does not line up is a table you cannot read. It uses visible-width
measurement rather than string length, so the ANSI colour codes do not
throw the alignment off.

    spdng speedtest --trace    # measure, and show the path
    spdng compare              # three providers side by side
    spdng repeat               # the same test, several times, for spread

No dependencies, standard library only, Python 3.10+, Linux and macOS.

    pip install spdng

Source: https://github.com/patpadgett/spdng

---

## 4. Hacker News

HN is hostile to launch posts and hostile to marketing. A technical
"Show HN" that leads with the interesting bug is the only version worth
posting.

**Title:** `Show HN: spdng – a speedtest CLI that shows you which hop is eating your packet loss`

**Body:**

`speedtest-cli` is archived (last release April 2021) and Homebrew removes
it on 2027-01-18. I built a replacement, and the interesting part is not
the speed test.

I wanted per-hop path analysis as a first-class feature, so the tool
probes every TTL concurrently and shows you an mtr-style view of where
loss and latency begin.

Some things I found while reading the old code, all reproducible with the
`proof.py` script in the repo:

- It subclasses `http.client.HTTPConnection` with no redirect handling.
  Current Ookla servers answer the legacy hostname with a 307.
- A failed probe appends the sentinel `3600` seconds and averages it in
  as `sum(cum)/6*1000.0`, which is exactly `1800000.0` ms - a 30-minute
  "latency" reported as a measurement.
- It takes three probes and divides the sum by six, so every successful
  latency is half its true value.
- It uses a static server list. From Tampa it picked Toronto, 1,753 km
  away.

The traceroute is the part I would like feedback on. Per-hop results need
ICMP errors delivered to an unprivileged socket, so there are three
approaches: raw ICMP sockets (need `CAP_NET_RAW`), the `ping` binary
(unprivileged where it has `cap_net_raw=ep`), and UDP with `IP_RECVERR`
(Linux-only). It probes in parallel with all three and uses whichever is
available. `spdng doctor` reports which one it chose.

The other trap worth mentioning: latency probes must use a warm
connection. The obvious implementation opens a fresh TCP+TLS connection
per probe and therefore mostly measures handshakes - on a healthy link
that reported 460 ms average with 519 ms of jitter. Keeping the
connection open and discarding the warm-up gives 20-25 ms with 1-4 ms
jitter.

Pure standard library, no dependencies, Python 3.10+, Linux and macOS.

    pip install spdng

    spdng speedtest --trace
    spdng live 1.1.1.1
    spdng compare
    spdng doctor

https://github.com/patpadgett/spdng

---

## 5. The LinkedIn post

LinkedIn rewards specificity and a story over a feature list. Patrick's
credibility here is 14 years of telecom billing mediation - he has seen
these kinds of measurement bugs in production, at a telecom, at scale.
That is the angle that no other poster can take.

---

## 6. The tweet / X post

The 4 defects in one line. The 1,753 km number. The link.

---

## Posting checklist

- [ ] Upstream issue: repo has issues DISABLED. Cannot file there.
- [ ] Homebrew core PR: opened, then auto-closed for an unfilled template.
      Needs the template filled in by a human.
- [ ] Personal tap: LIVE, no form required.
- [ ] PyPI: LIVE.
- [ ] Reddit: no account exists yet. Create one, wait a day, then post.
- [ ] HN: needs an account with some history.
- [ ] Post one community at a time, not all three Reddit subs.
