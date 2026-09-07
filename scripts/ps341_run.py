"""PS-341 RUNNER — the live measurement. See ps341_engine_continuity.py for why.

WHAT THIS DOES, IN ORDER
------------------------
 1. builds a real persona profile and launches it through the REAL product path
    (``services/browser/process.spawn_browser``) on build N;
 2. reads Q1-Q4 from the RUNNING browser over CDP, writes a sentinel cookie,
    and shuts the session down cleanly;
 3. moves the engine backwards through the SHIPPING OPERATOR GESTURE
    (``updater.revert_to_previous_build`` — what the rollback button calls);
 4. asserts the POSITIVE CONTROL: the build really moved (record, bytes, and
    the running process's own UA major);
 5. relaunches the SAME profile dir on build N-1 through the SAME product path
    and reads Q1-Q4 again with a FRESH reader;
 6. diffs, and writes the whole thing to readings/.

TWO SUBSTITUTIONS, BOTH DISCLOSED, NEITHER TOUCHING THE CODE UNDER TEST
-----------------------------------------------------------------------
⚠️ **(1) UPSTREAM HAS ONLY ONE PERSONIUM RELEASE.** ``matching-refs/tags/
personium-`` returns exactly one tag today (``personium-152.0.7977.75``), so
there is no second PUBLISHED build for a revert to reach. The revert path is
therefore pointed at a LOOPBACK release document serving a REAL PREDECESSOR
ENGINE — ``adryfish/fingerprint-chromium 148.0.7778.215``, the upstream
persona's own engine is built from, i.e. a genuine older fingerprint-chromium
and not a stand-in. ONLY the module's API-URL CONSTANT is redirected. Every
line of ``revert_to_previous_build`` runs verbatim: ``rollback_target()``, the
``_engine_in_use()`` guard, ``fetch_release_full``, ``download_engine`` against
the RECORDED digest, ``record_installed_build``, ``write_version``, ``_set_pin``.
The bytes that land are verified against the digest recorded on disk, exactly
as they would be from GitHub.

⚠️ **(2) ``--no-sandbox`` IS INJECTED, INTO BOTH LEGS IDENTICALLY.** This
container sets ``kernel.apparmor_restrict_unprivileged_userns=1`` and is not
root, so Chromium's zygote aborts before any window paints — on BOTH builds,
and on stock ``/usr/bin/chromium`` too. It is injected PROBE-SIDE by wrapping
``Popen``; ``process.py`` is not modified. Because it is applied identically to
both legs it cannot author a DIFFERENCE between them, and a difference is the
entire subject of this measurement.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from scripts.ps341_engine_continuity import (  # noqa: E402
    _sha256,
    _tree_snapshot,
    read_live,
    write_cookie,
)

PRED_APPIMAGE = os.environ["PS341_PREDECESSOR_APPIMAGE"]
PRED_VERSION = os.environ.get("PS341_PREDECESSOR_VERSION", "148.0.7778.215")
OUT = os.environ.get("PS341_OUT", "/tmp/ps341/reading.json")


# --- the loopback release document (substitution 1) --------------------------


def _serve_predecessor(digest: str) -> tuple[HTTPServer, str]:
    asset_name = f"personium-{PRED_VERSION}-linux-x86_64.AppImage"
    holder = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def do_GET(self):
            if self.path.endswith("/asset"):
                size = os.path.getsize(PRED_APPIMAGE)
                start = 0
                rng = self.headers.get("Range")
                if rng and rng.startswith("bytes="):
                    start = int(rng.split("=", 1)[1].split("-", 1)[0] or 0)
                self.send_response(206 if start else 200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(size - start))
                if start:
                    self.send_header(
                        "Content-Range", f"bytes {start}-{size-1}/{size}"
                    )
                self.end_headers()
                with open(PRED_APPIMAGE, "rb") as f:
                    f.seek(start)
                    shutil.copyfileobj(f, self.wfile)
                return
            # the release document, in GitHub's shape
            doc = {
                "tag_name": f"personium-{PRED_VERSION}",
                "draft": False,
                "prerelease": True,
                "assets": [
                    {
                        "name": asset_name,
                        "browser_download_url": holder["base"] + "/asset",
                        "digest": f"sha256:{digest}",
                    }
                ],
            }
            body = json.dumps(doc).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", 0), H)
    base = f"http://127.0.0.1:{srv.server_port}"
    holder["base"] = base
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, base


def _serve_origin() -> tuple[HTTPServer, str]:
    """A real loopback http:// origin for the page readings.

    Needed because the two cheaper alternatives are both WRONG here: the
    engine's start page refuses ``set_content`` (Trusted Types), and a ``data:``
    URL is an opaque origin whose answers differ on exactly the origin-sensitive
    readings this measurement takes.
    """

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = b"<!doctype html><html><body><h1>ps341</h1></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}/"


# --- substitution 2: the sandbox waiver, applied to BOTH legs ----------------


def _install_sandbox_waiver(process_mod):
    """Inject ``--no-sandbox`` into BOTH legs, identically, without editing
    ``process.py``.

    ⚠️ INSERTED AFTER ``--appimage-extract-and-run``, NOT BEFORE IT. That flag
    is consumed by the AppImage RUNTIME rather than by Chromium and must stay
    argv[1] — displacing it makes the runtime try to FUSE-mount instead, which
    on this container fails with ``fuse: device not found`` and exit 127. That
    is a probe fault that reads exactly like the engine refusing to open the
    profile, i.e. a false positive on Q1, and it was observed before this line
    was corrected. ``tests/test_verify_engine_selection.py`` pins the same
    position property for the product's own argv.
    """
    real_popen = process_mod.subprocess.Popen

    def patched(args, *a, **kw):
        if isinstance(args, list) and args and any(
            str(x).startswith("--user-data-dir") for x in args
        ):
            if "--no-sandbox" not in args:
                args = list(args)
                at = (
                    args.index("--appimage-extract-and-run") + 1
                    if "--appimage-extract-and-run" in args
                    else 1
                )
                args.insert(at, "--no-sandbox")
        return real_popen(args, *a, **kw)

    process_mod.subprocess.Popen = patched


# --- one leg -----------------------------------------------------------------


def run_leg(label: str, profile, profile_dir: str, origin: str) -> dict:
    from src.services.browser import process as bp
    from src.services.browser.cdp import read_cdp_port
    from src.services.engine import updater

    leg = {
        "label": label,
        "engine_record_version": updater.current_version(),
        "engine_binary_sha256": _sha256(updater.ENGINE_BINARY),
        "engine_binary_size": os.path.getsize(updater.ENGINE_BINARY),
        "tree_before_launch": _tree_snapshot(profile_dir)
        if os.path.isdir(profile_dir)
        else None,
    }
    started = time.time() - 1
    proc = bp.spawn_browser(profile)
    leg["pid"] = proc.pid
    port = None
    deadline = time.time() + 90
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        try:
            port = read_cdp_port(profile.name, not_before=started)
            break
        except Exception:
            time.sleep(1)
    leg["exit_code_during_startup"] = proc.poll()
    if port is None:
        # Q1's failure mode, recorded as an OUTCOME rather than an absence.
        try:
            out = proc.stdout.read() if proc.stdout else ""
        except Exception:
            out = ""
        leg["opened"] = False
        leg["failure_mode"] = {
            "exit_code": proc.poll(),
            "stdout_tail": (out or "")[-4000:],
        }
        leg["tree_after_launch"] = _tree_snapshot(profile_dir)
        return leg

    leg["opened"] = True
    leg["cdp_port"] = port
    time.sleep(3)
    if label == "N":
        write_cookie(port, origin)
        time.sleep(1)
    leg["live"] = read_live(port, label, origin)
    leg["tree_while_running"] = _tree_snapshot(profile_dir)

    # A CLEAN shutdown: Chromium writes Last Version / Preferences on exit, so
    # a SIGKILL here would measure a crashed profile rather than a closed one.
    try:
        proc.terminate()
        proc.wait(timeout=40)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=20)
        except Exception:
            pass
    # ⚠️ REAP THE WHOLE GROUP, NOT THE WRAPPER. persona's chromium is an
    # AppImage WRAPPER around a multi-process browser, so the pid held here is
    # two layers above the renderers and `terminate()` alone orphans the tree
    # to init — which is exactly what `process_group`'s own comment records for
    # the product. In a HARNESS that launches many sessions the leak is not
    # untidy, it is fatal to the measurement: leaked trees from earlier legs
    # exhausted this container's 2048-PID cgroup budget, and the NEXT leg then
    # died at startup with `pthread_create: Resource temporarily unavailable`
    # and exit 133. That reads exactly like "the older build refuses to open
    # the profile" — a FALSE POSITIVE on Q1, the headline question — and it
    # was observed doing so before this teardown existed.
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        pass
    time.sleep(2)
    # ⚠️ SETTLE, AND THE LENGTH IS MEASURED RATHER THAN GUESSED. Chromium's
    # cookie store is flushed by a background task that OUTLIVES the parent
    # process exit: polling the SQLite file after a clean terminate, the
    # sentinel row was ABSENT at +2s / +5s / +10s and PRESENT at +20s. An
    # earlier draft waited 3s, so the NEXT leg opened a profile whose cookie
    # had not been written yet — which read as "the older build lost the
    # operator's session", a FALSE POSITIVE on exactly the derived-state
    # question this measurement exists to answer. The controls caught it: the
    # same "loss" reproduced with NO build change at all, on both builds.
    #
    # A shorter wait here does not produce a weaker reading, it produces a
    # WRONG one, so this is deliberately generous.
    time.sleep(45)
    leg["tree_after_shutdown"] = _tree_snapshot(profile_dir)
    return leg


def main() -> int:
    from src.core.config import DATA_DIR
    from src.models.profile import Profile
    from src.services.browser import process as bp
    from src.services.engine import updater

    _install_sandbox_waiver(bp)
    # The revert's in-use oracle FAILS CLOSED with nothing wired (it is only
    # ever reached from inside the app, which wires it). Wire the honest answer
    # for this harness: nothing is running at the moment we revert, because the
    # leg-N session was terminated and waited on above.
    running = {"any": False}
    updater.set_in_use_provider(lambda: running["any"])

    result = {
        "meta": {
            "host_chromium_for_reference": subprocess.run(
                ["/usr/bin/chromium", "--version"],
                capture_output=True,
                text=True,
            ).stdout.strip(),
            "display": os.environ.get("DISPLAY"),
            "persona_home": os.environ.get("PERSONA_HOME"),
            "sandbox_waived": True,
            "sandbox_waiver_reason": (
                "kernel.apparmor_restrict_unprivileged_userns=1 on this "
                "container and no root; chromium's zygote aborts before any "
                "window paints on BOTH builds and on stock chromium. Injected "
                "probe-side into BOTH legs identically, so it cannot author a "
                "difference between them."
            ),
            "predecessor_source": (
                "adryfish/fingerprint-chromium 148.0.7778.215 — a REAL older "
                "fingerprint-chromium, the upstream persona's engine is built "
                "from. Served from loopback because upstream publishes exactly "
                "one personium- tag today."
            ),
        }
    }

    profile = Profile(
        name="ps341-continuity",
        proxy=None,
        os_type="windows",
        engine="chromium",
        search_engine="brave",  # NOT the default: a default would be ambiguous
        ai_control=True,  # opens the CDP port the reader attaches to
    )
    profile_dir = os.path.join(DATA_DIR, profile.name)
    shutil.rmtree(profile_dir, ignore_errors=True)
    result["profile"] = {
        "name": profile.name,
        "os_type": profile.os_type,
        "search_engine": profile.search_engine,
        "fingerprint_seed": profile.fingerprint_seed,
        "profile_dir": profile_dir,
    }

    # ---- leg N -------------------------------------------------------------
    print("=== LEG N ===", flush=True)
    origin_srv, origin = _serve_origin()
    result["meta"]["origin"] = origin
    result["leg_N"] = run_leg("N", profile, profile_dir, origin)
    print(json.dumps({"N_opened": result["leg_N"]["opened"]}), flush=True)

    # ---- the REVERT, through the shipping gesture ---------------------------
    print("=== REVERT ===", flush=True)
    pred_digest = _sha256(PRED_APPIMAGE)
    srv, base = _serve_predecessor(pred_digest)
    # Redirect ONLY the API URL constant. Everything else in the gesture runs
    # verbatim, including verification against the digest recorded on disk.
    updater.RELEASE_BY_TAG_API = base + "/releases/tags/{tag}"
    # Record the predecessor as the "previous" build, which is what an operator
    # who had UPDATED from it would have on disk. record_installed_build is the
    # product's own writer; the sequence below is the same one an update
    # performs (install 148, then install 152), so the resulting builds.json is
    # a state the shipping code produces rather than one hand-authored.
    updater.record_installed_build(f"personium-{PRED_VERSION}", pred_digest)
    cur = result["leg_N"]["engine_record_version"]
    cur_digest = _sha256(updater.ENGINE_BINARY)
    updater.record_installed_build(cur, cur_digest)
    result["builds_json_before_revert"] = updater._read_builds()
    result["rollback_target"] = updater.rollback_target()

    t0 = time.time()
    ok, message = updater.revert_to_previous_build(
        timeout=900, log=lambda m: print("REVERT LOG:", m, flush=True)
    )
    result["revert"] = {
        "ok": ok,
        "message": message,
        "seconds": round(time.time() - t0, 1),
        "builds_json_after": updater._read_builds(),
        "version_txt_after": updater.current_version(),
        "pinned_build_after": updater.pinned_build(),
    }
    srv.shutdown()
    if not ok:
        result["verdict"] = "REVERT REFUSED — nothing measured"
        with open(OUT, "w") as f:
            json.dump(result, f, indent=2)
        return 2

    # ---- POSITIVE CONTROL: did the build REALLY move? -----------------------
    result["positive_control"] = {
        "record_before": result["leg_N"]["engine_record_version"],
        "record_after": updater.current_version(),
        "record_moved": result["leg_N"]["engine_record_version"]
        != updater.current_version(),
        "sha_before": result["leg_N"]["engine_binary_sha256"],
        "sha_after": _sha256(updater.ENGINE_BINARY),
        "sha_moved": result["leg_N"]["engine_binary_sha256"]
        != _sha256(updater.ENGINE_BINARY),
    }

    # ---- leg N-1, SAME profile dir -----------------------------------------
    print("=== LEG N-1 ===", flush=True)
    result["leg_N_minus_1"] = run_leg("N-1", profile, profile_dir, origin)
    origin_srv.shutdown()

    n_ua = ((result["leg_N"].get("live") or {}).get("page") or {}).get("uaMajor")
    p_ua = ((result["leg_N_minus_1"].get("live") or {}).get("page") or {}).get(
        "uaMajor"
    )
    result["positive_control"]["ua_major_before"] = n_ua
    result["positive_control"]["ua_major_after"] = p_ua
    result["positive_control"]["ua_moved"] = bool(n_ua and p_ua and n_ua != p_ua)

    with open(OUT, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print("WROTE", OUT, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
