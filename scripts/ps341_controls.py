"""PS-341 CONTROLS — what makes the two candidate findings mean anything.

The main run (``ps341_run.py``) produced two differences across the build
change. NEITHER is interpretable on its own, and this file is the reason:

  * **WebGL identity moved** (``RTX 3060`` on 152 → ``Iris Xe`` on 148, same
    profile, same seed). Ambiguous between *the build change moved it* and
    *the value is simply not stable across launches at all*. A value that
    re-rolls on every launch of ONE build is a different (and much larger)
    defect than one that is stable within a build and moves across builds —
    and it is not the one this ticket is about.
  * **The sentinel cookie was not served** on the older build, though its row
    was still in the SQLite file. Ambiguous between *the older build could not
    read it* and *the cookie never persisted across ANY restart*, which would
    make the reading a fact about my probe rather than about the downgrade.

So each candidate gets the control that separates those:

  C1  same build (N), two launches, same profile — is the GPU pair STABLE
      within one build? If it re-rolls here, the cross-build difference is not
      attributable to the build.
  C2  same build (N-1), two launches — the same question on the other side.
  C3  same build (N), cookie written then read back after a full restart — does
      the cookie survive a restart AT ALL when the build does not move?
  C4  forward again (N-1 → N) through the same gesture — does the cookie the
      older build could not serve come BACK on the newer one? This is what
      tells "could not read it" apart from "destroyed it", and they are
      completely different outcomes for an operator.

C4 is also a second, independent positive control on the revert mechanism
itself: it moves the build a second time, in the opposite direction.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from scripts.ps341_engine_continuity import _sha256, _tree_snapshot, read_live, write_cookie  # noqa: E402
from scripts.ps341_run import _install_sandbox_waiver, _serve_origin, run_leg  # noqa: E402

OUT = os.environ.get("PS341_CONTROL_OUT", "/tmp/ps341/controls.json")


def main() -> int:
    from src.core.config import DATA_DIR
    from src.models.profile import Profile
    from src.services.browser import process as bp
    from src.services.engine import updater

    _install_sandbox_waiver(bp)
    updater.set_in_use_provider(lambda: False)
    origin_srv, origin = _serve_origin()

    res = {"origin": origin, "engine_at_start": updater.current_version()}

    # The SAME profile identity as the main run — same name, so the same seed,
    # which is what makes the GPU comparison meaningful.
    profile = Profile(
        name="ps341-continuity",
        proxy=None,
        os_type="windows",
        engine="chromium",
        search_engine="brave",
        ai_control=True,
    )
    profile_dir = os.path.join(DATA_DIR, profile.name)

    which = os.environ.get("PS341_CONTROL", "gpu")

    if which == "gpu":
        # C1/C2 — TWO launches of the SAME build on a FRESH profile each time,
        # so the question is purely "does this build, at this seed, produce the
        # same identity twice?"
        legs = []
        for i in (1, 2):
            shutil.rmtree(profile_dir, ignore_errors=True)
            leg = run_leg(f"same-build-{i}", profile, profile_dir, origin)
            legs.append(leg)
        res["build"] = updater.current_version()
        res["legs"] = [
            {
                "label": leg["label"],
                "opened": leg["opened"],
                "webgl": [
                    (leg.get("live") or {}).get("page", {}).get("webglVendor"),
                    (leg.get("live") or {}).get("page", {}).get("webglRenderer"),
                ],
                "ua": (leg.get("live") or {}).get("page", {}).get("ua"),
                "canvas": (leg.get("live") or {}).get("page", {}).get("canvas"),
                "screen": (leg.get("live") or {}).get("page", {}).get("screen"),
                "hw": (leg.get("live") or {}).get("page", {}).get("hw"),
            }
            for leg in legs
        ]
        res["gpu_stable_within_build"] = (
            res["legs"][0]["webgl"] == res["legs"][1]["webgl"]
        )
        res["canvas_stable_within_build"] = (
            res["legs"][0]["canvas"] == res["legs"][1]["canvas"]
        )

    elif which == "cookie":
        # C3 — write on launch 1, read back on launch 2, SAME build, SAME
        # profile dir. If the cookie is absent here too, the main run's cookie
        # reading is a fact about the probe and not about the downgrade.
        shutil.rmtree(profile_dir, ignore_errors=True)
        a = run_leg("N", profile, profile_dir, origin)  # run_leg writes on "N"
        b = run_leg("restart-same-build", profile, profile_dir, origin)
        res["build"] = updater.current_version()
        res["write_leg_cookies"] = (a.get("live") or {}).get("cookies_live")
        res["read_leg_cookies"] = (b.get("live") or {}).get("cookies_live")
        res["cookie_survives_plain_restart"] = bool(
            (b.get("live") or {}).get("cookies_live")
        )

    elif which == "forward":
        # C4 — go FORWARD again through the same gesture and re-read. Tells
        # "the old build could not read it" apart from "the old build
        # destroyed it".
        res["tree_before_forward"] = _tree_snapshot(profile_dir)
        res["builds_before"] = updater._read_builds()
        ok, msg = updater.revert_to_previous_build(
            timeout=900, log=lambda m: print("FWD LOG:", m, flush=True)
        )
        res["forward"] = {
            "ok": ok,
            "message": msg,
            "version_after": updater.current_version(),
            "sha_after": _sha256(updater.ENGINE_BINARY),
        }
        if ok:
            leg = run_leg("forward-again", profile, profile_dir, origin)
            res["forward_leg"] = {
                "opened": leg["opened"],
                "ua": (leg.get("live") or {}).get("page", {}).get("ua"),
                "cookies": (leg.get("live") or {}).get("cookies_live"),
                "webgl": [
                    (leg.get("live") or {}).get("page", {}).get("webglVendor"),
                    (leg.get("live") or {}).get("page", {}).get("webglRenderer"),
                ],
                "canvas": (leg.get("live") or {}).get("page", {}).get("canvas"),
                "tree_after": leg.get("tree_after_shutdown"),
            }

    origin_srv.shutdown()
    with open(OUT, "w") as f:
        json.dump(res, f, indent=2, default=str)
    print("WROTE", OUT, flush=True)
    print(json.dumps({k: v for k, v in res.items() if "leg" not in k}, indent=1)[:1500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
