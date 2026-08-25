"""PS-171 arm H — do the two instruments COUNT THE SAME PROCESSES?

WHY THIS FILE EXISTS
--------------------
Code review (round 2) observed that the two harnesses in this directory do not
merely differ in their CPU column — they may not be counting the same SET of
processes at all:

    abd_harness.py:56,64  ps -eo ...,comm   , matched with `"firefox" in comm`
    a2.py:74-77 / e2.py:60  walk /proc      , matched with `ENGINE_DIR in cmdline`

`comm` is the kernel's TASK_COMM_LEN field, capped at 15 characters, and
Firefox's content children are named `Isolated Web Co` / `Web Content` — neither
contains "firefox". So the `ps comm` matcher would see the PARENT ONLY, while the
`/proc cmdline` matcher sees the WHOLE PROCESS TREE.

That was an inference from reading the source. This arm MEASURES it instead:
one real engine, both matchers applied AT THE SAME INSTANT, printing what each
one actually matched. If the inference is right, `n` is not comparable across
arms B/D and arms A2/E/F, and the record must say so.

This arm deliberately does NOT try to reach the stall. It only needs a live
engine with a couple of tabs open to compare the two matchers. It is cheap and
it settles a question the committed data can otherwise only hint at (arms B and
F are the same configuration and report n=2 vs n=11-12).
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, "/workspace/persona")

LOG = open(os.environ.get("PS171_LOG", "H_matchers.txt"), "w", buffering=1)


def say(msg):
    LOG.write(msg + "\n")
    LOG.flush()
    print(msg, flush=True)


ENGINE_DIR = os.path.expanduser("~/.cache/invisible-playwright")
_t0 = time.time()


def match_ps_comm():
    """EXACTLY the matcher abd_harness.py uses (arms B, D)."""
    out = subprocess.run(
        ["ps", "-eo", "pid,stat,pcpu,rss,nlwp,etimes,comm"],
        capture_output=True, text=True, timeout=10,
    ).stdout
    hits = []
    for line in out.strip().splitlines()[1:]:
        f = line.split(None, 6)
        if len(f) == 7 and "firefox" in f[6].lower():
            hits.append((int(f[0]), f[6]))
    return hits


def match_proc_cmdline():
    """EXACTLY the matcher a2.py / e2.py use (arms A2, E, F)."""
    hits = []
    for pid in sorted(p for p in os.listdir("/proc") if p.isdigit()):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                if ENGINE_DIR not in fh.read().decode("utf8", "replace"):
                    continue
            with open(f"/proc/{pid}/comm") as fh:
                comm = fh.read().strip()
        except Exception:
            continue
        hits.append((int(pid), comm))
    return hits


def both(label):
    """Apply both matchers as close to the same instant as possible."""
    a = match_ps_comm()
    b = match_proc_cmdline()
    sa, sb = {p for p, _ in a}, {p for p, _ in b}
    say(f"--- {label} | t={round(time.time()-_t0,1)}")
    say(f"    ps comm      n={len(a):<3} pids={sorted(sa)}")
    say(f"    /proc cmdline n={len(b):<3} pids={sorted(sb)}")
    say(f"    SEEN ONLY BY /proc cmdline (n={len(sb-sa)}): "
        + json.dumps(sorted((p, c) for p, c in b if p not in sa)))
    say(f"    seen only by ps comm (n={len(sa-sb)}): "
        + json.dumps(sorted((p, c) for p, c in a if p not in sb)))
    return len(a), len(b)


from invisible_playwright import InvisiblePlaywright  # noqa: E402
from src.services.verify.masking_layer import DEFAULT_LOCALE, context_for  # noqa: E402

say("START arm H | both matchers, same instant, one real engine (layer OFF, headless)")
say(f"ENGINE_DIR={ENGINE_DIR}")
say("PURPOSE: settle whether `n` is comparable across arms B/D and arms A2/E/F.")
try:
    with InvisiblePlaywright(headless=True, humanize=False,
                             locale=DEFAULT_LOCALE) as live:
        ctx, note = context_for(live)
        say(f"LAUNCH {round(time.time()-_t0,2)}s | {note}")

        first = ctx.new_page()
        first.goto("about:blank", timeout=30000)
        n1a, n1b = both("tab 1 open")

        second = ctx.new_page()
        second.goto("about:blank", timeout=30000)
        n2a, n2b = both("tab 2 open")

        say("")
        say("RESULT " + json.dumps({
            "arm": "H",
            "ps_comm_n": {"tab1": n1a, "tab2": n2a},
            "proc_cmdline_n": {"tab1": n1b, "tab2": n2b},
            "matchers_agree": n1a == n1b and n2a == n2b,
        }))
except Exception as exc:
    say(f"!! arm H failed: {type(exc).__name__}: {exc}")
    raise
