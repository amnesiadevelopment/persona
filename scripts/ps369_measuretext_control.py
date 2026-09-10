#!/usr/bin/env python3
"""PS-369 — what a STOCK Firefox reports on ``measureText``, read beside
persona's Firefox on the SAME host in the SAME run.

⚠️⚠️ THE STOCK ARM IS A **CONTROL**, AND IT IS NOT THE PRODUCT. ⚠️⚠️
Nothing this script reads under ``--stock`` may be attributed to persona's
behaviour, in EITHER direction. That is the ``readings/ps159-2026-08-25``
rule stated in that record's own words, and the same discipline
``scripts/ps150_stock_control.py``, ``scripts/ps301_engine_launch.py`` and
``scripts/ps350_stealth_control.py`` carry: a control arm is launched DIRECTLY
here, deliberately never through the product's engine resolver, so a stock
browser can never produce a complete-looking record of something that is not
persona.

WHY A CONTROL IS THE WHOLE SLICE
--------------------------------
``tests/test_engine_masking_matrix.py`` records the Firefox ``measuretext``
cell as ``position_not_established`` with the gap stated in its own words:
*"No spoof, and NO RECORDED REASON. The Chromium builder repairs noise the
FINGERPRINT ENGINE injects into Canvas::measureText, so a plausible position is
'not applicable — a different engine does not inject that noise'. Not recorded,
so not claimed."*

That sentence is a claim about TWO browsers. The 20 committed Firefox artifacts
under ``readings/`` answer the first half unanimously — persona's Firefox
reports real, font-differentiated widths and leaves ``measureText`` unwrapped —
and every one of them is persona's own engine, so they cannot answer the second
half at any sample size. More of them would not help. Only a stock Firefox can.

THE FOUR ARMS, and why each is needed
-------------------------------------
====  ==========================  ==========================================
arm   what it is                  what it can attribute
====  ==========================  ==========================================
A1    STOCK Firefox 151.0,        the CONTROL — what an ordinary Firefox
      bare binary, marionette,    reports. Attributable to Mozilla, never to
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
                                  wrap measureText after all".
R     REVEAL CONTROL — stock,     that the probes are a LIVE instrument. Two
      with a MULTIPLICATIVE       arms agreeing on 28 numbers is exactly the
      noise wrapper installed     shape a probe that reports a constant
      over ``measureText``        would produce. This arm installs the very
                                  defect ``measuretext_ext`` exists to
                                  repair — a scale factor on every metric —
                                  and BOTH readable rows must move: the
                                  widths collapse AND ``masking.measureText``
                                  stops reading ``[native code]``.
N     NO-OP CONTROL — stock,      ⭐ THE CELL'S CENTRAL CLAIM, MEASURED. The
      with ``measuretext_ext``'s  ticket argues a Firefox spoof would be "one
      OWN repair installed        tell traded for two". This arm installs the
                                  repair on a browser with nothing to repair
                                  and reads BOTH halves: its arithmetic is a
                                  STRUCTURAL no-op (the ``corrupt`` guard
                                  returns native metrics untouched, so the
                                  widths do not move) while the WRAPPER is
                                  observable anyway (``masking.measureText``
                                  stops reading ``[native code]``). Argued,
                                  that is a prediction; measured, it is the
                                  reason the cell says what it says.
====  ==========================  ==========================================

A1 and A2 both report ``Mozilla Firefox 151.0`` (``invisible_playwright``'s
``FIREFOX_UPSTREAM_VERSION``), so this is NOT a version comparison — it isolates
persona's engine patches with the version, the channel, the gesture, the
instrument and the page all held constant. PS-171's arm C is the in-tree model
for that construction, and PS-350's ``ps350_stealth_control.py`` is the direct
ancestor of this harness.

⛔ THREE CONFOUNDS THAT VOID A READING, each guarded here rather than merely
   documented:

1. **THE HOST FONT STACK.** ``fonts.measureText`` is in ``ENV_SENSITIVE_PROBES``
   (``src/services/verify/baseline.py``) precisely because everything that moves
   a text width between two machines — which fonts are installed, which
   rasteriser, which hinting — moves this probe. So the two arms are read on ONE
   host in ONE run, and the host's installed families are recorded in the
   environment block. ⚠️ An ABSOLUTE-magnitude comparison against an artifact
   taken on ANOTHER host measures the font stack and reports it as the engine.
   The rows this cell rests on are therefore the HOST-INVARIANT ones — the noise
   signature and the wrapper shape — never the raw widths.

2. **AN OPAQUE ORIGIN.** ``data:`` and ``about:blank`` are not secure contexts.
   Every arm here navigates to a real ``http://127.0.0.1`` document and
   ``window.isSecureContext`` is asserted true before any row is recorded.

3. **A DEAD CHANNEL.** A browser that never started, or an eval channel that
   answers nothing, is byte-identical to a perfect match: every row reads absent
   and the two arms "agree". So each arm proves the channel with ``1+1`` and
   proves the page RENDERED before a single row is read, and the harness REFUSES
   to emit an arm whose gates did not pass.

WHAT IS READ
------------
``fonts.measureText`` and ``masking.measureText``, IMPORTED VERBATIM from
``src/services/verify/probes.py`` by ``Probe.id`` rather than retyped, through
the product's own ``runner.window_expression`` / ``runner.worker_expression`` —
so this measures what persona records, in the two realms persona records it in,
and it cannot drift from the inventory. A retyped expression measures the
typist.

⚠️ THE TWO REALMS ARE READ AND REPORTED SEPARATELY, and that is not tidiness.
Across the 20 committed Firefox artifacts ``masking.measureText`` reads
``function measureText() { [native code] }`` in the WINDOW realm and
``absent:undefined`` in the WORKER realm, unanimously in both. Those are
different facts — the first says nothing is wrapped, the second says the probe
finds no such function to inspect at all — and a record that collapsed them
would claim more than the tree holds.

**No product code is modified to take this reading.** The reveal control is a
probe-side injection confined to arm R, as PS-312 and PS-350 did.

Usage::

    python3 scripts/ps369_measuretext_control.py \\
        --stock /path/to/stock/firefox --out readings/ps369-YYYY-MM-DD
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
MEASURETEXT_PROBE_IDS = ("fonts.measureText", "masking.measureText")

#: Cold engine start budget. Sized off PS-312's measured value (a cold start on
#: a loaded host intermittently crossed 60s), not guessed.
START_TIMEOUT_S = 120.0

#: The engine documents that ~half of fresh launches can wedge on a
#: half-destroyed initial-window attach and retries internally on a fresh
#: worker. A harness that took one wedged launch as final would report a host
#: problem as a product finding.
LAUNCH_ATTEMPTS = 3

#: THE NO-OP CONTROL — ``measuretext_ext``'s OWN repair, installed on a browser
#: that has nothing to repair.
#:
#: This is the arm that turns the cell's central claim from an ARGUMENT into a
#: MEASUREMENT. The ticket argues that a Firefox measureText spoof "would be a
#: REGRESSION, not a fix — one tell traded for two". That is two predictions,
#: and both are testable on a stock browser:
#:
#:   1. The repair's ARITHMETIC does nothing here. ``patch()`` is guarded
#:      ``var corrupt = hasText && !(Math.abs(m.width) >= 1); if (!corrupt)
#:      return m;`` — real widths are ~200, so the guard returns the native
#:      metrics untouched and the divisor never runs. A STRUCTURAL no-op, the
#:      same shape PS-350 measured on ``stealth_ext``'s downlinkMax shim.
#:   2. The WRAPPER is observable anyway. Installing it replaces a native
#:      function with a JS one, which ``masking.measureText`` reads directly.
#:
#: ⚠️ REDUCED, AND THE REDUCTION IS NAMED. This is ``CONTENT_SCRIPT``'s
#: ``patch()`` — its ``corrupt`` guard, its ``trueWidth`` fallback path and its
#: ``length``/``name``/``__pnaName`` cloak — lifted and reduced, exactly as
#: PS-350 lifted ``stealth_ext``'s two ``defineProperty`` calls. It deliberately
#: does NOT carry the realm registry (``__pnaSlot``), the worker bootstrap, or
#: ``_native_cloak_js``, which the Firefox route inlines as a PRELUDE into every
#: init script it installs. So row 2 measures the wrapper's shape WITHOUT that
#: cloak, and the artifact says so: a product route carrying the prelude would
#: answer ``Function.prototype.toString`` differently. ⛔ THAT IS NOT THE SAME
#: AS "the tell goes away" — the project's own knowledge article PS-22 records
#: that toString is one read out of at least four, and that
#: ``getOwnPropertyNames`` / ``.length`` / ``.name`` are CHEAPER for a detector
#: to run. The reduction bounds this arm's claim; it does not soften it.
NOOP_JS = """
(function () {
  try {
    var P = self.CanvasRenderingContext2D && self.CanvasRenderingContext2D.prototype;
    if (!P || !P.measureText) return 'FAILED: no CanvasRenderingContext2D.measureText';
    var orig = P.measureText;
    function measureText(text) {
      var m = orig.call(this, text);
      try {
        var hasText = String(text).length > 0;
        var corrupt = hasText && !(Math.abs(m.width) >= 1);
        if (!corrupt) return m;
        return new Proxy(m, {
          get: function (t, p) {
            var v = t[p];
            return (typeof v === 'number') ? v / 1e-6 : v;
          },
        });
      } catch (e) {}
      return m;
    }
    var inner = measureText;
    var shell = ({ m() { return inner.apply(this, arguments); } }).m;
    Object.defineProperty(shell, 'length', { value: orig.length });
    Object.defineProperty(shell, 'name', { value: 'measureText' });
    Object.defineProperty(shell, '__pnaName', { value: 'measureText' });
    P.measureText = shell;
    return 'noop-installed';
  } catch (e) { return 'FAILED: ' + e; }
})()
"""

#: THE REVEAL CONTROL, and it is the DEFECT rather than the repair.
#:
#: ``measuretext_ext`` exists to REPAIR a multiplicative noise factor the
#: Chromium fingerprint engine injects into every ``measureText`` metric. On the
#: committed Chromium artifacts that noise collapses all 14 font widths to
#: ``|v| < 1`` (0.001 / -0.0 / -0.001, moving with the seed). So the honest
#: reveal here is to INSTALL THAT DEFECT on a stock Firefox and check that the
#: instrument sees it: if the widths do not collapse under a wrapper that
#: multiplies them by 1e-6, then a reading of "no noise" on the unmutated arms
#: says nothing at all.
#:
#: It is a TWO-ROW reveal on purpose, because the cell rests on two rows:
#:   * ``fonts.measureText`` must COLLAPSE to the ``|v| < 1`` signature, and
#:   * ``masking.measureText`` must STOP reading ``[native code]``.
#: The second half is the sharper one — it is the observable wrapper tell this
#: ticket argues a Firefox spoof would ADD, demonstrated rather than asserted.
REVEAL_JS = """
(function () {
  try {
    var P = self.CanvasRenderingContext2D && self.CanvasRenderingContext2D.prototype;
    if (!P || !P.measureText) return 'FAILED: no CanvasRenderingContext2D.measureText';
    var native = P.measureText;
    P.measureText = function measureText(text) {
      var m = native.apply(this, arguments);
      var out = {};
      for (var k in m) {
        var v = m[k];
        out[k] = (typeof v === 'number') ? v * 1e-6 : v;
      }
      return out;
    };
    return 'reveal-installed';
  } catch (e) { return 'FAILED: ' + e; }
})()
"""


# --- the page every arm is read on ------------------------------------------


class _Origin(threading.Thread):
    """A loopback page for the reading to happen on.

    ``http://127.0.0.1`` is a potentially-trustworthy origin and therefore a
    SECURE CONTEXT with a real (non-opaque) origin. Confound 2 in the module
    docstring is what this exists to remove. Loopback also keeps the whole
    measurement off the network.
    """

    daemon = True

    def __init__(self):
        super().__init__()

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"<!doctype html><title>ps369</title><p id=ps369>measureText probe"
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


# --- the marionette wire, for the three BARE arms ----------------------------
#
# Stock Firefox has no juggler channel, so a stock arm cannot use the gesture
# persona's launcher uses. Driving stock over marionette and persona over
# juggler would confound TWO variables at once (BINARY and CHANNEL), and an
# agreement from such a pair could not be attributed to either. So A1, A2 and R
# all run over marionette. That is PS-171 arm C's construction, and PS-350's
# harness is the in-tree copy this is descended from.


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
        The worker harness returns a promise, so this always awaits.
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
    other one. The arms run on DIFFERENT ports so a leftover process from one
    can never be dialled by another.
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

    ⛔ NOT COSMETIC PROVENANCE. The arm's LABEL is what the harness INTENDED to
    launch; this is what the kernel says ran. ``navigator.buildID`` cannot do
    this job — the engine pins it — so the one identifier a page could offer is
    exactly the one this engine spoofs.

    ⚠️ THE GRAIN IS THE DIRECTORY, NOT THE FILE, and PS-350 paid for that
    correction: upstream's ``firefox`` execs ``firefox-bin`` from the SAME
    install, so a realpath comparison refuses every stock arm over a true fact
    about Mozilla's launcher. The directory is the axis that actually separates
    the arms (stock tarball vs the invisible-playwright cache).
    """
    try:
        return os.path.dirname(os.path.realpath(f"/proc/{proc.pid}/exe"))
    except Exception:
        return None


# --- the reading itself, shared by every arm ---------------------------------


def _gates(evaluate) -> dict:
    """Prove the channel and the page BEFORE any measureText row is read.

    Confound 3 in the module docstring: a browser that never started and a
    browser that reports nothing produce byte-identical rows. Each gate is
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
    # still answer `location.href` — and this probe measures TEXT, so a browser
    # that never laid anything out is exactly the wrong instrument.
    gates["layout_width"] = evaluate(
        "(function(){var e=document.getElementById('ps369');"
        "return e ? Math.round(e.getBoundingClientRect().width) : -1;})()"
    )
    # A canvas 2d context must EXIST for `fonts.measureText` to report anything
    # but null. A null from an absent context and a null from a refusing engine
    # are different findings, so the context is gated rather than inferred.
    gates["canvas2d_available"] = evaluate(
        "(function(){try{var c=document.createElement('canvas');"
        "return !!(c && c.getContext && c.getContext('2d'));}catch(e){return false;}})()"
    )
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
        return "the origin is OPAQUE, so every row below would be a false absence"
    if gates.get("secure_context") is not True:
        return "the reading is not in a secure context"
    if "Gecko" not in str(gates.get("user_agent") or ""):
        return f"the UA is not Gecko: {gates.get('user_agent')!r}"
    if not isinstance(gates.get("layout_width"), (int, float)) or gates["layout_width"] <= 0:
        return "the document parsed but never RENDERED (no layout box)"
    if gates.get("canvas2d_available") is not True:
        return (
            "no canvas 2d context is available, so fonts.measureText would "
            "report null for a reason that has nothing to do with this cell"
        )
    if gates.get("worker_available") is not True:
        return "Worker is unavailable, so the worker realm cannot be entered"
    return None


def read_arm(evaluate, *, label: str, reveal: bool = False, noop: bool = False) -> dict:
    """Read the two measureText probes in BOTH realms, through the PRODUCT's
    own harness.

    ``runner.window_expression`` / ``runner.worker_expression`` are imported
    rather than reimplemented, and the probes come out of ``PROBES`` by id, so
    an arm cannot read a hand-retyped expression that measures the typist. The
    worker realm is not optional here: ``baseline.py`` states why in its own
    words — *"a spoof that reaches the window and not the worker is a
    load-bearing leak, and it is invisible unless the worker realm is read."*
    """
    from src.services.verify.probes import PROBES
    from src.services.verify.runner import window_expression, worker_expression

    wanted = [p for p in PROBES if p.id in MEASURETEXT_PROBE_IDS]
    missing = set(MEASURETEXT_PROBE_IDS) - {p.id for p in wanted}
    if missing:
        raise SystemExit(
            f"probes.py no longer carries {sorted(missing)} — the inventory "
            f"moved and this reading would measure a different vector"
        )

    out = {"arm": label, "reveal_control": reveal, "noop_control": noop}
    out["gates"] = _gates(evaluate)
    refusal = _gates_ok(out["gates"])
    if refusal:
        out["unobtained"] = refusal
        return out

    if reveal:
        out["reveal_installed"] = evaluate(REVEAL_JS)
    if noop:
        out["noop_installed"] = evaluate(NOOP_JS)

    window = {}
    for probe in wanted:
        window[probe.id] = evaluate(window_expression(probe))
    out["window"] = window
    out["worker"] = evaluate(worker_expression(wanted))
    return out


#: The probe's OWN font list, lifted from the ``fonts.measureText`` expression
#: so the comparison surface cannot enumerate a different set than the probe
#: measured. Asserted against the probe at read time by ``_font_list``.
def _font_list() -> "list[str]":
    from src.services.verify.probes import PROBES

    probe = next(p for p in PROBES if p.id == "fonts.measureText")
    expr = probe.expr
    start = expr.index("var FONTS=[") + len("var FONTS=[")
    end = expr.index("]", start)
    fonts = [chunk.strip().strip("'") for chunk in expr[start:end].split(",")]
    if len(fonts) != 14:
        raise SystemExit(
            f"the probe's font list is {len(fonts)} long, not 14 — this "
            f"reading's comparison surface would silently measure a different "
            f"vector than the cell is about"
        )
    return fonts


def _unwrap(entry):
    return entry.get("v") if isinstance(entry, dict) and "v" in entry else entry


def _rows(arm: dict) -> dict:
    """The arm's readings reduced to the cell's OWN rows, per realm.

    Deliberately a projection of what was recorded and never a second reading:
    the raw ``window``/``worker`` maps stay in the artifact, and this is only
    the comparison surface.
    """
    rows = {}
    for realm in ("window", "worker"):
        block = arm.get(realm) or {}
        rows[realm] = {pid: _unwrap(block.get(pid)) for pid in MEASURETEXT_PROBE_IDS}
    return rows


#: The committed Firefox baseline artifact, used as the CORPUS arm of the
#: comparison. Named as a path rather than inlined so a reader can re-read it.
CORPUS_ARTIFACT = "tests/fixtures/engine-fingerprint-baseline.firefox.json"


def _corpus_widths() -> dict:
    """``fonts.measureText`` as the COMMITTED artifact holds it, per realm.

    ⚠️ THIS IS A DIFFERENT HOST, and that is precisely what makes it useful
    here rather than a confound. The 20 committed Firefox recordings were taken
    on Windows desktop profiles with a full font stack; this reading is taken on
    a 3-family Linux host. So an arm's AGREEMENT with these numbers is not a
    same-host comparison at all — it is the question "does this arm reproduce
    widths the host cannot account for", which is the one direction a
    cross-host comparison CAN be read in.
    """
    data = json.loads((REPO_ROOT / CORPUS_ARTIFACT).read_text(encoding="utf-8"))
    return {
        realm: data["probes"][realm]["fonts.measureText"]["value"]
        for realm in ("window", "worker")
    }


def _corpus_agreement(widths, corpus) -> dict:
    """How many of the 14 fonts this arm reports at the COMMITTED value.

    ⛔ READ THE DIRECTION, because it is the opposite of the usual one. A LOW
    score is NOT a defect and a HIGH score is NOT "the arms agree": the corpus
    was recorded on another host, so a browser that simply reports what THIS
    host's fontconfig resolves must score low. A high score means the arm
    reproduced widths for fonts this host does not have installed — i.e. it
    carried its own font set rather than the host's. That is the only claim a
    cross-host magnitude comparison can support, and it is why this is reported
    as a named ladder rather than as a diff.
    """
    if not isinstance(widths, dict) or not widths:
        return {"readable": False, "matching": None, "total": 0, "differing": None}
    differing = sorted(k for k in corpus if widths.get(k) != corpus[k])
    return {
        "readable": True,
        "matching": len(corpus) - len(differing),
        "total": len(corpus),
        "differing": differing,
    }


def _realm_liveness(rows: dict) -> dict:
    """THE SECOND LIVENESS CONTROL, and the WORKER realm's only one.

    ⛔ THE REVEAL ARM IS WINDOW-SCOPED AND CANNOT COVER THE WORKER, stated here
    rather than left for a reader to discover. Arm R patches
    ``CanvasRenderingContext2D.prototype`` in the PAGE realm over marionette;
    the worker harness builds a fresh ``Worker`` from a Blob with its own
    globals, which a page-realm prototype patch never reaches. So R moves the
    window rows and leaves the worker rows exactly where A1 left them — that is
    the injection's scope, NOT a worker probe that failed to see a defect, and
    reading it the second way would be wrong.

    What establishes the WORKER leg as a live instrument is INTER-ARM VARIATION:
    a probe that reported a constant could not return three DIFFERENT vectors
    from three different browsers on one host in one run. This counts the
    distinct vectors the non-reveal arms produced, per realm. One distinct
    vector across three arms would mean the realm is not discriminating and no
    absence read there could be trusted.
    """
    out = {}
    for realm in ("window", "worker"):
        vectors = {}
        for label, realm_rows in rows.items():
            if label.startswith(("R_", "N_")):
                continue  # a mutated arm is not evidence of discrimination
            widths = realm_rows.get(realm, {}).get("fonts.measureText")
            if isinstance(widths, dict) and widths:
                vectors[label] = json.dumps(widths, sort_keys=True)
        out[realm] = {
            "arms_read": sorted(vectors),
            "distinct_vectors": len(set(vectors.values())),
        }
    return out


def _noise_signature(widths) -> dict:
    """THE HOST-INVARIANT ROW, and the reason this reading survives a font stack.

    The Chromium defect ``measuretext_ext`` repairs is a MULTIPLICATIVE factor of
    ~1e-6 applied to every metric, which collapses all 14 widths to ``|v| < 1``.
    Real text widths at 16px are two orders of magnitude above that on ANY font
    stack — even a fully-fallback host reports ~200 — so this count separates
    "noised" from "unnoised" without depending on which fonts are installed.
    That is exactly what an absolute-width comparison cannot do.
    """
    if not isinstance(widths, dict) or not widths:
        return {"readable": False, "below_one": None, "total": 0, "distinct": None}
    numeric = [v for v in widths.values() if isinstance(v, (int, float))]
    return {
        "readable": len(numeric) == len(widths),
        "below_one": sum(1 for v in numeric if abs(v) < 1),
        "total": len(widths),
        "distinct": len({round(float(v), 3) for v in numeric}),
    }


# --- arm B: the SHIPPING launch path -----------------------------------------


def read_product_arm(profile_name: str, home: Path, origin_url: str) -> dict:
    """Launch through ``spawn_browser`` — the path the product actually takes.

    A1/A2 hold the channel constant and vary the binary; this arm varies
    EVERYTHING the product does (its launcher, its prefs, its per-tab masking
    layer, headful under a display) and is the only arm that can say the
    shipping path does not wrap ``measureText`` after all. PS-330's instrument
    is the model and this is a trimmed copy of its launch block.

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
        # a text width is not a proxied-exit question, and a direct launch
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


def _installed_font_families() -> "list[str] | None":
    """THE HOST'S OWN FONT STACK, recorded because this probe is env-sensitive.

    ``fonts.measureText`` is in ``ENV_SENSITIVE_PROBES``, and a reading of it
    that does not say which families were installed cannot be re-read later:
    a future reader comparing magnitudes across hosts would attribute the font
    stack to the engine. Recorded as data, and ``None`` when ``fc-list`` is
    absent — never an empty list, which would read as "no fonts installed".
    """
    if not shutil.which("fc-list"):
        return None
    try:
        out = subprocess.run(
            ["fc-list", ":", "family"], capture_output=True, text=True, timeout=60
        )
        families = set()
        for line in out.stdout.splitlines():
            for name in line.split(","):
                name = name.strip()
                if name:
                    families.add(name)
        return sorted(families)
    except Exception:
        return None


def _environment(stock_binary: str, engine_binary: str) -> dict:
    """The shelf-life block.

    ⚠️ NAMES THE HOST PLATFORM AND ITS FONTS, not only the engine build. PS-314
    recorded a row moving between two artifacts whose ``engine_build`` was
    IDENTICAL because the row was gated on the HOST — and ``fonts.measureText``
    is a host-gated row by its own ``ENV_SENSITIVE_PROBES`` entry. A record
    naming only the build would attribute a host-gated row to the engine.
    """
    from src.services.browser.engine_install import active_build

    return {
        "host_platform": platform.platform(),
        "host_machine": platform.machine(),
        "host_system": platform.system(),
        "kernel": platform.release(),
        "display": os.environ.get("DISPLAY") or None,
        "installed_font_families": _installed_font_families(),
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
    ``ps150``/``ps301``/``ps350`` carry is that a STOCK binary must never be
    resolved through the product's resolver, because that is how a control arm
    produces a complete-looking record of something that is not the product. The
    stock path here is required on the command line and is never resolved. This
    helper is for arm A2, which IS persona's engine, and asking the product
    where its own binary lives is the honest way to find it — a hand-built glob
    would be a second source of truth that could name a different build than the
    one ``active_build()`` reports in the environment block.
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
        default=os.environ.get("PS369_STOCK_FIREFOX", ""),
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
    fonts = _font_list()

    from src.services.verify.chromium_tier import _ensure_display

    display, _xvfb = _ensure_display()
    os.environ["DISPLAY"] = display

    rev = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True
    ).stdout.strip()

    origin = _Origin()
    origin.start()

    record = {
        "ticket": "PS-369",
        "taken_at": datetime.now(timezone.utc).isoformat(),
        "tree": rev,
        "origin_url": origin.url,
        "probe_ids": list(MEASURETEXT_PROBE_IDS),
        "probe_font_list": fonts,
        "environment": _environment(args.stock, engine),
        "arms": [],
    }

    workdir = tempfile.mkdtemp(prefix="ps369-")
    try:
        for label, binary, port, reveal, noop in (
            ("A1_stock_control", args.stock, 2840, False, False),
            ("A2_persona_engine_bare", engine, 2841, False, False),
            ("R_reveal_control_stock", args.stock, 2842, True, False),
            ("N_noop_control_stock", args.stock, 2843, False, True),
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
                arm = read_arm(mn.evaluate, label=label, reveal=reveal, noop=noop)
                arm["answering_install"] = _answering_install(proc)
            except Exception as exc:
                arm = {
                    "arm": label,
                    "reveal_control": reveal,
                    "noop_control": noop,
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
            # names is WORSE than a missing arm: A1-vs-A2 is a comparison of two
            # BINARIES, so an arm answering from the wrong one produces a
            # confident, readable, wrong row. The arm is REFUSED rather than
            # annotated, because a caveat on a row still leaves the row in the
            # comparison.
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

        if not args.skip_product:
            print("--- B_persona_product_path: spawn_browser", flush=True)
            home = Path(workdir) / "persona-home"
            home.mkdir(parents=True, exist_ok=True)
            try:
                arm = read_product_arm("ps369-measuretext", home, origin.url)
            except Exception as exc:
                arm = {
                    "arm": "B_persona_product_path",
                    "unobtained": f"{type(exc).__name__}: {exc}",
                }
            record["arms"].append(arm)
    finally:
        origin.stop()

    # --- the comparison, computed from the recorded arms ---------------------
    by_label = {a["arm"]: a for a in record["arms"]}
    record["rows"] = {label: _rows(arm) for label, arm in by_label.items()}

    # THE HOST-INVARIANT SURFACE. Absolute widths are NOT compared between arms
    # here, deliberately: they are the env-sensitive half, and this host's
    # 3-family font stack makes them a statement about fontconfig rather than
    # about either engine. What IS compared is the noise signature and the
    # wrapper shape, neither of which depends on which fonts are installed.
    record["noise_signature"] = {
        label: {
            realm: _noise_signature(rows[realm].get("fonts.measureText"))
            for realm in ("window", "worker")
        }
        for label, rows in record["rows"].items()
    }
    record["wrapper_shape"] = {
        label: {
            realm: rows[realm].get("masking.measureText")
            for realm in ("window", "worker")
        }
        for label, rows in record["rows"].items()
    }
    # THE CROSS-HOST LADDER, and the only reading an absolute-magnitude
    # comparison supports (see ``_corpus_agreement``'s own warning about its
    # direction). Recorded as data beside the raw arms so a reader can redo it.
    corpus = _corpus_widths()
    record["corpus_artifact"] = CORPUS_ARTIFACT
    record["corpus_agreement"] = {
        label: {
            realm: _corpus_agreement(rows[realm].get("fonts.measureText"), corpus[realm])
            for realm in ("window", "worker")
        }
        for label, rows in record["rows"].items()
    }
    record["realm_liveness"] = _realm_liveness(record["rows"])

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
