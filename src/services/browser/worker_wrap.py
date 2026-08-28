"""Shared JS that carries every MAIN-world spoof patch into every reachable realm.

A content script runs in the page realm (and, with all_frames, in each frame the
browser itself creates), but NOT into realms the page builds at runtime: a Web
Worker, a fresh about:blank/srcdoc iframe, a worker spawned from inside such an
iframe, or a nested iframe. A detector (CreepJS, Pixelscan) reads a fingerprint
from one of those pristine realms and sees the real, unspoofed values — a
page/child mismatch is a hard tell (creepjs read the real GPU from an
OffscreenCanvas in a worker created inside an about:blank iframe).

Every spoof extension calls ``realm_bootstrap_js("applyPatch")``. Each installs
a self-contained bootstrap that (a) runs its own leaf ``applyPatch`` in this
realm, (b) CHAINS a wrapper onto this realm's Worker/SharedWorker, and (c)
CHAINS its iframe accessors, so its leaf re-runs in every worker and child
frame, recursively.

NOTHING IS STORED ON THE GLOBAL OBJECT. Each module's leaf, its source text and
its per-realm dedup set live in the bootstrap's own closure. A page cannot name
them, cannot reach them by enumerating the global object, and — the point of
PS-48 — cannot recover the profile seed that the leaf source carries.

That closure-only rule is what forced the shared registry out. The previous
design kept the leaves in ``G.__pnaBoots`` and their source text in
``G.__pnaBootSrc`` because N extensions each had to reach child realms, and a
per-module *guard* on HTMLIFrameElement would let only the FIRST module win the
single iframe getter. The registry answered that, at the cost of publishing
every leaf's source — and with it the seed compiled inside it — under a
readable name. CHAINING answers the same question without any shared state: a
module wraps whatever wrapper is already installed rather than guarding on a
global, so all N compose. (Precedent in tree: the Firefox cloak chains
``Function.prototype.toString`` and holds its registry in a closure WeakMap —
``tests/test_ff_language_override.py`` pins the absence of a marker.)

Realm-crossing composes the same way, and this is the subtle part. A function
cannot be cloned into a Worker, so the leaf has to cross as TEXT. Each chain
link builds its own one-module fragment (its leaf's source plus this
bootstrap's own source, via ``__pnaInstall.toString()``) and prepends it to the
worker body, then hands the re-blobbed body to the link BELOW it, which
prepends its own fragment, and so on. The payload that finally reaches the
worker therefore carries every module's leaf even though no module ever saw
another's — and each fragment carries only its OWN source, once, so the payload
grows linearly with module count and worker depth rather than exponentially.

Lessons the old registry paid for, which still bind here:

* ``__pnaInstall`` is a NAMED function expression and re-binds itself by that
  name inside its own serialized body. A bare anonymous ``(fn)(self)`` left the
  installer unresolvable in the worker, so a NESTED worker's wrapper threw and
  ran completely unspoofed (a creepjs GPU tell).
* The fragment is built AT new-Worker time, not when the wrapper is installed.
  Modules register across separate content scripts; a payload snapshotted at
  install time would carry only the leaves present when the first module ran.
  (Under chaining this is structural — each link builds its own fragment — but
  the ordering requirement is unchanged.)
* The helpers live INSIDE ``__pnaInstall`` so that its ``toString()`` carries
  them. A helper defined beside it would be undefined in the worker realm.
* Every realm the page can reach must be covered, at every depth. A depth-2
  worker that silently receives nothing does not throw — it just reports the
  REAL GPU/hardwareConcurrency/audio/fonts while the page reports spoofed ones.
  ``tests/test_worker_wrap.py`` executes the generated bootstrap through three
  worker generations in isolated ``node:vm`` realms for exactly this reason.

⚠️ ONE REALM IS NOT COVERED, AND IT LEAKS: THE SERVICE WORKER (PS-189)
-----------------------------------------------------------------------
Read the bullet above as the INTENT it is, not as a completed inventory. The
chaining below wraps ``Worker`` and ``SharedWorker`` — both CONSTRUCTORS the
page calls — and a ``ServiceWorkerGlobalScope`` is reached by NEITHER, because
a service worker is never constructed by the page at all: it is REGISTERED with
the browser (``navigator.serviceWorker.register``) and started by the browser,
often for a later navigation, so there is no construction for a constructor
wrapper to intercept and no realm handle to chain onto. An MV3 content script
does not run there either.

MEASURED, not deduced — ``scripts/ps189_realm_gpu.py``, layer ON, one launch,
one instant, both seeds. Twelve realms read; ELEVEN reported the profile's
authored card and the service worker reported something else:

    linux/24601   11 realms: ANGLE (Intel, Mesa Intel(R) UHD Graphics 630 ...)
                  service_worker: ANGLE (Google, ... SwiftShader ...)  <- THE HOST
    macos/24601   11 realms: ANGLE (Apple, ... Apple M1 ...)
                  service_worker: ANGLE (Apple, ... Apple M2 ...)      <- the ENGINE

That is Invariant #0 on linux (the GPU-less container's real software
rasteriser reaching a third-party page) and the PS-155/PS-161 two-author
contradiction on macos — ONE hole with two faces, depending only on whether the
engine happens to author that arm. Confirmed live: creepjs prints
``ServiceWorkerGlobalScope`` immediately above the ``gpu:`` row it leaked, and
it is the only worker-scope label on the page.

WINDOWS IS CLEAN HERE FOR A REASON THAT DOES NOT GENERALISE. On windows
``gpu_ext.ENGINE_AUTHORED_IDENTITY_ARMS`` stands our layer down entirely, so the
ENGINE authors every realm including this one and there is no second author to
disagree with. So a green windows reading is NOT evidence that this module's
realm coverage is complete, and windows must never be used as the control for
that question.

WHY THIS IS NOT FIXED HERE. Both techniques this module already relies on were
tried against the service worker and REFUSED by the browser (measured, same
script): registering a SW from a ``blob:`` URL — the re-blob trick the whole
worker path is built on — fails with *"The URL protocol of the script
('blob:...') is not supported"*, and a cross-origin script URL fails with a
``SecurityError``. ``ServiceWorkerContainer.prototype.register`` IS patchable
(writable and configurable), so a HOOK exists, but no delivery technique does:
there is nowhere to put our leaf. Suppressing registration outright would close
the leak and break every site that needs a service worker — a product decision,
not this module's to take. See PS-189 for the full record.

Worker scheme (proven by locale_ext): blob:/data: workers are re-blobbed under
the same scheme (the site's CSP already allows that scheme, so it stays allowed)
with the fragment prepended; http(s) workers get an importScripts shim. Module
workers get a module blob that dynamic-imports the original.

The Blob/URL/XHR constructors used to assemble a payload are captured from the
realm at install time and called through those captured references. They run at
document_start, before any page script, so what they capture is native. A page
that later overrides ``URL.createObjectURL`` or ``Blob`` would otherwise be
handed the assembled payload — the seed-bearing leaf source — as an argument,
which is the same disclosure PS-48 closes on the global object.
"""

import json
import textwrap
from typing import NamedTuple


class WorkerCloak(NamedTuple):
    """Every ENGINE-SPECIFIC seam in the bootstrap, in one object.

    The bootstrap installs wrappers of its own — ``Worker``, ``SharedWorker`` and
    the two ``HTMLIFrameElement`` accessors — quite apart from whatever the leaf
    patches. Those wrappers need the same cloak the leaf's do, and which cloak is
    correct depends on the engine, so it arrives here as a seam instead of being
    hard-coded. Four cloak fields, because the marker is applied in four
    syntactic positions and only one of them is a statement:

    * ``setup``   — statements spliced inside ``__pnaInstall``, once per realm.
    * ``apply``   — statements that cloak the ``Worker``/``SharedWorker`` wrapper.
    * ``frame_open`` / ``frame_close`` — wrapped AROUND the iframe accessor
      function expression, which is an argument position and cannot take one.
    * ``hook_mark`` — statements spliced inside ``__markHook``, which cloaks the
      DOM-insertion wrappers the INDEXED-frame reach installs (PS-215). Those
      are ``appendChild``/``insertBefore``/``replaceChild``, the five variadic
      ``Element`` inserters and the ``innerHTML`` setter — hotter and more
      commonly probed than anything else the bootstrap wraps, so this seam is
      mandatory rather than optional. It receives ``f`` (the wrapper), ``n``
      (its ``.name``) and ``__sn`` (the name its SOURCE TEXT should report,
      already defaulted to ``n``); ``.name`` is set by the shared code, so an
      engine's text here only has to add its own native-source marker.

    Two further fields carry the engine's WORKER-BODY DELIVERY, which is not a
    cloak at all:

    * ``blob_setup``   — statements spliced inside ``__pnaInstall`` that prepare
      whatever the delivery needs (Firefox: retain the ``Blob`` behind each
      object URL).
    * ``blob_resolve`` — statements spliced at the TOP of the ``blob:``/``data:``
      branch of the Worker wrapper, which may ``return`` a constructed worker to
      pre-empt the shared sync-XHR path below.

    THEY LIVE IN THE SAME OBJECT DELIBERATELY, and that is a correctness
    property rather than tidiness. ``blob_setup`` defines ``__rb`` and cloaks its
    ``createObjectURL`` chain through ``__bcloak``, which only exists if ``setup``
    put it there — so a Firefox delivery paired with a Chromium cloak would
    install an UNCLOAKED wrapper, i.e. the exact class of tell PS-78 round 3 was
    rejected for. One object per engine makes that pairing unexpressible.

    Chromium's form was the ORIGINAL text, kept byte-identical because it is the
    baseline every prior readback was taken against (the PS-78 boundary,
    "Chromium is unchanged"). Its frame pair and both delivery fields are still
    EMPTY STRINGS spliced at points chosen so that the empty case reproduces the
    old template exactly — no stray blank line, no moved indentation.

    ⚠️ ``setup`` IS NO LONGER EMPTY ON CHROMIUM, and that is a deliberate,
    scoped break of the sentence above rather than an oversight. PS-215 added
    the INDEXED-frame reach, whose DOM-insertion wrappers must be cloaked on
    BOTH engines, so Chromium's ``setup`` now carries
    :data:`_CHROMIUM_HOOK_CLOAK_SETUP`.

    ⛔ AN EARLIER REVISION JUSTIFIED THAT BREAK WITH "Nothing PREVIOUSLY
    generated changed." THAT SENTENCE WAS FALSE, and it is recorded here as a
    retraction rather than deleted, because it was the load-bearing argument for
    crossing the PS-78 boundary and a reader who carries it needs to find where
    it ended. ``setup`` is spliced INSIDE ``__pnaInstall`` — which every module
    riding this bootstrap carries — so it does not ship to the frame-related
    modules, it ships to ALL THIRTEEN. Measured, by generating every rider's
    bundle at the merge-base and at HEAD and diffing the trees:

    * Every rider's bundle grew by EXACTLY the shared bootstrap's own delta
      (+13555 bytes at the time of writing). ``device.js`` grew by twice that,
      because it splices two bootstraps (``applyScreenPatch`` and
      ``applyHwPatch``); ``webgl.js`` by that plus its own leaf change.
    * So ``device.js`` — a module with nothing to do with frames — DID change,
      and that is what broke
      ``tests/test_device_ext.py::test_script_spoofs_screen_and_mediadevices``,
      which pins ``"[native code]" not in js``.

    WHAT THE BOUNDARY ACTUALLY REQUIRES, then, is not "no text is ever added"
    (unachievable through a shared splice point) but the narrower, testable
    claim below — which IS satisfied, and is satisfied by construction rather
    than by luck:

    * NO GENERATED BUNDLE SYNTHESISES A NATIVE STRING THAT DID NOT ALREADY.
      The cloak DERIVES the engine's native shape by reading it off a real
      native function rather than embedding a literal, so the count of
      ``[native code]`` occurrences per generated bundle is unchanged from the
      merge-base in all thirteen — verified, not assumed. This is also why the
      ``device_ext`` invariant was RESTORED rather than its assertion rewritten:
      a module that never synthesises a native string should not carry the text
      of one.
    * The ``Worker``/``SharedWorker`` wrapper still takes ``__pnaName``
      (``apply`` is untouched, and
      ``tests/test_ff_webgl_seed.py::test_chromiums_bootstrap_keeps_its_marker``
      pins it), and the two iframe accessors are still spliced bare.
    * The added text only installs a closure WeakMap and CHAINS
      ``Function.prototype.toString``, which composes with native_ext's
      ``__pnaName`` reader rather than replacing or racing it.
    * It is additive per realm and reaches no value channel, so no digest moves
      — ``tests/test_realm_value_channels.py`` is the neighbour that pins that.

    THE THIRTEEN-DEEP CHAIN IS THE PRICE OF THAT SPLICE POINT, and it was
    measured rather than reasoned about (``tests/test_ps215_tostring_chain.py``).
    Thirteen riders means thirteen installations per realm, each closing over
    the previous as ``__hpts``. After thirteen chainings, in one realm:
    ``Function.prototype.toString`` still stringifies BYTE-IDENTICALLY to the
    pristine intrinsic; an untouched native (``Array.prototype.map``) is
    likewise unchanged; the patch's own properties are exactly
    ``["length", "name"]``; and a marked wrapper renders the native form exactly
    ONCE whether it was marked by the innermost or the outermost installation.
    Delegating through all thirteen is not a cost worth optimising ON THIS
    CHAIN: a marked hit answered at the innermost link measured FASTER than an
    unmarked passthrough (372ns vs 426ns), because the passthrough reaches the
    real intrinsic anyway.

    ⚠️ THAT RESULT IS ABOUT ``Function.prototype.toString`` AND DOES NOT
    TRANSFER TO THE DOM-INSERTION WRAPPERS, which are thirteen deep for the same
    reason and have a completely different cost shape. Each ``toString`` link is
    an O(1) delegation; each INSERTION link runs ``collectFrames``, i.e. a
    ``querySelectorAll`` walk over the whole inserted subtree. Measured on the
    generated bootstrap, both engine arms identical, ONE native insertion of a
    200-node subtree::

        door                     scans @ N=1   scans @ N=13   nodes walked @ 13
        appendChild(element)         1              13              2587
        appendChild(fragment)        1              13              2600
        innerHTML = "..."            1              13                26
        appendChild(textNode)        0               0                 0

    Left unguarded that is a 13x O(subtree) amplification on ``appendChild``,
    ``insertBefore`` and the ``innerHTML`` setter — the hottest, most heavily
    probed functions in the DOM — and it is a TIMING tell on precisely the
    functions whose static tells the cloak above works so hard to close: a
    detector needs a large subtree and a clock, with no own property and no
    ``toString`` comparison. AC2 forbids trading the Level 2 failure for a fresh
    tell, so the reach carries a guard that keeps the scan count at ONE at every
    depth (``tests/test_ps215_insertion_depth.py`` pins it, with falsification
    arms that reproduce the table above on demand).

    THE SCAN IS DEDUPLICATED; THE WRAPPERS ARE NOT — and the difference is the
    whole design. "Wrap the prototype once per realm" is the obvious-looking fix
    and it collapses the scans correctly, but each rider's wrapper closes over
    its OWN ``LEAF``: measured on thirteen distinct leaves through creepjs's own
    gesture, a wrap-once build reaches the phantom realm with 1 of 13 leaves
    installed — PS-215's own defect, reintroduced in the name of closing a tell.
    So only the OUTERMOST wrapper scans (an O(1) identity check at call time),
    and delivery goes through the ACCESSOR CHAIN, which is already every rider's
    link. ``test_wrap_once_would_break_delivery`` keeps the rejected design
    executable so it cannot quietly come back.

    WHY THIRTEEN COPIES RATHER THAN ONE SHARED INSTALLATION GUARDED PER REALM:
    a single installation needs the N riders to COORDINATE, and they are
    separate content scripts in one MAIN world with no shared closure and no
    guaranteed load order — so the guard has to live under a name all thirteen
    can spell, i.e. on the global object. That is precisely the enumerable
    ``G.__pnaToStringPatched`` flag PS-48 removed: ``Object.keys(window)`` finds
    it in one line, in EVERY realm, at every worker/iframe depth, under
    persona's own prefix — positive identification of a persona-family tool.
    Chaining costs thirteen delegations and publishes NO shared name at all
    (measured: zero matching enumerable globals). The module docstring's
    "CHAINING answers the same question without any shared state" is the same
    argument; this is that argument holding at N=13 rather than N=2.

    WHY THESE WRAPPERS DO NOT TAKE ``__pnaName`` LIKE EVERY OTHER CHROMIUM ONE:
    see :data:`_CHROMIUM_HOOK_CLOAK_SETUP`. Briefly — ``appendChild``'s
    own-property list is known by heart, so a marker there is a cheaper tell
    than the ``toString`` comparison it exists to satisfy.
    """

    setup: str
    apply: str
    frame_open: str
    frame_close: str
    hook_mark: str = ""
    blob_setup: str = ""
    blob_resolve: str = ""


# --- the DOM-insertion wrappers' cloak, on CHROMIUM -------------------------
#
# THE ONE PLACE THE CHROMIUM BOOTSTRAP DOES NOT USE `__pnaName`, and the reason
# is a constraint rather than a preference.
#
# Everywhere else on this engine a wrapper carries a non-enumerable `__pnaName`
# own property and native_ext.py's single `Function.prototype.toString` patch
# reads it. That is a fine trade for `Worker`: a page that enumerates
# `Worker`'s own properties is already doing something unusual.
#
# It is NOT a fine trade for the functions the PS-215 indexed-frame reach wraps.
# `appendChild`, `insertBefore` and the `innerHTML` setter are among the most
# heavily exercised functions in the DOM, their own-property lists (`length`,
# `name`) are known by heart, and `Object.getOwnPropertyNames(el.appendChild)`
# returning a third name is a ONE-LINE tell — cheaper to run than the
# `toString` comparison the marker exists to satisfy. PS-215's AC2 forbids
# trading the Level 2 failure it fixes for a fresh detectable tell, so these
# wrappers add no own property on EITHER engine.
#
# Hence a closure WeakMap here too, mirroring the Firefox seam below. It is
# CHAINED, not flag-guarded, so it composes with native_ext's `__pnaName`
# reader, with the leaf's cloak, and with the other twelve modules' copies
# instead of racing them for the single slot: whichever patch is outermost
# answers a hit it knows and otherwise delegates down.
#
# V8's ONE-LINE native shape, not SpiderMonkey's three-line one — emitting the
# wrong engine's form is itself a masking tell, one
# `Array.prototype.map.toString()` comparison away.
#
# Spliced INSIDE `__pnaInstall` (like every other `setup`) so it ships with
# `__pnaInstall.toString()` and every realm gets its own; a map in an enclosing
# scope would be undefined in a worker.
_CHROMIUM_HOOK_CLOAK_SETUP = r"""

      // --- cloak for the DOM-insertion wrappers (Chromium) -----------------
      // See the note beside _CHROMIUM_HOOK_CLOAK_SETUP in worker_wrap.py for
      // why these wrappers do NOT take native_ext's `__pnaName` marker.
      var __hnm = (typeof WeakMap === "function") ? new WeakMap() : null;
      // G's Function, never the lexical one: the installer reaches a child
      // frame as a PARENT-REALM function object, so a bare `Function.prototype`
      // would re-patch the parent's and leave the child's pristine while the
      // wrappers ARE installed into the child.
      var __hF = G.Function;
      var __hpts = (__hF && __hF.prototype && __hF.prototype.toString)
                   || Function.prototype.toString;
      // THE NATIVE SHAPE IS DERIVED FROM THE ENGINE, NEVER HARD-CODED. Two
      // reasons, and the second is why this is not merely tidier:
      //
      //   1. A literal here is spliced into `__pnaInstall`, which ALL 13
      //      bootstrap riders carry -- so the marker string would ship inside
      //      `device.js`, `voice.js`, `locale.js` and every other bundle that
      //      has nothing to do with frames. `test_device_ext.py` pins that
      //      absence ("we keep real toString via nativeWrap") and is right to:
      //      a module that never synthesises a native string should not carry
      //      the text of one.
      //   2. V8's one-line form and SpiderMonkey's three-line one differ, and
      //      emitting the WRONG engine's form is itself a masking tell -- one
      //      `Array.prototype.map.toString()` comparison away. Reading the
      //      shape off a real native function cannot get that wrong on ANY
      //      engine, including one whose form nobody here anticipated.
      //
      // `hasOwnProperty` is the probe: native everywhere, and not a function
      // any leaf in this project wraps (so it is never one of our own).
      //
      // The open-paren is built with `fromCharCode(40)` rather than written as
      // a literal: `test_worker_wrap.test_bootstrap_is_syntactically_balanced`
      // counts raw parens across the whole template, so an unpaired one inside
      // a string literal reads to it as an unbalanced bootstrap.
      var __hshape = null;
      try {
        var __hlp = String.fromCharCode(40);
        var __hpr = G.Object && G.Object.prototype && G.Object.prototype.hasOwnProperty;
        var __hps = __hpr && __hpts.call(__hpr);
        var __hpi = __hps ? __hps.indexOf(__hlp) : -1;
        if (__hpi > 0) { __hshape = __hps.slice(__hpi); }
      } catch (e) {}
      // METHOD SHORTHAND, not a function expression. Once installed this object
      // IS `Function.prototype.toString`, so a detector reading
      // `Object.getOwnPropertyNames(Function.prototype.toString)` must find
      // only `length`/`name`; a function expression owns `prototype` as well,
      // and `delete` cannot repair it (a function's `prototype` is
      // non-configurable). Same tell the DOM wrappers below avoid the same way.
      var __hts = ({
        m() {
          'use strict';
          try {
            var n = __hnm && __hnm.get(this);
            // No derived shape means no honest answer, so DELEGATE rather than
            // guess: a wrong-shaped native string is a SHARPER tell than the
            // raw source it would be hiding.
            if (typeof n === "string" && __hshape) {
              return "function " + n + __hshape;
            }
          } catch (e) {}
          return __hpts.apply(this, arguments);
        },
      }).m;
      try {
        // The patch must itself read as native: a detector stringifies
        // Function.prototype.toString to catch exactly this trick.
        if (__hnm) { __hnm.set(__hts, "toString"); }
        Object.defineProperty(__hts, "name", { value: "toString", configurable: true });
        // Arity is an own property too, and `Function.prototype.toString`
        // reports 0. Copied from the original rather than hard-coded.
        Object.defineProperty(__hts, "length", { value: __hpts.length, configurable: true });
        if (__hF && __hF.prototype) { __hF.prototype.toString = __hts; }
      } catch (e) {}"""


# Chromium: mark the wrapper for the single `Function.prototype.toString` patch
# native_ext.py installs from the extension side, which reads `__pnaName`.
CHROMIUM_WORKER_CLOAK = WorkerCloak(
    setup=_CHROMIUM_HOOK_CLOAK_SETUP,
    apply=(
        '        try { Object.defineProperty(W, "__pnaName", { value: Orig.name }); } catch (e) {}\n'
        '        try { Object.defineProperty(W, "name", { value: Orig.name }); } catch (e) {}'
    ),
    frame_open="",
    frame_close="",
    hook_mark=(
        "\n            try { if (__hnm) { __hnm.set(f, __sn); } } catch (e) {}"
    ),
)


def realm_bootstrap_js(
    apply_fn_name: str, cloak: WorkerCloak = CHROMIUM_WORKER_CLOAK
) -> str:
    """Return JS that runs ``apply_fn_name`` (a leaf applyPatch(G)) in this realm
    and chains it into every realm this one can reach.

    ``cloak`` selects how the bootstrap's OWN wrappers are made to stringify as
    native; it defaults to the Chromium form, which is the original text. Pass
    ``firefox_worker_cloak()`` on that engine — see its docstring for why the
    default is actively wrong there rather than merely unnecessary.

    The caller must have defined ``apply_fn_name`` already. Nothing is written to
    the global object: the leaf, its source and the dedup set stay in closure.

    ONE ``blob:``/``data:`` BRANCH, ON BOTH ENGINES — with a Firefox-only
    delivery seam spliced at the top of it (``blob_resolve``), never a second
    branch. An earlier revision of PS-78 added a ``blob_via_import_scripts`` flag
    that gave Firefox a separate ``blob:`` branch using an ``importScripts``
    shim, on the belief that this engine refuses a SYNCHRONOUS XHR against a
    ``blob:`` URL (``NetworkError``). THE FLAG IS GONE, and its premise is only
    HALF right — which is worse than plainly wrong, because it is right on every
    origin a casual measurement reaches.

    IT IS NOT AN ENGINE FACT, IT IS A CSP FACT. The sync XHR is subject to the
    page's ``connect-src``, so the answer depends on the ORIGIN, not on the
    browser. Measured on firefox-20 / 151.0 through this project's own launch
    path::

        origin                    new XMLHttpRequest().open("GET", blobUrl, false)
        https://example.com/      -> status 200, body read back intact
        http://127.0.0.1          -> status 200
        data: / about:blank       -> status 200
        https://duckduckgo.com/   -> THROWS NetworkError

    DuckDuckGo sends ``default-src 'none'`` with no ``blob:`` in its
    ``connect-src``, so the fetch is refused by policy. An earlier round recorded
    the "status 200" half here as a settled general fact; it was measured only on
    origins that ship no CSP, and the conclusion did not survive an origin that
    does. THAT ORIGIN IS THE DEFAULT START PAGE — ``_ensure_firefox_policies()``
    pins DuckDuckGo — so the refusing case is where every Firefox profile already
    is the moment it opens, not an exotic corner.

    The in-tree corroboration is real but proves less than it appears to: the
    locale spoof at ``invisible_launch.py:504`` has shipped this identical
    sync-XHR ``blob:`` path in production all along, which establishes that the
    path WORKS, not that it works EVERYWHERE. It is subject to the same
    ``connect-src`` on the same origins.

    Hence ``blob_resolve``: on Firefox the retained ``Blob`` is composed directly
    and the XHR below is never reached for a url this realm minted. See
    :data:`_FIREFOX_BLOB_RETAIN_SETUP` for the measurement and for why neither
    the XHR nor the deleted shim clears both constraints alone.

    WHY THE SHIM WAS NOT MERELY REDUNDANT BUT HARMFUL. ``importScripts(blobUrl)``
    DEFERS the fetch of the original body out of the ``new Worker(...)`` call and
    into the worker's own startup, so the page's blob URL has to still be alive
    at that later moment. Revoking right after construction is the pattern MDN
    documents and bundlers emit::

        const w = new Worker(url);
        URL.revokeObjectURL(url);   // ← body has not been read yet

    Measured on the real launch path with the shim installed: the worker emits
    ZERO events — ``importScripts`` throws ``SecurityError`` against the revoked
    URL, the shim's own ``catch(e){}`` swallows it inside the worker realm, and
    the original body never runs. That is a FUNCTIONAL BREAK, strictly worse than
    the leak it was meant to close, and the outer ``try``/``catch`` around the
    construct cannot save it because the throw happens in another realm.

    The sync-XHR branch below has the property the shim lacked: it reads the body
    SYNCHRONOUSLY, inside the constructor, while the URL is still valid. So it
    survives both orderings. Verified on the real launch path, every realm
    reading its own marker from the inside — depth-1, depth-2 (a worker spawning
    a worker), a worker created inside a fresh ``about:blank`` iframe, and a
    ``data:`` worker — all spoofed, with and without a revoke.

    Keeping ONE branch is also what keeps Chromium byte-identical: there is no
    per-engine text to drift.
    """
    return (
        r"""
  var SELF = (typeof self !== "undefined") ? self : this;

  // The installer is a NAMED function expression: it must be able to re-bind
  // itself by name inside its own serialized body, because that body is what
  // crosses into a worker and has to keep covering the worker's OWN children.
  var __pnaInstall = function __pnaInstall(G, LEAF) {
    try {
      if (!G || typeof LEAF !== "function") return;

      // Realms this module has already covered. A closure WeakSet, NOT a marker
      // property on the realm: the iframe accessor below can fire many times for
      // the same child window and must not re-run the leaf each time. Declared
      // inside the installer so it ships with __pnaInstall.toString() and each
      // realm gets its own; a set in an enclosing scope would be undefined in a
      // worker, which is the failure mode this file exists to prevent.
      var SEEN = (typeof WeakSet === "function") ? new WeakSet() : null;
      var fresh = function (w) {
        try {
          if (!SEEN) return true;
          if (SEEN.has(w)) return false;
          SEEN.add(w);
          return true;
        } catch (e) { return true; }
      };

      // Capture the payload-assembly built-ins NOW, at document_start, before
      // any page script has run. Called through these references so a later
      // page override cannot be handed the assembled payload (which carries the
      // leaf source, and with it the seed).
      var _URL = G.URL, _cou = _URL && _URL.createObjectURL;
      var _Blob = G.Blob;
      var _XHR = G.XMLHttpRequest;
      var _Ref = G.Reflect || (typeof Reflect !== "undefined" ? Reflect : null);

      var mkurl = function (body) {
        return _cou.call(_URL, new _Blob([body], { type: "application/javascript" }));
      };

      // This module's one-module payload fragment, rebuilt per construction.
      // It carries the leaf's source and the installer's own source — and
      // nothing else. The next link in the chain prepends its own fragment to
      // whatever this produced, which is how N modules compose without ever
      // sharing state.
      var fragment = function () {
        return "(function(){try{var L=(" + LEAF.toString() + ");" +
          "var I=(" + __pnaInstall.toString() + ");" +
          "I((typeof self!=='undefined'?self:this),L);}catch(e){}})();";
      };

      // Run this module's leaf in this realm.
      try { LEAF(G); } catch (e) {}%(cloak_setup)s%(blob_setup)s

      // --- workers ---------------------------------------------------------
      // Chain onto whatever Worker is already installed. Delegating to Orig is
      // what makes composition work: Orig is the PREVIOUS module's wrapper, so
      // it re-blobs what this one produced and prepends its own fragment.
      var wrapWorker = function (Orig) {
        if (typeof Orig !== "function") return Orig;
        var W = function (url, options) {
          try {
            var BOOT = fragment();
            var s = String(url);
            // Resolve a RELATIVE worker URL (e.g. creepjs's './creep.js') to an
            // absolute one so it takes the http(s) importScripts path below. A
            // relative URL matched none of the scheme tests and fell through to
            // the native, UNSPOOFED construct — the real path creepjs uses to
            // read the engine-default GPU from a worker.
            if (!/^(https?:|blob:|data:)/i.test(s)) {
              try {
                var base = (G.location && G.location.href) || undefined;
                var rs = new URL(s, base).href;
                if (/^https?:/i.test(rs)) s = rs;
              } catch (er) {}
            }
            if (options && options.type === "module") {
              try {
                var abs = s;
                try { abs = new URL(s, (G.location && G.location.href) || undefined).href; } catch (e0) {}
                var mbody = BOOT + "\nimport(" + JSON.stringify(abs) + ").catch(function(e){});";
                return _Ref.construct(Orig, [mkurl(mbody), options], W);
              } catch (em) {
                return _Ref.construct(Orig, [url, options], W);
              }
            }
            if (/^https?:/i.test(s)) {
              var body = BOOT + "\ntry{importScripts(" + JSON.stringify(s) + ");}catch(e){}";
              return _Ref.construct(Orig, [mkurl(body), options], W);
            }
            if (/^blob:|^data:/i.test(s)) {%(blob_resolve)s
              try {
                var x = new _XHR();
                x.open("GET", s, false);
                x.send();
                if (x.status === 0 || (x.status >= 200 && x.status < 300)) {
                  return _Ref.construct(Orig, [mkurl(BOOT + "\n" + x.responseText), options], W);
                }
              } catch (e) {}
              return _Ref.construct(Orig, [url, options], W);
            }
            return _Ref.construct(Orig, [url, options], W);
          } catch (e) { return _Ref.construct(Orig, [url, options], W); }
        };
        W.prototype = Orig.prototype;
%(cloak_apply)s
        return W;
      };

      try {
        if (G.Worker) G.Worker = wrapWorker(G.Worker);
        if (G.SharedWorker) G.SharedWorker = wrapWorker(G.SharedWorker);
      } catch (e) {}

      // --- child frames ----------------------------------------------------
      // Same-realm about:blank / srcdoc children, recursively. CHAIN the
      // accessor rather than guarding on a marker: a per-module guard would let
      // only the first module win the single iframe getter, which is the
      // objection the shared registry used to answer. Re-running the whole
      // installer in the child covers that child's own workers and frames.
      try {
        var IF = G.HTMLIFrameElement;
        // The NATIVE contentWindow getter, kept as the connection trigger's
        // FALLBACK for the case where no accessor could be chained at all.
        var nativeCW = null;
        // The CHAINED contentWindow getter, captured AFTER this rider added its
        // own link below. Reading a frame's window through THIS runs every
        // rider's accessor wrapper, so one read delivers all thirteen leaves --
        // which is what lets the connection trigger below do its work exactly
        // ONCE per insertion instead of once per rider. Captured as a
        // REFERENCE, so replacing the property afterwards (as the AC3 suite's
        // seal does, to prove the consumer's own `.contentWindow` is never what
        // triggers this) does not reach it.
        var chainedCW = null;
        if (IF && IF.prototype) {
          ["contentWindow", "contentDocument"].forEach(function (prop) {
            var d0 = Object.getOwnPropertyDescriptor(IF.prototype, prop);
            if (!d0 || !d0.get) return;
            if (prop === "contentWindow") nativeCW = d0.get;
            Object.defineProperty(IF.prototype, prop, {
              configurable: true, enumerable: d0.enumerable,
              get: %(cloak_frame_open)sfunction () {
                var r = d0.get.call(this);
                try {
                  var w = prop === "contentWindow" ? r : (r && r.defaultView);
                  if (w && fresh(w)) __pnaInstall(w, LEAF);
                } catch (e) {}
                return r;
              }%(cloak_frame_close)s,
            });
            // Capture the chain AS IT NOW STANDS -- this rider's link on top of
            // every earlier rider's. One call through this reference therefore
            // runs all of them, which is what lets the connection trigger below
            // scan and deliver ONCE per insertion rather than once per rider.
            if (prop === "contentWindow") {
              try {
                var dc = Object.getOwnPropertyDescriptor(IF.prototype, prop);
                if (dc && dc.get) chainedCW = dc.get;
              } catch (e) {}
            }
          });
        }
      } catch (e) {}

      // --- child frames reached by INDEXED access (self[N]) ----------------
      // The accessors above are the only door into a child realm, and a
      // consumer that never opens it never triggers them. CreepJS takes its
      // phantom iframe by INDEX -- `self[numberOfIframes]`, a WindowProxy read
      // off the window's indexed properties -- and builds its WebGL context
      // from that realm. That read does not invoke
      // `HTMLIFrameElement.prototype.contentWindow`, so the leaf was never
      // installed there and the detector received UNPERTURBED pixels while the
      // page realm reported spoofed ones (PS-193; readings/ps193-2026-08-26).
      //
      // So this is a TRIGGER problem, not a DOOR problem: the installer below
      // is the same `__pnaInstall(w, LEAF)` the accessor calls, and the window
      // still comes from the captured NATIVE getter. What is added is an event
      // that fires when the consumer never touches the accessor -- the frame
      // becoming CONNECTED to the document.
      //
      // WHY IT MUST BE SYNCHRONOUS, AND WHY MutationObserver IS WRONG HERE.
      // CreepJS's read is synchronous with the insertion: `appendChild(frag)`
      // is followed on the NEXT STATEMENT by `self[n]`. A same-document
      // iframe's window exists as soon as it is connected, so a synchronous
      // hook on the insertion call lands before the read. A MutationObserver
      // delivers on a MICROTASK -- after that read has already taken the
      // unspoofed realm. Every asynchronous trigger is structurally too late,
      // which is worth recording because an observer is the obvious-looking
      // answer.
      //
      // The `SEEN`/`fresh()` guard above is reused rather than duplicated, so a
      // frame that is inserted, removed and re-inserted -- or one reached BOTH
      // by connection and later by the accessor -- installs exactly once.
      //
      // THESE ARE HOT, HEAVILY PROBED FUNCTIONS and the reach must not itself
      // become a readable tell, so every wrapper here goes through the engine's
      // cloak seam (`cloak_hook_mark`) exactly as the accessor pair does. PS-78
      // round 2 left a bare `__pnaName` on Firefox's `Worker` -- an own property
      // no browser has, on an engine with no extension to read it. Not again.
      try {
        var _Node = G.Node, _El = G.Element;
        if (_Node && _Node.prototype && _El && _El.prototype) {
          // `f` gets `.name` = n; its SOURCE TEXT reports `s` (a setter's name
          // carries a `set ` prefix that its source text does not).
          var __markHook = function (f, n, s, len) {
            var __sn = (s === undefined) ? n : s;
            try {
              Object.defineProperty(f, "name", { value: n, configurable: true });
            } catch (e) {}
            // ARITY IS AN OWN PROPERTY TOO, and it is as cheap to read as the
            // name. `appendChild.length` is 1 and `insertBefore.length` is 2;
            // a wrapper that takes its arguments through `arguments` reports 0,
            // so leaving it alone would swap the `toString` tell this seam
            // closes for a `length` tell it opened. Copied from the ORIGINAL
            // rather than hard-coded, so an engine whose arity differs from the
            // spec still matches itself.
            try {
              if (typeof len === "number") {
                Object.defineProperty(f, "length", { value: len, configurable: true });
              }
            } catch (e) {}%(cloak_hook_mark)s
            return f;
          };

          // Collect iframes from a node BEFORE it is inserted: a
          // DocumentFragment is EMPTIED by insertion, so a scan afterwards
          // finds nothing. CreepJS inserts exactly that -- its iframe rides in
          // on a fragment.
          var collectFrames = function (n, acc) {
            try {
              if (!n || typeof n !== "object") return acc;
              var nt = n.nodeType;
              // 1 = element, 11 = DocumentFragment. Anything else (text,
              // comment) cannot contain a frame; bail before touching the DOM.
              if (nt !== 1 && nt !== 11) return acc;
              if (nt === 1) {
                try {
                  var tn = n.tagName;
                  if (tn && String(tn).toLowerCase() === "iframe") acc.push(n);
                } catch (e) {}
              }
              try {
                var qsa = n.querySelectorAll;
                if (typeof qsa === "function") {
                  var list = qsa.call(n, "iframe");
                  for (var i = 0; i < list.length; i++) acc.push(list[i]);
                }
              } catch (e) {}
            } catch (e) {}
            return acc;
          };

          // Deliver into each now-connected frame's window. Read through the
          // CHAINED accessor (`chainedCW`) rather than the native one: that
          // chain is every rider's link, each with its OWN leaf and its OWN
          // `fresh()` guard, so ONE read here installs all thirteen leaves.
          // This is what makes the single-scan guard below correct rather than
          // merely cheap -- the scan is redundant across riders, but the
          // WRAPPERS ARE NOT: each closes over a different LEAF. Suppressing
          // twelve wrappers to save twelve scans would silently deliver ONE
          // leaf into the phantom realm instead of thirteen, which is the very
          // defect this ticket exists to fix. Measured, not assumed: a
          // wrap-once build reaches the realm with 1 of 13 leaves present.
          //
          // `chainedCW` is a captured REFERENCE, so a later replacement of the
          // property (a page's own override -- or the AC3 suite's throwing
          // seal, which exists to prove the CONSUMER's `.contentWindow` read is
          // never what triggers this) does not reach it.
          var reachFrames = function (acc) {
            try {
              for (var i = 0; i < acc.length; i++) {
                try {
                  var el = acc[i];
                  if (!el) continue;
                  // A frame that did not end up in the document has no window
                  // yet; the accessor still covers it if it is read later.
                  if (el.isConnected === false) continue;
                  if (chainedCW) {
                    // The chain installs every rider's leaf, including this
                    // one's, so there is nothing left for us to install.
                    chainedCW.call(el);
                  } else {
                    // No accessor could be chained at all. Deliver this
                    // rider's own leaf directly; with no chain there is no
                    // other rider to defer to.
                    var cw = nativeCW ? nativeCW.call(el) : el.contentWindow;
                    if (cw && fresh(cw)) __pnaInstall(cw, LEAF);
                  }
                } catch (e) {}
              }
            } catch (e) {}
          };

          // Wrap an insertion METHOD. Every argument is scanned, which covers
          // all eight uniformly: appendChild(n), insertBefore(n, ref),
          // replaceChild(new, old) and the variadic Element methods.
          var hookInsert = function (proto, prop) {
            try {
              var orig = proto[prop];
              if (typeof orig !== "function") return;
              // METHOD SHORTHAND -- `({ m() {} }).m`, NOT `({ m: function
              // () {} }).m`. The two look interchangeable and are not: a
              // function EXPRESSION owns `prototype` (plus `arguments` and
              // `caller`), a native DOM method owns only `length` and `name`,
              // and `Object.getOwnPropertyNames(el.appendChild)` returning
              // "prototype" is a ONE-LINE tell -- cheaper for a detector to run
              // than the `toString` comparison the cloak below satisfies.
              // `delete` cannot repair it afterwards, because a function's
              // `prototype` is non-configurable. A shorthand method is not a
              // constructor and so has none to begin with, while still binding
              // its own `this` and `arguments` (an arrow would not).
              var wrapped = ({
                m() {
                  // ONLY THE OUTERMOST RIDER SCANS. Thirteen riders nest
                  // thirteen wrappers here, and -- unlike the `toString` chain,
                  // where each link is an O(1) delegation -- each link of THIS
                  // chain would otherwise run `collectFrames`, i.e. a
                  // `querySelectorAll` walk over the whole inserted subtree.
                  // Measured on the generated bootstrap, both engine arms
                  // identical: a 200-node subtree was walked 199 times at N=1
                  // and 2587 times at N=13, for ONE native call. That is a 13x
                  // O(subtree) amplification on `appendChild`/`insertBefore`/
                  // the `innerHTML` setter -- the hottest, most heavily probed
                  // functions in the DOM -- and it is a TIMING signature on
                  // exactly the functions whose static tells the cloak above
                  // goes to such lengths to close. A detector needs only a
                  // large subtree and a clock; no own property and no
                  // `toString` comparison.
                  //
                  // `proto[prop] === wrapped` is that guard, and it is an O(1)
                  // identity read taken at CALL time rather than install time,
                  // because which rider ends up outermost is decided by load
                  // order that no rider can know while installing. The
                  // outermost is the one the caller actually invoked; the
                  // twelve below it are pure passthroughs.
                  //
                  // WHY THE SCAN IS DEDUPLICATED BUT THE WRAPPERS ARE NOT.
                  // Suppressing twelve WRAPPERS (wrap-once, guarded per realm)
                  // is the obvious-looking version of this fix and it is
                  // WRONG: each wrapper closes over its OWN `LEAF`, so twelve
                  // fewer wrappers is twelve fewer spoofs in the phantom realm.
                  // Measured on 13 distinct leaves through creepjs's own
                  // gesture: wrap-once reaches the realm with 1 of 13 leaves
                  // installed, which is the very defect this ticket exists to
                  // fix, reintroduced in the name of closing a tell. So the
                  // redundant thing -- the SCAN -- is what gets deduplicated,
                  // and delivery goes through the ACCESSOR CHAIN, which is
                  // already every rider's link (see `reachFrames`).
                  //
                  // THE ONE BOUNDARY, STATED RATHER THAN PAPERED OVER. This
                  // recognises "an outer RIDER will scan" and cannot recognise
                  // "an outer NON-RIDER will not". A rider knows the wrapper it
                  // built and the one it wrapped; it cannot know which riders
                  // installed after it, so the rider sitting highest among the
                  // thirteen cannot tell a fourteenth rider above it from a
                  // page script that wrapped the chain later. If a page wraps
                  // these methods AFTER document_start, `proto[prop]` matches
                  // no rider's `wrapped`, every rider reads "not outermost",
                  // and the connection trigger goes quiet for that method.
                  //
                  // A fail-open default was tried and rejected: it inverts the
                  // failure, so the NORMAL thirteen-rider case scans thirteen
                  // times again and the amplification comes straight back.
                  //
                  // What the boundary costs is bounded, and the bound is why it
                  // is acceptable: the accessor chain is untouched, so such a
                  // frame is still covered the moment anything reads its
                  // `.contentWindow`/`.contentDocument`. Only the INDEXED read
                  // is missed, and only on a page that re-wraps DOM insertion
                  // after document_start -- which is exactly the pre-PS-215
                  // behaviour for that page, never a regression below it.
                  var outermost = true;
                  try { outermost = (proto[prop] === wrapped); } catch (e) {}

                  var acc = [];
                  if (outermost) {
                    try {
                      for (var i = 0; i < arguments.length; i++) {
                        collectFrames(arguments[i], acc);
                      }
                    } catch (e) {}
                  }
                  // Call through FIRST and let any native throw propagate
                  // untouched: the wrapper must be transparent, including when
                  // the DOM refuses the insertion.
                  var r = orig.apply(this, arguments);
                  if (acc.length) reachFrames(acc);
                  return r;
                },
              }).m;
              __markHook(wrapped, prop, undefined, orig.length);
              proto[prop] = wrapped;
            } catch (e) {}
          };

          ["appendChild", "insertBefore", "replaceChild"].forEach(function (p) {
            hookInsert(_Node.prototype, p);
          });
          ["append", "prepend", "after", "before", "replaceWith"].forEach(
            function (p) { hookInsert(_El.prototype, p); });

          // The parse-and-insert door. Scanned AFTER the set, on `this`: the
          // frames do not exist until the parser has built them.
          try {
            var hd = Object.getOwnPropertyDescriptor(_El.prototype, "innerHTML");
            if (hd && hd.set) {
              // ACCESSOR SYNTAX, for the same reason the inserters are method
              // shorthand: a native setter owns only `length` and `name`, while
              // a `function (v) {}` expression also owns `prototype`,
              // `arguments` and `caller`. Scanned AFTER the set, on `this` --
              // the frames do not exist until the parser has built them.
              var hset = Object.getOwnPropertyDescriptor({
                set m(v) {
                  var r = hd.set.call(this, v);
                  try {
                    // ONLY THE OUTERMOST RIDER SCANS -- the same guard, and the
                    // same reason, as the insertion methods above. This is a
                    // SEPARATE door with its own wrapper, so it needs its own
                    // check: guarding the methods alone left this one still
                    // walking the subtree thirteen times per assignment
                    // (measured -- `appendChild` collapsed to 1 while
                    // `innerHTML` stayed at 13).
                    //
                    // The identity read is on the DESCRIPTOR's setter rather
                    // than on the property's value: `_El.prototype.innerHTML`
                    // would INVOKE the getter and yield markup, never a
                    // function to compare.
                    var top = true;
                    try {
                      var cd = Object.getOwnPropertyDescriptor(
                        _El.prototype, "innerHTML");
                      top = !!(cd && cd.set === hset);
                    } catch (e) {}
                    if (top) {
                      var acc = collectFrames(this, []);
                      if (acc.length) reachFrames(acc);
                    }
                  } catch (e) {}
                  return r;
                },
              }, "m").set;
              __markHook(hset, "set innerHTML", "innerHTML", hd.set.length);
              Object.defineProperty(_El.prototype, "innerHTML", {
                configurable: true, enumerable: hd.enumerable,
                get: hd.get, set: hset,
              });
            }
          } catch (e) {}
        }
      } catch (e) {}
    } catch (e) {}
  };

  try { __pnaInstall(SELF, %(fn)s); } catch (e) {}
"""
        % {
            "fn": apply_fn_name,
            "cloak_setup": cloak.setup,
            "cloak_apply": cloak.apply,
            "cloak_frame_open": cloak.frame_open,
            "cloak_frame_close": cloak.frame_close,
            "cloak_hook_mark": cloak.hook_mark,
            "blob_setup": cloak.blob_setup,
            "blob_resolve": cloak.blob_resolve,
        }
    )


# --- the BOOTSTRAP's own cloak, on Firefox ----------------------------------
#
# Distinct from `_FIREFOX_NATIVE_WRAP` below, and the distinction is the whole
# point of PS-78 round 3. That one cloaks the LEAF's wrappers (readPixels &c);
# this one cloaks the wrappers the BOOTSTRAP installs — `Worker`,
# `SharedWorker`, and the two `HTMLIFrameElement` accessors. They were left
# carrying `__pnaName` when this PR became the first thing to deliver
# `realm_bootstrap_js` to Firefox at all, where no extension exists to read that
# marker: a bare own property on every wrapper, plus a `toString()` returning
# raw patch source where every real engine returns `[native code]`. Two
# independent reads, either sufficient on its own.
#
# It cannot simply REUSE the leaf's cloak. The leaf's `__nm`/`nativeWrap` live
# inside `applyWebglPatch`'s body; the bootstrap is a sibling scope and a
# separate serialisation unit. More decisively, `setup` below is spliced INSIDE
# `__pnaInstall`, because `__pnaInstall.toString()` is what crosses into a
# worker — anything defined in an enclosing scope is undefined there, which is
# the exact failure this module's docstring records (a depth-2 worker silently
# reporting REAL values). So the bootstrap carries its own copy, per realm.
#
# It is installed AFTER `LEAF(G)` deliberately: `Function.prototype.toString` is
# CHAINED, not flag-guarded, so leaf-then-bootstrap composes with the leaf's
# cloak (and with the locale/outer-size cloaks already in the realm) instead of
# racing it for the single slot.
_FIREFOX_WORKER_CLOAK_SETUP = r"""

      // --- cloak for the wrappers THIS BOOTSTRAP installs (Firefox) --------
      // See the note beside _FIREFOX_WORKER_CLOAK_SETUP in worker_wrap.py.
      // Spliced inside __pnaInstall so it ships with __pnaInstall.toString()
      // and every realm — page, worker, nested worker, child frame — gets its
      // own. Mirrors invisible_launch._native_cloak_js: closure WeakMap (no own
      // property to enumerate), SpiderMonkey's three-line native shape (NOT
      // V8's one-liner, which is itself a tell on this engine), and a CHAINED
      // Function.prototype.toString.
      var __bnm = (typeof WeakMap === "function") ? new WeakMap() : null;
      var __bnl = String.fromCharCode(10);
      // G's Function, never the lexical one: the leaf reaches a child frame as
      // a PARENT-REALM function object, so a bare `Function.prototype` here
      // would re-patch the parent's and leave the child's pristine while the
      // wrappers ARE installed into the child.
      var __bF = G.Function;
      var __bpts = (__bF && __bF.prototype && __bF.prototype.toString)
                   || Function.prototype.toString;
      // METHOD SHORTHAND, not a function expression. Once installed this object
      // IS `Function.prototype.toString`, so a detector reading
      // `Object.getOwnPropertyNames(Function.prototype.toString)` must find
      // only `length`/`name` -- a function expression also owns `prototype`
      // (plus `arguments`/`caller`), and `delete` cannot repair it because a
      // function's `prototype` is non-configurable. This was a bare `function
      // () {}` until PS-215's own AC2 probe caught it: the SAME fault the DOM
      // wrappers below avoid the same way, on the patch that cloaks them.
      var __bts = ({
        m() {
          'use strict';
          try {
            var n = __bnm && __bnm.get(this);
            if (typeof n === "string") {
              return "function " + n + "() {" + __bnl + "    [native code]" + __bnl + "}";
            }
          } catch (e) {}
          return __bpts.apply(this, arguments);
        },
      }).m;
      // `f` gets `.name` = n; its SOURCE TEXT reports `s` (an accessor's name
      // carries a `get ` prefix that its source text does not).
      var __bcloak = function (f, n, s) {
        try {
          Object.defineProperty(f, "name", { value: n, configurable: true });
          if (__bnm) { __bnm.set(f, s === undefined ? n : s); }
        } catch (e) {}
        return f;
      };
      try {
        // Cloak the patch as "toString": a detector stringifies
        // Function.prototype.toString to catch exactly this trick.
        if (__bnm) { __bnm.set(__bts, "toString"); }
        Object.defineProperty(__bts, "name", { value: "toString", configurable: true });
        if (__bF && __bF.prototype) { __bF.prototype.toString = __bts; }
      } catch (e) {}"""


# --- the Firefox WORKER-BODY DELIVERY seam ----------------------------------
#
# NOT A CLOAK. This is how the boot payload gets INTO a blob: worker at all, and
# it exists because the shared sync-XHR path does not survive a real CSP.
#
# THE DEFECT, measured on the real engine through this project's own launch path
# (xvfb + spawn_browser(in_process=True) + get_ff_eval, seeds pinned directly via
# `fingerprint_seed_value` so the digest tracks the SEED and not the name):
#
#     origin                    sync XHR on blob:   page (1000 vs 2000)   WORKER
#     https://duckduckgo.com/   THREW NetworkError  differ                COLLIDE
#     https://example.com/      status 200          differ                differ
#
# The Worker wrapper reads the blob body with a SYNCHRONOUS XHR, and that XHR is
# subject to the page's `connect-src`. DuckDuckGo sends `default-src 'none'` with
# no `blob:` in its connect-src, so the XHR throws, the wrapper's own catch falls
# back to constructing the ORIGINAL Worker, and the result is the worst available
# shape: a worker that RUNS NORMALLY AND CARRIES NO SPOOF. Two profiles then read
# the SAME readPixels digest in the worker realm — the exact linkability
# `webgl_ext.py` exists to prevent, in exactly the realm its docstring names as
# the one detectors read.
#
# AND THAT ORIGIN IS THE DEFAULT START PAGE. `_ensure_firefox_policies()` pins
# DuckDuckGo, so this is not an exotic origin reached by browsing somewhere
# unusual — it is where every Firefox profile already is the moment it opens.
#
# WHY THIS ROUTE AND NOT THE OTHER TWO. Three deliveries were measured against
# the two constraints that have each already rejected a round of PS-78, on
# duckduckgo.com:
#
#     route                      reaches the worker under CSP   survives revoke
#     sync XHR (shared path)     NO  (NetworkError)             yes
#     importScripts shim         yes                            NO (SecurityError)
#     new Blob([BOOT, body])     YES                            YES
#
# NEITHER OF THE FIRST TWO CLEARS BOTH, which is why this is not a matter of
# taste: the `importScripts` shim was deleted in round 2 precisely because a
# worker whose blob URL is revoked right after construction (the pattern MDN
# documents and bundlers emit) never ran at all. Composing the Blob OBJECT needs
# no fetch, no XHR and no importScripts, so no CSP directive governs it; and a
# `Blob` outlives `revokeObjectURL`, which tears down only the URL mapping. One
# change retires both rejections instead of trading one for the other.
#
# HOW THE BLOB IS OBTAINED. `URL.createObjectURL` is CHAINED so each minted url
# is remembered against the `Blob` it came from, and `revokeObjectURL` is chained
# to forget it. THE RETENTION THIS ADDS IS ZERO, which is the point of chaining
# the revoke as well: an un-revoked object URL already pins its blob alive in the
# engine for the document's lifetime, so the map's lifetime is the engine's own
# and a page that revokes properly frees the entry immediately. A plain Map, not
# a WeakMap: the key is a STRING, which a WeakMap cannot hold.
#
# IT DEGRADES TO THE OLD BEHAVIOUR, NEVER BELOW IT. A `blob:` url the map has
# never seen (minted before this script ran, or in another realm) simply falls
# through to the sync-XHR path below — which is what it would have done anyway.
# `data:` is untouched by this seam and keeps the XHR path, correctly: there is
# no object URL to retain, and a data: URL carries its own body inline.
#
# BOTH NEW WRAPPERS ARE CLOAKED, through the same `__bcloak` the rest of this
# object installs. An uncloaked `URL.createObjectURL` stringifying as raw patch
# source is the identical class of tell that got round 3 rejected, and it would
# be a fresh one introduced by the fix for the previous one.
_FIREFOX_BLOB_RETAIN_SETUP = r"""

      // --- retain the Blob behind each object URL (Firefox delivery) -------
      // See the note beside _FIREFOX_BLOB_RETAIN_SETUP in worker_wrap.py. The
      // worker wrapper composes `new Blob([BOOT, retained])` instead of
      // re-fetching the url, because a restrictive `connect-src` (the DEFAULT
      // start page ships one) refuses the fetch and leaves the worker unspoofed.
      //
      // Spliced inside __pnaInstall so it ships with __pnaInstall.toString() and
      // every realm gets its OWN map — a map in an enclosing scope would be
      // undefined in a worker, the exact failure this module's docstring records.
      var __rb = (typeof Map === "function") ? new Map() : null;
      try {
        if (__rb && _URL && typeof _cou === "function"
            && typeof _URL.revokeObjectURL === "function") {
          var __orv = _URL.revokeObjectURL;
          var __wcou = function createObjectURL(obj) {
            var u = _cou.apply(this, arguments);
            // Only Blob bodies are useful to compose with; a MediaSource has no
            // body to prepend to and must fall through to the old path.
            try { if (obj instanceof _Blob) { __rb.set(String(u), obj); } } catch (e) {}
            return u;
          };
          var __wrv = function revokeObjectURL(u) {
            // Forget FIRST, so the map can never outlive the engine's own
            // mapping even if the underlying revoke throws.
            try { __rb["delete"](String(u)); } catch (e) {}
            return __orv.apply(this, arguments);
          };
          __bcloak(__wcou, "createObjectURL");
          __bcloak(__wrv, "revokeObjectURL");
          _URL.createObjectURL = __wcou;
          _URL.revokeObjectURL = __wrv;
        }
      } catch (e) {}"""

# Spliced at the TOP of the `blob:`/`data:` branch, so it pre-empts the sync XHR
# for a blob: url whose Blob we still hold and falls through for everything else.
# `_Blob` is the captured native constructor, so a page that later overrode
# `Blob` is neither consulted nor handed the seed-bearing payload (PS-48).
_FIREFOX_BLOB_RESOLVE = r"""
              // Compose the retained Blob rather than re-fetching the url: no
              // fetch means no `connect-src` to refuse it, and a Blob outlives
              // revokeObjectURL. Falls through to the XHR path below when this
              // realm never minted the url. See _FIREFOX_BLOB_RETAIN_SETUP.
              try {
                var rb = __rb && __rb.get(s);
                if (rb) {
                  var nb = new _Blob([BOOT + "\n", rb], { type: "application/javascript" });
                  return _Ref.construct(Orig, [_cou.call(_URL, nb), options], W);
                }
              } catch (e) {}"""


def firefox_worker_cloak() -> WorkerCloak:
    """The ``WorkerCloak`` a FIREFOX caller passes to :func:`realm_bootstrap_js`.

    Cloaks the bootstrap's own ``Worker``/``SharedWorker`` wrappers and its two
    ``HTMLIFrameElement`` accessors through a closure WeakMap, adding NO own
    property — the standard already set in tree by the locale spoof's Firefox
    worker wrapper (``invisible_launch.py:511``, ``return __cloak(W,Orig.name)``,
    whose marker-absence ``tests/test_ff_language_override.py:166`` pins).

    The Chromium default is not merely unnecessary on this engine, it is a tell:
    ``__pnaName`` exists on no browser, and nothing on Firefox reads it.

    ALSO carries this engine's WORKER-BODY DELIVERY (``blob_setup`` /
    ``blob_resolve``), which is not a cloak: the shared sync-XHR path is refused
    by a restrictive ``connect-src`` and leaves the worker realm UNSPOOFED on the
    default start page. See :data:`_FIREFOX_BLOB_RETAIN_SETUP` for the
    measurement and for why neither of the two previously-tried routes clears
    both constraints. The two travel in one object because ``blob_setup`` calls
    ``__bcloak``, which ``setup`` defines — pairing this delivery with Chromium's
    cloak would install an uncloaked wrapper.
    """
    return WorkerCloak(
        setup=_FIREFOX_WORKER_CLOAK_SETUP,
        apply="        __bcloak(W, Orig.name);",
        # An accessor's `.name` is "get contentWindow" while its source text
        # stringifies as "function contentWindow() { [native code] }" — hence the
        # two names. Wrapped AROUND the function expression because that is an
        # argument position and cannot take a statement.
        frame_open="__bcloak(",
        frame_close=', "get " + prop, prop)',
        # The DOM-insertion wrappers of the PS-215 indexed-frame reach. Same
        # closure WeakMap as everything else on this engine, so they add no own
        # property — `__bcloak` already sets `.name`, and `__markHook` has set
        # it too by the time this runs, so only the source-text registration is
        # needed here.
        hook_mark=(
            "\n            try { if (__bnm) { __bnm.set(f, __sn); } } catch (e) {}"
        ),
        blob_setup=_FIREFOX_BLOB_RETAIN_SETUP,
        blob_resolve=_FIREFOX_BLOB_RESOLVE,
    )


# --- the Firefox cloak seam, shared by every init-script spoof --------------
#
# On CHROMIUM every spoof is an MV3 extension and native_ext.py installs one
# Function.prototype.toString patch that reads each wrapper's `__pnaName` marker
# property. FIREFOX loads no persona extension at all (invisible_launch.py is
# the whole launch path), so on that engine `__pnaName` has nobody to read it:
# the marker is not a cloak there, it is a bare own property on every wrapper —
# a tell rather than a hiding place. Each Firefox init-script spoof therefore
# carries the cloak itself, and this is the one copy of it.
#
# Three deliberate differences from the Chromium form, each mirroring the cloak
# already in invisible_launch._native_cloak_js:
#
#   * SPIDERMONKEY's native shape (three lines, four-space indent), NOT V8's
#     one-liner. Emitting V8's form on Firefox is itself a masking tell — one
#     `Array.prototype.map.toString()` comparison away.
#   * a closure WeakMap, so NO own property is added to any wrapper and the
#     registry cannot be enumerated or swept for symbols.
#   * `Function.prototype.toString` is CHAINED, not flag-guarded, so this
#     composes with the locale/outer-size cloaks already installed in the realm
#     instead of racing them for the single slot.
#
# It must be spliced INSIDE the leaf `applyPatch`: the leaf crosses into a
# worker as SOURCE TEXT (``realm_bootstrap_js`` serialises it with
# ``LEAF.toString()``), so anything defined in an enclosing scope is undefined
# in the worker realm — the exact failure this module's docstring records, where
# a depth-2 worker silently reported REAL values while the page reported
# spoofed ones.
_FIREFOX_NATIVE_WRAP = r"""  var __nm = (typeof WeakMap === 'function') ? new WeakMap() : null;
  var __nl = String.fromCharCode(10);
  // THE REALM MUST COME FROM G, NEVER FROM THE LEXICAL SCOPE. The leaf reaches
  // a child frame as a PARENT-REALM FUNCTION OBJECT — the chained
  // `contentWindow` accessor above calls `__pnaInstall(childWindow, LEAF)` with
  // the leaf itself, not with source text. So a bare `Function.prototype` here
  // resolves to the PARENT's: it re-patches an already-patched toString (a
  // no-op) and leaves the child realm's own pristine, while the wrappers ARE
  // installed into the child. Read from inside that child — where a detector
  // runs — the wrapper then stringifies as raw patch source. Measured in two
  // isolated realms, not reasoned.
  //
  // `G.Function.prototype` is Chromium's shape (native_ext.py:32,48) and the
  // reason its comment names "a fresh about:blank iframe … has its own
  // Function.prototype". The WORKER realm never had this problem — the leaf
  // crosses there as SOURCE TEXT and is re-evaluated in the worker's own realm,
  // so both forms resolve correctly there; this is the frame case only.
  //
  // FAIL SOFT, NEVER `return`: this block is spliced INSIDE the leaf, so
  // bailing out here would skip the spoof's own overrides too — trading the
  // unlinkability fix for the masking one. A realm without a usable
  // Function.prototype loses the cloak and keeps the perturbation.
  var __F = G.Function;
  var __pts = (__F && __F.prototype && __F.prototype.toString)
              || Function.prototype.toString;
  var __ts = function () {
    'use strict';
    // `this` is the function being stringified. Strict mode so a primitive
    // `this` stays primitive and still reaches the original for its TypeError.
    try {
      var n = __nm && __nm.get(this);
      if (typeof n === 'string') {
        return 'function ' + n + '() {' + __nl + '    [native code]' + __nl + '}';
      }
    } catch (e) {}
    return __pts.apply(this, arguments);
  };
  try {
    // Cloak the patch as "toString": a detector stringifies
    // Function.prototype.toString to catch exactly this trick.
    if (__nm) { __nm.set(__ts, 'toString'); }
    Object.defineProperty(__ts, 'name', { value: 'toString', configurable: true });
    // G's prototype, not the lexical one — see the note above.
    if (__F && __F.prototype) { __F.prototype.toString = __ts; }
  } catch (e) {}

  function nativeWrap(orig, replacement) {
    try {
      // configurable: true is the descriptor a native function's own `name`
      // carries; the Chromium form omits it because its marker property, not
      // `name`, is what its extension-side cloak reads.
      Object.defineProperty(replacement, 'name',
                            { value: orig.name, configurable: true });
      if (__nm) { __nm.set(replacement, orig.name); }
    } catch (e) {}
    return replacement;
  }"""


def firefox_native_wrap_js() -> str:
    """The ``nativeWrap`` seam a FIREFOX init-script spoof splices into its leaf.

    Returns the Firefox form: a chained ``Function.prototype.toString`` backed by
    a closure WeakMap, emitting SpiderMonkey's native shape. Splice it where the
    Chromium build puts its own ``nativeWrap`` — everything else in a spoof's
    patch body is shared between the two engines, and this is the only part that
    differs.
    """
    return _FIREFOX_NATIVE_WRAP


# ---------------------------------------------------------------------------
# Per-realm idempotency guard (PS-93)
# ---------------------------------------------------------------------------
# Replaces the 11 `G.__personaX = true` markers that used to sit ENUMERABLE on
# the global object, where `Object.getOwnPropertyNames(self)` — the sweep every
# fingerprinter runs, and the one `realm.bootMarkers` encodes — reads them.
#
# THE CONSTRAINT SET, which is narrower than it looks. Dedup must be:
#
#   (a) per-realm    — one realm's state must not answer for another's, or a
#                      child frame silently never gets the leaf;
#   (b) shared across INDEPENDENT `__pnaInstall` invocations into the SAME
#       realm — `all_frames:true` means a same-origin child runs the content
#       script ITSELF *and* is installed into by the parent's `contentWindow`
#       accessor, and Firefox re-evaluates whole leaves into open tabs
#       (invisible_launch.py `_apply_audio_to_open_tabs`). Those two invocations
#       share no closure AND hold DIFFERENT `LEAF` function objects, so neither
#       a closure `WeakSet` nor a marker on the leaf can carry state between
#       them;
#   (c) carried into a worker AS TEXT — `fragment()` above serialises only
#       `LEAF.toString()` + `__pnaInstall.toString()`, so anything defined in an
#       enclosing scope is `undefined` in a worker and the leaf would silently
#       never apply there.
#
# The closure `SEEN` WeakSet above satisfies (c) only. It is per-INVOCATION (it
# is declared inside `__pnaInstall`'s body), so it cannot satisfy (b) and is NOT
# a substitute for this guard — it stays where it is, guarding the iframe
# accessor, which is the one place its per-invocation lifetime is correct.
#
# (b) is what forces the state to hang off an object resolved FROM `G`, and (c)
# forces the whole thing to be inline text. So the registry lives on the realm's
# own `Object` constructor, NON-ENUMERABLY, keyed per module:
#
#   * `G.Object` is per-realm, is present in every realm including a DOM-less
#     worker, and is resolved identically by two independent invocations —
#     which is exactly the trio (a)/(b)/(c) asks for;
#   * one slot holds all 11 module keys, so this REPLACES eleven names with one.
#
# HONEST BOUND, stated rather than implied: this is not invisibility. It moves
# the flag off the global sweep, but a detector that walks
# `Object.getOwnPropertyNames(Object)` still finds `__pnaRealm`. That is the
# same shape — and the same `__pna` family — as the `__pnaName` marker already
# carried on every Chromium wrapper, which this ticket scopes out on exactly
# that ground. Symbol keying was NOT chosen instead: this file's own standard
# (see the Firefox cloak note) is that the registry must not be "enumerated OR
# SWEPT FOR SYMBOLS", so a symbol would satisfy the probe's regex while
# breaching the in-tree rule it encodes.
#
# FAIL OPEN, never closed. If `defineProperty` is refused the leaf RE-RUNS
# rather than bailing — matching `fresh()`, which returns true when `WeakSet` is
# unavailable. A re-run costs a double-applied spoof; a false bail costs an
# UNMASKED realm, which is strictly worse.
def realm_guard_js(module_key: str, indent: int = 4) -> str:
    """Inline JS that returns early if ``module_key`` already ran in this realm.

    THE ONLY SOURCE OF THIS TEXT. Every leaf that needs the guard carries a
    ``*_REALM_GUARD__`` placeholder in its template and fills it from HERE in its
    builder's ``.replace()`` chain, exactly as ``realm_bootstrap_js`` is already
    consumed in those same files. Do NOT paste the emitted body into a leaf: it
    was pasted into all twelve sites once, and the copies were coupled to this
    function by nothing — editing the helper silently changed nothing that
    shipped, and editing one copy silently diverged it from eleven others. The
    structural suite in tests/test_realm_guard.py asserts every generated script
    contains ``realm_guard_js(<its key>)``, so a dropped placeholder or an
    unwired builder goes red per-site rather than becoming a leaf with no
    idempotency at all.

    Splice INSIDE the leaf (it emits a bare ``return``), AFTER the leaf's own
    preconditions and before it patches anything. That ordering matters where a
    leaf legitimately bails in some realms (canvas_ctx and measuretext bail
    without a DOM): a realm where the leaf did no work must NOT be recorded as
    covered, or a later invocation that COULD have patched it returns early
    against an empty realm. Those two therefore keep their placeholder at the
    point where the leaf is known to be doing work, not at the top of the body.

    Emitted as text with no free variables other than ``G``, so it survives the
    trip into a worker realm. ``indent`` is the leaf body's own indentation —
    voice_ext.py indents its body by 2, every other leaf by 4 — and a wrong
    value here is a real mismatch the structural suite catches, not cosmetic.
    """
    key = json.dumps(module_key)
    body = (
        "var __pnaReg = null;\n"
        "try {\n"
        "  var __pnaO = G.Object;\n"
        "  if (__pnaO) {\n"
        "    __pnaReg = __pnaO.__pnaRealm;\n"
        "    if (!__pnaReg) {\n"
        "      __pnaReg = {};\n"
        "      __pnaO.defineProperty(__pnaO, '__pnaRealm',\n"
        "                            { value: __pnaReg, configurable: true });\n"
        "    }\n"
        "  }\n"
        "} catch (e) { __pnaReg = null; }\n"
        "try {\n"
        "  if (__pnaReg) {\n"
        "    if (__pnaReg[" + key + "] === true) return;\n"
        "    __pnaReg[" + key + "] = true;\n"
        "  }\n"
        "} catch (e) {}"
    )
    return textwrap.indent(body, " " * indent)


# ---------------------------------------------------------------------------
# Per-realm VALUE slot (PS-139)
# ---------------------------------------------------------------------------
# The second consumer of the same `Object.__pnaRealm` object the guard above
# creates. It replaces the last two `__persona*` names that sat ENUMERABLE on
# the global object — and unlike the eleven booleans PS-93 removed, these two
# handed the page live session values rather than mere tool presence:
#
#   * `__personaScreenWH` -> the profile's resolved screen geometry {W, H};
#   * `__personaMtFactor` -> the measureText noise factor, i.e. the divisor
#     that INVERTS the text-metrics spoof.
#
# NO NEW GLOBAL NAME IS ADDED. The slot already ships (see realm_guard_js), so
# this is two keys inside one existing object; `getOwnPropertyNames(G)` gains
# nothing and loses two.
#
# WHY A HELPER AND NOT `__pnaReg`. The guard already resolves this realm's own
# slot into a local `__pnaReg`, and the own-realm write could have reused it.
# It does not, for two reasons: the value channels must also read ANOTHER
# realm's slot (the iframe->top crossing below), which `__pnaReg` cannot do; and
# reusing a variable the guard happens to declare would couple two leaves to the
# guard's internal spelling with nothing asserting the coupling.
#
# THE CROSSING THIS SUPPORTS, precisely. `__pnaRealm` is built per-realm off
# each realm's OWN `G.Object`, so a child reaches the top's copy the same way it
# reached `top.__personaScreenWH` before: through the `top` hop, same-origin
# only. That means:
#
#   * iframe -> top   WORKS, and is the channel both values exist for. Every
#                     realm must agree on one screen geometry and one noise
#                     factor; divergence between realms is a worse tell than
#                     the global this replaces.
#   * cross-origin    UNCHANGED, not improved — such a child could not read
#                     `top.__personaScreenWH` either. This is not a frame
#                     isolation fix and must not be read as one.
#   * worker -> top    STILL IMPOSSIBLE. `WorkerGlobalScope` has no `top`, so a
#                     worker never read either value and does not now. Nothing
#                     regresses; nothing improves. Carrying a RUNTIME-LEARNED
#                     value into a worker needs a mechanism nobody has
#                     specified — the leaf crosses as SOURCE TEXT built at
#                     new-Worker time (`fragment()` above), which by
#                     construction cannot carry a value learned later. That is
#                     tracked separately and this slice does not block on it.
#
# FAIL OPEN, exactly like the guard: every path returns null rather than
# throwing, and each caller falls back to computing the value locally. A null
# slot costs a realm re-deriving its own geometry (what happens today when
# `top` is cross-origin); a throw would cost an UNMASKED realm.
#
# HONEST BOUND, inherited not closed: a detector walking
# `getOwnPropertyNames(Object)` still finds `__pnaRealm`. PS-93 states that
# bound in place and this slice does not claim to close it.
def realm_slot_js(indent: int = 4) -> str:
    """Inline JS defining ``__pnaSlot(R, create)`` — the per-realm value slot.

    THE ONLY SOURCE OF THIS TEXT, on the same terms as ``realm_guard_js``: a
    leaf carries a ``*_REALM_SLOT__`` placeholder and fills it from HERE in its
    builder's ``.replace()`` chain. Do not paste the emitted body into a leaf.

    ``__pnaSlot(R, create)`` returns realm ``R``'s ``Object.__pnaRealm``, or
    null when it cannot be reached (no ``Object``, a cross-origin ``R``, a
    refused ``defineProperty``). ``create`` is for the realm you OWN; pass false
    when reading another realm's slot, so a read never mints an empty registry
    in a realm that has not booted.

    Keys inside the slot are a flat namespace shared with the guard's module
    keys, so a value key must not collide with one: the guard's are module names
    (``audio``, ``screen``, ``hw``, ``measuretext``, …) and the value keys are
    ``screenWH`` / ``mtFactor``. A collision would make a leaf's guard read a
    value object as its ``=== true`` boot flag — which fails OPEN (the leaf
    re-applies) rather than silently skipping, but is still a bug.

    Emitted as text with no free variables at all, so it survives the trip into
    a worker realm inside ``LEAF.toString()``. ``indent`` is the leaf body's own
    indentation, as with the guard.
    """
    body = (
        "function __pnaSlot(R, create) {\n"
        "  try {\n"
        "    var O = R && R.Object;\n"
        "    if (!O) return null;\n"
        "    var s = O.__pnaRealm;\n"
        "    if (!s && create) {\n"
        "      s = {};\n"
        "      O.defineProperty(O, '__pnaRealm', { value: s, configurable: true });\n"
        "      s = O.__pnaRealm || s;\n"
        "    }\n"
        "    return s || null;\n"
        "  } catch (e) { return null; }\n"
        "}"
    )
    return textwrap.indent(body, " " * indent)
