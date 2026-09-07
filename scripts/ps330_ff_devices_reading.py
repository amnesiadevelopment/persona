#!/usr/bin/env python3
"""PS-330 — read what a page ACTUALLY receives from
``navigator.mediaDevices.enumerateDevices()`` on a REAL Firefox launch.

This is the measurement instrument, not the guard. The guard is
``tests/test_ps330_ff_mediadevices_live.py``, which runs the same launch under
pytest with skip gates; this script exists so the reading can be taken once,
deliberately, and COMMITTED under ``readings/`` where a later slice can re-read
it without re-launching a browser.

WHAT IT READS, and why it is the payload rather than the source
---------------------------------------------------------------
``enumerateDevices()`` returns a PROMISE, so every value here is what the
resolved list actually held — kinds AND ``deviceId``/``groupId``. Never a
builder having been called; never a substring of generated source. That is
AC1's whole point, and it is this project's recorded failure mode (PS-11:
*"tests that assert on what was written, not on what happens"*).

⛔ ``label`` IS DELIBERATELY NOT RECORDED. ``probes.py``'s device probe states
the restriction in the tree's own words — *"labels are user-identifying and are
deliberately NOT recorded into a file the operator may share"* — and this
script writes a file into ``readings/``, which is exactly such a file. It
records ``label_len`` and ``label_empty`` instead, so "the engine returned
empty labels" is still a readable fact without recording the string.

THE POSITIVE CONTROL IS NOT OPTIONAL
------------------------------------
An empty device list is ambiguous between *the engine reported none* and *my
eval channel is dead*, and the second reads as the first. So the channel is
proved with ``1+1`` before anything is read through it, and the reading records
``api_present`` separately from the list: "``navigator.mediaDevices`` is
undefined" and "it enumerated zero devices" are two different findings that
look identical in a bare count.

TWO SEEDS, because AC2's question is linkability
------------------------------------------------
The committed artifacts establish that the KIND COUNT is constant across four
seeds. The kind count is not the interesting axis — *"1 mic, 1 camera"* is what
most real machines look like. The sharp question is whether two DIFFERENT
profiles receive the same ``deviceId``/``groupId``, which nothing has ever read
on this engine. So two profiles are launched, with different names and
therefore different seeds, and their ids are compared.

Usage::

    python3 scripts/ps330_ff_devices_reading.py [--out readings/DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

#: Cold engine start budget. Sized off PS-312's measured value (a cold start on
#: a loaded host intermittently crossed 60s), not guessed.
START_TIMEOUT_S = 120.0

#: The engine documents that ~half of fresh launches can wedge on a
#: half-destroyed initial-window attach and retries internally on a fresh
#: worker. A harness that took one wedged launch as final would report a host
#: problem as a product finding.
LAUNCH_ATTEMPTS = 3

#: The probe. Resolves on the promise's settlement in EITHER direction and
#: times out explicitly, so a hang is a recorded outcome rather than an absent
#: one — the same discipline PS-312's callback probe uses.
#:
#: ``label`` is read only as a LENGTH. See the module docstring.
DEVICES_PROBE = """(() => new Promise(resolve => {
    const t0 = Date.now(); let done = false;
    const fin = o => { if (done) return; done = true;
                       o.elapsed_ms = Date.now() - t0; resolve(o); };
    const timer = setTimeout(() => fin({outcome: 'timeout'}), %d);
    try {
        if (!navigator.mediaDevices) {
            clearTimeout(timer);
            return fin({outcome: 'no_api', which: 'navigator.mediaDevices'});
        }
        if (!navigator.mediaDevices.enumerateDevices) {
            clearTimeout(timer);
            return fin({outcome: 'no_api', which: 'enumerateDevices'});
        }
        navigator.mediaDevices.enumerateDevices().then(list => {
            clearTimeout(timer);
            const devices = (list || []).map(d => ({
                kind: String(d.kind),
                deviceId: String(d.deviceId),
                groupId: String(d.groupId),
                label_len: (d.label == null) ? null : String(d.label).length,
                label_empty: (d.label == null) ? null : String(d.label) === ''
            }));
            const kindCounts = {};
            devices.forEach(d => { kindCounts[d.kind] = (kindCounts[d.kind]||0)+1; });
            const sorted = {};
            Object.keys(kindCounts).sort().forEach(k => { sorted[k] = kindCounts[k]; });
            fin({outcome: 'list', devices: devices, kindCounts: sorted,
                 count: devices.length});
        }, e => { clearTimeout(timer);
                  fin({outcome: 'rejected', message: String(e)}); });
    } catch (e) { clearTimeout(timer);
                  fin({outcome: 'throw', message: String(e)}); }
}))()"""

WAIT_MS = 20000


class _Origin(threading.Thread):
    """A loopback page for the reading to happen on.

    ``http://127.0.0.1`` is a potentially-trustworthy origin and therefore a
    SECURE CONTEXT. ``enumerateDevices`` is gated on one, so a reading taken in
    a non-secure context would measure the CONTEXT and report it as the
    engine's device posture — the same trap PS-312 guards with
    ``test_the_reading_happens_in_a_secure_context``. Loopback also keeps this
    off the network.
    """

    daemon = True

    def __init__(self):
        super().__init__()

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"<!doctype html><title>ps330</title><p>devices probe"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self._srv = HTTPServer(("127.0.0.1", 0), H)
        self.port = self._srv.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def run(self):
        self._srv.serve_forever()

    def stop(self):
        try:
            self._srv.shutdown()
        except Exception:
            pass


def _ensure_display():
    from src.services.verify.chromium_tier import _ensure_display as _ed

    return _ed()


def read_devices(profile_name: str, home: Path, *, granted: bool = False) -> dict:
    """Launch ONE real Firefox profile and ask the PAGE.

    ``in_process=True`` is required rather than preferred: the eval hook is
    published in a per-process dict (``register_ff_eval``) and Linux launches
    FORK by default, so a forked session registers its hook where this process
    can never see it.

    ``granted`` is a TEST-SIDE pref overlay (monkeypatched over
    ``_profile_prefs`` for one launch and restored after) and is NEVER a
    product change. It exists because an empty ``deviceId`` under the shipped
    ``prompt`` default is ambiguous between *the value is a constant* and *the
    value is a pre-permission placeholder that fills in once permission is
    granted* — and those are different findings. PS-312 recorded the general
    form of this trap: a permission-gated web API measured under its shipped
    ``prompt`` default measures the DOORHANGER, not the engine.
    """
    os.environ["PERSONA_HOME"] = str(home)

    from src.services.browser import invisible_launch as il
    from src.services.browser.process import spawn_browser
    from src.services.profile.manager import ProfileManager

    origin = _Origin()
    origin.start()
    proc = None
    out: dict = {"profile": profile_name, "permission_granted_overlay": granted}
    # Recorded PER READING, not only once at the top of the run: the AC3 leg is
    # a property of the host the reading was TAKEN on, and a test that consumes
    # one reading must be able to state which leg it is holding without
    # depending on the run-level record. An assertion that silently reads a
    # missing key as "no devices" would pass on a webcam-carrying host.
    out["host_devices"] = _host_devices()
    _orig_prefs = il._profile_prefs

    def _overlaid(cfg):
        prefs = dict(_orig_prefs(cfg))
        prefs["permissions.default.camera"] = 1        # 1 == ALLOW
        prefs["permissions.default.microphone"] = 1
        prefs["media.navigator.permission.disabled"] = True
        return prefs

    if granted:
        il._profile_prefs = _overlaid
    try:
        pm = ProfileManager()
        # os_type 'windows': coherence refuses every other os_type for firefox,
        # so this is the only coherent pairing rather than a choice. No proxy —
        # the device roster is not a proxied-exit question and a direct launch
        # removes the relay as a variable.
        pm.add_profile(profile_name, "", "windows", engine="firefox")
        profile = pm.profiles[profile_name]
        # The FIELD NAME matters: an earlier draft read ``profile.seed``, which
        # does not exist, so every reading recorded ``seed: null`` — and AC2's
        # whole question is whether two DIFFERENT seeds produce the same ids. A
        # null seed would have made two identical readings unattributable to
        # two profiles, i.e. would have looked like the answer while proving
        # nothing. Recorded from the attribute the model actually carries.
        out["seed"] = getattr(profile, "fingerprint_seed_value", None)
        out["hardware_generation"] = getattr(
            profile, "hardware_generation_value", None
        )

        started = False
        for attempt in range(LAUNCH_ATTEMPTS):
            if attempt:
                try:
                    proc.terminate()
                    proc.wait(timeout=20)
                except Exception:
                    pass
                try:
                    il.unregister_ff_eval(profile_name)
                except Exception:
                    pass
                time.sleep(5)

            proc = spawn_browser(profile, in_process=True)

            # BROWSER_STARTED on a PUMP THREAD: readline() blocks unboundedly,
            # so a deadline tested only BETWEEN reads never fires against a
            # session that starts and then goes silent.
            lines: "queue.Queue[str]" = queue.Queue()

            def pump(stream=proc.stdout, sink=lines):
                try:
                    for line in iter(stream.readline, ""):
                        if not line:
                            break
                        sink.put(line.rstrip())
                except Exception:
                    pass

            threading.Thread(target=pump, daemon=True).start()
            deadline = time.monotonic() + START_TIMEOUT_S
            while time.monotonic() < deadline:
                try:
                    line = lines.get(timeout=2)
                except queue.Empty:
                    continue
                if "BROWSER_STARTED" in line:
                    started = True
                    break
            if started:
                break

        if not started:
            out["unobtained"] = (
                f"the firefox session never reported BROWSER_STARTED in "
                f"{LAUNCH_ATTEMPTS} attempts"
            )
            return out

        hook = il.get_ff_eval(profile_name)
        if not hook or not callable(hook.get("eval")):
            out["unobtained"] = "the session started but published no eval hook"
            return out
        raw, goto = hook["eval"], hook["goto"]

        def ev(expr, tries=4):
            for _ in range(tries):
                try:
                    value = raw(expr)
                except Exception:
                    value = None
                if value is not None:
                    return value
                time.sleep(4)
            return None

        # THE POSITIVE CONTROL FOR THE CHANNEL, before anything is read through
        # it. A dead eval channel answers None to every probe below, which
        # would read as "the engine returned nothing".
        control = ev("1+1")
        out["channel_control"] = control
        if control != 2:
            out["unobtained"] = "the eval channel never answered"
            return out

        for _ in range(3):
            try:
                goto(origin.url)
                break
            except Exception:
                time.sleep(4)
        time.sleep(2)

        out["href"] = ev("location.href")
        out["secure_context"] = ev("window.isSecureContext")
        out["api_present"] = ev(
            "(typeof navigator.mediaDevices !== 'undefined') && "
            "(navigator.mediaDevices !== null)"
        )
        out["enumerate_present"] = ev(
            "!!(navigator.mediaDevices && "
            "navigator.mediaDevices.enumerateDevices)"
        )
        # The anti-tell reading: does enumerateDevices still render as native?
        # A JS override is visible here, and that is exactly what shipping a
        # spoof would cost.
        out["native_source"] = ev(
            "(navigator.mediaDevices && navigator.mediaDevices.enumerateDevices)"
            " ? Function.prototype.toString.call("
            "navigator.mediaDevices.enumerateDevices) : null"
        )
        out["permission_camera"] = ev(
            "(async()=>{try{return (await navigator.permissions.query("
            "{name:'camera'})).state}catch(e){return 'ERR '+e}})()"
        )
        out["permission_microphone"] = ev(
            "(async()=>{try{return (await navigator.permissions.query("
            "{name:'microphone'})).state}catch(e){return 'ERR '+e}})()"
        )
        # tries=1: the probe resolves its OWN promise in every branch, so a
        # retry would only re-run the wait after a genuine answer.
        out["devices"] = ev(DEVICES_PROBE % WAIT_MS, tries=1)
        return out
    finally:
        il._profile_prefs = _orig_prefs
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=30)
            except Exception:
                pass
        try:
            il.unregister_ff_eval(profile_name)
        except Exception:
            pass
        origin.stop()


def _host_devices() -> dict:
    """What audio/video devices THIS HOST actually has.

    AC3's discriminator needs the host's own device inventory recorded beside
    the page's, because the whole question is whether the roster the page
    receives TRACKS the host. Recorded as facts about paths, so the reading
    says which leg of AC3 it is rather than leaving a reader to infer it.
    """
    import glob

    return {
        "dev_snd": sorted(glob.glob("/dev/snd/*")),
        "dev_video": sorted(glob.glob("/dev/video*")),
        "has_audio_device": bool(glob.glob("/dev/snd/*")),
        "has_video_device": bool(glob.glob("/dev/video*")),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None, help="directory to write reading.json into")
    ap.add_argument("--profiles", type=int, default=2)
    args = ap.parse_args()

    inherited = os.environ.get("DISPLAY", "").strip()
    display, xvfb = _ensure_display()
    os.environ["DISPLAY"] = display

    rev = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
        capture_output=True, text=True,
    ).stdout.strip()

    from src.services.browser.engine_install import active_build

    record = {
        "ticket": "PS-330",
        "taken_at": datetime.now(timezone.utc).isoformat(),
        "head": rev,
        "engine_build": active_build(),
        "display": display,
        "host": _host_devices(),
        # AC3's second leg, stated rather than inferred. See EVIDENCE.md.
        "ac3_leg_measured": "host WITHOUT audio/video devices",
        "ac3_leg_unmeasured": "host WITH real audio/video devices",
        "readings": [],
    }

    try:
        with tempfile.TemporaryDirectory(prefix="ps330-home-") as td:
            for i in range(args.profiles):
                name = f"ps330-probe-{i}"
                print(f"[ps330] launching {name} ...", flush=True)
                r = read_devices(name, Path(td) / name)
                record["readings"].append(r)
                print(json.dumps(r, indent=2)[:2000], flush=True)
            # THE PERMISSION-GRANTED LEG. Not a third profile for its own sake:
            # it is the control that tells a CONSTANT apart from a
            # PRE-PERMISSION PLACEHOLDER, which the shipped `prompt` default
            # cannot distinguish.
            name = "ps330-probe-granted"
            print(f"[ps330] launching {name} (permission overlay) ...", flush=True)
            r = read_devices(name, Path(td) / name, granted=True)
            record["readings"].append(r)
            print(json.dumps(r, indent=2)[:2000], flush=True)
    finally:
        if xvfb is not None:
            try:
                xvfb.terminate()
                xvfb.wait(timeout=10)
            except Exception:
                pass
        if inherited:
            os.environ["DISPLAY"] = inherited
        else:
            os.environ.pop("DISPLAY", None)

    if args.out:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "reading.json").write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8"
        )
        print(f"[ps330] wrote {out_dir / 'reading.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
