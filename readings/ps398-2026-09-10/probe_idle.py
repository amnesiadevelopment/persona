"""PS-398 — measure the announce->publish WINDOW, NOT the product's flake rate.

⚠️ WHAT THIS IS NOT: it does not launch Firefox, so it is NOT the "~1 run in 3"
figure PS-380 recorded and must not be reported as one. No engine binary,
no invisible_playwright and no xvfb exist in this container.

WHAT IT IS: the REAL `emit` writer and the REAL `register_ff_eval`/`get_ff_eval`
registry, driven in the ordering `_launch_and_watch` uses (emit BROWSER_STARTED,
define three closures, then register), with a reader on another thread doing
exactly what the pre-fix recorder does: read the line, then look up the hook
ONCE. It measures whether a reader that arrives on the announcement can observe
an empty registry — i.e. whether the window is reachable at all.
"""
import os
import sys
import threading
import time

sys.path.insert(0, "/workspace/persona")

from src.services.browser.invisible_launch import (  # noqa: E402
    get_ff_eval,
    register_ff_eval,
    unregister_ff_eval,
)

NAME = "ps398-window-probe"
TRIALS = 300


def trial():
    unregister_ff_eval(NAME)
    r, w = os.pipe()
    # Same construction as invisible_launch's emit writer: line-buffered.
    out = os.fdopen(w, "w", buffering=1)
    reader = os.fdopen(r)
    missed = {"v": None}

    def session():
        # emit("BROWSER_STARTED") — a write() + flush, which releases the GIL.
        out.write("BROWSER_STARTED\n")
        out.flush()

        # The three closures _launch_and_watch defines between the two lines.
        def _live_page():
            return None

        def _ff_eval(expr):
            return None

        def _ff_goto(url):
            return None

        register_ff_eval(NAME, {"eval": _ff_eval, "goto": _ff_goto})

    def recorder():
        # _await_started: return the instant BROWSER_STARTED is seen.
        while True:
            line = reader.readline()
            if not line:
                return
            if line.strip() == "BROWSER_STARTED":
                break
        # The pre-fix read: exactly once, no wait.
        hook = get_ff_eval(NAME)
        missed["v"] = not (hook and callable(hook.get("eval")))

    t = threading.Thread(target=recorder)
    t.start()
    session()
    t.join(5)
    out.close()
    reader.close()
    return missed["v"]


misses = 0
unread = 0
for _ in range(TRIALS):
    r = trial()
    if r is None:
        unread += 1
    elif r:
        misses += 1

print(f"trials={TRIALS} single-read MISSES={misses} unread={unread}")
print(f"window reachable: {misses > 0}")

# And the same trial with the FIX's bounded wait in place of the single read.
from src.services.verify import baseline as bl  # noqa: E402


class _Alive:
    def poll(self):
        return None


def trial_fixed():
    unregister_ff_eval(NAME)
    r, w = os.pipe()
    out = os.fdopen(w, "w", buffering=1)
    reader = os.fdopen(r)
    missed = {"v": None}

    def session():
        out.write("BROWSER_STARTED\n")
        out.flush()

        def _ff_eval(expr):
            return None

        register_ff_eval(NAME, {"eval": _ff_eval, "goto": lambda u: None})

    def recorder():
        while True:
            line = reader.readline()
            if not line:
                return
            if line.strip() == "BROWSER_STARTED":
                break
        hook = bl._await_ff_eval_hook(_Alive(), NAME)
        missed["v"] = hook is None

    t = threading.Thread(target=recorder)
    t.start()
    session()
    t.join(10)
    out.close()
    reader.close()
    return missed["v"]


misses2 = 0
unread2 = 0
started = time.monotonic()
for _ in range(TRIALS):
    r = trial_fixed()
    if r is None:
        unread2 += 1
    elif r:
        misses2 += 1
elapsed = time.monotonic() - started
print(f"trials={TRIALS} bounded-wait MISSES={misses2} unread={unread2}")
print(f"bounded-wait total elapsed={elapsed:.2f}s ({elapsed/TRIALS*1000:.2f} ms/trial)")
unregister_ff_eval(NAME)
