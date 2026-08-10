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
