#!/usr/bin/env python3
"""PS-350 — what a STOCK Firefox reports on the two APIs ``stealth_ext`` adds,
read beside persona's Firefox on the SAME host in the SAME run.

⚠️⚠️ THE STOCK ARM IS A **CONTROL**, AND IT IS NOT THE PRODUCT. ⚠️⚠️
Nothing this script reads under ``--stock`` may be attributed to persona's
behaviour, in EITHER direction. That is the ``readings/ps159-2026-08-25``
rule stated in that record's own words, and the same discipline
``scripts/ps150_stock_control.py`` and ``scripts/ps301_engine_launch.py``
carry: a control arm is launched DIRECTLY here, deliberately never through the
product's engine resolver, so a stock browser can never produce a
complete-looking record of something that is not persona.

WHY A CONTROL IS THE WHOLE SLICE
--------------------------------
``tests/test_engine_masking_matrix.py`` records the Firefox ``stealth`` cell as
``position_not_established`` with the gap stated in its own words: *"A plausible
position is 'not applicable — Firefox exposes neither API, so a real Firefox is
missing them too' — but plausible is not recorded, and this file will not mint a
position the tree does not hold."*

That sentence is a claim about TWO browsers. The 20 committed Firefox artifacts
under ``readings/`` answer the first half unanimously — persona's Firefox
exposes neither API — and they are ALL persona's engine, so they cannot answer
the second half at any sample size. More of them would not help. Only a stock
Firefox can, and nothing in this project had ever read one.

THE FOUR ARMS, and why each is needed
-------------------------------------
====  ==========================  ==========================================
arm   what it is                  what it can attribute
====  ==========================  ==========================================
A1    STOCK Firefox 151.0,        the CONTROL — what an ordinary Firefox
      bare binary, marionette,    exposes. Attributable to Mozilla, never to
      headless                    persona.
A2    persona's PATCHED engine    the matched SUBJECT. Same channel, same
      binary, bare, marionette,   gesture, same page, same host, same
      headless                    minute — the ONLY difference from A1 is
                                  WHICH BINARY IS EXECUTED, so an A1/A2
                                  divergence is attributable to persona's
                                  engine patches and to nothing else.
B     persona's SHIPPING launch   the PRODUCT. ``spawn_browser`` →
      path (``spawn_browser``,    ``_spawn_invisible`` → the masking layer,
      in_process, headful/Xvfb)   headful, prefs and init scripts and all.
                                  Answers "does anything on the launch path
                                  change the answer A2 gives".
R     REVEAL CONTROL — stock,     that the probes are a LIVE instrument.
      with ``stealth_ext``'s own  Five absences agreeing across two binaries
      two shims injected          is exactly the shape a probe that reports
                                  absence unconditionally would produce.
M     the SECOND reveal, because  that ``stealth.connection``'s null is the
      R only half fires — the     ENGINE's answer and not a dead row. It also
      whole ``connection`` object  establishes the sharper finding: R cannot
      is manufactured             move that row because ``stealth_ext``'s
                                  downlinkMax shim is guarded on ``conn &&``
                                  and Firefox exposes no ``navigator.
                                  connection`` to hang it off — the shim is
                                  a STRUCTURAL NO-OP on this engine.
====  ==========================  ==========================================

A1 and A2 both report ``Mozilla Firefox 151.0`` (``invisible_playwright``'s
``FIREFOX_UPSTREAM_VERSION``), so this is NOT a version comparison — it isolates
persona's engine patches with the version, the channel, the gesture, the
instrument and the page all held constant. PS-171's arm C is the in-tree model
for that construction and this harness is a trimmed descendant of its
``c_stock.py``.

⛔ TWO CONFOUNDS THAT VOID A READING, both hit by earlier attempts at this
   measurement and both guarded here rather than merely documented:

1. **AN OPAQUE ORIGIN.** ``data:`` and ``about:blank`` are not secure contexts
   and are opaque origins. Service workers are unavailable there, so
   ``ServiceWorkerRegistration`` reads ``undefined`` on a browser that exposes
   it perfectly well — a clean, reproducible, FALSE divergence on a
   CreepJS-counted row. Every arm here navigates to a real ``http://127.0.0.1``
   document, and ``window.isSecureContext`` is asserted true before any row is
   recorded. PS-312 guards the same trap with
   ``test_the_reading_happens_in_a_secure_context``.

2. **A DEAD CHANNEL.** A browser that never started, or an eval channel that
   answers nothing, is byte-identical to a perfect match: every row reads
   "absent" and the two arms "agree". So each arm proves the channel with
   ``1+1`` and proves the page RENDERED before a single stealth row is read,
   and the harness REFUSES to emit an arm whose gates did not pass.

WHAT IS READ
------------
The three ``stealth.*`` probes, IMPORTED VERBATIM from
``src/services/verify/probes.py`` rather than retyped, through the product's own
``runner.window_expression`` / ``runner.worker_expression`` — so this measures
what persona records, in the two realms persona records it in, and it cannot
drift from the inventory. A retyped expression measures the typist.

Usage::

    python3 scripts/ps350_stealth_control.py --out readings/ps350-YYYY-MM-DD
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import queue
import shutil
import socket
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

#: The vector's own probes. Named as data so an arm cannot quietly read a
#: different set than another arm does.
STEALTH_PROBE_IDS = ("stealth.connection", "stealth.contentIndex", "stealth.apiPresence")

#: The two API names the vector installs, and the ONLY rows this cell is about.
#: ``ServiceWorkerRegistration`` rides with them because the ContentIndex shim
#: hangs off its prototype — an absent SWR would make ``hasIndex`` unreadable
#: rather than false, and those are different findings.
VECTOR_KEYS = ("navigator.connection", "NetworkInformation", "ServiceWorkerRegistration")

#: Cold engine start budget. Sized off PS-312's measured value (a cold start on
#: a loaded host intermittently crossed 60s), not guessed.
START_TIMEOUT_S = 120.0

#: The engine documents that ~half of fresh launches can wedge on a
#: half-destroyed initial-window attach and retries internally on a fresh
#: worker. A harness that took one wedged launch as final would report a host
#: problem as a product finding.
LAUNCH_ATTEMPTS = 3

#: ``stealth_ext``'s OWN two shims, lifted from ``CONTENT_SCRIPT`` and reduced
#: to the two ``Object.defineProperty`` calls, for the reveal control. This is
#: injected ONLY into arm R and never into a reading arm.
REVEAL_JS = """
(function () {
  try {
    var conn = navigator.connection;
    if (conn && !('downlinkMax' in conn)) {
      Object.defineProperty(conn, 'downlinkMax', {
        get: function () { return Infinity; },
        configurable: true, enumerable: true });
    }
  } catch (e) {}
  try {
    var SWR = self.ServiceWorkerRegistration;
    if (SWR && !('index' in SWR.prototype)) {
      function ContentIndex() {}
      ContentIndex.prototype.getAll = function () { return Promise.resolve([]); };
      Object.defineProperty(SWR.prototype, 'index', {
        get: function () { return new ContentIndex(); },
        configurable: true, enumerable: true });
    }
  } catch (e) {}
  return 'reveal-installed';
})()
"""

#: THE SECOND REVEAL ARM, and it exists because the first one only HALF fired.
#:
#: Arm R installs ``stealth_ext``'s shims verbatim and moves ``contentIndex``
#: to ``hasIndex: true`` — but leaves ``stealth.connection`` at ``null``. Read
#: carelessly that is "the connection probe is inert", which would make every
#: ``null`` in this reading worthless. It is the opposite, and the distinction
#: is the sharpest thing this measurement found:
#:
#:   ``stealth_ext``'s downlinkMax shim is guarded ``if (conn && ...)``. Firefox
#:   exposes NO ``navigator.connection`` for it to hang off, so THE SHIM IS A
#:   STRUCTURAL NO-OP ON THIS ENGINE — it cannot fire, whatever it is asked to
#:   do. The Chromium builder is not merely unnecessary here; half of it is
#:   INAPPLICABLE by construction.
#:
#: So this arm MANUFACTURES the whole object the shim expects. If the probe
#: moves, the row is live and the ``null`` is the engine's answer rather than a
#: dead instrument — which is exactly the two-opposite-mutations discipline the
#: project's own testing article asks of a reveal control.
MANUFACTURE_JS = """
(function () {
  try {
    var fake = {};
    Object.defineProperty(fake, 'downlinkMax', {
      get: function () { return Infinity; },
      configurable: true, enumerable: true });
    Object.defineProperty(fake, 'type', {
      get: function () { return 'ethernet'; },
      configurable: true, enumerable: true });
    Object.defineProperty(Navigator.prototype, 'connection', {
      get: function () { return fake; },
      configurable: true, enumerable: true });
    return 'manufactured';
  } catch (e) { return 'FAILED: ' + e; }
})()
"""


# --- the page every arm is read on ------------------------------------------


class _Origin(threading.Thread):
    """A loopback page for the reading to happen on.

    ``http://127.0.0.1`` is a potentially-trustworthy origin and therefore a
    SECURE CONTEXT with a real (non-opaque) origin. Confound 1 in the module
    docstring is what this exists to remove: on ``data:`` or ``about:blank``
    the service-worker surface is legitimately absent, and a reading taken
    there measures the CONTEXT and reports it as the browser's posture.
    Loopback also keeps the whole measurement off the network.
    """

    daemon = True

    def __init__(self):
        super().__init__()

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"<!doctype html><title>ps350</title><p id=ps350>stealth probe"
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


# --- the marionette wire, for the two BARE arms ------------------------------
#
# Stock Firefox has no juggler channel, so a stock arm cannot use the gesture
# persona's launcher uses. Driving stock over marionette and persona over
# juggler would confound TWO variables at once (BINARY and CHANNEL), and an
# agreement from such a pair could not be attributed to either. So A1 and A2
# both run over marionette. That is PS-171 arm C's construction, and its reason
# is stated there in the same words.


class MarionetteError(RuntimeError):
    pass


class Marionette:
    """Length-prefixed JSON over a socket — Firefox's own remote protocol."""

    def __init__(self, sock):
        self._sock = sock
        self._mid = 0
        # Firefox pushes an UNSOLICITED handshake frame the moment marionette
        # accepts the connection. It must be consumed here: left in the buffer
        # it becomes the reply to the FIRST command, and every reply after it
        # is off by one — which reads as a protocol error many frames away from
        # its cause.
        self.handshake = self._recv()

    def _recv(self):
        n = b""
        while not n.endswith(b":"):
            c = self._sock.recv(1)
            if not c:
                raise MarionetteError("marionette socket closed")
            n += c
        need = int(n[:-1].decode())
        buf = b""
        while len(buf) < need:
            chunk = self._sock.recv(need - len(buf))
            if not chunk:
                raise MarionetteError("marionette socket closed mid-frame")
            buf += chunk
        return json.loads(buf.decode())

    def cmd(self, name, params):
        self._mid += 1
        b = json.dumps([0, self._mid, name, params]).encode()
        self._sock.sendall(str(len(b)).encode() + b":" + b)
        reply = self._recv()
        if isinstance(reply, list) and len(reply) >= 3 and reply[2]:
            raise MarionetteError(f"{name}: {reply[2]}")
        return reply[3] if isinstance(reply, list) and len(reply) > 3 else None

    def evaluate(self, expr: str):
        """Evaluate ONE expression in the page realm and AWAIT a promise.

        The signature the product's runner expects: ``evaluate(expr) -> value``.
        Every probe expression may return a promise (``stealth.*`` do not, but
        the worker harness does), so this always awaits.
        """
        script = (
            "const cb = arguments[arguments.length - 1];"
            "try { Promise.resolve((function(){ return (" + expr + "); })())"
            ".then(function(v){ cb({ok: true, v: v}); },"
            " function(e){ cb({ok: false, e: String(e)}); }); }"
            "catch (e) { cb({ok: false, e: String(e)}); }"
        )
        reply = self.cmd(
            "WebDriver:ExecuteAsyncScript",
            {"script": script, "args": [], "newSandbox": False},
        )
        value = reply.get("value") if isinstance(reply, dict) else reply
        if isinstance(value, dict) and value.get("ok") is False:
            raise MarionetteError(f"page threw: {value.get('e')}")
        return value.get("v") if isinstance(value, dict) else value


def _launch_bare(binary: str, port: int, profile_dir: str, stderr_path: str):
    """Start ``binary`` headless with marionette on ``port``. Returns (proc, sock).

    ⚠️ The port MUST be set as a PREF. ``MOZ_MARIONETTE_PORT`` is NOT read by
    Firefox — PS-171 hit exactly this: it set the env var, Firefox listened on
    the default 2828 anyway, and the harness dialled the requested port and
    reported "never listened" while a perfectly healthy browser sat on the
    other one. The two bare arms run on DIFFERENT ports so a leftover process
    from one can never be dialled by the other.
    """
    os.makedirs(profile_dir, exist_ok=True)
    with open(os.path.join(profile_dir, "user.js"), "w") as fh:
        fh.write(
            'user_pref("browser.shell.checkDefaultBrowser", false);\n'
            'user_pref("datareporting.policy.dataSubmissionEnabled", false);\n'
            'user_pref("browser.sessionstore.resume_from_crash", false);\n'
            'user_pref("toolkit.telemetry.enabled", false);\n'
            'user_pref("marionette.port", %d);\n' % port
        )
    env = {
        **os.environ,
        "MOZ_DISABLE_CONTENT_SANDBOX": "1",
        "MOZ_HEADLESS": "1",
        "MOZ_CRASHREPORTER_DISABLE": "1",
    }
    errf = open(stderr_path, "w")
    proc = subprocess.Popen(
        [binary, "--headless", "--marionette", "-profile", profile_dir],
        stdout=errf,
        stderr=errf,
        env=env,
    )
    sock = None
    for _ in range(240):
        if proc.poll() is not None:
            raise MarionetteError(
                f"{binary} exited with {proc.returncode} before marionette listened"
            )
        try:
            sock = socket.create_connection(("127.0.0.1", port), 5)
            sock.settimeout(120)
            break
        except Exception:
            time.sleep(0.5)
    if sock is None:
        try:
            proc.terminate()
        except Exception:
            pass
        raise MarionetteError(f"marionette never listened on {port} for {binary}")
    return proc, sock


def _answering_install(proc) -> "str | None":
    """The INSTALL DIRECTORY of the process that ACTUALLY answered, off ``/proc``.

    ⛔ NOT COSMETIC PROVENANCE — this exists because round 1 of this reading
    produced an arm that disagreed with three fresh isolation runs of the same
    binary on one row, and nothing in the artifact could adjudicate which
    binary had answered. The arm's LABEL is what the harness INTENDED to
    launch; this is what the kernel says ran. ``navigator.buildID`` cannot do
    this job — the engine pins it (both binaries report ``20181001000000``), so
    the one identifier a page could offer is exactly the one this engine
    spoofs.

    ⚠️ THE GRAIN IS THE DIRECTORY, NOT THE FILE, and that is a correction paid
    for rather than assumed: a first version compared realpaths and refused
    every stock arm, because upstream's ``firefox`` execs ``firefox-bin`` from
    the SAME install — a true fact about Mozilla's launcher and a false alarm
    about identity. The directory is the axis that actually separates the arms
    (stock tarball vs the invisible-playwright cache), which is the same key
    PS-171's arm C matches its process tree on, for the same reason.
    """
    try:
        return os.path.dirname(os.path.realpath(f"/proc/{proc.pid}/exe"))
    except Exception:
        return None


# --- the reading itself, shared by every arm ---------------------------------


def _gates(evaluate) -> dict:
    """Prove the channel and the page BEFORE any stealth row is read.

    Confound 2 in the module docstring: a browser that never started and a
    browser that exposes nothing produce byte-identical rows. Each gate is
    recorded as a value rather than asserted away, so a reader can see WHICH
    one failed rather than only that the arm is missing.
    """
    gates = {}
    gates["channel_arith"] = evaluate("1+1")
    gates["href"] = evaluate("location.href")
    gates["origin"] = evaluate("location.origin")
    gates["secure_context"] = evaluate("window.isSecureContext")
    gates["user_agent"] = evaluate("navigator.userAgent")
    # RENDERED, not merely parsed: a layout box has a non-zero width only if
    # the document was actually laid out. A parsed-but-unrendered document can
    # still answer `location.href`.
    gates["layout_width"] = evaluate(
        "(function(){var e=document.getElementById('ps350');"
        "return e ? Math.round(e.getBoundingClientRect().width) : -1;})()"
    )
    # The engine identity, so a reader never has to trust the arm's LABEL for
    # which binary answered.
    gates["build_id"] = evaluate("navigator.buildID || null")
    gates["worker_available"] = evaluate("typeof Worker === 'function'")
    return gates


def _gates_ok(gates: dict) -> "str | None":
    """The refusal. Returns a reason, or None when every gate passed."""
    if gates.get("channel_arith") != 2:
        return "the eval channel never answered 1+1 == 2"
    if not str(gates.get("href") or "").startswith("http://127.0.0.1"):
        return f"the page is not the loopback document: href={gates.get('href')!r}"
    origin = str(gates.get("origin") or "")
    if origin in ("", "null"):
        return (
            "the origin is OPAQUE, so the service-worker surface is "
            "legitimately absent and every row below would be a false absence"
        )
    if gates.get("secure_context") is not True:
        return "the reading is not in a secure context"
    if "Gecko" not in str(gates.get("user_agent") or ""):
        return f"the UA is not Gecko: {gates.get('user_agent')!r}"
    if not isinstance(gates.get("layout_width"), (int, float)) or gates["layout_width"] <= 0:
        return "the document parsed but never RENDERED (no layout box)"
    if gates.get("worker_available") is not True:
        return "Worker is unavailable, so the worker realm cannot be entered"
    return None


def read_arm(evaluate, *, label: str, reveal: bool = False, manufacture: bool = False) -> dict:
    """Read the three stealth probes in BOTH realms, through the PRODUCT's own
    harness.

    ``runner.window_expression`` / ``runner.worker_expression`` are imported
    rather than reimplemented, and the probes come out of ``PROBES`` by id, so
    an arm cannot read a hand-retyped expression that measures the typist. The
    worker realm is not optional here: ``baseline.py`` states why in its own
    words — *"a spoof that reaches the window and not the worker is a
    load-bearing leak, and it is invisible unless the worker realm is read."*
    """
    from src.services.verify.probes import PROBES
    from src.services.verify.runner import window_expression, worker_expression

    wanted = [p for p in PROBES if p.id in STEALTH_PROBE_IDS]
    missing = set(STEALTH_PROBE_IDS) - {p.id for p in wanted}
    if missing:
        raise SystemExit(
            f"probes.py no longer carries {sorted(missing)} — the inventory "
            f"moved and this reading would measure a different vector"
        )

    out = {"arm": label, "reveal_control": reveal, "manufacture_control": manufacture}
    out["gates"] = _gates(evaluate)
    refusal = _gates_ok(out["gates"])
    if refusal:
        out["unobtained"] = refusal
        return out

    if reveal:
        out["reveal_installed"] = evaluate(REVEAL_JS)
    if manufacture:
        out["manufacture_installed"] = evaluate(MANUFACTURE_JS)

    window = {}
    for probe in wanted:
        raw = evaluate(window_expression(probe))
        window[probe.id] = raw
    out["window"] = window

    worker_raw = evaluate(worker_expression(wanted))
    out["worker"] = worker_raw
    return out


def _rows(arm: dict) -> dict:
    """The arm's readings reduced to the cell's OWN rows, per realm.

    Deliberately a projection of what was recorded and never a second reading:
    the raw ``window``/``worker`` maps stay in the artifact, and this is only
    the comparison surface.
    """
    rows = {}
    for realm in ("window", "worker"):
        block = arm.get(realm) or {}
        realm_rows = {}
        for pid in ("stealth.connection", "stealth.contentIndex"):
            entry = block.get(pid)
            realm_rows[pid] = entry.get("v") if isinstance(entry, dict) else entry
        presence = block.get("stealth.apiPresence")
        presence = presence.get("v") if isinstance(presence, dict) else presence
        if isinstance(presence, dict):
            for key in VECTOR_KEYS:
                realm_rows[f"apiPresence[{key}]"] = presence.get(key)
        rows[realm] = realm_rows
    return rows


def _full_presence(arm: dict, realm: str) -> dict:
    block = (arm.get(realm) or {}).get("stealth.apiPresence")
    value = block.get("v") if isinstance(block, dict) else block
    return value if isinstance(value, dict) else {}


# --- arm B: the SHIPPING launch path -----------------------------------------


def read_product_arm(profile_name: str, home: Path, origin_url: str) -> dict:
    """Launch through ``spawn_browser`` — the path the product actually takes.

    A1/A2 hold the channel constant and vary the binary; this arm varies
    EVERYTHING the product does (its launcher, its prefs, its per-tab masking
    layer, headful under a display) and is the only arm that can say the
    shipping path does not change the answer. PS-330's instrument is the model
    and this is a trimmed copy of its launch block.

    ``in_process=True`` is required rather than preferred: the eval hook is
    published in a per-process dict (``register_ff_eval``) and Linux launches
    FORK by default, so a forked session registers its hook where this process
    could never see it.
    """
    os.environ["PERSONA_HOME"] = str(home)

    from src.services.browser import invisible_launch as il
    from src.services.browser.process import spawn_browser
    from src.services.profile.manager import ProfileManager

    proc = None
    try:
        pm = ProfileManager()
        # os_type 'windows': coherence refuses every other os_type for firefox,
        # so this is the only coherent pairing rather than a choice. No proxy —
        # these two APIs are not a proxied-exit question, and a direct launch
        # removes the relay as a variable.
        pm.add_profile(profile_name, "", "windows", engine="firefox")
        profile = pm.profiles[profile_name]

        started = False
        for attempt in range(LAUNCH_ATTEMPTS):
            if attempt and proc is not None:
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
            return {
                "arm": "B_persona_product_path",
                "unobtained": (
                    f"the firefox session never reported BROWSER_STARTED in "
                    f"{LAUNCH_ATTEMPTS} attempts"
                ),
            }

        hook = il.get_ff_eval(profile_name)
        if not hook or not callable(hook.get("eval")):
            return {
                "arm": "B_persona_product_path",
                "unobtained": "the session started but published no eval hook",
            }
        raw, goto = hook["eval"], hook["goto"]

        def evaluate(expr):
            last = None
            for _ in range(4):
                try:
                    value = raw(expr)
                except Exception as exc:
                    last = exc
                    value = None
                if value is not None:
                    return value
                time.sleep(3)
            if last is not None:
                raise last
            return None

        for _ in range(3):
            try:
                goto(origin_url)
                break
            except Exception:
                time.sleep(4)
        time.sleep(3)

        arm = read_arm(evaluate, label="B_persona_product_path")
        arm["profile"] = profile_name
        arm["seed"] = getattr(profile, "fingerprint_seed_value", None)
        return arm
    finally:
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


# --- provenance --------------------------------------------------------------


def _sha256(path: str) -> "str | None":
    import hashlib

    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _binary_version(binary: str) -> "str | None":
    try:
        out = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=60
        )
        for line in (out.stdout + out.stderr).splitlines():
            if "Firefox" in line:
                return line.strip()
    except Exception:
        pass
    return None


def _environment(stock_binary: str, engine_binary: str) -> dict:
    """The shelf-life block.

    ⚠️ NAMES THE HOST PLATFORM, not only the engine build. PS-314 recorded a
    row of this very probe (``Serial`` / ``navigator.serial``) moving between
    two artifacts whose ``engine_build`` was IDENTICAL — WebSerial is gated on
    the HOST PLATFORM, not on anything persona does. A position recorded from a
    reading that named only the build would attribute a host-gated row to the
    engine, which is the same class of error that correction exists to fix.
    """
    from src.services.browser.engine_install import active_build

    return {
        "host_platform": platform.platform(),
        "host_machine": platform.machine(),
        "host_system": platform.system(),
        "kernel": platform.release(),
        "display": os.environ.get("DISPLAY") or None,
        "engine_build": active_build(),
        "stock_binary": stock_binary,
        "stock_version": _binary_version(stock_binary),
        "stock_sha256": _sha256(stock_binary),
        "engine_binary": engine_binary,
        "engine_version": _binary_version(engine_binary),
        "engine_sha256": _sha256(engine_binary),
    }


def _engine_binary_path() -> str:
    """persona's OWN patched engine binary, located through the product's own
    layout helper.

    ⚠️ THE FENCE RUNS THE OTHER WAY, so note which arm this serves. The rule
    ``ps150``/``ps301`` carry is that a STOCK binary must never be resolved
    through the product's resolver, because that is how a control arm produces
    a complete-looking record of something that is not the product. The stock
    path here is required on the command line and is never resolved. This
    helper is for arm A2, which IS persona's engine, and asking the product
    where its own binary lives is the honest way to find it — a hand-built
    glob would be a second source of truth that could name a different build
    than the one ``active_build()`` reports in the environment block.
    """
    from src.services.browser.engine_install import _invisible_binary_path

    path = _invisible_binary_path()
    if not path or not os.path.exists(path):
        raise SystemExit(
            "persona's engine binary is not on this host — run "
            "`python -m invisible_playwright fetch` first (the CLI verb is "
            "`fetch`, not `install`)"
        )
    return str(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None, help="directory to write reading.json into")
    ap.add_argument(
        "--stock",
        default=os.environ.get("PS350_STOCK_FIREFOX", ""),
        help="path to the STOCK firefox binary (the CONTROL arm)",
    )
    ap.add_argument("--skip-product", action="store_true")
    args = ap.parse_args()

    if not args.stock or not os.path.exists(args.stock):
        raise SystemExit(
            "--stock must point at an upstream Firefox binary. This arm is the "
            "CONTROL and is deliberately never resolved through the product's "
            "engine resolver."
        )

    engine = _engine_binary_path()

    from src.services.verify.chromium_tier import _ensure_display

    display, _xvfb = _ensure_display()
    os.environ["DISPLAY"] = display

    rev = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True
    ).stdout.strip()

    origin = _Origin()
    origin.start()

    record = {
        "ticket": "PS-350",
        "taken_at": datetime.now(timezone.utc).isoformat(),
        "tree": rev,
        "origin_url": origin.url,
        "probe_ids": list(STEALTH_PROBE_IDS),
        "vector_keys": list(VECTOR_KEYS),
        "environment": _environment(args.stock, engine),
        "arms": [],
    }

    workdir = tempfile.mkdtemp(prefix="ps350-")
    try:
        # --- A1: the STOCK CONTROL, and A2: its channel-matched twin --------
        for label, binary, port, reveal, manufacture in (
            ("A1_stock_control", args.stock, 2830, False, False),
            ("A2_persona_engine_bare", engine, 2831, False, False),
            ("R_reveal_control_stock", args.stock, 2832, True, False),
            ("M_manufacture_control_stock", args.stock, 2833, False, True),
        ):
            print(f"--- {label}: {binary}", flush=True)
            proc = sock = None
            try:
                proc, sock = _launch_bare(
                    binary,
                    port,
                    os.path.join(workdir, label),
                    os.path.join(workdir, f"{label}.stderr"),
                )
                mn = Marionette(sock)
                mn.cmd("WebDriver:NewSession", {})
                mn.cmd("WebDriver:Navigate", {"url": origin.url})
                arm = read_arm(
                    mn.evaluate, label=label, reveal=reveal, manufacture=manufacture
                )
                arm["answering_install"] = _answering_install(proc)
            except Exception as exc:
                arm = {
                    "arm": label,
                    "reveal_control": reveal,
                    "manufacture_control": manufacture,
                    "unobtained": f"{type(exc).__name__}: {exc}",
                }
            finally:
                try:
                    if sock:
                        sock.close()
                except Exception:
                    pass
                if proc is not None:
                    try:
                        proc.terminate()
                        proc.wait(timeout=30)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
            arm["binary"] = binary
            # ⛔ THE IDENTITY GATE. An arm that did not run the binary its label
            # names is WORSE than a missing arm: A1-vs-A2 is a comparison of
            # two BINARIES, so an arm answering from the wrong one produces a
            # confident, readable, wrong row. Round 1 of this reading had
            # exactly one arm disagree with three fresh isolation runs of the
            # same binary, and nothing in the artifact could adjudicate it. The
            # arm is REFUSED rather than annotated, because a caveat on a row
            # still leaves the row in the comparison.
            answering = arm.get("answering_install")
            expected = os.path.dirname(os.path.realpath(binary))
            if answering and expected != answering:
                arm["unobtained"] = (
                    f"IDENTITY GATE: this arm was told to launch out of "
                    f"{expected!r} but the process that answered runs out of "
                    f"{answering!r}. The reading is discarded rather than "
                    f"attributed to the wrong binary."
                )
            record["arms"].append(arm)

        # --- B: the SHIPPING launch path ------------------------------------
        if not args.skip_product:
            print("--- B_persona_product_path: spawn_browser", flush=True)
            home = Path(workdir) / "persona-home"
            home.mkdir(parents=True, exist_ok=True)
            try:
                arm = read_product_arm("ps350-stealth", home, origin.url)
            except Exception as exc:
                arm = {
                    "arm": "B_persona_product_path",
                    "unobtained": f"{type(exc).__name__}: {exc}",
                }
            record["arms"].append(arm)
    finally:
        origin.stop()

    # --- the comparison, computed from the recorded arms -------------------
    by_label = {a["arm"]: a for a in record["arms"]}
    record["rows"] = {label: _rows(arm) for label, arm in by_label.items()}
    record["apiPresence_full_diff"] = {}
    stock = by_label.get("A1_stock_control")
    for other_label in ("A2_persona_engine_bare", "B_persona_product_path"):
        other = by_label.get(other_label)
        if not stock or not other or "unobtained" in stock or "unobtained" in other:
            continue
        diff = {}
        for realm in ("window", "worker"):
            s, o = _full_presence(stock, realm), _full_presence(other, realm)
            realm_diff = {
                k: [s.get(k), o.get(k)]
                for k in sorted(set(s) | set(o))
                if s.get(k) != o.get(k)
            }
            diff[realm] = realm_diff
        record["apiPresence_full_diff"][f"A1_vs_{other_label}"] = diff

    out_dir = Path(args.out) if args.out else None
    text = json.dumps(record, indent=2, sort_keys=False)
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "reading.json").write_text(text + "\n", encoding="utf-8")
        print(f"wrote {out_dir / 'reading.json'}")
    else:
        print(text)

    shutil.rmtree(workdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
