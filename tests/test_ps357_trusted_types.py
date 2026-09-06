"""PS-357: the worker spoof must survive a site that enforces Trusted Types.

THE DEFECT. ``worker_wrap`` re-blobs each worker payload and hands the real
constructor a STRING url. A site sending ``require-trusted-types-for 'script'``
— Google does — BLOCKS a string at that sink. The wrapper's own outer ``catch``
then fell through to the original constructor with the page's own url, so:

    the page's worker RAN, the page looked FINE, and the spoof was LOST.

Workers on every enforcing site reported REAL HOST VALUES. Silently — the only
symptom was console text (the operator saw it 20x on a Google Sheet).

⛔ WHY THIS FILE EXECUTES THE BOOTSTRAP INSTEAD OF GREPPING IT. The obvious
test — assert ``"trustedTypes"`` appears in the generated source — is worthless
here and the ticket says so outright: PS-314's mobile extension passed exactly
that kind of substring check *while failing to execute at all*. A string being
present proves the text was written, not that the sink was satisfied. So every
assertion below runs the REAL generated bootstrap in a ``node:vm`` realm
carrying a SIMULATED Trusted Types enforcement, and reads what the constructor
actually received.

⚠️ VENUE, STATED RATHER THAN IMPLIED. This is a SIMULATION of the platform
rule, not the platform. It models the one behaviour that matters (the sink
rejects a value that is not a TrustedScriptURL) because that is what the real
engine was measured doing. The measurements that anchor it were taken in real
headless Chromium against a live CSP header and are recorded in the PR; a live
Google Sheets confirmation is the owner's arm. PS-78's rule applies: a
measurement taken against one origin's CSP measures that CSP — so this file
proves the wrapper's behaviour under the rule, never that a given real site
sends it.
"""

import json
import os
import shutil
import subprocess
import tempfile

os.environ.setdefault("PERSONA_HOME", tempfile.mkdtemp())

import pytest  # noqa: E402

from src.services.browser.worker_wrap import realm_bootstrap_js  # noqa: E402


# ---------------------------------------------------------------------------
# The harness: a realm that ENFORCES Trusted Types the way the engine does.
# ---------------------------------------------------------------------------
#
# `mode` selects which of the two required arms this realm is:
#   "allowed"    — enforcement ON, our policy may be created  (arm 1)
#   "disallowed" — enforcement ON, createPolicy always throws (arm 2), which is
#                  what a `trusted-types <allowlist>` directive does to a name
#                  it does not list.
#
# The Worker stub is the SINK, and it applies the real rule: a string is
# REFUSED, a TrustedScriptURL is accepted. It also records the payload body so
# a test can ask the question that actually matters — did the SPOOF arrive —
# rather than merely whether a constructor was reached.

_PROBE = r"""
const vm = require("vm");
const fs = require("fs");

const BOOTSTRAP = fs.readFileSync(process.argv[2], "utf8");
const MODE = process.argv[3];

const BLOBS = new Map();
let blobN = 0;

function makeRealm(mode) {
  const record = {
    constructed: [],      // what the sink ACCEPTED
    refused: [],          // what the sink REJECTED (the defect's signature)
    violations: [],       // securitypolicyviolation samples a page could read
    policyNames: [],      // every name we attempted to register
  };

  // --- the Trusted Types platform, as the engine implements it -------------
  function TrustedScriptURL(v) { this._v = v; }
  TrustedScriptURL.prototype.toString = function () { return this._v; };

  const trustedTypes = {
    createPolicy: function (name, rules) {
      record.policyNames.push(name);
      if (mode === "disallowed") {
        // Exactly what Chromium does for a name outside the allowlist: throw,
        // AND emit a violation whose sample is the NAME. Measured in real
        // Chromium -- this is the actual fingerprint surface.
        record.violations.push({ directive: "trusted-types", sample: name });
        throw new TypeError("Failed to execute 'createPolicy': Policy \"" + name + "\" disallowed.");
      }
      return {
        createScriptURL: function (s) { return new TrustedScriptURL(rules.createScriptURL(s)); },
      };
    },
  };

  // --- the sink -----------------------------------------------------------
  function Worker(url) {
    if (!(url instanceof TrustedScriptURL)) {
      // The real engine's behaviour: refuse, and fire a violation naming the
      // sink. This is what silently cost us the spoof on every Google page.
      record.refused.push(String(url));
      record.violations.push({ directive: "require-trusted-types-for", sample: "Worker constructor" });
      throw new TypeError("This document requires 'TrustedScriptURL' assignment.");
    }
    const body = BLOBS.get(String(url));
    record.constructed.push(body === undefined ? String(url) : body);
  }

  const G = {
    trustedTypes: trustedTypes,
    Worker: Worker,
    SharedWorker: Worker,
    location: { href: "https://sheets.example/doc" },
    URL: {
      createObjectURL: function (blob) {
        const u = "blob:https://sheets.example/" + (++blobN);
        BLOBS.set(u, blob.__body);
        return u;
      },
    },
    Blob: function (parts) { this.__body = parts.join(""); },
    XMLHttpRequest: function () {
      this.open = function (m, u) { this.__u = String(u); };
      this.send = function () {
        this.status = BLOBS.has(this.__u) ? 200 : 404;
        this.responseText = BLOBS.get(this.__u) || "";
      };
    },
    Reflect: Reflect,
  };
  G.self = G;
  G.globalThis = G;
  const ctx = vm.createContext(G);
  // The page's OWN trusted url, minted through the page's own policy exactly
  // as a real enforcing site does before calling the constructor.
  G.PAGE_URL = new TrustedScriptURL("https://sheets.example/w.js");
  return { ctx, record, G };
}

const LEAF = "function applyAlpha(G){ try { G.__SPOOF_APPLIED__ = 424242; } catch(e){} }\n";

const realm = makeRealm(MODE);
vm.runInContext("(function(){" + LEAF + BOOTSTRAP + "})();", realm.ctx);

// The page constructs a worker from its OWN url -- the ordinary case that
// makes worker_wrap re-blob a payload carrying the leaf.
//
// ⚠️ THE PAGE PASSES A *TrustedScriptURL*, NOT A STRING, AND THAT IS NOT A
// CONVENIENCE -- IT IS WHAT A REAL ENFORCING SITE DOES. Measured in real
// Chromium under `require-trusted-types-for 'script'`: a page passing a bare
// string is BLOCKED BY ITS OWN CSP with no wrapper involved at all ("page
// passes STRING = BLOCKED"), while a page passing its own TrustedScriptURL is
// CREATED. So a site like Google necessarily mints its own trusted url before
// calling the constructor.
//
// This matters for arm 2 specifically: when our policy is refused, the
// wrapper's fallback re-passes THE PAGE'S OWN ARGUMENT untouched, and because
// that argument is already trusted the page's worker runs normally. Modelling
// the page as passing a string would have tested a page that breaks itself and
// then blamed the wrapper for it.
let threw = null;
try {
  vm.runInContext("new Worker(PAGE_URL)", realm.ctx);
} catch (e) {
  threw = String(e && e.name);
}

const r = realm.record;
process.stdout.write(JSON.stringify({
  mode: MODE,
  workerCreated: r.constructed.length > 0,
  pageBroken: threw !== null,
  // Did the payload the sink RECEIVED actually carry our leaf? This is the
  // spoof-delivery question; "a worker was created" is not the same claim.
  spoofDelivered: r.constructed.some((b) => b.indexOf("__SPOOF_APPLIED__") !== -1),
  refusedCount: r.refused.length,
  violations: r.violations,
  policyNames: r.policyNames,
}));
"""


def _run(mode):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    d = tempfile.mkdtemp(prefix="ps357-")
    boot = os.path.join(d, "boot.js")
    probe = os.path.join(d, "probe.js")
    with open(boot, "w", encoding="utf-8") as fh:
        fh.write(realm_bootstrap_js("applyAlpha"))
    with open(probe, "w", encoding="utf-8") as fh:
        fh.write(_PROBE)
    out = subprocess.run(
        [node, probe, boot, mode],
        capture_output=True, text=True, timeout=120, encoding="utf-8",
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def allowed():
    return _run("allowed")


@pytest.fixture(scope="module")
def disallowed():
    return _run("disallowed")


# ---------------------------------------------------------------------------
# ARM 1 — enforcement ON, policy allowed. The spoof must SURVIVE.
# ---------------------------------------------------------------------------


def test_arm1_the_worker_is_created_under_enforcement(allowed):
    assert allowed["workerCreated"] is True, (
        "the sink accepted nothing under Trusted Types enforcement — the "
        "wrapper never minted a TrustedScriptURL"
    )
    assert allowed["pageBroken"] is False, (
        "the page's own worker construction threw; a masking fix must never "
        "break the page"
    )


def test_arm1_the_spoof_actually_REACHES_the_worker(allowed):
    """THE ASSERTION THIS FILE EXISTS FOR.

    Not "a worker was constructed" and emphatically not "the source mentions
    trustedTypes" — the payload the sink RECEIVED must carry our leaf. Before
    the fix the wrapper fell back to the page's own url, so a worker was still
    created and the page still worked while carrying NO spoof at all. Every
    weaker assertion passes against that defect.
    """
    assert allowed["spoofDelivered"] is True, (
        "a worker was created but the payload it received does not carry the "
        "leaf — this is exactly the silent loss PS-357 was filed for"
    )


def test_arm1_nothing_was_refused_by_the_sink(allowed):
    assert allowed["refusedCount"] == 0, (
        "the wrapper still handed the sink a bare string; that is the console "
        "flood the operator reported"
    )
    assert allowed["violations"] == [], (
        f"enforcement was satisfied but violations were still emitted: "
        f"{allowed['violations']}"
    )


# ---------------------------------------------------------------------------
# ARM 2 — enforcement ON, policy DISALLOWED. The PAGE must survive.
# ---------------------------------------------------------------------------


def test_arm2_the_page_still_works_when_our_policy_is_refused(disallowed):
    """⛔ THE NON-NEGOTIABLE HALF.

    When the site's CSP will not let us create a policy, the spoof is lost on
    that page — that is accepted and unavoidable. What is NOT acceptable is
    breaking the page's own workers to chase it: that trades a masking gap for
    a functional break on major sites, which is strictly worse.
    """
    assert disallowed["pageBroken"] is False, (
        "our policy was refused and the page's worker construction THREW — "
        "the fallback to the original constructor is not working, and this "
        "breaks real sites"
    )


def test_arm2_the_spoof_is_lost_rather_than_falsely_assumed(disallowed):
    """The honest half of arm 2: state the loss, do not paper over it.

    A test that asserted the spoof still arrived here would be asserting
    something impossible, and the only way to make it pass would be to weaken
    the page's CSP — which the ticket forbids outright.
    """
    assert disallowed["spoofDelivered"] is False, (
        "the spoof appears to have been delivered despite the policy being "
        "refused — either the simulation is not enforcing, or something is "
        "bypassing the page's CSP, which is never acceptable"
    )


# ---------------------------------------------------------------------------
# The policy NAME — a page-readable fingerprint surface (PS-224 territory)
# ---------------------------------------------------------------------------


def test_the_policy_name_is_never_our_product_name(allowed, disallowed):
    """⛔ A persona-specific policy name would be a UNIQUE MARKER identifying
    every one of our users on every Trusted-Types site.

    `core/strings.py` forbids the product name reaching anything a page can
    read (PS-224), and this is such a place: a refused createPolicy emits a
    `securitypolicyviolation` whose `sample` IS THE NAME WE TRIED — measured in
    real Chromium — and any page listener can read it.

    Asserted against the name the code ACTUALLY registers, in both arms, rather
    than against a constant this test also defines.
    """
    from src.core.strings import CHROMIUM_ENGINE_NAME

    banned = [CHROMIUM_ENGINE_NAME.lower(), "persona", "pna", "invisible"]
    for arm in (allowed, disallowed):
        for name in arm["policyNames"]:
            low = name.lower()
            for b in banned:
                assert b not in low, (
                    f"the Trusted Types policy name {name!r} carries {b!r}. "
                    "That string is broadcast to the page in a "
                    "securitypolicyviolation sample when the CSP refuses it, "
                    "and would identify every one of our users."
                )


def test_only_ONE_policy_name_is_ever_attempted(disallowed):
    """Trying a LIST of names would be a far stronger marker than any one name.

    Each refused attempt emits a violation carrying that name, so a fallback
    chain would broadcast a distinctive SEQUENCE — a signature no single
    innocuous string could match. The disallowed arm is where this is
    observable, because that is where attempts fail and keep going.
    """
    assert len(disallowed["policyNames"]) == 1, (
        "more than one policy name was attempted: "
        f"{disallowed['policyNames']} — this broadcasts a recognisable "
        "sequence of violation samples to the page"
    )


def test_the_refusal_is_cached_so_it_cannot_flood(disallowed):
    """One violation per PAGE, not one per worker.

    The operator's original report was a 20x console flood. A fix that retried
    the policy on every construction would reproduce exactly that, and would
    also hand a page listener a repeating pattern to count.
    """
    tt = [v for v in disallowed["violations"] if v["directive"] == "trusted-types"]
    assert len(tt) <= 1, (
        f"the policy mint was attempted more than once: {tt} — each failure "
        "emits a violation the page can read"
    )
