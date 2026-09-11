#!/usr/bin/env python3
"""PS-409 acceptance 1-4: is the measureText wrapper PRESENT on an unfixed
engine and ABSENT on a fixed one — read off a real page, with the FULL
extension set loaded?

WHY THIS SCRIPT EXISTS AND WHY THE UNIT TESTS ARE NOT IT
────────────────────────────────────────────────────────
``tests/test_ps409_measuretext_gate.py`` and the matrix's both-arm test settle
what the PRODUCT DECIDES: which extension dirs land on ``--load-extension`` for
which installed engine. That is a claim about argv. It is not a claim about what
a page can read, and this ticket's acceptance is written in page terms:

    ⭐ no wrapper is present at all — ``Function.prototype.toString`` on
    ``measureText`` reads ``[native code]``, and ``getOwnPropertyNames`` shows
    no added marker.

So this launches two engines and asks the page.

⛔⛔ THE READING CORRECTED THE TICKET'S OBSERVABILITY PREMISE. READ THIS BEFORE
QUOTING ANY VERDICT BELOW.
────────────────────────────────────────────────────────────────────────────────
PS-409's acceptance item 2 names two facts as the evidence that no wrapper is
present::

    ``Function.prototype.toString`` on ``measureText`` reads ``[native code]``,
    and ``getOwnPropertyNames`` shows no added marker.

⛔ **MEASURED, BOTH ARE TRUE ON ALL FOUR ARMS — including the arm where the
repair is on the command line and demonstrably repairing.** So neither fact
discriminates, and a reading that checked only those two on only the fixed engine
would have matched the criterion word for word while establishing nothing.

The cause is in the tree and is not a defect: **PS-368 gave every Chromium leaf
its own closure-WeakMap toString cloak.** ``measuretext_ext`` splices
``chromium_leaf_cloak_js`` into its body and calls ``__pncMark(shell,
'measureText')``, so the wrapper stringifies as ``function measureText() {
[native code] }`` and carries exactly ``["length","name"]`` — the same reading a
native method gives, which is the whole point of that ticket. Measured here:
``length=1``, ``name='measureText'``, ``hasPrototype=false`` on every arm.

⭐ SO WHERE DID THE TICKET'S PREMISE COME FROM? Its own evidence says: **PR #327's
Arm N, on FIREFOX.** That arm installed this repair on a stock Gecko and watched
``masking.measureText`` stop reading ``[native code]`` — and it is true there,
because the Firefox path installs no such cloak. ⛔ The premise was carried from
Gecko to Chromium, and Chromium is the target. The ticket is right that the
wrapper buys nothing on a fixed engine; it is wrong about which surface sees it.

⭐ WHAT *DOES* DISCRIMINATE, FOUND BY MEASURING RATHER THAN ASSUMED: the repair
returns a **Proxy** over the native ``TextMetrics`` when it fires. A native
accessor invoked with a Proxy as its receiver throws ``TypeError`` — the getter
needs an internal slot the proxy does not have — so::

    Object.getOwnPropertyDescriptor(TextMetrics.prototype, 'width').get.call(m)

answers a number on every arm EXCEPT the one where the repair fired, where it
throws. One line, no timing and no statistics. ⚠️ That makes the tell this ticket
removes **sharper** than the stringification it was written around, which
strengthens the case for the gate rather than weakening it — but the acceptance
criterion as written cannot see it, so this script asserts BOTH: the two named
facts (which must not regress) and the observable that actually discriminates.

⚠️ AND ONE HONEST BOUND ON THE PROXY TELL: it is only reachable when the repair
FIRES, which on an unfixed engine is every call with text. On a FIXED engine the
guard refuses and the wrapper returns the native object untouched — so a wrapper
left installed there is invisible to this probe too. The ticket's instruction to
OMIT rather than neuter still stands on its own terms (an installed extension is
a file on disk, a content script in every frame, and an entry in
``chrome.runtime``-adjacent surfaces this probe does not read), and the stated
cost of leaving it is now "a wrapper that no probe in this reading can see"
rather than "an observable tell". ⛔ That is a WEAKER claim than the ticket makes
and it is recorded as such.

⛔ THE VERDICT DIRECTION. This script exits NON-ZERO when the arms do not
discriminate. It is a guard, not a transcript — the ``ps301_measuretext_repro``
lesson ("162 lines, one conditional, zero non-zero exit paths").

    exit 0  →  unfixed arm: repair present AND firing, geometry Sheets-shaped.
               fixed arm:   no proxy, no marker, [native code].
    exit 1  →  at least one arm disagreed. Which one is printed.

THE FOUR ARMS, AND WHY EACH EXISTS
──────────────────────────────────
====  ==============================  ========================================
arm   engine + version.txt            what it can attribute
====  ==============================  ========================================
A     SHIPPED engine, version.txt     THE PRESENT ARM. Acceptance 1. The
      at ``152.0.7977.75`` (the       repair must be installed AND FIRING: the
      string every real install       Proxy tell is reachable and the widths it
      carries), threshold set to a    returns are Sheets-shaped rather than
      later build                     ~1e-6.
B     FIXED CI engine, version.txt    THE ABSENT ARM. Acceptance 2. Same
      at ``152.0.7977.75.1``,         product, same nine other extensions, and
      threshold the same string       no wrapper: no proxy, no added own
                                      property, [native code].
C     FIXED engine, version.txt at    ⭐ THE CONTROL. B's absence could be
      ``152.0.7977.75`` (i.e. the     caused by the extension failing to load,
      gate says "still unfixed")      by a crash, or by a probe in the wrong
                                      realm. This arm runs THE SAME BINARY
                                      with only the GATE's answer moved, so
                                      the extension must come BACK on the
                                      command line — and, because the repair's
                                      guard refuses on a fixed engine, it also
                                      measures the wrapper's INERTNESS there
                                      (identical widths to B).
D     SHIPPED engine, threshold       ⭐ THE FALSIFICATION. Arm A's presence
      LOWERED to the installed        could likewise be an artefact. This arm
      build so the gate omits         omits the repair from the BROKEN engine
      the repair                      and must read the defect raw: ~1e-6
                                      widths. It is what "Sheets is broken"
                                      looks like through this exact
                                      instrument — the state the strict
                                      failure would ship.
====  ==============================  ========================================

⭐ ARMS C AND D ARE THE WHOLE POINT OF THE DESIGN. A and B alone show a
difference between two binaries, and cannot separate "the gate works" from "the
fixed binary happens to behave differently for some other reason". C holds the
BINARY fixed and moves the GATE; D holds the GATE's direction and moves nothing
else. Between them the wrapper's presence is attributed to the gate's decision
and to nothing else.

⚠️ ACCEPTANCE 4 — THE FULL SET. Every arm launches through the product's own
``spawn_browser``, so the reading is taken with the whole extension set loaded
and not with ``measuretext_ext`` in isolation. PS-391 established that a
single-extension reading proves nothing about the product: these leaves compose
(``native_ext`` patches ``Function.prototype.toString`` for the whole layer, so
a measureText wrapper read in isolation would stringify differently from one
read beside native_ext). Each arm prints the extension count it actually got and
REFUSES below the expected number rather than reading on.

⚠️ HOW THE ENGINE VERSION IS MOVED, and why this is not a stubbed constant.
Acceptance 3 requires the version be read from what is actually installed. It
is: each arm writes a real ``ENGINE_DIR/version.txt`` and puts a real AppImage
at ``ENGINE_DIR/fpchrome.AppImage``, then runs the unmodified product. Nothing
in ``src/`` is patched and no gate function is stubbed — the only things that
move between arms are the two facts a real install carries.

⚠️ WHAT THIS DOES NOT ESTABLISH. It is a LINUX reading and says nothing about a
Windows engine (no Windows compile venue exists — PS-406). It touches no
checker, no proxy and no pixelscan, so it speaks to neither the masking badge
nor Invariant #0. And it is HEADLESS: persona's own launch is headed, and this
container has no Xvfb, so ``--headless=new``, ``--no-sandbox`` and
``--disable-dev-shm-usage`` are appended to the product's argv. All three are
flags persona itself never passes. They are stated on every arm's record. What
they could plausibly move is the RENDERING path; the two facts read here are a
function's source text and its own-property list, which are properties of the JS
realm the content script installs into. ⚠️ Note the product's OWN argv already
arranges software rendering (``--use-gl=angle --use-angle=swiftshader``), which
is left exactly as it is — see ``run_arm`` on why ``--disable-gpu`` is NOT added.
"""
import asyncio
import http.server
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import websockets

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

SHIPPED = os.environ.get("PS409_SHIPPED", "/tmp/ps409/shipped.AppImage")
FIXED = os.environ.get(
    "PS409_FIXED",
    "/tmp/ps409/package-out/patched/"
    "ps218-patched-ungoogled-chromium-152.0.7977.75-1-x86_64.AppImage",
)

# Set by main() once, before any arm runs: see there on why one origin serves
# all four.
PAGE_URL = ""

SHIPPED_TAG = "152.0.7977.75"
FIXED_TAG = "152.0.7977.75.1"

# ⭐ THE FULL SET FOR THIS CONFIGURATION IS TEN, NOT TWELVE, AND THE NUMBER WAS
# READ OFF THE PRODUCT RATHER THAN COUNTED FROM THE SOURCE. The first revision of
# this script asserted ELEVEN and the product answered TEN — which is exactly why
# this is a refusal and not a comment: a miscounted expectation either halts the
# reading (as it did) or, if it had been too LOW, would have let a short set pass
# as "full".
#
# The thirteen Chromium builders are native, locale, voice, stealth, measuretext,
# search, audio, mobile, device, webgl, gpu, canvas_ctx, geo. A DIRECT DESKTOP
# LINUX profile — what this container can launch — gets ten of them, and the three
# absences are the product's own conditions rather than gaps in the reading:
#
#   * ``search``  — ``not _platform.IS_LINUX``. On Linux the plaintext pref seed
#                   already sticks, so the extension would only raise the "an
#                   extension changed your search settings" bubble. It is also
#                   NOT a masking vector (``masking_layer`` excludes it in those
#                   words: "a settings override, not masking").
#   * ``mobile``  — a desktop profile takes the ``device`` branch instead. The
#                   two are branches of one ``if``, never two gates.
#   * ``geo``     — ``proxy``. This reading has no exit, and PS-406 established
#                   the owner's exits are not reachable from a worker container.
#
# ⚠️ SO THE ACCEPTANCE CRITERION'S "twelve-extension set" IS SATISFIED IN ITS
# SUBSTANCE AND NOT IN ITS ARITHMETIC, and that is stated rather than glossed:
# what PS-391 established is that a reading taken with ONE extension proves
# nothing about the product, because these leaves COMPOSE. All ten that this
# configuration installs are present on every arm, including ``native_ext`` —
# which is the one whose composition actually bears on this question, since it
# patches ``Function.prototype.toString`` for the whole layer and is therefore
# what could make a wrapper read as ``[native code]`` when it is not one.
EXPECTED_WITHOUT_MEASURETEXT = 9

# ⭐ THE PROBE. Two facts, both named by the acceptance criteria, both read in
# the page's own realm.
#
# `toString` is read through `Function.prototype.toString.call` rather than
# `ctx.measureText.toString()` on purpose: the latter would go through whatever
# `toString` the object chain resolves, and the whole question is whether
# something has been installed in that chain.
PAGE_JS = r"""
(() => {
  const c = document.createElement('canvas');
  const ctx = c.getContext('2d');
  ctx.font = '16px sans-serif';
  const mt = ctx.measureText;

  const src = Function.prototype.toString.call(mt);

  // Sheets-shaped geometry: a real grid measures ordinary label text. The
  // defect collapses every metric by a constant factor, so any string shows it.
  const widths = {};
  const metricKinds = {};
  for (const s of ['W', 'Sheet1', 'Personium measureText probe 12345']) {
    const m = ctx.measureText(s);
    widths[s] = m.width;
    // ⭐ WHETHER THE REPAIR FIRED IS VISIBLE IN THE RETURN VALUE'S IDENTITY, not
    // only in its numbers: measuretext_ext returns a PROXY over the native
    // TextMetrics when it repairs, and hands back the untouched native object
    // when its guard refuses. A Proxy keeps the target's prototype, so
    // `instanceof` cannot tell them apart — but a Proxy's own-property list is
    // the TARGET's, and `width` is an ACCESSOR on TextMetrics.prototype rather
    // than an own property, so both read []. What DOES differ is that reading a
    // numeric field through the proxy divides it.
    metricKinds[s] = {
      proto: Object.prototype.toString.call(m),
      isTextMetrics: (typeof TextMetrics !== 'undefined')
        ? (m instanceof TextMetrics) : null,
    };
  }

  // An un-noised reference for the same string in the same font, via the DOM
  // rect (which the engine does not scale). The RATIO of the two is what says
  // whether the geometry a layout engine would use is sane — the sign is NOT
  // the test, per the PS-345 invariant (what condemns is the factor's centre).
  const span = document.createElement('span');
  span.style.cssText =
    'position:absolute;left:-99999px;top:0;white-space:pre;visibility:hidden;' +
    'margin:0;padding:0;border:0;letter-spacing:0';
  span.style.font = ctx.font;
  span.textContent = 'Personium measureText probe 12345';
  document.documentElement.appendChild(span);
  const domWidth = span.getBoundingClientRect().width;
  document.documentElement.removeChild(span);

  // The realm marker the extension's idempotency guard installs. PS-93 moved it
  // off the global object onto the realm's own `Object` constructor, and PS-368
  // moved the per-wrapper name into a closure WeakMap — so this is read on
  // `Object`, which is where a detector that walks getOwnPropertyNames(Object)
  // would find it. It is NOT measureText's own property, which is the whole
  // point of those two tickets, so the acceptance criterion's
  // `getOwnPropertyNames` check is read on BOTH objects rather than one.
  const realmSlot = Object.getOwnPropertyNames(Object).filter(
    (n) => n.indexOf('__pna') === 0 || n.indexOf('__pnc') === 0);

  // ⭐ THE DISCRIMINATOR HUNT. PS-368 gave every Chromium leaf its own toString
  // cloak, so the acceptance criterion's two named facts ([native code] + no
  // added own property) are satisfied by a wrapper that IS there. This block
  // asks what else a page could read, so the finding is measured rather than
  // asserted in either direction.
  const probes = {};
  try {
    // The cloak delegates when it has no registered name, so Function.prototype
    // .toString is ITSELF wrapped. Reading IT is one hop up from the leaf.
    const tsSrc = Function.prototype.toString.call(Function.prototype.toString);
    probes.toStringOfToString = tsSrc;
    probes.toStringIsNative = /\{\s*\[native code\]\s*\}/.test(tsSrc);
  } catch (e) { probes.toStringOfToString = 'threw: ' + e; }
  try {
    // The return value's IDENTITY. measuretext_ext hands back a Proxy when it
    // repairs and the untouched native TextMetrics when its guard refuses.
    const m = ctx.measureText('Sheet1');
    probes.metricOwnProps = Object.getOwnPropertyNames(m);
    probes.metricHasOwnWidth = Object.prototype.hasOwnProperty.call(m, 'width');
    // A Proxy forwards getPrototypeOf, so this is NOT expected to differ — read
    // anyway, because an expectation unmeasured is an assumption.
    probes.metricProtoIsTextMetrics =
      (typeof TextMetrics !== 'undefined') &&
      Object.getPrototypeOf(m) === TextMetrics.prototype;
    // A Proxy over a platform object throws on an internal-slot read performed
    // with the PROXY as receiver. The native accessor lives on the prototype, so
    // calling it explicitly with the proxy as `this` is the classic check.
    try {
      const d = Object.getOwnPropertyDescriptor(TextMetrics.prototype, 'width');
      probes.widthGetterOnProxy = String(d.get.call(m));
    } catch (e) { probes.widthGetterOnProxy = 'threw: ' + e.constructor.name; }
  } catch (e) { probes.metricErr = String(e); }

  return {
    probeResults: probes,
    toStringSrc: src,
    isNativeCode: /\{\s*\[native code\]\s*\}/.test(src),
    ownProps: Object.getOwnPropertyNames(mt),
    fnLength: mt.length,
    fnName: mt.name,
    hasPrototype: Object.prototype.hasOwnProperty.call(mt, 'prototype'),
    protoOwnProps: Object.getOwnPropertyNames(
      Object.getPrototypeOf(ctx)).filter((n) => n.indexOf('__pn') === 0),
    realmSlotNames: realmSlot,
    widths: widths,
    metricKinds: metricKinds,
    domWidth: domWidth,
  };
})()
"""


async def read_via_cdp(ws_url, page_url):
    async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Target.getTargets"}))
        target = None
        while target is None:
            m = json.loads(await ws.recv())
            if m.get("id") == 1:
                for t in m["result"]["targetInfos"]:
                    if t["type"] == "page":
                        target = t["targetId"]
                        break
        await ws.send(json.dumps({
            "id": 2, "method": "Target.attachToTarget",
            "params": {"targetId": target, "flatten": True},
        }))
        session = None
        while session is None:
            m = json.loads(await ws.recv())
            if m.get("id") == 2:
                session = m["result"]["sessionId"]

        # ⛔ A REAL http:// ORIGIN, AND THIS IS THE ONE THING THE FIRST REVISION
        # GOT WRONG — it cost a whole four-arm run and produced a clean-looking
        # false negative. The page was a `data:text/html,...` URL, and an MV3
        # content script declared `matches: ["<all_urls>"]` DOES NOT RUN on a
        # data: URL: that pattern covers http/https/ws/wss/ftp and file (with
        # permission), and nothing else. So the probe read an unwrapped native
        # measureText on EVERY arm — including the one where the repair was
        # demonstrably on the command line — and the run reported "no wrapper"
        # with perfect internal consistency.
        #
        # ⭐ THAT IS EXACTLY WHY ARMS A AND C EXIST. A reading of only the fixed
        # engine would have read [native code], matched the acceptance criterion
        # word for word, and been WRONG — the wrapper was absent because the
        # probe was in the wrong realm, not because the gate omitted it. An
        # instrument that cannot be seen to report PRESENCE cannot be trusted
        # when it reports absence.
        await ws.send(json.dumps({
            "id": 3, "sessionId": session, "method": "Page.navigate",
            "params": {"url": page_url},
        }))
        while True:
            m = json.loads(await ws.recv())
            if m.get("id") == 3:
                break
        await asyncio.sleep(1.5)

        await ws.send(json.dumps({
            "id": 4, "sessionId": session, "method": "Runtime.evaluate",
            "params": {"expression": PAGE_JS, "returnByValue": True,
                       "awaitPromise": True},
        }))
        while True:
            m = json.loads(await ws.recv())
            if m.get("id") == 4:
                r = m["result"]["result"]
                if "value" not in r:
                    raise RuntimeError(f"eval failed: {json.dumps(m)[:900]}")
                return r["value"]


class _Page(http.server.BaseHTTPRequestHandler):
    """A minimal http:// origin for the probe to run in.

    ⛔ THE SERVER IS NOT A CONVENIENCE — IT IS THE REALM. See `read_via_cdp`: an
    MV3 content script matching `<all_urls>` runs on http/https and not on
    `data:`, so without a real origin every arm reads an unwrapped measureText
    and the instrument silently answers "no wrapper" everywhere.

    Loopback, ephemeral port, and it serves exactly one document. A direct
    profile's argv carries no ``--proxy-server`` at all (verified on the captured
    argv), so 127.0.0.1 needs no bypass list.
    """

    def do_GET(self):
        body = b"<!doctype html><html><body>ps409</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def run_arm(label, engine_path, version_txt, threshold, expect_measuretext):
    """Install `engine_path` as THE engine, record `version_txt` as its version,
    set the policy threshold, and launch the product.

    Nothing in `src/` is modified. The engine dir and the policy file are the two
    real records a real install carries, and they are the only things that move.
    """
    # ⚠️ THE PREFIX IS SHORT ON PURPOSE, AND IT IS NOT COSMETIC. Chromium's
    # ProcessSingleton builds a unix socket path under the user-data-dir and
    # ABORTS — `FATAL ... Socket path too long`, core dumped, no
    # DevToolsActivePort — when the result exceeds the sockaddr_un limit. The
    # first revision used `ps409-home-{label}-` plus mkdtemp's suffix plus the
    # product's own `data/<profile>/.persona-tmp/org.chromium.Chromium.XXXXXX/`
    # and crossed it. The failure reads as "the engine never started", which
    # looks like a broken arm rather than a path length.
    home = tempfile.mkdtemp(prefix=f"p409{label[0]}")
    engine_dir = os.path.join(home, "engine")
    os.makedirs(engine_dir, exist_ok=True)
    # A hardlink where possible: a 370 MB copy per arm is four copies of the
    # same bytes, and the product only ever execs it.
    dest = os.path.join(engine_dir, "fpchrome.AppImage")
    try:
        os.link(engine_path, dest)
    except OSError:
        shutil.copy2(engine_path, dest)
    os.chmod(dest, 0o755)
    with open(os.path.join(engine_dir, "version.txt"), "w", encoding="utf-8") as f:
        f.write(version_txt)

    policy_file = os.path.join(home, "engine-policy.json")
    with open(policy_file, "w", encoding="utf-8") as f:
        json.dump({"measuretext_fix_min_version": threshold}, f)

    env = dict(os.environ)
    env["PERSONA_HOME"] = home
    env["PERSONA_ENGINE_DIR"] = engine_dir
    env["PERSONA_ENGINE_POLICY_FILE"] = policy_file
    env["PERSONA_DATA_DIR"] = os.path.join(home, "data")

    # The launcher runs in a CHILD process so PERSONA_HOME/ENGINE_DIR are read
    # at import time by the product's own config module rather than being
    # monkeypatched in-process. That is the difference between "the product read
    # this install" and "a test told the product what to think".
    driver = os.path.join(home, "drive.py")
    with open(driver, "w", encoding="utf-8") as f:
        f.write(
            "import json, os, sys\n"
            f"sys.path.insert(0, {str(REPO)!r})\n"
            "from src.services.browser import process as pr\n"
            "from src.models.profile import Profile\n"
            "real = pr.subprocess.Popen\n"
            "cap = {}\n"
            "def fake(args, **kw):\n"
            "    cap['args'] = list(args)\n"
            "    cap['env'] = kw.get('env')\n"
            "    class P:\n"
            "        pid = 0\n"
            "        def poll(self): return 0\n"
            "    return P()\n"
            "pr.subprocess.Popen = fake\n"
            "p = Profile(name='ps409', ai_control=True)\n"
            "try:\n"
            "    pr.spawn_browser(p)\n"
            "except Exception as e:\n"
            "    cap['error'] = f'{type(e).__name__}: {e}'\n"
            "print('__PS409__' + json.dumps(cap))\n"
        )
    out = subprocess.run(
        [sys.executable, driver], capture_output=True, text=True, env=env,
        encoding="utf-8", errors="replace",
    )
    line = [l for l in out.stdout.splitlines() if l.startswith("__PS409__")]
    if not line:
        raise RuntimeError(
            f"{label}: the product's launch path produced no argv.\n"
            f"stdout:\n{out.stdout[-2000:]}\nstderr:\n{out.stderr[-3000:]}"
        )
    cap = json.loads(line[0][len("__PS409__"):])
    if "error" in cap:
        raise RuntimeError(f"{label}: spawn_browser refused: {cap['error']}")

    args = cap["args"]
    ext_arg = [a for a in args if a.startswith("--load-extension=")]
    if not ext_arg:
        raise RuntimeError(f"{label}: the launch loaded NO extensions at all")
    ext_dirs = ext_arg[0].split("=", 1)[1].split(",")
    vectors = sorted(
        os.path.basename(d.rstrip("/\\"))[len(".persona-"):-len("-ext")]
        for d in ext_dirs
    )

    # ⛔ REFUSE rather than read on. A reading taken with a short extension set
    # is a reading of something that is not the product (PS-391), and the whole
    # value of arm B is that the OTHER ten leaves were present while this one
    # was not.
    want = EXPECTED_WITHOUT_MEASURETEXT + (1 if expect_measuretext else 0)
    if len(vectors) != want:
        raise RuntimeError(
            f"{label}: expected {want} extensions, got {len(vectors)}: {vectors}"
        )
    if expect_measuretext and "measuretext" not in vectors:
        raise RuntimeError(f"{label}: the gate omitted measuretext — arm is void")
    if not expect_measuretext and "measuretext" in vectors:
        raise RuntimeError(f"{label}: the gate installed measuretext — arm is void")

    # Headless + unsandboxed, appended to the PRODUCT's argv. Disclosed on the
    # record; see the module docstring on what they could and could not move.
    #
    # ⛔ `--disable-gpu` IS DELIBERATELY NOT HERE, and its absence is a measured
    # fact rather than an oversight. The product already passes
    # `--use-gl=angle --use-angle=swiftshader --enable-unsafe-swiftshader`, which
    # is its own software-rendering arrangement; adding `--disable-gpu` on top
    # made the engine never publish DevToolsActivePort at all. The first revision
    # of this script passed it and read zero arms — so the rule is to add the
    # MINIMUM the venue needs and let the product's own flags stand.
    extra = ["--headless=new", "--no-sandbox", "--disable-dev-shm-usage"]
    udd = [a for a in args if a.startswith("--user-data-dir=")][0].split("=", 1)[1]
    launch = args + extra
    _dbg = open(f"/tmp/ps409/engine-{label}.log", "w", encoding="utf-8")
    proc = subprocess.Popen(
        launch, stdout=_dbg, stderr=subprocess.STDOUT,
        env=cap.get("env") or env,
    )
    try:
        port_file = os.path.join(udd, "DevToolsActivePort")
        reading = None
        for _ in range(400):
            # ⛔ FAIL FAST AND LOUDLY ON A DEAD ENGINE rather than waiting out the
            # whole timeout: a crashed launch and a slow one both end in "never
            # published DevToolsActivePort", and only the first has a reason in
            # its log. Both were hit while building this (a ProcessSingleton
            # socket-path abort; a pthread_create failure under PID pressure),
            # and both read as a broken arm until the log was opened.
            if proc.poll() is not None:
                with open(f"/tmp/ps409/engine-{label}.log", encoding="utf-8",
                          errors="replace") as lf:
                    tail = lf.read()[-1200:]
                raise RuntimeError(
                    f"{label}: the engine EXITED rc={proc.returncode} before "
                    f"publishing a port. Engine log tail:\n{tail}"
                )
            if os.path.exists(port_file):
                with open(port_file, encoding="utf-8") as f:
                    txt = f.read().split("\n")
                if len(txt) >= 2 and txt[0].strip():
                    path = txt[1].strip()
                    if not path.startswith("/"):
                        path = "/" + path
                    time.sleep(1.0)
                    reading = asyncio.run(
                        read_via_cdp(
                            f"ws://127.0.0.1:{txt[0].strip()}{path}", PAGE_URL
                        )
                    )
                    break
            time.sleep(0.25)
        if reading is None:
            raise RuntimeError(f"{label}: engine never published DevToolsActivePort")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except Exception:
            proc.kill()
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
        _dbg.close()
        # ⛔ REAP BEFORE THE NEXT ARM, AND THIS IS NOT TIDINESS. Each arm is a
        # full Chromium process tree launched from an AppImage, and
        # `--appimage-extract-and-run` adds its own squashfs extraction on top.
        # Terminating the parent does not immediately reap the zygote/renderer
        # children, and the fourth arm died on `pthread_create: Resource
        # temporarily unavailable` → `FATAL: Failed to start BrowserThread:IO`
        # with three arms' worth of processes still draining. That failure reads
        # as "the engine never published DevToolsActivePort" — i.e. exactly like
        # a broken arm — so a reading that did not wait here would report a
        # false DEFECT on whichever arm happened to run last.
        time.sleep(8)

    reading["_arm"] = label
    reading["_page_url"] = PAGE_URL
    reading["_engine"] = engine_path
    reading["_version_txt"] = version_txt
    reading["_threshold"] = threshold
    reading["_vectors"] = vectors
    reading["_extension_count"] = len(vectors)
    reading["_extra_flags"] = extra
    shutil.rmtree(home, ignore_errors=True)
    return reading


def ratio(r):
    """noised width / un-noised DOM width for the long probe. A healthy factor
    is centred on 1; the defect collapses it by ~1e-6. THE SIGN IS NOT THE TEST
    (PS-345: half of all seeds produce a positive, spec-legal, equally-broken
    value)."""
    w = r["widths"]["Personium measureText probe 12345"]
    d = r["domWidth"]
    return (w / d) if d else float("nan")


def report(r):
    print(f"\n=== arm {r['_arm']} ===")
    print(f"  engine       : {os.path.basename(r['_engine'])}")
    print(f"  version.txt  : {r['_version_txt']}")
    print(f"  threshold    : {r['_threshold']}")
    print(f"  extensions   : {r['_extension_count']}  {r['_vectors']}")
    print(f"  extra flags  : {' '.join(r['_extra_flags'])}")
    print(f"  page         : {r['_page_url']}")
    print(f"  toString     : {r['toStringSrc'][:130]!r}")
    print(f"  [native code]: {r['isNativeCode']}")
    print(f"  own props    : {r['ownProps']}  length={r['fnLength']} "
          f"name={r['fnName']!r} hasPrototype={r['hasPrototype']}")
    print(f"  proto __pn*  : {r['protoOwnProps']}")
    print(f"  Object __pn* : {r['realmSlotNames']}")
    pr_ = r.get("probeResults", {})
    print(f"  F.p.toString native?  {pr_.get('toStringIsNative')}")
    print(f"  metric own props      {pr_.get('metricOwnProps')}")
    print(f"  metric proto is TM    {pr_.get('metricProtoIsTextMetrics')}")
    print(f"  width getter on proxy  {str(pr_.get('widthGetterOnProxy'))[:60]}")
    print(f"  DOM width    : {r['domWidth']:.4f}")
    for s, w in r["widths"].items():
        print(f"  width {s[:32]!r:36} = {w:>20.8f}")
    print(f"  ratio (canvas/DOM) = {ratio(r):.8f}")


def main():
    for p in (SHIPPED, FIXED):
        if not os.path.isfile(p):
            print(f"missing engine: {p}", file=sys.stderr)
            return 2

    # ONE server for all four arms: the origin must be identical across them, or
    # a difference between arms could be a difference between origins.
    global PAGE_URL
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Page)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    PAGE_URL = f"http://127.0.0.1:{srv.server_address[1]}/ps409"
    print(f"probe origin: {PAGE_URL}")

    arms = {}
    # A — the present arm: the engine in the field, gate says repair needed.
    arms["A"] = run_arm("A-shipped-gated-on", SHIPPED, SHIPPED_TAG, FIXED_TAG, True)
    # B — the absent arm: a fixed engine, gate says omit.
    arms["B"] = run_arm("B-fixed-gated-off", FIXED, FIXED_TAG, FIXED_TAG, False)
    # C — the same FIXED binary with only the gate's answer moved.
    arms["C"] = run_arm("C-fixed-gated-on", FIXED, SHIPPED_TAG, FIXED_TAG, True)
    # D — THE FALSIFICATION: the BROKEN engine with the repair OMITTED.
    #
    # ⚠️ THE THRESHOLD HERE CANNOT BE `""`, AND FINDING THAT OUT WAS THE GATE
    # PROVING ITSELF. The first revision passed `""` expecting "no threshold →
    # omit", and the product refused the arm by INSTALLING the repair — which is
    # the fail-open rule working exactly as specified: an unset threshold means
    # "we cannot name a fixed build", never "every build is fixed". So the only
    # way to reach the omit branch on the broken engine is to LIE about the
    # threshold — name a build at or below the one installed. That lie IS the
    # strict failure this gate exists to prevent, which makes it the right way to
    # stage the arm: it reproduces precisely what a mis-set threshold would ship.
    arms["D"] = run_arm(
        "D-shipped-gated-off", SHIPPED, SHIPPED_TAG, SHIPPED_TAG, False
    )

    for k in "ABCD":
        report(arms[k])

    A, B, C, D = arms["A"], arms["B"], arms["C"], arms["D"]
    verdicts = []

    def proxied(r):
        """Is the returned TextMetrics a PROXY — i.e. did the repair FIRE?

        ⭐ THIS IS THE OBSERVABLE THE READING FOUND, AND IT IS NOT THE ONE THE
        ACCEPTANCE CRITERION NAMES. See the FINDING block in the module
        docstring. A native accessor read with a Proxy as its receiver throws
        `TypeError` ("Illegal invocation"): the getter needs the internal slot
        its receiver does not have. On a native TextMetrics it returns a number.
        One line, no timing, no statistics — so it is a sharper page-readable
        tell than the stringification the ticket was written around.
        """
        v = str(r["probeResults"].get("widthGetterOnProxy", ""))
        return v.startswith("threw:")

    # ── ACCEPTANCE 1: on an engine WITHOUT the fix, the repair is installed AND
    # FUNCTIONS. Two halves, and the second is the one that matters: an
    # installed-but-broken repair would satisfy "the wrapper is there" while
    # Sheets stayed broken.
    a_present = proxied(A) and "measuretext" in A["_vectors"]
    a_repairs = 0.9 < ratio(A) < 1.1
    verdicts.append(("AC1/repair-PRESENT-and-FIRING-on-unfixed", a_present))
    verdicts.append(("AC1/geometry-is-sheets-shaped", a_repairs))

    # ── ACCEPTANCE 2: on an engine WITH the fix, NO WRAPPER AT ALL. The two
    # facts the criterion names, PLUS the observable that actually discriminates.
    #
    # ⚠️ THE FIRST TWO PASS ON EVERY ARM AND ARE THEREFORE NOT EVIDENCE ON THEIR
    # OWN — that is the finding, not a weakness of the gate. They are asserted
    # anyway, because the criterion names them and a future engine or a future
    # change to PS-368's cloak could make them discriminate again; if one ever
    # goes false on arm B, something regressed.
    b_native = B["isNativeCode"]
    b_no_marker = set(B["ownProps"]) <= set(D["ownProps"])
    b_not_proxied = not proxied(B)
    verdicts.append(("AC2/native-code-on-fixed", b_native))
    verdicts.append(("AC2/no-added-own-property", b_no_marker))
    verdicts.append(("AC2/NO-PROXY-on-fixed", b_not_proxied))

    # ── THE CONTROL: the same fixed BINARY, gate flipped, wrapper must RETURN.
    # Without this, arm B's absence is also consistent with a harness that
    # cannot see a wrapper at all — which is exactly the false negative the
    # first revision of this script produced from a `data:` URL.
    #
    # ⚠️ AND HERE THE CONTROL IS BLIND BY CONSTRUCTION, WHICH IS ITSELF THE
    # FINDING. On the FIXED engine the repair's own guard refuses (widths are
    # ~200, not ~1e-4), so the extension installs the wrapper and the wrapper
    # returns the NATIVE metrics untouched — no Proxy is ever minted. So arm C
    # cannot show the wrapper through the return value, and it is arm A that
    # carries the presence claim. What C still establishes is that the GATE put
    # the extension on the command line for this binary (`measuretext` in its
    # vector list, while B's identical binary lacks it) — which is the half this
    # arm was designed to isolate.
    c_gate_installed = "measuretext" in C["_vectors"]
    c_same_binary = C["_engine"] == B["_engine"]
    verdicts.append(("CONTROL/gate-reinstalls-on-the-SAME-binary",
                     c_gate_installed and c_same_binary))
    # ...and the inertness is asserted rather than excused: on a fixed engine the
    # wrapper changes NOTHING a page can read, which is precisely why omitting it
    # costs nothing and why PR #327's Arm N measured 14/14 identical widths.
    verdicts.append((
        "CONTROL/wrapper-is-a-STRUCTURAL-NO-OP-on-fixed",
        (not proxied(C))
        and C["widths"] == B["widths"]
        and C["isNativeCode"] == B["isNativeCode"],
    ))

    # ── THE FALSIFICATION: the broken engine with the repair omitted must read
    # the defect RAW. This is what the strict failure ships, and seeing it proves
    # the width probe can report a collapsed value at all.
    d_collapsed = abs(ratio(D)) < 0.01
    d_not_proxied = not proxied(D)
    verdicts.append(("FALSIFY/unfixed-WITHOUT-repair-is-broken", d_collapsed))
    verdicts.append(("FALSIFY/and-carries-no-wrapper", d_not_proxied))

    # ── AND THE PAIRING THAT IS THE TICKET'S WHOLE CLAIM, stated as one line: a
    # fixed engine loses the wrapper and keeps its geometry; an unfixed one keeps
    # the wrapper and needs it.
    verdicts.append((
        "CLAIM/omitting-on-fixed-costs-NOTHING",
        (0.9 < ratio(B) < 1.1) and (0.9 < ratio(C) < 1.1)
        and B["widths"] == C["widths"],
    ))
    verdicts.append((
        "CLAIM/omitting-on-unfixed-would-BREAK-SHEETS",
        abs(ratio(D)) < 0.01 and (0.9 < ratio(A) < 1.1),
    ))

    # ── ACCEPTANCE 4: every arm carried the full set. Already refused above, so
    # this restates it as a verdict line rather than discovering it.
    full_set = all(
        a["_extension_count"] == EXPECTED_WITHOUT_MEASURETEXT
        + (1 if "measuretext" in a["_vectors"] else 0)
        for a in arms.values()
    )
    verdicts.append(("AC4/full-extension-set-on-every-arm", full_set))

    print("\n=== VERDICTS ===")
    for name, ok in verdicts:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")

    out = {
        "arms": {k: v for k, v in arms.items()},
        "ratios": {k: ratio(v) for k, v in arms.items()},
        "verdicts": dict(verdicts),
    }
    dest = os.environ.get("PS409_OUT", "/tmp/ps409/reading.json")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"\nwrote {dest}")

    failed = [n for n, ok in verdicts if not ok]
    if failed:
        print(f"\nOVERALL: DEFECT — failing: {', '.join(failed)}")
        return 1
    print("\nOVERALL: the gate discriminates. Repair present and working on an "
          "unfixed engine; no wrapper at all on a fixed one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
