#!/usr/bin/env python3
"""ARM-COST — AC #8's figure, taken with a COMMITTED instrument.

⛔ WHY THIS FILE EXISTS RATHER THAN A NUMBER IN A COMMENT. Round 1 quoted
"0.26% of one core" from an ad-hoc measurement with no committed instrument, so
when the matcher changed under it the figure could not be re-taken — only
re-asserted. A cost figure that cannot be reproduced is a claim, not a
measurement.

WHAT IT MEASURES. The wall-clock cost of ONE ``_sample()`` call against whatever
tree the given profile dir resolves to, repeated, plus the bytes one emitted
line occupies. From those: samples/second, bytes/hour at the shipped cadence,
and the fraction of one core the sampler costs.

⚠️ THE DOMINANT TERM IS NOT THE TREE, IT IS THE /proc WALK, and that is the
finding this instrument exists to make visible. ``engine_pids_for`` opens
``cmdline`` for EVERY numeric entry under ``/proc``, so the cost is bounded by
how many processes the BOX runs, not by how many the browser runs. Rec 2's
"two /proc reads per sample" under-describes it. The ``--scale`` arm measures
that directly by timing the walk against synthetic /proc roots of growing size,
so a figure quoted without its /proc population is not reproducible.

    python3 readings/ps396-2026-09-11/arm-cost.py /tmp/ps396-sib/work
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.services.browser import session_series as ss  # noqa: E402


def _proc_population(proc_root: str = "/proc") -> int:
    return sum(1 for e in os.listdir(proc_root) if e.isdigit())


def measure(profile_dir: str, n: int = 30) -> None:
    clk = ss._clock_ticks()
    prev = ss._Readings()
    started = time.time()
    # One warm sample first: the FIRST sample of any session books counters and
    # yields nulls by construction, and it is not representative.
    ss._sample(profile_dir, prev, started, clk)

    durations = []
    for _ in range(n):
        t0 = time.perf_counter()
        sample = ss._sample(profile_dir, prev, started, clk)
        durations.append(time.perf_counter() - t0)
        time.sleep(0.05)

    durations.sort()
    med = durations[len(durations) // 2]
    mean = sum(durations) / len(durations)
    import json
    line_bytes = len(json.dumps(sample, separators=(",", ":")) + "\n")

    period = ss.PERIOD_S
    per_hour = 3600.0 / period
    print(f"/proc population        {_proc_population():>10d} numeric entries")
    print(f"tree size (nproc)       {sample['nproc']:>10d} processes")
    print(f"samples measured        {n:>10d}")
    print(f"per-sample median       {med * 1000:>10.2f} ms")
    print(f"per-sample mean         {mean * 1000:>10.2f} ms")
    print(f"cadence                 {period:>10.1f} s")
    print(f"samples/second          {1.0 / period:>10.3f}")
    print(f"bytes/sample            {line_bytes:>10d} B")
    print(f"bytes/hour              {per_hour * line_bytes / 1024:>10.1f} KiB")
    print(f"cap reached after       {ss.MAX_BYTES / (per_hour * line_bytes):>10.1f} h")
    print(f"⚠️ cpu cost             {med / period * 100:>10.2f} % of ONE core")
    print("   (= per-sample wall time / cadence; wall time, not cpu time, so")
    print("    it is an upper bound — a sample blocked on a /proc read is")
    print("    counted as if it were spinning.)")


def scale(reference_dir: str) -> None:
    """⚠️ THE COST IS BOUNDED BY THE BOX, NOT BY THE BROWSER — measured.

    Builds synthetic /proc roots of growing size, each with ONE matching entry,
    and times the matcher over them. If the walk dominates, the curve is linear
    in the /proc population and flat in the tree size.
    """
    import shutil
    import tempfile
    print("\n/proc entries   walk time   extrapolated % of one core at 2 s")
    root = tempfile.mkdtemp(prefix="ps396-scale-")
    try:
        for n in (100, 400, 800, 1600):
            base = os.path.join(root, str(n))
            os.makedirs(base, exist_ok=True)
            for pid in range(1, n + 1):
                d = os.path.join(base, str(pid))
                os.makedirs(d, exist_ok=True)
                cmd = (f"/engine/chromium --user-data-dir={reference_dir}"
                       if pid == 1 else f"/usr/bin/other-process-{pid} --flag")
                with open(os.path.join(d, "cmdline"), "wb") as fh:
                    fh.write(cmd.encode() + b"\0")
            t0 = time.perf_counter()
            for _ in range(5):
                ss.engine_pids_for(reference_dir, proc_root=base)
            dt = (time.perf_counter() - t0) / 5
            print(f"{n:>13d}   {dt * 1000:>7.2f} ms   {dt / ss.PERIOD_S * 100:>10.2f} %")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    measure(sys.argv[1])
    scale(sys.argv[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
