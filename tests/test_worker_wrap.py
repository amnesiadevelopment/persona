import json
import shutil
import subprocess

import pytest

from src.services.browser.worker_wrap import realm_bootstrap_js


def test_registers_leaf_and_installs_bootstrap():
    js = realm_bootstrap_js("applyGpuPatch")
    # registers this module's leaf into the shared per-realm registry
    assert "__pnaBoots.push(applyGpuPatch)" in js
    assert "__pnaBootSrc.push" in js
    # installs the shared bootstrap once per realm and runs it
    assert "__pnaBootInstalled" in js
    assert "__pnaBoot(SELF)" in js


def test_carries_registry_into_workers():
    # the worker payload rebuilds __pnaBoots from the stored leaf sources, then
    # re-runs the bootstrap (shipping only the closure would ReferenceError).
    js = realm_bootstrap_js("applyGpuPatch")
    assert "self.__pnaBoots=[" in js
    assert "applyGpuPatch.toString()" in js
    assert "XMLHttpRequest" in js
    assert "importScripts" in js
    assert "G.Worker" in js and "G.SharedWorker" in js


def test_worker_payload_is_rebuilt_at_new_worker_time():
    # Regression: modules register their leaves across separate content scripts.
    # If the worker payload were snapshotted when the Worker wrapper is installed
    # (by the first module to run __pnaBoot), every LATER module's leaf would be
    # missing in workers — a page/worker mismatch (real hardwareConcurrency leaked
    # in a worker while the page reported the spoofed value). The payload must be
    # built from __pnaBootSrc INSIDE the wrapped constructor (per new Worker), not
    # once at install.
    js = realm_bootstrap_js("applyGpuPatch")
    assert "__buildBoot" in js
    # the join of stored sources happens inside the builder, and the builder is
    # invoked inside the wrapped constructor W (not hoisted to install time).
    builder = js.split("__buildBoot = function", 1)[1].split("};", 1)[0]
    assert "__pnaBootSrc" in builder
    # W calls __buildBoot() each time it constructs a worker
    assert "var __BOOT = __buildBoot();" in js


def test_recurses_into_iframes_with_shared_registry():
    # iframe getter passes the registry by reference to the child and re-runs the
    # full bootstrap — every module's leaf reaches the child (not just the first).
    js = realm_bootstrap_js("applyGpuPatch")
    assert "contentWindow" in js and "contentDocument" in js
    assert "HTMLIFrameElement" in js
    assert "w.__pnaBoots = G.__pnaBoots" in js
    assert "__pnaBoot(w)" in js


def test_second_module_applies_without_reinstall():
    # a module loaded after the bootstrap is installed just applies its own leaf
    # to the current realm (the others already ran).
    js = realm_bootstrap_js("applyFoo")
    assert "applyFoo(SELF)" in js


def test_module_workers_are_wrapped():
    # A module worker (new Worker(url, {type:'module'})) can't importScripts, so
    # it used to run UNSPOOFED — creepjs reads WebGL from a worker and saw the
    # engine-default GPU (a page!=worker mismatch). The wrapper must handle the
    # module type by building a module blob that runs __BOOT then dynamic-imports
    # the original module.
    js = realm_bootstrap_js("applyGpuPatch")
    assert 'options.type === "module"' in js
    assert "import(" in js  # dynamic import of the original module


def test_relative_worker_urls_are_resolved():
    # creepjs spawns its worker from a RELATIVE url ('./creep.js'); a relative url
    # matched no scheme test and fell through to the native, unspoofed construct.
    # The wrapper resolves a relative url to an absolute one so it takes the
    # http(s) importScripts path.
    js = realm_bootstrap_js("applyGpuPatch")
    assert "new URL(s, base)" in js
    assert "(https?:|blob:|data:)" in js


def test_pnaboot_is_named_for_nested_workers():
    # The worker payload runs __pnaBoot, which itself must spawn-wrap the worker's
    # OWN nested workers — that needs __pnaBoot resolvable by name inside its own
    # serialized body (a named function expression), else a nested worker's
    # wrapper throws and runs unspoofed.
    js = realm_bootstrap_js("applyGpuPatch")
    assert "function __pnaBoot(G)" in js


def test_bootstrap_balanced():
    js = realm_bootstrap_js("applyFoo")
    assert js.count("{") == js.count("}")
    assert js.count("(") == js.count(")")


# --- nested workers (depth >= 2) --------------------------------------------
# Every test above this line is a substring assertion over the generated JS,
# which is exactly why none of them could see this: the payload said
# `self.__pnaBoots=[...]` in all the right places, so the strings all matched —
# while the payload never assigned `self.__pnaBootSrc`. __pnaBoots holds live
# functions and cannot cross a realm boundary; __pnaBootSrc holds their text and
# is the only one that can. A depth-1 worker therefore had no source array, its
# own __buildBoot fell back to `[]`, and every worker IT spawned received an
# EMPTY registry: at depth >= 2 no leaf ran at all and nothing threw, so a
# nested worker reported the REAL GPU/hardwareConcurrency/audio/fonts while the
# page reported spoofed ones. The tests below EXECUTE the generated bootstrap
# through two payload generations, in isolated realms, and assert a leaf still
# reaches depth 2 — the only shape that can catch a registry that arrives empty.


def test_worker_payload_reseeds_the_source_array():
    # criterion 1, and the one assertion here that needs no JS engine: the
    # payload must ASSIGN self.__pnaBootSrc, not merely .push to it at
    # registration time (:42) — the push runs in the realm that already has it.
    js = realm_bootstrap_js("applyFoo")
    builder = js.split("__buildBoot = function", 1)[1].split("};", 1)[0]
    assert "self.__pnaBootSrc=" in builder, (
        "the worker payload must re-seed __pnaBootSrc; without it a nested "
        "worker's registry is empty and every leaf silently stops running"
    )
    # ...derived from the functions just installed, NOT by embedding SRC a
    # second time — embedding twice re-doubles the payload at every level, so it
    # would grow exponentially with worker depth.
    assert builder.count('" + SRC + "') == 1


_NESTED_HARNESS = r"""
// Drive the REAL generated bootstrap through three payload generations —
// page -> depth-1 worker -> depth-2 worker -> depth-3 worker — with one
// node:vm context per realm, so a realm genuinely cannot see another's
// globals (which is the whole point: the registry has to be TRANSPORTED).
const fs = require("fs");
const vm = require("vm");

const BOOTSTRAP_A = fs.readFileSync(process.argv[2], "utf8");
const BOOTSTRAP_B = fs.readFileSync(process.argv[3], "utf8");

// Two leaves, registered by two separate "content scripts", each marking the
// realm it reaches — so this also covers the multi-module case (the second
// module takes the already-installed branch) and gives __pnaBootSrc a length
// worth comparing against __pnaBoots.
const LEAF_A = "function applyAlpha(G){ try { G.__ALPHA__ = true; } catch (e) {} }\n";
const LEAF_B = "function applyBeta(G){ try { G.__BETA__ = true; } catch (e) {} }\n";

// A realm: its own global, plus the minimum Worker/Blob/URL surface the
// wrapper's http(s) path touches. The Worker stub captures the payload the
// wrapper built instead of spawning anything.
function makeRealm() {
  const captured = [];
  const sandbox = {
    Reflect,
    URL: {
      createObjectURL: (b) => { captured.push(b.__parts[0]); return "blob:captured"; },
    },
    Blob: function Blob(parts) { this.__parts = parts; },
    Worker: function Worker(url) { this.url = url; },
    SharedWorker: function SharedWorker(url) { this.url = url; },
  };
  const ctx = vm.createContext(sandbox);
  vm.runInContext("var self = this; globalThis.self = globalThis;", ctx);
  return { ctx, captured };
}

// Spawn a worker FROM this realm and return the payload its wrapper prepended.
function spawn(realm) {
  vm.runInContext('new self.Worker("https://example.test/w.js");', realm.ctx);
  const body = realm.captured[realm.captured.length - 1];
  if (body === undefined) throw new Error("realm's Worker was not wrapped");
  // strip the trailing importScripts of the original worker script
  return body.replace(/\nimport|\ntry\{importScripts[\s\S]*$/, "");
}

function report(realm) {
  return vm.runInContext(
    "({leaves: (self.__pnaBoots||[]).length," +
    "  bootSrc: self.__pnaBootSrc === undefined ? -1 : self.__pnaBootSrc.length," +
    "  alpha: self.__ALPHA__ === true, beta: self.__BETA__ === true})",
    realm.ctx);
}

// --- page realm: two content scripts register two leaves --------------------
const page = makeRealm();
vm.runInContext(LEAF_A + BOOTSTRAP_A, page.ctx);
vm.runInContext(LEAF_B + BOOTSTRAP_B, page.ctx);

const out = { page: report(page), payloads: {}, depths: {} };

// --- descend: each realm is booted ONLY by the payload it was handed ---------
let parent = page;
for (let depth = 1; depth <= 3; depth++) {
  const payload = spawn(parent);
  out.payloads["d" + depth] = {
    length: payload.length,
    sets_boots: /self\.__pnaBoots=/.test(payload),
    sets_bootSrc: /self\.__pnaBootSrc=/.test(payload),
    // what the registry literal actually carries into that realm
    boots_literal_length: (/self\.__pnaBoots=\[([\s\S]*?)\];\}catch/.exec(payload) || [, null])[1].length,
  };
  const child = makeRealm();
  vm.runInContext(payload, child.ctx);
  out.depths["d" + depth] = report(child);
  parent = child;
}

console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def nested_worker_probe(tmp_path_factory):
    """Run the generated bootstrap down three worker generations and report what
    each realm actually ended up with."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    d = tmp_path_factory.mktemp("nested_worker")
    (d / "boot_a.js").write_text(realm_bootstrap_js("applyAlpha"), encoding="utf-8")
    (d / "boot_b.js").write_text(realm_bootstrap_js("applyBeta"), encoding="utf-8")
    (d / "harness.js").write_text(_NESTED_HARNESS, encoding="utf-8")
    out = subprocess.run(
        [node, str(d / "harness.js"), str(d / "boot_a.js"), str(d / "boot_b.js")],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_page_realm_registry_is_the_baseline(nested_worker_probe):
    # the control: the page realm has always worked, and both leaves ran there.
    page = nested_worker_probe["page"]
    assert page["leaves"] == 2
    assert page["bootSrc"] == 2
    assert page["alpha"] is True and page["beta"] is True


def test_depth_one_worker_receives_both_registry_globals(nested_worker_probe):
    # criterion 4: __pnaBootSrc must arrive alongside __pnaBoots and hold the
    # SAME number of entries. Before the fix this array was `undefined` in the
    # worker (reported here as -1) — the depth-1 realm looked perfectly fine
    # because its __pnaBoots was populated, which is why the break stayed hidden.
    d1 = nested_worker_probe["depths"]["d1"]
    assert d1["bootSrc"] != -1, "__pnaBootSrc is undefined inside the worker"
    assert d1["bootSrc"] == d1["leaves"] == 2
    assert d1["alpha"] is True and d1["beta"] is True


def test_nested_worker_registry_is_not_empty(nested_worker_probe):
    # criterion 2: the depth-2 payload's `self.__pnaBoots=[...]` literal is
    # non-empty. This is the arithmetic of the bug in one assertion: an empty
    # __pnaBootSrc at depth 1 makes SRC the empty string, which makes the
    # depth-2 payload literally `self.__pnaBoots=[]`. Fails on origin/main
    # (boots_literal_length == 0).
    d2 = nested_worker_probe["payloads"]["d2"]
    assert d2["sets_boots"] and d2["sets_bootSrc"]
    assert d2["boots_literal_length"] > 0, (
        "depth-2 payload carries an EMPTY registry: every spoof leaf is absent "
        "and the realm reports real host values"
    )


def test_leaves_actually_run_in_a_nested_worker(nested_worker_probe):
    # criterion 3, and the assertion that matters: not that the array is
    # non-empty but that a leaf REACHES depth 2 and RUNS there. A worker spawned
    # by a worker used to see none of them — real GPU, real hardwareConcurrency,
    # real audio, real fonts, one nested `new Worker` away.
    d2 = nested_worker_probe["depths"]["d2"]
    assert d2["leaves"] == 2
    assert d2["alpha"] is True and d2["beta"] is True, "nested worker ran UNSPOOFED"


def test_spoofing_recurses_past_depth_two(nested_worker_probe):
    # the docstring's actual standard is "recursively", not "twice": depth 3 is
    # reached only if depth 2 re-seeded the source array in its turn.
    d3 = nested_worker_probe["depths"]["d3"]
    assert d3["leaves"] == 2 and d3["bootSrc"] == 2
    assert d3["alpha"] is True and d3["beta"] is True


def test_payload_does_not_grow_with_worker_depth(nested_worker_probe):
    # criterion 5. The naive fix — embedding SRC a second time to set
    # __pnaBootSrc — doubles the payload at every generation, so a page that
    # nests workers a few deep ships megabytes. Deriving the source from the
    # functions just installed makes the payload a fixed point instead.
    p = nested_worker_probe["payloads"]
    assert p["d2"]["length"] <= 2 * p["d1"]["length"]
    assert p["d3"]["length"] <= 2 * p["d1"]["length"]
    # in fact it is stable, not merely bounded
    assert p["d1"]["length"] == p["d2"]["length"] == p["d3"]["length"]
