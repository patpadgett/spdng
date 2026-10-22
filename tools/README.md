# tools

Development helpers. They are not part of the installed package.

## `pty_capture.py`

Runs a command on a pty and renders the screen the way a terminal
would show it.

    python3 tools/pty_capture.py --rows 30 --cols 110 -- ./spdng watch

This is the only honest way to check a TUI layout. A pty capture is a
byte stream of cursor movement, not a picture of a screen. Reading it
as text gives a misleading picture: frames appear concatenated, and
you cannot tell whether a layout is right.

    python3 tools/pty_capture.py --keys "s,j,q" -- ./spdng live 1.1.1.1

sends keystrokes, one every half second, so you can prove a hotkey
works. Appearing in the on-screen footer is not proof; check that the
file landed.

## `pty_timing.py`

Runs a command on a pty, sends a key, and reports how long the process
took to exit.

    python3 tools/pty_timing.py --delay 2 --keys q -- ./spdng watch

This is how you check that a hotkey acts immediately rather than after
the current work finishes. The exit time is the measurement.

Three things a pty harness has to get right, and all three look like
the program is broken when they are wrong:

- The child needs a usable `TERM`. Without one, curses cannot
  initialise, the program falls back to a mode that does not read the
  keyboard, and the key does nothing.
- Turn `ECHO` off on the slave, or the key is echoed straight back and
  never reaches the program's read.
- The parent must close its copy of the slave after the fork.

## `proof.py`

Not a tool: it lives in the repository root because it is the
reproduction script for the `speedtest-cli` defects, and the README
points at it.
