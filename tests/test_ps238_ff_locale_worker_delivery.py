"""PS-238 — the Firefox locale spoof's worker delivery must survive a
restrictive ``connect-src``.

THE DEFECT. ``_worker_wrap_js`` carried the locale patch into a ``blob:`` worker
by reading the blob body back with a SYNCHRONOUS XHR. That XHR is subject to the
page's ``connect-src``, so on a restrictive origin it throws, the wrapper's own
catch falls back to constructing the ORIGINAL ``Worker``, and the result is the
worst available shape: a worker that runs normally and carries NO locale patch.
The page then reports the spoofed locale while the worker reports the HOST one —
a page/worker ``Intl.DateTimeFormat().resolvedOptions().locale`` disagreement
that is a SHARPER tell than the unspoofed value it was hiding.

AND THE REFUSING ORIGIN IS THE DEFAULT START PAGE. ``_ensure_firefox_policies()``
pins DuckDuckGo, which sends ``default-src 'none'`` with no ``blob:`` in its
``connect-src``. Re-measured live on firefox-20 / 151.0 through this project's
own launch path at ``62e1ac1`` (``spawn_browser(in_process=True)`` +
``get_ff_eval``, locale pinned ``de-DE`` via ``cfg["locale"]``)::

    origin                   sync XHR on blob:    page     WORKER
    https://duckduckgo.com/  THREW NetworkError   de-DE    en-US  <-- HOST
    https://example.com/     status 200           de-DE    de-DE

The second row is the CONTROL and it is what makes the first a refusal rather
than a broken probe.

⚠️ WHAT THESE TESTS ASSERT, AND WHY IT IS NOT SOURCE TEXT. Every assertion below
is on the LOCALE A REALM RETURNS after the generated script has actually RUN, in
an isolated ``node:vm`` realm — never on a substring of the emitted JS. A text
assertion (``"__rb" in js``) passes on a build that merely renames the payload
and would have passed on the broken build this ticket exists to fix, because the
old build emitted a perfectly good patch that was then never delivered. The
whole defect is that a value is WRITTEN and then not DELIVERED, so a test that
checks the writing cannot see it.

THE REFUSING REALM IS THE POINT. ``_make_realm(refuse_xhr=True)`` gives the
sandbox an ``XMLHttpRequest`` whose ``open`` THROWS, which is what DuckDuckGo's
CSP does to it. A harness whose "refusing" origin does not actually refuse
reproduces the OLD behaviour as a pass — so the differential (both arms, one
run) is asserted rather than assumed.
"""
import json
import shutil
import subprocess

import pytest

from src.services.browser import invisible_launch as il

HOST_LOCALE = "en-US"
TARGET_LOCALE = "de-DE"


# ---------------------------------------------------------------------------
# The realm harness. Deliberately NOT tests/realm_harness.py: that one's
# XMLHttpRequest ALWAYS succeeds when the blob is in its store, so it cannot
# express a refusing origin — which is the only interesting case here.
# ---------------------------------------------------------------------------
_PROBE = r"""
const vm = require("node:vm");
const fs = require("node:fs");

const script = fs.readFileSync(process.argv[2], "utf8");
const REFUSE = process.argv[3] === "refuse";
const HOST = process.argv[4];
const LEAF = "self.__leafRan = true;";

function makeRealm() {
  const captured = { bodies: [], usedXHR: false, xhrRefused: false };

  class FakeBlob {
    constructor(parts, opts) {
      this.parts = (parts || []).map((p) =>
        p instanceof FakeBlob ? p.text() : String(p));
      this.type = (opts && opts.type) || "";
    }
    text() { return this.parts.join(""); }
    get size() { return this.text().length; }
  }

  const urlMap = new Map();
  let n = 0;
  const FakeURL = {
    createObjectURL(obj) {
      const u = "blob:https://example.invalid/" + (++n);
      urlMap.set(u, obj);
      return u;
    },
    revokeObjectURL(u) { urlMap.delete(u); },
  };

  // The CSP. When REFUSE, `open` throws exactly as a restrictive
  // `connect-src` makes it throw on a blob: url.
  function FakeXHR() {}
  FakeXHR.prototype.open = function (m, u) {
    captured.usedXHR = true;
    if (REFUSE) {
      captured.xhrRefused = true;
      throw new Error("NetworkError: A network error occurred.");
    }
    this.__u = u;
  };
  FakeXHR.prototype.send = function () {
    const o = urlMap.get(String(this.__u));
    this.status = o ? 200 : 404;
    this.responseText = o ? o.text() : "";
  };

  function FakeWorker(url) {
    const obj = urlMap.get(String(url));
    // null == the wrapper fell back to the ORIGINAL Worker: no patch delivered.
    captured.bodies.push(obj ? obj.text() : null);
  }
  FakeWorker.prototype = {};

  const sandbox = {
    Blob: FakeBlob, URL: FakeURL, XMLHttpRequest: FakeXHR,
    Worker: FakeWorker, SharedWorker: FakeWorker,
    Reflect, JSON, String, Object, Array, Number, BigInt, Date, Map, WeakMap,
    Math, Function, Error, RegExp, isNaN, setTimeout, console,
  };
  sandbox.self = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.Navigator = function Navigator() {};
  sandbox.navigator = Object.create(sandbox.Navigator.prototype);

  // A minimal Intl family reporting the HOST locale until something pins it.
  sandbox.Intl = new Proxy({}, {
    get(t, k) {
      if (k in t) return t[k];
      if (typeof k !== "string") return undefined;
      const C = function (locales) {
        const l = Array.isArray(locales) ? locales[0] : locales;
        this._l = l || HOST;
      };
      Object.defineProperty(C, "name", { value: k, configurable: true });
      C.prototype.resolvedOptions = function () {
        return { locale: this._l || HOST };
      };
      C.supportedLocalesOf = function () { return []; };
      t[k] = C;
      return C;
    },
    set(t, k, v) { t[k] = v; return true; },
    has() { return true; },
  });

  return { ctx: vm.createContext(sandbox), sandbox, captured };
}

// Prove the realm's origin genuinely refuses, INDEPENDENTLY of whether the
// wrapper happened to use the XHR. With the fix the wrapper short-circuits to
// the retained Blob and never reaches the XHR at all, so observing the
// wrapper's own throw can no longer establish that anything was refused — and
// a "refusing" harness that does not actually refuse turns the whole result
// into a broken-probe artifact.
function cspRefusesDirectly(realm) {
  return vm.runInContext(
    '(function(){try{var u=URL.createObjectURL(new Blob(["x"]));' +
    'var x=new XMLHttpRequest();x.open("GET",u,false);x.send();return false;}' +
    'catch(e){return true;}})()', realm.ctx);
}

function localeOf(realm) {
  try {
    return vm.runInContext(
      "new Intl.DateTimeFormat().resolvedOptions().locale", realm.ctx);
  } catch (e) { return "ERR:" + e.message; }
}

// Spawn a blob: worker from `realm` and return the body its wrapper composed.
function spawnBlobWorker(realm) {
  vm.runInContext(
    'var __u = URL.createObjectURL(new Blob([' + JSON.stringify(LEAF) +
    '],{type:"application/javascript"}));new Worker(__u);', realm.ctx);
  return realm.captured.bodies[realm.captured.bodies.length - 1];
}

const out = {};

// The globals a realm has BEFORE anything is installed. The sandbox's own stubs
// (Blob, URL, Worker...) are enumerable in a vm context, so the meaningful
// reading is the DELTA a payload adds, never the absolute key list.
const baselineGlobals = (() => {
  const fresh = makeRealm();
  return vm.runInContext("Object.keys(globalThis)", fresh.ctx);
})();
out.baselineGlobals = baselineGlobals;
const added = (keys) => keys.filter((k) => baselineGlobals.indexOf(k) === -1);

// --- the page realm ------------------------------------------------------
const page = makeRealm();
vm.runInContext(script, page.ctx);
out.pageLocale = localeOf(page);

// what the page's global gained (AC6: no new enumerable residue)
out.pageGlobals = added(vm.runInContext("Object.keys(globalThis)", page.ctx));

// --- descend the worker chain -------------------------------------------
out.depths = [];
out.bodySizes = [];
let body = spawnBlobWorker(page);
out.xhrRefusedAtPage = page.captured.xhrRefused;
// ORDER MATTERS. Read what the WRAPPER did before running any probe of our own:
// `cspRefusesDirectly` issues an XHR itself, so calling it first would set
// `usedXHR` and the next line would report OUR probe's XHR as the wrapper's.
out.wrapperUsedXHR = page.captured.usedXHR;
// Only now the independent reading: does this origin refuse the sync XHR at all?
out.cspRefuses = cspRefusesDirectly(page);
out.deliveredAtPage = body !== null && body !== undefined;

for (let lvl = 1; lvl <= 3 && body; lvl++) {
  const child = makeRealm();
  vm.runInContext(body, child.ctx);
  out.bodySizes.push(body.length);
  out.depths.push({
    level: lvl,
    locale: localeOf(child),
    leafRan: child.sandbox.__leafRan === true,
    globals: added(vm.runInContext("Object.keys(globalThis)", child.ctx)),
  });
  body = spawnBlobWorker(child);
}

console.log(JSON.stringify(out));
"""


def _run(tmp_path, locale, *, refuse):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    (tmp_path / "probe.js").write_text(_PROBE, encoding="utf-8")
    (tmp_path / "script.js").write_text(
        il._language_override_script(locale), encoding="utf-8"
    )
    out = subprocess.run(
        [
            node,
            str(tmp_path / "probe.js"),
            str(tmp_path / "script.js"),
            "refuse" if refuse else "allow",
            HOST_LOCALE,
        ],
        capture_output=True, text=True, timeout=120, encoding="utf-8",
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def refused(tmp_path_factory):
    """The DuckDuckGo arm: the sync XHR is refused by `connect-src`."""
    return _run(tmp_path_factory.mktemp("ps238_refuse"), TARGET_LOCALE, refuse=True)


@pytest.fixture(scope="module")
def allowed(tmp_path_factory):
    """The example.com CONTROL arm: nothing refuses."""
    return _run(tmp_path_factory.mktemp("ps238_allow"), TARGET_LOCALE, refuse=False)


# --- AC1: the spoofed locale reaches a blob: worker on a refusing origin ----

def test_blob_worker_reports_spoofed_locale_when_xhr_is_refused(refused):
    # THE TICKET'S CENTRAL CLAIM. Before the fix this read the HOST locale,
    # because the refused XHR made the wrapper fall back to the original Worker.
    # Asserted on the locale the realm RETURNS, never on source text.
    assert refused["depths"], "no worker realm was reached at all"
    assert refused["depths"][0]["locale"] == TARGET_LOCALE


def test_the_patch_is_actually_delivered_not_merely_written(refused):
    # The distinction this ticket turns on: a body was composed for the worker,
    # rather than the wrapper silently constructing the ORIGINAL Worker.
    assert refused["deliveredAtPage"] is True


def test_the_original_worker_body_still_runs(refused):
    # A delivery that loses the page's own worker code would be a regression
    # dressed as a fix: the leaf must still execute alongside the patch.
    assert refused["depths"][0]["leafRan"] is True


# --- AC3: the differential control, asserted in the same suite -------------

def test_the_refusing_arm_genuinely_refused(refused):
    # Without this, "the locale now arrives" is equally satisfied by a harness
    # where nothing was ever refused — the negative would be an artifact.
    #
    # Measured by an INDEPENDENT direct probe, not by watching the wrapper
    # throw: with the fix the wrapper short-circuits to the retained Blob and
    # never reaches the XHR, so the wrapper's own behaviour can no longer
    # establish that this origin refuses anything.
    assert refused["cspRefuses"] is True


def test_the_control_arm_genuinely_does_not_refuse(allowed):
    # The other side of the same control: the "allowing" arm must really allow,
    # or the two arms are not a differential at all.
    assert allowed["cspRefuses"] is False


def test_the_fix_delivers_without_reaching_the_xhr(refused):
    # The signature of the retained-Blob path: on a url this realm minted, the
    # delivery is composed directly and the sync XHR — the part `connect-src`
    # governs — is never touched. This is what makes the fix origin-independent
    # rather than merely lucky.
    assert refused["wrapperUsedXHR"] is False


def test_the_control_arm_also_delivers_the_spoofed_locale(allowed):
    # The other half of the differential: where nothing refuses, the worker got
    # the spoof both before and after this change.
    assert allowed["xhrRefusedAtPage"] is False
    assert allowed["depths"][0]["locale"] == TARGET_LOCALE


def test_both_arms_agree_so_the_fix_is_origin_independent(refused, allowed):
    # The whole point: the worker's locale no longer depends on the origin's CSP.
    assert (
        refused["depths"][0]["locale"] == allowed["depths"][0]["locale"] == TARGET_LOCALE
    )


def test_the_page_realm_is_spoofed_in_both_arms(refused, allowed):
    # The page was never the broken half; pin it so a fix cannot regress it.
    assert refused["pageLocale"] == allowed["pageLocale"] == TARGET_LOCALE


# --- PS-205's depth carry must survive the new delivery (AC5) --------------

@pytest.mark.parametrize("level", [0, 1, 2])
def test_locale_carries_to_every_worker_depth_under_refusal(refused, level):
    # PS-205 fixed the depth axis (a worker spawned from a worker read the HOST
    # locale). This ticket must not buy the origin axis by giving that back —
    # and depth>=2 is the case that regressed before.
    assert len(refused["depths"]) > level, "chain stopped short"
    assert refused["depths"][level]["locale"] == TARGET_LOCALE


def test_payload_does_not_grow_per_depth(refused):
    # PS-205's fixed point: `__pnaLoc` re-emits itself BY NAME, so the body is
    # the same size at every level. The naive fix embeds the payload text inside
    # itself, which DOUBLES it per level (exponential in depth). Constraint (2)
    # of this ticket names that as the specific way the retain map could break
    # it, so this is measured rather than assumed.
    sizes = refused["bodySizes"]
    assert len(sizes) >= 2
    assert len(set(sizes)) == 1, f"payload grows per level: {sizes}"


# --- AC6: no new enumerable residue on the worker's global ----------------

def test_no_new_enumerable_globals_in_the_worker_realm(refused):
    # `__rb` must be a CLOSURE variable and the two URL wrappers named function
    # EXPRESSIONS, so nothing this fix adds is enumerable on the worker global
    # for a page to read. The leaf's own marker is the only expected key.
    for depth in refused["depths"]:
        residue = [k for k in depth["globals"] if k != "__leafRan"]
        assert residue == [], f"new enumerable globals at depth: {residue}"


def test_no_retain_map_name_on_the_page_global(refused):
    assert "__rb" not in refused["pageGlobals"]
    assert "wrapW" not in refused["pageGlobals"]


# --- the emission rules, which the delivery must not break (AC7) ----------

def test_worker_wrapper_text_obeys_the_single_quoted_literal_rules():
    # The wrapper is inlined inside a SINGLE-quoted JS literal, so a `'` would
    # terminate it and a backslash escape would be consumed by the OUTER literal
    # and put a raw newline inside a double-quoted string — a SyntaxError. The
    # newline the blob body needs is `__nl`, never an escape.
    for payload in ("P", "WP"):
        text = il._worker_wrap_js(payload)
        assert "\n" not in text
        assert "\\" not in text
        assert "'" not in text
        assert text.count("{") == text.count("}")
        assert text.count("(") == text.count(")")


def test_the_retain_map_is_emitted_at_both_call_sites():
    # Constraint (2): the map must live INSIDE `__pnaLoc`, not in an enclosing
    # scope — a map defined outside is `undefined` in a worker. Emitting it from
    # the builder is what puts it in the right scope at BOTH call sites, so each
    # realm gets its OWN map. This is a structural claim about scope, which is
    # why it is the one place a text check is the honest instrument; the
    # BEHAVIOUR it protects is asserted by the depth tests above.
    assert "var __rb=" in il._worker_wrap_js("P")
    assert "var __rb=" in il._worker_wrap_js("WP")
