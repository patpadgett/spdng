# Contributing to spdng

Thanks for looking at it. This is a small tool with a narrow job, so
the bar for a change is mostly: does it make the tool more honest, or
more correct?

## Ground rules

**Never report a number you did not measure.** This is the whole
project. A failed probe is loss and "unavailable", never a placeholder.
If a value cannot be measured, say so in the output.

**Colour is never the only carrier of information.** Every coloured
value also carries its text, so the output reads the same in a
monochrome terminal or in a log file.

**The columns have to line up.** Plain and coloured, in every view, at
every width. `len()` on a coloured string counts the escape sequences,
so measure the *visible* width instead.

**Do not add a dependency without a good reason.** The standard
library covers HTTP, TLS, concurrency, curses and CSV, and zero
dependencies is a real advantage for a tool you install on a server.

## Getting set up

    git clone https://github.com/patpadgett/spdng && cd spdng
    python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
    .venv/bin/python -m pytest -q
    .venv/bin/ruff check .

The test suite is offline by default. The network tests are opt-in:

    SPDNG_LIVE=1 .venv/bin/python -m pytest -m live -q

## Before you open a pull request

- `pytest -q` passes, and `ruff check .` is clean.
- If you touched a view, check the layout at more than one width. The
  tests assert it, but a look at the real thing catches more.
- If you changed a measurement, say so in the commit message and say
  why the new way is more correct. The commit history is an argument
  about measurement correctness, and it should stay one.
- If you fixed a bug, add the test that would have caught it.

## The two things that will be asked about

**"Does this change the measurement?"** If it does, say so plainly, and
say what the number was before and after. A change that moves the
reported speed by more than a few percent needs an explanation.

**"Does this work without a TTY?"** It has to. Every view has to work
when the output is a pipe. Test it: `spdng speedtest | cat`.

## Reporting a bug

The output of `spdng doctor` is the first thing to include. It says
what the environment supports: raw ICMP sockets, unprivileged ICMP, the
`ping` binary, the traceroute method in use, and whether colour is on.
Most traceroute problems are environment problems, and that output
answers most of them.

Include the command you ran and the exact output. If it is a
measurement problem, the server it picked matters: `spdng servers`
lists the candidates, nearest first.

## Style

- Follow the surrounding code. It is plain, commented where the reason
  is not obvious, and it avoids cleverness.
- Comments explain WHY, not WHAT. The code says what it does.
- Keep the standard library imports at the top and the local ones
  below, which is what `ruff` enforces.
