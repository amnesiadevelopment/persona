"""PS-344: the published-engine verdict, and the guard that must be able to go RED.

WHAT THESE TESTS ARE FOR
────────────────────────
`scripts/ps344_verdict.py` decides one thing: are the fingerprint patches
present and functioning in the binary an operator downloads? The measured run
against the real published `personium-152.0.7977.75` AppImage answers "yes" and
exits 0 — and a guard that has only ever been seen to pass is not known to work.

So the load-bearing tests here are the **red** ones. Each takes a synthetic
reading pair the verdict correctly calls LIVE, breaks exactly one thing, and
asserts the verdict goes ABSENT with exit 1 *and names the vector it broke*. A
test that asserted only the exit code would pass for the wrong reason — that is
the lesson PS-343's own suite records ("two of these tests originally checked
`run(...) == 2` and passed even with the fix they guard reverted"), so every
assertion here is about a named row.

WHY THE FIXTURES ARE SYNTHESISED, NOT DOWNLOADED
─────────────────────────────────────────────────
The real arms are a 202 MB AppImage and a 194 MB stock Chrome, launched under
Xvfb for four cells each. A test suite that needs 400 MB and a browser to have
an opinion is a test suite that goes quiet the day CI has neither. The
*measured* run is recorded in `readings/ps344-2026-09-07/` and reproduced by the
commands in its REPORT.md; what is pinned HERE is the decision logic, on
readings shaped exactly as the harness emits them.

THE ONE TEST THAT IS NOT A PARAPHRASE OF THE OTHERS
────────────────────────────────────────────────────
`test_partial_leak_one_realm_one_seed_is_caught` guards a defect that was real
and was found by sabotage, not by prediction. `ps301_compare` scores a realm's
`differs_from_control` with `any()` across seeds, so a realm leaking the host's
GPU on ONE seed while spoofing it on the other keeps `all_realms_differ` true —
and the verdict, leaning on that aggregate, reported PATCHES LIVE on a
sabotaged reading. That is precisely the shape of a partial engine-level realm
leak. `_every_realm_and_seed_differs` was added for it; this test is why it
cannot be simplified back.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "ps344_verdict.py"

REALMS = (
    "page",
    "iframe_same_origin",
    "iframe_about_blank",
    "iframe_srcdoc",
    "worker_blob",
    "worker_in_iframe",
    "worker_nested",
)
DOM_REALMS = REALMS[:4]
SEEDS = (24601, 5150)


def _load():
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    spec = importlib.util.spec_from_file_location("ps344_verdict", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures: readings in the shape scripts/ps344_launch_published.py emits.
# ---------------------------------------------------------------------------


def _realm_body(*, seed: int, patched: bool, has_dom: bool) -> dict:
    """One realm's reading. `patched` selects the product or the stock values."""
    if patched:
        gpu_renderer = f"ANGLE (NVIDIA Corporation, NVIDIA GeForce RTX {seed}, OpenGL 4.5.0)"
        gpu_vendor = "Google Inc. (NVIDIA Corporation)"
        hc = 18 if seed == 24601 else 12
        tz, tzoff = "America/Chicago", 300
        webdriver = False
        readpixels = 1000 + seed
        getimagedata = 2000 + seed
        todataurl = 3000 + seed
        rect_x = 8.0 - seed / 1e9
    else:
        gpu_renderer = "ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device), SwiftShader driver)"
        gpu_vendor = "Google Inc. (Google)"
        hc = 8
        tz, tzoff = "UTC", 0
        webdriver = True
        readpixels = 555
        getimagedata = 666
        todataurl = 777
        rect_x = 8.0

    body: dict = {
        "webgl": {
            "available": True,
            "unmasked_vendor": gpu_vendor,
            "unmasked_renderer": gpu_renderer,
            "masked_vendor": "WebKit",
            "masked_renderer": "WebKit WebGL",
            "readpixels_hash": readpixels,
        },
        "canvas": {
            "getimagedata_hash": getimagedata,
            "measuretext_width": 172.1083984375,
        },
        "switches": {
            "hardware_concurrency": hc,
            "platform": "Linux x86_64",
            "user_agent": "Mozilla/5.0 (X11; Linux x86_64)",
            "uad_platform": "Linux",
            "timezone": tz,
            "tz_offset_minutes": tzoff,
            "device_pixel_ratio": 1,
        },
    }
    if has_dom:
        body["switches"]["webdriver"] = webdriver
        body["switches"]["screen_width"] = 800
        body["switches"]["screen_height"] = 600
        body["canvas"]["todataurl_hash"] = todataurl
        # Never seed-dependent: patch 014 Offsets rather than Scales, and the
        # exempt shape is exempt in both arms. These are the negative controls.
        body["client_rects"] = {"x": rect_x, "y": 41.0, "width": 135.375}
        body["client_rects_exempt"] = {"x": 13.296875, "width": 111.6875}
    else:
        body["canvas"]["todataurl"] = "n/a-in-this-realm"
        body["client_rects"] = {"note": "no DOM in this realm"}
        body["client_rects_exempt"] = {"note": "no DOM in this realm"}
    return body


def _arm(*, engine_id: str, patched: bool, label: str = "") -> dict:
    records = []
    for seed in SEEDS:
        for layer in ("on", "off"):
            records.append(
                {
                    "engine": engine_id,
                    "arm": "linux",
                    "seed": seed,
                    "masking_layer": layer,
                    "layer_installed": ["gpu"] if layer == "on" else [],
                    "reading": {
                        "realms": {
                            r: _realm_body(
                                seed=seed, patched=patched, has_dom=r in DOM_REALMS
                            )
                            for r in REALMS
                        }
                    },
                }
            )
    return {
        "ticket": "PS-344",
        "engine_id": engine_id,
        "engine_label": label or engine_id,
        "binary": {
            "sha256": "0" * 64 if patched else "1" * 64,
            "version_string": "Chromium 152.0.7977.75"
            if patched
            else "Google Chrome for Testing 152.0.7977.75",
        },
        "records": records,
    }


@pytest.fixture()
def arms(tmp_path):
    """A directory holding a PRODUCT arm and a CONTROL arm the verdict calls LIVE."""

    def _write(product=None, control=None, name="readings-published-152.json"):
        (tmp_path / name).write_text(
            json.dumps(product or _arm(engine_id="published-152", patched=True)),
            encoding="utf-8",
        )
        (tmp_path / "readings-stock-cft-152.json").write_text(
            json.dumps(control or _arm(engine_id="stock-cft-152", patched=False)),
            encoding="utf-8",
        )
        return tmp_path

    return _write


def _run(mod, directory, product="readings-published-152.json", extra=()):
    return mod.main(["--dir", str(directory), "--product", product, *extra])


# ---------------------------------------------------------------------------
# The GREEN arm — asserted first, because every red test below is only
# meaningful against a baseline the verdict genuinely passes.
# ---------------------------------------------------------------------------


def test_a_fully_patched_engine_is_reported_live(arms, capsys):
    mod = _load()
    assert _run(mod, arms()) == 0
    assert "PATCHES LIVE" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The RED arms. Each breaks ONE thing and must name it.
# ---------------------------------------------------------------------------


def test_stock_as_product_is_reported_absent(arms, capsys):
    """The ticket's non-waivable falsification, in miniature.

    The stock control labelled as the product must come out ABSENT — every
    required signal, not merely some of them.
    """
    mod = _load()
    stock_as_product = _arm(engine_id="stock-as-product", patched=False)
    d = arms(product=stock_as_product)
    assert _run(mod, d) == 1
    out = capsys.readouterr().out
    assert "PATCHES ABSENT" in out
    assert f"{len(mod.REQUIRED)} of {len(mod.REQUIRED)} required signals" in out


def test_stock_as_product_satisfies_expect_absent(arms):
    """`--expect absent` inverts the code so CI can DEMAND the red arm.

    Without it a runner has to negate an exit status by hand, which is exactly
    where a "|| true" creeps in and the falsification silently stops being run.
    """
    mod = _load()
    d = arms(product=_arm(engine_id="stock-as-product", patched=False))
    assert _run(mod, d, extra=("--expect", "absent")) == 0
    # And the same flag must FAIL when the arm does not match its expectation.
    assert _run(mod, arms(), extra=("--expect", "absent")) == 1


def test_partial_leak_one_realm_one_seed_is_caught(arms, capsys):
    """THE test this file exists for — see the module docstring.

    One realm, ONE seed, leaking the host's real GPU. `all_realms_differ` stays
    true because ps301_compare scores a realm with `any()` across seeds, so the
    aggregate cannot see this. The verdict must, and must name the realm.
    """
    mod = _load()
    product = _arm(engine_id="published-152", patched=True)
    stock_gpu = _realm_body(seed=24601, patched=False, has_dom=False)["webgl"][
        "unmasked_renderer"
    ]
    for rec in product["records"]:
        if rec["seed"] == 24601 and rec["masking_layer"] == "off":
            rec["reading"]["realms"]["worker_nested"]["webgl"][
                "unmasked_renderer"
            ] = stock_gpu

    assert _run(mod, arms(product=product)) == 1
    out = capsys.readouterr().out
    assert "PATCHES ABSENT" in out
    assert "webgl.unmasked_renderer" in out
    assert "worker_nested" in out, "the leaking realm must be NAMED, not just counted"


def test_a_constant_fake_is_not_accepted_as_a_spoof(arms, capsys):
    """Differing from stock is not enough — a seed-derived vector must MOVE.

    A patch returning one fixed fake value and a patch deriving a value from the
    seed both "differ from stock"; only the second is the product's contract,
    and a single-seed record cannot tell them apart.
    """
    mod = _load()
    product = _arm(engine_id="published-152", patched=True)
    frozen = None
    for rec in product["records"]:
        if rec["masking_layer"] != "off":
            continue
        for r in REALMS:
            body = rec["reading"]["realms"][r]["canvas"]
            frozen = frozen if frozen is not None else body["getimagedata_hash"]
            body["getimagedata_hash"] = frozen

    assert _run(mod, arms(product=product)) == 1
    out = capsys.readouterr().out
    assert "canvas.getimagedata_hash" in out
    assert "does not move with the seed" in out


def test_a_moving_negative_control_condemns_the_run(arms, capsys):
    """A run where EVERYTHING differs is a broken instrument, not a good engine.

    patch 014 calls Offset(), never Scale(), so a rect's WIDTH must read
    identical to stock. If it moves, the reading is not trustworthy and no
    amount of green on the required rows rescues it.
    """
    mod = _load()
    product = _arm(engine_id="published-152", patched=True)
    for rec in product["records"]:
        for r in DOM_REALMS:
            rec["reading"]["realms"][r]["client_rects"]["width"] = 999.0

    assert _run(mod, arms(product=product)) == 1
    out = capsys.readouterr().out
    assert "INSTRUMENT SUSPECT" in out
    assert "client_rects.width" in out


def test_layer_on_differences_cannot_rescue_a_stock_engine(arms, capsys):
    """Every signal is read layer-OFF, and this is what that buys.

    With persona's JS masking layer ON, a difference from stock could be the
    extension rather than the engine — so an unpatched engine wearing a working
    layer must STILL be reported absent. This is the reading that would
    otherwise hide an unpatched shipped binary behind our own JS.
    """
    mod = _load()
    product = _arm(engine_id="stock-with-layer", patched=False)
    for rec in product["records"]:
        if rec["masking_layer"] != "on":
            continue
        for r in REALMS:
            rec["reading"]["realms"][r]["webgl"]["unmasked_renderer"] = "SPOOFED BY JS"
            rec["reading"]["realms"][r]["switches"]["hardware_concurrency"] = 18

    assert _run(mod, arms(product=product)) == 1
    assert "PATCHES ABSENT" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The third status. "Could not measure" and "fine" are different answers.
# ---------------------------------------------------------------------------


def test_a_failed_cell_is_indeterminate_not_a_pass(arms, capsys):
    mod = _load()
    product = _arm(engine_id="published-152", patched=True)
    product["records"][0] = {
        "seed": 24601,
        "masking_layer": "on",
        "error": "ChromiumUnavailable: engine never published a CDP port",
        "reading": None,
    }
    assert _run(mod, arms(product=product)) == 2
    out = capsys.readouterr().out
    assert "INDETERMINATE" in out
    assert "NOT a pass" in out


def test_a_timed_out_realm_is_indeterminate_not_a_pass(arms, capsys):
    """An absent realm and a realm that could not be read are different findings.

    Collapsing them is how a broken realm reads as a realm that agreed.
    """
    mod = _load()
    product = _arm(engine_id="published-152", patched=True)
    product["records"][1]["reading"]["realms"]["worker_nested"] = {"error": "timeout"}
    assert _run(mod, arms(product=product)) == 2
    out = capsys.readouterr().out
    assert "INDETERMINATE" in out
    assert "worker_nested" in out


def test_a_missing_arm_is_indeterminate_not_a_pass(tmp_path, capsys):
    mod = _load()
    (tmp_path / "readings-published-152.json").write_text(
        json.dumps(_arm(engine_id="published-152", patched=True)),
        encoding="utf-8",
    )
    # No control arm written at all.
    assert mod.main(["--dir", str(tmp_path)]) == 2
    assert "INDETERMINATE" in capsys.readouterr().out


def test_required_signals_cover_the_patches_the_report_claims(arms):
    """A guard whose required set silently shrank would go green on less.

    Pinned by NAME rather than by count, so deleting one row and adding another
    cannot keep the total looking right.
    """
    mod = _load()
    vectors = {v for v, _, _, _ in mod.REQUIRED}
    assert vectors == {
        "switches.hardware_concurrency",
        "switches.timezone",
        "switches.tz_offset_minutes",
        "switches.webdriver",
        "webgl.unmasked_vendor",
        "webgl.unmasked_renderer",
        "webgl.readpixels_hash",
        "canvas.getimagedata_hash",
        "canvas.todataurl_hash",
        "client_rects.x",
    }
    assert {v for v, _ in mod.NEGATIVE_CONTROLS} == {
        "client_rects.width",
        "client_rects_exempt.x",
    }
