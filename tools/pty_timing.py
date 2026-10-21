"""Run a command on a pty, send a key, and report how long it took to exit.

Usage:  python3 tools/pty_timing.py [--delay S] [--keys "q"] -- cmd args...

This is how you check that a hotkey acts immediately rather than after
the current work finishes. The exit time is the measurement.

Four things bite when you write this yourself, and all of them cost
real time:

  * The exit check has to come BEFORE draining the pty. Once the child
    is gone the pty can still hold data for ever, because the slave
    stays open while any of the child's threads live. select() then
    says "ready" for ever and the drain loop never finishes.
  * The read must be non-blocking. A read that starts just as the
    child exits waits for the master to close, which only happens when
    every copy of the slave is closed.
  * The pty slave starts in ECHO mode, so a key written to the master
    comes straight back out and never reaches the program's read.
    Turn echo off on the slave BEFORE the child starts.
  * The parent must close its copy of the slave after the fork. If it
    keeps a second copy open, the child's exit does not close the
    master, and the exit is not noticed the way it should be.
  * The child needs a usable TERM. Without one, curses cannot
    initialise, the program falls back to a mode that never reads the
    keyboard, and the key does nothing at all - which looks exactly
    like a program that ignores the keyboard.
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--delay", type=float, default=1.0,
                        help="seconds before sending the keys")
    parser.add_argument("--keys", default="q")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--rows", type=int, default=30)
    parser.add_argument("--cols", type=int, default=100)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("no command given")

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ,
                struct.pack("HHHH", args.rows, args.cols, 0, 0))
    # Echo off, so the key reaches the program instead of coming back.
    attrs = termios.tcgetattr(slave)
    attrs[3] &= ~termios.ECHO
    termios.tcsetattr(slave, termios.TCSANOW, attrs)

    pid = os.fork()
    if pid == 0:
        os.setsid()
        os.dup2(slave, 0)
        os.dup2(slave, 1)
        os.dup2(slave, 2)
        os.close(master)
        # The child needs a usable TERM, or curses cannot initialise
        # and the program falls back to a mode that never reads the
        # keyboard. The key then does nothing at all, which looks
        # exactly like a program that ignores the keyboard.
        environ = dict(os.environ)
        environ.setdefault("TERM", "xterm-256color")
        os.execve(command[0], command, environ)
        os._exit(127)
    os.close(slave)

    start = time.time()
    sent = False
    while time.time() - start < args.timeout:
        try:
            wpid, _status = os.waitpid(pid, os.WNOHANG)
            if wpid == pid:
                elapsed = time.time() - start
                print(f"  pressed {args.keys!r} at {args.delay:.1f}s; "
                      f"exited at {elapsed:.2f}s "
                      f"(waited {elapsed - args.delay:.2f}s)")
                return 0
        except ChildProcessError:
            break
        ready, _, _ = select.select([master], [], [], 0.05)
        if ready:
            try:
                os.set_blocking(master, False)
                while True:
                    chunk = os.read(master, 65536)
                    if not chunk:
                        break
            except (BlockingIOError, OSError):
                pass
        if not sent and time.time() - start > args.delay:
            os.write(master, args.keys.encode())
            sent = True
    print(f"  pressed {args.keys!r} at {args.delay:.1f}s; "
          f"DID NOT EXIT within {args.timeout:.0f}s")
    try:
        os.kill(pid, 9)
    except ProcessLookupError:
        pass
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
