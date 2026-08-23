"""The ONE `node:vm` realm harness: page realm, worker generations, child frame.

Extracted verbatim from `tests/test_worker_wrap.py`, which built it to prove that
each module's leaf really arrives in every realm the page can reach. PS-68 needs
the same three-generation transport to ask a DIFFERENT question of the realms it
produces — does the `Function.prototype.toString` cloak still render the native
form at depth once the two Chromium scripts CHAIN instead of sharing a global
flag — and PS-68's AC6 says to reuse this harness rather than write a second.

So it lives here and both suites include it. A copy would have been the wrong
shape twice over: two harnesses drift, and the transport semantics below are
subtle enough (see BLOBS) that a divergence would show up as a test that
under-reports coverage rather than as a test that fails.

Everything here is a JS SOURCE FRAGMENT, not Python. A consumer writes
``HARNESS + its own probe`` to a file and runs it under node. The fragment
defines `fs`, `vm`, `BLOBS`, `makeRealm()` and `spawn()`, and nothing else — a
`report()` is deliberately NOT provided, because what a realm should be asked is
exactly what differs between the two suites.
"""

# --------------------------------------------------------------------------
# Shared realm machinery. One `vm` context per realm, so a realm genuinely
# cannot see another's globals and a payload only arrives if it was really
# transported.
# --------------------------------------------------------------------------
HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

// One shared blob store, like the browser's: a blob: URL created in one realm
// resolves anywhere. This matters — each chain link re-reads the blob the link
// above it produced, so a store that forgets content silently drops a module's
// fragment and a test would under-report coverage.
const BLOBS = new Map();

function makeRealm() {
  const captured = [];
  const sandbox = {
    Reflect, WeakSet,
    URL: {
      createObjectURL: (b) => {
        const u = "blob:pna-" + (BLOBS.size + 1);
        BLOBS.set(u, b.__parts[0]);
        captured.push(b.__parts[0]);
        return u;
      },
    },
    Blob: function Blob(parts) { this.__parts = parts; },
    Worker: function Worker(url) { this.url = url; },
    SharedWorker: function SharedWorker(url) { this.url = url; },
    XMLHttpRequest: function XMLHttpRequest() {
      const me = this;
      this.status = 0; this.responseText = "";
      this.open = function (m, u) { me.__u = u; };
      this.send = function () {
        if (BLOBS.has(me.__u)) { me.status = 200; me.responseText = BLOBS.get(me.__u); }
        else { me.status = 404; }
      };
    },
  };
  const ctx = vm.createContext(sandbox);
  vm.runInContext("var self = this; globalThis.self = globalThis;", ctx);
  return { ctx, captured };
}

// Spawn a worker FROM this realm; return the payload its wrapper prepended.
function spawn(realm) {
  vm.runInContext('new self.Worker("https://example.test/w.js");', realm.ctx);
  const body = realm.captured[realm.captured.length - 1];
  if (body === undefined) throw new Error("realm's Worker was not wrapped");
  return body.replace(/\ntry\{importScripts[\s\S]*$/, "");
}
"""
