"""PS-78: the Firefox arm of the WebGL readPixels vector, and its delivery.

Three defects were filed together because they share one delivery path:

  1. ``build_webgl_extension`` has a single call site (``process.py:503``) that
     sits ~150 lines AFTER ``spawn_browser`` returns on the Firefox arm, so the
     WebGL delta was UNREACHABLE on that engine — the strings were spoofed per
     seed and the pixels were not.
  2. ``add_init_script`` does not reach RESTORED tabs, so the locale and
     outer-size overrides were present on a first launch and absent on every
     restart.
  3. The realm bootstrap's ``blob:`` branch silently produced an UNSPOOFED
     worker on Firefox.

What each test PINS is the observable a detector reads, not the text this
implementation happens to emit. The behavioural proof — real launches, real
restarts, two profiles — is recorded on the ticket; these are the fast
regression seats around the parts that are decidable without a browser.
"""

import pathlib
import tempfile

from src.services.browser.webgl_ext import (
    build_webgl_extension,
    firefox_webgl_init_script,
)
from src.services.browser.worker_wrap import realm_bootstrap_js


def _code_only(js: str) -> str:
    """Drop ``//`` comment text.

    These scripts are heavily commented, and several of the comments NAME the
    very construct the assertion is checking for the absence of (the data:
    branch's comment explains why it does not use importScripts). Asserting over
    raw text would match the explanation and report a defect that is not there —
    which is exactly the "assert on what was written, not on what happens" trap
    PS-11 catalogues. Compare CODE.
    """
    out = []
    for line in js.splitlines():
        i = line.find("//")
        out.append(line[:i] if i >= 0 else line)
    return "\n".join(out)


# --- the Firefox script exists, is self-contained, and carries the seed ------


def test_firefox_script_is_self_contained_and_seed_bearing():
    """It is evaluated as one expression, both by add_init_script and by the
    restored-tab replay, so it must be a single self-invoking unit."""
    js = firefox_webgl_init_script(0xDEADBEEF)
    assert js.startswith("(function(){")
    assert js.rstrip().endswith("})();")
    assert "readPixels" in js
    assert "applyWebglPatch" in js


def test_the_seed_reaches_the_generated_patch():
    """Two seeds must produce different text — the necessary (not sufficient)
    condition for two profiles reading different pixels. The SUFFICIENT one is
    behavioural and is measured on a real launch; see the ticket."""
    assert firefox_webgl_init_script(1) != firefox_webgl_init_script(2)


def test_same_seed_is_stable():
    """A vector that varies per launch is not an identity: it would make a
    profile unrecognisable to itself across a restart, trading unlinkability for
    the restart-continuity outcome."""
    assert firefox_webgl_init_script(999) == firefox_webgl_init_script(999)


def test_seed_is_masked_into_range():
    """The seed is masked to 32 bits, so equivalent seeds must agree rather than
    emitting an out-of-range literal into the JS."""
    assert firefox_webgl_init_script(0) == firefox_webgl_init_script(2**32)
    assert firefox_webgl_init_script(-1) == firefox_webgl_init_script(2**32 - 1)


def test_firefox_carries_the_cloak_and_chromiums_marker_is_absent():
    """Firefox loads NO persona extension, so ``__pnaName`` has nobody to read
    it: on that engine the marker is not a cloak, it is a bare own property on
    every wrapper — a tell rather than a hiding place. The Firefox script must
    carry its own closure-WeakMap cloak instead.

    Pinned as ABSENCE of the marker, mirroring the standard already set by
    tests/test_ff_language_override.py, which asserts ``"__pnaName" not in``.
    """
    js = _code_only(firefox_webgl_init_script(7))
    # The leaf ends where the shared realm bootstrap begins (`var SELF =`).
    # NOT split on "__pnaInstall": that name appears inside the cloak's own
    # comment, so splitting there truncates the leaf mid-cloak and the test
    # measures the wrong region.
    leaf = js.split("function applyWebglPatch(G)", 1)[1].split("var SELF =", 1)[0]
    # the leaf's own body must not stamp the Chromium marker onto wrappers
    assert "__pnaName" not in leaf
    assert "WeakMap" in leaf
    # SpiderMonkey's native shape, not V8's one-liner: emitting V8's form on
    # Firefox is itself a masking tell.
    assert "[native code]" in leaf


def test_firefox_and_chromium_share_the_perturbation():
    """A profile's WebGL identity must not depend on which engine it launched.

    The two engines differ ONLY in the cloak seam; everything that computes the
    perturbation (seed mixing, stride, byte nudging, the readPixels overrides) is
    one shared body. Pinned by comparing the parts that must agree.
    """
    ff = firefox_webgl_init_script(4242)
    d = tempfile.mkdtemp()
    chrome = (
        pathlib.Path(build_webgl_extension(4242, str(pathlib.Path(d) / "ext")))
        / "webgl.js"
    ).read_text()
    for shared in (
        "var SEED = 4242;",
        "var STRIDE = 17;",
        "function perturbBytes(buf)",
        "proto.readPixels = nativeWrap(orig,",
    ):
        assert shared in ff, f"firefox script lost {shared!r}"
        assert shared in chrome, f"chromium script lost {shared!r}"


# --- the boundary: Chromium must not move -----------------------------------


def test_chromium_extension_text_is_stable_across_seeds():
    """Regression seat for the boundary: the same seed must always produce the
    same Chromium bytes, and equivalent (masked) seeds must agree.

    The full byte-identity check against the pre-refactor tree was run at
    development time across 11 seeds including the boundary values (0, 1,
    2**31-1, 2**31, 2**32-1, 2**32, -1); it is recorded on the ticket. This seat
    keeps the property from drifting afterwards.
    """
    def text(seed):
        d = tempfile.mkdtemp()
        return (
            pathlib.Path(build_webgl_extension(seed, str(pathlib.Path(d) / "e")))
            / "webgl.js"
        ).read_bytes()

    for seed in (0, 1, 2**31 - 1, 2**31, 2**32 - 1, 123456789):
        assert text(seed) == text(seed), f"seed {seed} is not deterministic"
    # masking equivalences
    assert text(0) == text(2**32)
    assert text(-1) == text(2**32 - 1)
    # and the seed genuinely reaches the output
    assert text(1) != text(2)


def test_chromium_bootstrap_is_untouched_by_the_firefox_blob_fix():
    """THE BOUNDARY THAT MATTERS MOST for the worker fix.

    ``blob_via_import_scripts`` defaults to False precisely so that every
    Chromium extension's generated text is unchanged. Chromium's sync-XHR path
    works there, and its delivery path is out of scope.
    """
    for leaf in (
        "applyWebglPatch",
        "applyGpuPatch",
        "applyAudioPatch",
        "applyLocalePatch",
        "applyNativePatch",
    ):
        default = _code_only(realm_bootstrap_js(leaf))
        # the pre-PS-78 shape: ONE combined branch, read through a sync XHR
        assert "/^blob:|^data:/i.test(s)" in default
        assert "bbody" not in default, f"{leaf}: firefox shim leaked into default"


# --- the worker fix ---------------------------------------------------------


def test_firefox_blob_workers_take_the_importscripts_shim():
    """MEASURED DEFECT (PS-78): Firefox refuses a SYNCHRONOUS XHR against a
    ``blob:`` URL — ``NetworkError: A network error occurred.`` The branch caught
    that and fell through to ``_Ref.construct(Orig, [url, options], W)``, the
    UNMODIFIED worker. Silent: the worker spawns, runs, and carries nothing.

    Read from inside a real worker before the fix, the leaf's own realm marker
    was ``undefined`` while the page's was ``true``. A page/worker mismatch is a
    SHARPER tell than no spoof at all, which is the failure worker_wrap.py's
    docstring exists to prevent.
    """
    ff = _code_only(realm_bootstrap_js("applyWebglPatch", blob_via_import_scripts=True))
    # blob: and data: must be SEPARATE branches now
    assert "/^blob:/i.test(s)" in ff
    assert "/^data:/i.test(s)" in ff
    assert "/^blob:|^data:/i.test(s)" not in ff
    # blob: uses the importScripts shim (the shape the http(s) branch uses)
    blob_branch = ff.split("/^blob:/i.test(s)", 1)[1].split("/^data:/i.test(s)", 1)[0]
    assert "importScripts" in blob_branch
    assert "_XHR" not in blob_branch, "blob: must not use the broken sync XHR"


def test_data_workers_deliberately_keep_the_old_path():
    """THE PART THAT MUST NOT BE 'TIDIED UP' INTO ONE BRANCH.

    ``importScripts`` against a ``data:`` URL does not merely fail on this
    engine, it HANGS — measured: the worker never reaches its first message.
    Routing data: through the shim would trade a silent leak for a BROKEN
    WORKER, i.e. a functional break on any site that uses one.

    An unspoofed data: worker is a defect; a data: worker that never runs is a
    worse one. This seat is what stops the two branches being merged back.
    """
    ff = _code_only(realm_bootstrap_js("applyWebglPatch", blob_via_import_scripts=True))
    data_branch = ff.split("/^data:/i.test(s)", 1)[1].split("} catch (e)", 1)[0]
    assert "_XHR" in data_branch, "data: must keep the read-and-re-blob attempt"
    assert "importScripts" not in data_branch, (
        "importScripts against a data: URL HANGS the worker on Firefox — "
        "this branch must not be merged with the blob: one"
    )


def test_the_firefox_webgl_script_opts_into_the_worker_fix():
    """The wiring: the Firefox script must actually REQUEST the fixed bootstrap,
    or the worker realm silently keeps reading the real GPU."""
    js = _code_only(firefox_webgl_init_script(5))
    assert "/^blob:/i.test(s)" in js
    assert "/^blob:|^data:/i.test(s)" not in js
