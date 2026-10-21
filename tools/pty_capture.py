"""Capture a command's output from a pty and render the screen.

A pty capture is a byte stream of cursor movement, not a picture of a
screen. Reading it as text gives a misleading picture: frames appear
concatenated and you cannot tell whether a layout is right. This replays
the stream onto a character grid the way a terminal would.

Usage:
    python3 pty_capture.py [--rows N] [--cols N] [--keys "s,j,q"]
                           [--wait SECONDS] -- command args...

The --keys option sends keystrokes, comma separated, one every half
second. That is how you prove a hotkey works: send the byte, then check
the file landed. Appearing in the on-screen footer is not proof.
"""
from __future__ import annotations

import argparse
import fcntl
import os
import pty
import select
import struct
import termios
import time

try:
    import pyte
except ImportError:
    pyte = None


def capture(command, rows=40, cols=120, keys="", wait=0.0, env=None):
    """Run `command` on a pty and return everything it wrote."""
    master, slave = pty.openpty()
    # The window size must be set, or curses initialises against a 0x0
    # terminal and the whole run proves nothing.
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    pid = os.fork()
    if pid == 0:
        os.setsid()
        os.dup2(slave, 0)
        os.dup2(slave, 1)
        os.dup2(slave, 2)
        os.close(master)
        full_env = dict(os.environ)
        if env:
            full_env.update(env)
        os.execvpe(command[0], command, full_env)
        os._exit(127)

    os.close(slave)
    buf = b""
    pending = [k for k in keys.split(",") if k]
    deadline = time.time() + wait if wait else None
    next_key = time.time() + 1.0

    while True:
        timeout = 0.2
        try:
            ready, _, _ = select.select([master], [], [], timeout)
        except OSError:
            break
        if ready:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
        if pending and time.time() >= next_key:
            try:
                os.write(master, pending.pop(0).encode())
            except OSError:
                pass
            next_key = time.time() + 0.5
        if deadline and time.time() > deadline:
            try:
                os.write(master, b"q")
                time.sleep(0.3)
            except OSError:
                pass
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass
            break
        try:
            wpid, _ = os.waitpid(pid, os.WNOHANG)
            if wpid == pid:
                # Drain whatever is left.
                deadline = time.time() + 0.3
                while time.time() < deadline:
                    r, _, _ = select.select([master], [], [], 0.1)
                    if not r:
                        break
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    buf += chunk
                break
        except ChildProcessError:
            break

    try:
        os.close(master)
    except OSError:
        pass
    return buf


def render(data: bytes, rows=40, cols=120):
    """Render the byte stream onto a character grid."""
    text = data.decode("utf-8", "replace")
    if pyte is None:
        return text
    screen = pyte.Screen(cols, rows)
    stream = pyte.Stream(screen)
    stream.feed(text)
    return "\n".join(line.rstrip() for line in screen.display).rstrip()


def render_colours(data: bytes, rows=40, cols=120):
    """Render, and report which cells ended up in which colour."""
    if pyte is None:
        return {}
    text = data.decode("utf-8", "replace")
    screen = pyte.Screen(cols, rows)
    stream = pyte.Stream(screen)
    stream.feed(text)
    counts = {}
    for y in range(rows):
        line = screen.buffer[y]
        for x in range(cols):
            cell = line[x]
            if cell.data and cell.data.strip():
                key = (cell.fg, cell.bold)
                counts[key] = counts.get(key, 0) + 1
    return counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=40)
    parser.add_argument("--cols", type=int, default=120)
    parser.add_argument("--keys", default="")
    parser.add_argument("--wait", type=float, default=0.0)
    parser.add_argument("--raw", action="store_true",
                        help="print the raw bytes instead of the screen")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("no command given")

    data = capture(command, rows=args.rows, cols=args.cols, keys=args.keys,
                   wait=args.wait)
    if args.raw:
        print(repr(data))
        return
    print(render(data, rows=args.rows, cols=args.cols))
    counts = render_colours(data, rows=args.rows, cols=args.cols)
    if counts:
        print()
        print("colours used (fg, bold) -> cells:")
        for key, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"  {key} -> {n}")


if __name__ == "__main__":
    main()
