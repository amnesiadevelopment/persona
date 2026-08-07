from src.services.browser.worker_wrap import iframe_carry_js, worker_wrap_js


def test_iframe_carry_references_fn_and_frame_getters():
    js = iframe_carry_js("applyGpuPatch")
    assert "applyGpuPatch(w)" in js
    assert "contentWindow" in js
    assert "contentDocument" in js
    assert "HTMLIFrameElement" in js
    assert js.count("{") == js.count("}")
    assert js.count("(") == js.count(")")


def test_emits_worker_wrap_for_named_fn():
    js = worker_wrap_js("applyGpuPatch")
    # references the patch function, wraps both worker constructors
    assert "applyGpuPatch.toString()" in js
    assert "SELF.Worker" in js
    assert "SELF.SharedWorker" in js


def test_carries_patch_via_reblob_and_importscripts():
    js = worker_wrap_js("applyFoo")
    # blob/data workers re-blobbed via sync XHR; http workers via importScripts
    assert "XMLHttpRequest" in js
    assert "importScripts" in js
    assert "blob:|^data:" in js
    # module workers left untouched (can't prepend to an ES module)
    assert 'options.type === "module"' in js


def test_balanced_braces_and_parens():
    js = worker_wrap_js("applyFoo")
    assert js.count("{") == js.count("}")
    assert js.count("(") == js.count(")")
