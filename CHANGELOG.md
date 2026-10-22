# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.7.0] - 2026-10-21

The first public release.

### Added

- **An mtr-style per-hop trace** to the test server, with loss, latency
  and a probe history per hop. `spdng live` runs it as a repeating
  curses view, the way mtr does.
- **A live watch view** with a bar graph per metric and a time-based
  chart of download over time. The upload chart can be shown and hidden
  with `u`.
- **Three providers** - Ookla, Cloudflare and LibreSpeed - so you can
  tell a slow link from a slow route. `spdng compare` runs them side by
  side and says how each differs from the first.
- **A local history**, so you can see whether a connection is getting
  worse rather than only what it is right now.
- **CSV, JSON and text exports** from every view, plus a save hotkey in
  the live views.
- **`spdng doctor`**, which reports what the environment supports: raw
  ICMP sockets, unprivileged ICMP, the `ping` binary, the traceroute
  method in use, and whether colour is on.
- **Boxed output**, with the columns lined up exactly, plain and
  coloured, at any terminal width.

### Fixed

These are the defects in `speedtest-cli` that `spdng` does not have.
Each one is reproducible with `proof.py` in the repository root.

- **Latency is measured on a warm connection.** `speedtest-cli` opened
  a fresh TCP and TLS connection for every probe, so the number it
  reported was the handshake plus the round trip - and a single slow
  handshake could drag a five-sample average up by hundreds of
  milliseconds. `spdng` opens the connection once, discards the
  handshake, and reports the handshake separately.
- **A failed probe is never a number.** `speedtest-cli` appended the
  sentinel `3600` seconds on failure and averaged it in with the real
  measurements, which is how a failure turned into exactly
  `1800000.0` ms. `spdng` reports loss, and never a placeholder.
- **HTTP redirects are followed**, using the canonical host so there is
  no extra round trip. `speedtest-cli` did not follow them at all, so
  every latency probe against a current server failed.
- **The nearest server is used.** `speedtest-cli` used a static,
  non-geo-aware list; from Tampa it measured against a server 1,753 km
  away. `spdng` ranks by distance.
- **The divisor matches the probe count.** `speedtest-cli` took three
  probes and divided the sum by six, so every successful latency was
  reported at half its true value.

### Notes

- Zero dependencies. The standard library covers HTTP, TLS, concurrency,
  curses and CSV.
- Python 3.10 and later, on Linux and macOS.
- The name is distinct from `speedtest-cli` on purpose: the original
  author owns that name, and this is a different tool.

[Unreleased]: https://github.com/patpadgett/spdng/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/patpadgett/spdng/releases/tag/v0.7.0
