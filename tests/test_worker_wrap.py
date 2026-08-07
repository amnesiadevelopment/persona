from src.services.browser.worker_wrap import realm_bootstrap_js


def test_bootstrap_defines_pnaboot_and_invokes():
    js = realm_bootstrap_js("applyGpuPatch")
    assert "function __pnaBoot(G)" in js
    assert "__pnaBoot(SELF)" in js
    # leaf patch applied inside the bootstrap
    assert "applyGpuPatch(G)" in js


def test_bootstrap_carries_leaf_and_boot_into_workers():
    # __BOOT must ship BOTH the leaf applyPatch source AND __pnaBoot — shipping
    # only __pnaBoot would ReferenceError in the worker (leaf undefined there).
    js = realm_bootstrap_js("applyGpuPatch")
    assert "applyGpuPatch.toString()" in js
    assert "__pnaBoot.toString()" in js
    # worker constructor wrapping via re-blob (blob/data) + importScripts (http)
    assert "XMLHttpRequest" in js
    assert "importScripts" in js
    assert "G.Worker" in js and "G.SharedWorker" in js


def test_bootstrap_recurses_into_iframes():
    # iframe getters call __pnaBoot(childWindow) — the FULL bootstrap, so the
    # child re-establishes its own worker-wrap and iframe-carry (a worker or
    # nested frame under the child is covered too).
    js = realm_bootstrap_js("applyGpuPatch")
    assert "contentWindow" in js and "contentDocument" in js
    assert "HTMLIFrameElement" in js
    assert "__pnaBoot(w)" in js


def test_bootstrap_balanced():
    js = realm_bootstrap_js("applyFoo")
    assert js.count("{") == js.count("}")
    assert js.count("(") == js.count(")")
