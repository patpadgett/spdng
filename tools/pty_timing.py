"""Run a command on a pty, send keys, and report when it exited.

Usage:  python3 tools/pty_timing.py [--delay S] [--keys "q"] -- cmd args...

Prints how long the process took to exit after the key was sent. This
is how you check that a hotkey acts immediately rather than after the
current work finishes.

Three things bite when you write this yourself, and all three cost
real time:

  * The exit check has to come BEFORE draining the pty. Once the child
    is gone the pty can still hold data for ever, because the slave
    stays open while any of the child's threads live. select() then
    says "ready" for ever and the drain loop never finishes.
  * The read must be non-blocking. A read that starts just as the
    child exits waits for the master to close, which only happens
    when every copy of the slave is closed.
  * The read must be the ONLY thing that is non-blocking. Set the
    master non-blocking for the read and back to blocking afterwards,
    or the write of the key can be dropped with EAGAIN and the
    program never sees it.
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
    parser.add_argument("--rows", type=int, default=40)
    parser.add_argument("--cols", type=int, default=120)
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
    # Echo off, so a key reaches the program instead of coming back.
    try:
        attrs = termios.tcgetattr(slave)
        attrs[3] &= ~termios.ECHO
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
    except termios.error:
        pass

    pid = os.fork()
    if pid == 0:
        os.setsid()
        os.dup2(slave, 0)
        os.dup2(slave, 1)
        os.dup2(slave, 2)
        os.close(master)
        os.execv(command[0], command)
        os._exit(127)
    os.close(slave)

    start = time.time()
    sent = False
    while time.time() - start < args.timeout:
        try:
            wpid, _status = os.waitpid(pid, os.WNOHANG)
            if wpid == pid:
                elapsed = time.time() - start
                print(f"  sent {args.keys!r} at {args.delay:.1f}s; "
                      f"PROCESS EXITED at {elapsed:.2f}s "
                      f"(waited {elapsed - args.delay:.2f}s)")
                return 0
        except ChildProcessError:
            break

        ready, _, _ = select.select([master], [], [], 0.1)
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
            try:
                os.write(master, args.keys.encode())
            except OSError as exc:
                print(f"  could not send the key: {exc}")
            sent = True

    state = "gone"
    try:
        stat = open(f"/proc/{pid}/stat").read().split()
        state = (f"{stat[2]} "
                 f"threads={len(os.listdir(f'/proc/{pid}/task'))}")
    except (OSError, IndexError):
        pass
    try:
        os.kill(pid, 9)
    except ProcessLookupError:
        pass
    print(f"  sent {args.keys!r} at {args.delay:.1f}s; "
          f"DID NOT EXIT within {args.timeout:.0f}s  [child: {state}]")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
