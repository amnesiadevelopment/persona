"""PS-161: WHOSE GPU is it, and does the ENGINE's own one vary per profile?

This script takes the two measurements PS-161 requires BEFORE the owner's
(a)/(b) choice can be made, and it takes them in one grid because they are two
readings of the same cell:

**M1 — is ``gpu_ext.py``'s header rationale still true?**
    The module header justifies the extension's existence by asserting that
    WITHOUT it the engine reads as a generic ``Google Inc. (Google)`` /
    SwiftShader pair — an instant headless tell. The ``ps143`` layer-off control
    measured the opposite on ONE arm and ONE seed. This re-measures it.
    It matters because **option (a) means extending a module whose reason for
    existing has not been re-verified.**

**M2 — does the engine's own GPU identity vary PER PROFILE?** (the decisive one)
    Added by the planner's comment and confirmed by confirm-review, and it is
    NOT the same question as M1. M1 asks whether the engine's value is
    *plausible*; M2 asks whether it is *different per profile*. They come apart
    cleanly: an engine that hands every profile an identical, entirely
    believable ``AMD Radeon (0x00001638)`` PASSES M1 and FAILS M2.

    * engine's value VARIES by seed  -> (b) is viable. One author by
      construction, unlinkability preserved.
    * every seed gets the SAME card  -> **(b) is off the table** and there is
      nothing for the owner to decide. Deferring to the engine would hand every
      persona profile on that OS the same graphics card — a shared cross-profile
      identifier, and Level 2 of the project's bar is *mutual unlinkability*.
      (a) becomes forced and the ticket proceeds without a decision.

WHY THE GRID IS PER DECLARED ARM AND NOT ONE CELL
--------------------------------------------------
Every ``0x1638`` reading in PS-161 is from the LINUX arm, at one seed. Prior
worker-seat evidence (PS-69) recorded the engine's GPU varying by seed on the
windows and macos arms (``Intel Iris Xe``/``RTX 4060``, ``Apple M4``/``M2``) —
none of which appear anywhere in ``src/``, so they are the engine's own
seed-derived values and not ours leaking in. Our own pool confirms the arms are
structurally different: ``LINUX_GPUS`` has 8 entries, ``WIN_GPUS`` 5.

So a LINUX-ONLY result would answer the question for one arm and be silently
wrong for the others, and an arm-blind "the engine varies" would be equally
wrong the other way. **The answer may legitimately be a SPLIT** — the engine
varying by seed on windows/macos while pinning one value on linux is a live
possibility, and it would make (b) viable on some arms and bar-violating on
others. The record reports per arm and never collapses to a single verdict.

BOTH LAYER STATES, BECAUSE THE DIFFERENCE IS THE SUBJECT
---------------------------------------------------------
Each cell is read twice — layer OFF and layer ON. OFF is what M1 and M2 are
about. ON is what the product actually presents, and the pair is what makes
"there are two independent spoofers and they disagree" a thing this record
SHOWS rather than cites. A cell where OFF and ON report the same value would
mean our layer did not reach that realm at all, which is a finding in itself.

THE VENUE IS LOOPBACK, DELIBERATELY, AND THAT IS NOT THE EXIT WAIVER
----------------------------------------------------------------------
PS-161 states the proxied exit is mandatory and must not fall back. That rule
is about CHECKER readings: a direct-connection reading hands the operator's real
address to creepjs/pixelscan. **This script contacts no third party.** It reads
what the browser reports to a page served from ``127.0.0.1`` — there is no
remote observer, so there is no address to leak and nothing an exit would
protect. ``local_probe.py`` establishes exactly this venue for exactly this
reason, and PS-10 records an explicit instruction not to re-introduce the exit
dependency for reads that do not need it.

What this does NOT do, stated so the record cannot be over-read: it does not
discharge PS-161's live half. The definition-of-done's "fresh live run through
the proxied exit, verified by the merged consistency check" is a CHECKER-matrix
run and still requires the exit. This is the investigation that precedes the
decision, not the verification that follows the fix.

THE INSTRUMENT IS CHECKED BEFORE THE PRODUCT (PS-14)
------------------------------------------------------
On Linux the tier deliberately launches with ``--use-angle=swiftshader``
(``chromium_tier.py`` ``_launch_args``). So a SwiftShader row here could
originate in the INSTRUMENT'S launch flags rather than in the product — which
is a pre-registered explanation to reach for, not a defect to assume. The argv
actually used is captured and written into the record for every cell, so a
reader can check the flags before attributing anything. Note the trap this
creates for M1 specifically: the header's claim is *"the engine falls back to
SwiftShader"*, and a harness that FORCES swiftshader could manufacture a false
confirmation of it. The ``ps143`` control says it does not currently manifest
(the engine populates a believable adapter with the flag set), but the argv is
recorded so that stays checkable rather than assumed.

Run it from the repo root::

    .venv/bin/python -m scripts.ps161_engine_gpu_identity -o readings/ps161-.../
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import time

# The declared arms. Each is a `--fingerprint-platform` value the engine
# honours, and they are structurally different products of the engine's own
# spoofer — see the module docstring on why one arm cannot stand for the rest.
ARMS = ("linux", "windows", "macos")

# Two seeds is the minimum that can answer M2 at all: one seed can only report
# a value, never whether it varies. They are the SAME two the prior worker-seat
# measurement (PS-69) used, so this grid is comparable to that record rather
# than starting a fresh, incomparable series.
SEEDS = (4242, 1337)

# How long to let the page settle after load before reading it. WebGL context
# creation plus the extension's overrides are synchronous, so this is short;
# it exists so a slow first paint does not read as "the page said nothing".
SETTLE_SECONDS = 2.0


# The page reads the vectors the question is ABOUT and nothing else.
#
# Both WebGL1 and WebGL2 are read, and MASKED alongside UNMASKED. Not because
# PS-155's "one spoof leaking through an uninstrumented read path" hypothesis is
# being revived — PS-161 closes that lead explicitly and this script does not
# restart from it — but because the two realms are cheap to read at the same
# instant, and a record that read only one could not tell a reader whether the
# arm it reported was representative. Reading them says what was seen; it does
# not reopen the hypothesis.
GPU_PROBE_JS = """
(function () {
  function readCtx(kind) {
    var c = document.createElement('canvas');
    c.width = 64; c.height = 64;
    var gl = null;
    try {
      if (kind === 'webgl2') {
        gl = c.getContext('webgl2');
      } else {
        gl = c.getContext('webgl') || c.getContext('experimental-webgl');
      }
    } catch (e) { gl = null; }
    if (!gl) return {available: 'false'};
    var out = {available: 'true'};
    try { out.vendor = String(gl.getParameter(gl.VENDOR)); }
    catch (e) { out.vendor = 'throws:' + e; }
    try { out.renderer = String(gl.getParameter(gl.RENDERER)); }
    catch (e) { out.renderer = 'throws:' + e; }
    try {
      var d = gl.getExtension('WEBGL_debug_renderer_info');
      if (!d) {
        out.unmasked_vendor = 'no-debug-renderer-info';
        out.unmasked_renderer = 'no-debug-renderer-info';
      } else {
        out.unmasked_vendor = String(gl.getParameter(d.UNMASKED_VENDOR_WEBGL));
        out.unmasked_renderer = String(gl.getParameter(d.UNMASKED_RENDERER_WEBGL));
      }
    } catch (e) {
      out.unmasked_vendor = 'throws:' + e;
      out.unmasked_renderer = 'throws:' + e;
    }
    try {
      var lc = gl.getExtension('WEBGL_lose_context');
      if (lc) lc.loseContext();
    } catch (e) {}
    return out;
  }
  return {webgl1: readCtx('webgl1'), webgl2: readCtx('webgl2')};
})()
"""

# Read through inner_text, the SAME path a real checker page is read through
# (`page.evaluate` is blocked by CSP on real checker pages, which is why the
# tier reads pages this way). One reading path, so nothing here can succeed
# through a route the real run does not have.
GPU_PAGE_TEMPLATE = """<!doctype html>
<meta charset="utf-8">
<title>PS-161 gpu identity probe</title>
<body><pre id="out">reading...</pre>
<script>
  try {
    document.getElementById('out').textContent =
      JSON.stringify(%%GPU_JS%%, null, 2);
  } catch (e) {
    document.getElementById('out').textContent =
      JSON.stringify({error: String(e)}, null, 2);
  }
</script>
</body>
"""


def _serve_gpu_page():
    """A loopback server for the GPU probe page, as a context manager.

    Reuses ``local_probe``'s handler shape rather than inventing a second web
    server: ephemeral port on 127.0.0.1, silent logging, no CSP header so the
    page can run its own inline script.
    """
    import http.server
    import threading

    html = GPU_PAGE_TEMPLATE.replace("%%GPU_JS%%", GPU_PROBE_JS)

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib naming
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a, **kw):
            pass

    class _Server:
        def __enter__(self):
            self._srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            self._t = threading.Thread(
                target=self._srv.serve_forever,
                kwargs={"poll_interval": 0.1},
                daemon=True,
            )
            self._t.start()
            host, port = self._srv.server_address[:2]
            self.url = f"http://{host}:{port}/"
            return self

        def __exit__(self, *exc):
            try:
                self._srv.shutdown()
            finally:
                self._srv.server_close()
            self._t.join(timeout=5)

    return _Server()


def _read_cell(url: str, *, arm: str, seed: int, install_layer: bool) -> dict:
    """One cell of the grid: one arm, one seed, one layer state.

    Returns a record dict. A cell that FAILS is recorded as a failed cell with
    its error rather than omitted — an absent cell and a cell that could not be
    read are different findings, and collapsing them would let a broken arm read
    as an arm that agreed with the others.
    """
    from src.services.verify import chromium_tier

    original_args = chromium_tier._launch_args
    captured: dict = {}

    def _capturing_args(*a, **kw):
        args = original_args(*a, **kw)
        # The SURFACE THAT WAS PRESENTED, read off the command line rather than
        # echoed from the request — the PS-103 discipline the tier already
        # applies to --no-sandbox. It is what lets a reader check the
        # swiftshader flag interaction before attributing a SwiftShader row to
        # the product.
        captured["argv"] = list(args)
        return args

    chromium_tier._launch_args = _capturing_args
    record: dict = {
        "arm": arm,
        "seed": seed,
        "masking_layer": "on" if install_layer else "off",
    }
    try:
        session = chromium_tier.ChromiumSession(
            # The loopback venue: this page is served from 127.0.0.1 and has no
            # exit in the picture at all. See the module docstring — this is
            # NOT a waiver of PS-161's exit rule, which governs checker reads.
            #
            # THE EMPTY STRING, NOT ``NO_PROXY``. The sentinel is what
            # ``_proxy_server_and_bridge`` RETURNS for a no-exit venue, not what
            # it TAKES: passing it as the credential sends it down the parse
            # path and emits a literal ``--proxy-server=socks5://__no_proxy__:1080``
            # instead of ``--no-proxy-server``. Caught by reading back the argv
            # this script records — the launch had a bogus proxy that chromium
            # then bypassed for loopback anyway, so it would have read as a
            # clean run. An empty credential plus ``allow_no_proxy=True`` is the
            # form the helper's own docstring specifies.
            "",
            seed=seed,
            declared_machine=arm,
            allow_unsandboxed=True,
            allow_no_proxy=True,
            install_layer=install_layer,
        )
        with session as live:
            page = live.new_page()
            page.goto(url, timeout=90000, wait_until="load")
            time.sleep(SETTLE_SECONDS)
            text = page.inner_text("body")
            record["sandbox_waived"] = session.sandbox_waived
            record["dev_shm_waived"] = session.dev_shm_waived
            layer = getattr(session, "layer_report", None)
            if layer is not None:
                installed = getattr(layer, "installed", None)
                record["layer_installed"] = (
                    sorted(installed) if isinstance(installed, (list, tuple, set))
                    else installed
                )
        try:
            record["reading"] = json.loads(text)
        except Exception:
            record["reading"] = None
            record["raw_text"] = text[:2000]
            record["error"] = "the probe page's output was not JSON"
    except Exception as exc:
        record["reading"] = None
        record["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        chromium_tier._launch_args = original_args
        record["argv"] = captured.get("argv", [])
    return record


def _unmasked(cell: dict, realm: str = "webgl1") -> "str | None":
    """The unmasked vendor/renderer pair a cell read, or None if it read none."""
    reading = cell.get("reading") or {}
    r = reading.get(realm) or {}
    v, n = r.get("unmasked_vendor"), r.get("unmasked_renderer")
    if not v or not n:
        return None
    return f"{v} | {n}"


def _looks_like_software_rasteriser(value: str) -> bool:
    """Whether a vendor/renderer pair is the headless tell the header names.

    Deliberately a SUBSTRING test over the known software-rasteriser markers
    rather than a claim to classify every GPU string on earth. It answers the
    header's specific assertion (``Google Inc. (Google)`` / SwiftShader) and the
    adjacent llvmpipe/Mesa forms, and nothing wider.
    """
    low = value.lower()
    return any(
        m in low
        for m in ("swiftshader", "llvmpipe", "software", "google inc. (google)")
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--out", default="", help="directory to write the record into")
    ap.add_argument(
        "--arms",
        default=",".join(ARMS),
        help=f"comma-separated declared arms (default: {','.join(ARMS)})",
    )
    ap.add_argument(
        "--seeds",
        default=",".join(str(s) for s in SEEDS),
        help=f"comma-separated seeds (default: {','.join(str(s) for s in SEEDS)})",
    )
    ap.add_argument(
        "--layer-off-only",
        action="store_true",
        help="skip the layer-ON half of each cell (M1/M2 only need layer OFF)",
    )
    args = ap.parse_args()

    arms = tuple(a.strip() for a in args.arms.split(",") if a.strip())
    seeds = tuple(int(s.strip()) for s in args.seeds.split(",") if s.strip())
    layer_states = (False,) if args.layer_off_only else (False, True)

    from src.services.verify import chromium_tier

    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    engine_binary = ""
    engine_error = ""
    try:
        engine_binary = chromium_tier._engine_binary()
    except Exception as exc:
        # The scoping obligation, answered as data rather than as a crash: an
        # absent engine is a recordable outcome and PS-161 says to say so.
        engine_error = f"{type(exc).__name__}: {exc}"

    cells: list = []
    if not engine_error:
        with _serve_gpu_page() as server:
            for arm in arms:
                for seed in seeds:
                    for install_layer in layer_states:
                        label = (
                            f"{arm}/seed{seed}/"
                            f"layer-{'on' if install_layer else 'off'}"
                        )
                        print(f"[ps161] reading {label} ...", flush=True)
                        cell = _read_cell(
                            server.url,
                            arm=arm,
                            seed=seed,
                            install_layer=install_layer,
                        )
                        got = _unmasked(cell) or cell.get("error") or "(no reading)"
                        print(f"[ps161]   -> {got}", flush=True)
                        cells.append(cell)

    # ---- M2: does the ENGINE's own identity vary per seed, PER ARM? ----------
    #
    # Reported per arm and never collapsed. "The engine varies" as a single
    # verdict would be wrong in both directions if the arms disagree, and the
    # arms disagreeing is a live possibility this grid exists to detect.
    per_arm: dict = {}
    for arm in arms:
        off = [
            c for c in cells
            if c["arm"] == arm and c["masking_layer"] == "off"
        ]
        values = {c["seed"]: _unmasked(c) for c in off}
        readable = {s: v for s, v in values.items() if v}
        distinct = sorted(set(readable.values()))
        if len(readable) < 2:
            verdict = "INCONCLUSIVE"
            detail = (
                f"only {len(readable)} of {len(off)} layer-off cells produced a "
                "reading; two are needed to say whether the value varies"
            )
        elif len(distinct) > 1:
            verdict = "VARIES_BY_SEED"
            detail = (
                "the engine's own GPU identity differs per seed on this arm, so "
                "deferring to it does NOT create a shared cross-profile "
                "identifier here — (b) is viable on this arm"
            )
        else:
            verdict = "CONSTANT_ACROSS_SEEDS"
            detail = (
                "every seed read the SAME card on this arm. Deferring to the "
                "engine would give every persona profile on this OS one shared "
                "graphics card — a cross-profile identifier, which Level 2 of "
                "the bar (mutual unlinkability) forbids. (b) is NOT viable here"
            )
        # ---- M1: is the header's SwiftShader rationale still true, per arm? --
        software = {s: _looks_like_software_rasteriser(v) for s, v in readable.items()}
        if not readable:
            m1 = "INCONCLUSIVE"
        elif all(software.values()):
            m1 = "HEADER_RATIONALE_HOLDS"
        elif any(software.values()):
            m1 = "MIXED"
        else:
            m1 = "HEADER_RATIONALE_STALE"
        per_arm[arm] = {
            "layer_off_by_seed": values,
            "m2_seed_variance": verdict,
            "m2_detail": detail,
            "m1_header_rationale": m1,
            "m1_detail": (
                "the engine WITHOUT our layer reads as a software rasteriser on "
                "this arm, as gpu_ext.py's header asserts"
                if m1 == "HEADER_RATIONALE_HOLDS"
                else "the engine WITHOUT our layer reads as a plausible real GPU "
                "on this arm, NOT the generic Google/SwiftShader pair the "
                "gpu_ext.py header asserts it falls back to"
                if m1 == "HEADER_RATIONALE_STALE"
                else "not established on this arm"
            ),
            "layer_on_by_seed": {
                c["seed"]: _unmasked(c)
                for c in cells
                if c["arm"] == arm and c["masking_layer"] == "on"
            },
        }

    record = {
        "ticket": "PS-161",
        "question": (
            "M1: is gpu_ext.py's header SwiftShader rationale still true? "
            "M2 (decisive): does the ENGINE's own GPU identity vary per profile, "
            "per declared arm?"
        ),
        "started_at": started,
        "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "engine_binary": engine_binary,
        "engine_error": engine_error,
        "venue": (
            "LOOPBACK (127.0.0.1). No third party is contacted, so there is no "
            "address to leak and no exit to prove. This is NOT a waiver of "
            "PS-161's proxied-exit rule, which governs CHECKER reads; the live "
            "checker-matrix half of PS-161 still requires the exit."
        ),
        "seeds": list(seeds),
        "arms": list(arms),
        "per_arm": per_arm,
        "cells": cells,
    }

    text = json.dumps(record, indent=2, sort_keys=False)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, "engine-gpu-identity.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"[ps161] wrote {path}", flush=True)
    else:
        print(text)

    print("\n[ps161] ==== SUMMARY ====", flush=True)
    if engine_error:
        print(f"[ps161] ENGINE UNAVAILABLE: {engine_error}", flush=True)
        return 1
    for arm, r in per_arm.items():
        print(
            f"[ps161] {arm}: M1={r['m1_header_rationale']} "
            f"M2={r['m2_seed_variance']}",
            flush=True,
        )
        for seed, v in r["layer_off_by_seed"].items():
            print(f"[ps161]     layer-off seed {seed}: {v}", flush=True)
        for seed, v in r["layer_on_by_seed"].items():
            print(f"[ps161]     layer-on  seed {seed}: {v}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
