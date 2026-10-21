"""A minimal curses + daemon thread program, to test the exit path.

Run it under a pty and press 'q'. If it hangs after the screen closes,
the hang is in curses, not in spdng.
"""
import curses
import threading
import time


def worker():
    time.sleep(30)


def main(scr):
    threading.Thread(target=worker, daemon=True).start()
    scr.timeout(100)
    for _ in range(200):
        key = scr.getch()
        if key == ord("q"):
            break
    print("  got q, leaving curses", flush=True)


curses.wrapper(main)
print("  after wrapper, exiting", flush=True)
# The daemon thread is still sleeping. Python's shutdown joins
# non-daemon threads, but this one IS a daemon, so it should not
# block. If it does, the hang is in the interpreter's finalisation.
print("  threads at exit:", threading.enumerate(), flush=True)
