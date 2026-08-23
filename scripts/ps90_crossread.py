"""PS-90's internal webgl.readback probe, on the SAME two seeds the live
checker readings used — the opposite side of the PS-97 vector.

The whole point of PS-97 was that an INTERNAL difference failed to survive the
trip to the checker. So the two sides have to be read against each other: an
internal probe seeing two different buffers while CreepJS reads one identical
hash IS that failure, and reading either side alone cannot detect it.

One axis varied: the fingerprint seed. Everything else is the pinned baseline
profile, so a difference is attributable to the seed and to nothing else.
"""
import dataclasses
import json
import os
import sys

# Python puts THIS FILE's directory on sys.path, not the cwd — so `python
# scripts/ps90_crossread.py` from the repo root dies on `No module named
# 'src'`. Measured, not guessed: that is exactly how this script first failed.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.verify import baseline  # noqa: E402

SEEDS = (4242, 1337)
out = {}

for seed in SEEDS:
    prof = baseline.baseline_profile(name=f"ps128-readback-seed{seed}")
    # Freeze the seed explicitly rather than letting it fall out of crc32(name):
    # the seed is THE axis under test, so it is stated, not derived.
    prof = dataclasses.replace(prof, fingerprint_seed_value=seed)
    assert prof.fingerprint_seed == seed, prof.fingerprint_seed

    snap = baseline.record_snapshot(profile=prof, fresh=True)
    win = snap["probes"]["window"]
    rb = win.get("webgl.readback", {})
    unmasked = win.get("webgl.unmasked", {})
    out[seed] = {
        "readback": rb.get("value"),
        "unmasked": unmasked.get("value"),
        "errors": baseline.count_errors(snap),
    }
    print(f"seed {seed}: readback={rb.get('value')} errors={out[seed]['errors']}",
          flush=True)

print()
a, b = out[SEEDS[0]], out[SEEDS[1]]
da = (a["readback"] or {}).get("digest")
db = (b["readback"] or {}).get("digest")
print(f"seed {SEEDS[0]} digest: {da}")
print(f"seed {SEEDS[1]} digest: {db}")
print(f"INTERNAL PROBE DIFFERS: {da != db}")
json.dump(out, open("/tmp/ps90_crossread.json", "w"), indent=2)
