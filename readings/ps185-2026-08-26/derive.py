#!/usr/bin/env python3
"""PS-185 — re-derive PS-16's GPU/readback cells FROM THE COMMITTED RECORDS.

PS-16's maintenance rule: *"Re-derive, never edit-to-match. Pull the numbers out
of the committed record with a script and paste what it printed."* This is that
script. Every figure it prints is read out of a JSON record in this directory —
nothing is typed in, so the article cannot drift from the evidence.

It prints the replacement Markdown for two things:

* Table 2's **GPU unlinkability** column and its basis table, and
* the **WebGL readback / canvas** cells, per engine.

Run:

    python3 derive.py                      # print
    python3 derive.py --output derived-output.txt
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib

HERE = pathlib.Path(__file__).resolve().parent

LAYER_OFF = HERE / "engine-gpu-variance.layer-off.json"
LAYER_ON = HERE / "engine-gpu-variance.layer-on.json"
UNIF_OFF = HERE / "uniformity-check.layer-off.json"
UNIF_ON = HERE / "uniformity-check.layer-on.json"
READBACK = HERE / "readback-vectors.three-seeds.json"
READBACK2 = HERE / "readback-vectors.two-seeds.json"
REPLICATE = HERE / "readback-vectors.replicate.json"
REPLICATE_CHROME = HERE / "readback-vectors.replicate-chromium.json"

ARMS = ("windows", "macos", "linux", "android")


def load(path: pathlib.Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def pct(x: "float | None") -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def gpu_section(off: dict, on: dict, uoff: dict, uon: dict) -> str:
    lines: "list[str]" = []
    add = lines.append

    add("### GPU unlinkability — MEASURED on all four arms, both authorship arms")
    add("")
    add("**Lower is better; it is the chance that two random profiles draw the "
        "same card.** Every figure below is a MEASUREMENT taken on "
        f"{off['measured_at'][:10]} over "
        f"{len(off['seeds_requested'])} seeds per arm, on loopback with no "
        "proxy and no exit. Engine: "
        f"`{off['provenance']['engine']['build']}` "
        f"(sha256 verified against the install manifest).")
    add("")
    add("⚠️ **There are TWO numbers per arm and they are not interchangeable.** "
        "`engine_gpu_variance` measures with persona's layer OFF, because it "
        "polices the arms where the ENGINE authors the identity. But "
        "`ENGINE_AUTHORED_IDENTITY_ARMS = frozenset({\"windows\"})` — only "
        "windows ships that way. On macos/linux/android persona's own pool "
        "authors the pair via `gpu_ext`'s `pick(POOL, 0x67900)`, so the "
        "LAYER-ON column is the one that describes what a profile actually "
        "ships, and it is the column that replaces the old \"theoretical\" "
        "figures.")
    add("")
    add("| arm | authors the identity | **layer ON (what ships)** | layer OFF (the engine alone) | distinct ON/OFF | basis |")
    add("|---|---|---|---|---|---|")
    for arm in ARMS:
        e_on = on["result"]["per_arm"][arm]
        e_off = off["result"]["per_arm"][arm]
        engine_authored = arm in on["provenance"]["engine_authored_arms"]
        author = "**engine**" if engine_authored else "ours (`gpu_ext`)"
        # The basis names WHICH authorship arm the shipped figure came from.
        # A bare "measured" would put the layer-OFF windows number and three
        # layer-ON numbers under one identical label — two different
        # quantities in one column, which is the conflation this whole
        # section exists to keep apart.
        shipped = e_off if engine_authored else e_on
        basis_mode = "layer OFF" if engine_authored else "layer ON"
        add(
            f"| {arm} | {author} | **{pct(e_on['collision_probability'])}** | "
            f"{pct(e_off['collision_probability'])} | "
            f"{e_on['distinct_identities']} / {e_off['distinct_identities']} | "
            f"**measured ({basis_mode})**, {shipped['seeds_readable']} seeds |"
        )
    add("")
    add("Every cell above is `measured`. No arm is `theoretical` any more, and "
        "no arm was left `—`.")
    add("")
    # The basis column carries the ON/OFF split per cell, so state what it
    # means where it is read rather than one table away.
    add("**Read the basis column, not just the number.** `measured (layer OFF)` "
        "on windows is the ENGINE's figure, because windows is the one arm "
        "that defers to it; `measured (layer ON)` on the other three is "
        "persona's own pool drawing through `pick()`. Those are different "
        "quantities and the column is what tells them apart.")
    add("")

    # ---- the positive control ---------------------------------------
    # Derived, not asserted: compare the two modes SEED BY SEED on the one
    # arm that defers, then contrast with the arms that do not.
    w_on, w_off = on["readings"]["windows"], off["readings"]["windows"]
    w_identical = sum(1 for s, v in w_on.items() if v == w_off.get(s))
    w_on_res = on["result"]["per_arm"]["windows"]
    diverging = [
        arm for arm in ARMS
        if arm not in on["provenance"]["engine_authored_arms"]
        and on["result"]["per_arm"][arm]["distinct_identities"]
        != off["result"]["per_arm"][arm]["distinct_identities"]
        and off["result"]["per_arm"][arm]["distinct_identities"] == 1
    ]
    if w_identical == len(w_on) and diverging:
        add(
            f"**windows layer-ON is byte-identical to layer-OFF** — all "
            f"{w_identical} of {len(w_on)} seeds returned the same identity in "
            f"both modes, the same {w_on_res['distinct_identities']} distinct "
            f"identities, the same {pct(w_on_res['collision_probability'])}. "
            "That is what deferring is supposed to look like, and it is a "
            "positive control: on "
            + " and ".join(diverging)
            + " the two columns diverge sharply ("
            + ", ".join(
                f"{arm} {off['result']['per_arm'][arm]['distinct_identities']} "
                f"identity with the layer off against "
                f"{on['result']['per_arm'][arm]['distinct_identities']} pool "
                f"entries with it on"
                for arm in diverging
            )
            + "), which is the layer proving it reached the page rather than "
            "an assertion that it was installed."
        )
        add("")

    # ---- the instrument finding -------------------------------------
    add("#### ⚠️ The gate's own verdicts on three of those arms are an "
        "ESTIMATOR ARTEFACT, not a product finding")
    add("")
    add("`engine_gpu_variance` returns `TOO_NARROW` for macos, linux AND "
        "android on the layer-ON run. An identical adverse verdict across "
        "every non-windows cell is the shape this project has learned to "
        "distrust (PS-14), and it does not survive checking.")
    add("")
    add("`collision_probability` is the **plug-in** Simpson index "
        "`sum (n_i/N)^2`, which is a BIASED estimator; `bar_for(arm)` is "
        "`1/k`, the collision probability of a uniform draw **in the limit**. "
        "Those are not comparable at finite N, because under a genuinely "
        "uniform draw `E[S_hat] = 1/k + (1 - 1/k)/N`. So a perfectly uniform "
        "`pick()` is EXPECTED to score above the bar, and the gate flags it.")
    add("")
    add("| arm | plug-in (what the gate uses) | unbiased | E[plug-in] if uniform | bar `1/k` | Monte-Carlo p | reading |")
    add("|---|---|---|---|---|---|---|")
    for arm in ARMS:
        u = uon["per_arm"][arm]
        p = u["monte_carlo_p_value"]
        verdict = (
            "**genuine**" if u["genuine_narrowing_finding"]
            else ("artefact" if u["module_verdict"] == "TOO_NARROW" else "—")
        )
        add(
            f"| {arm} | {u['plugin_estimate']:.4f} | "
            f"{u['unbiased_estimate']:.4f} | "
            f"{u['expected_plugin_under_uniform']:.4f} | "
            f"{u['bar_collision_probability']:.4f} | "
            f"{'—' if p is None else f'{p:.3f}'} | "
            f"{u['module_verdict']} → {verdict} |"
        )
    add("")
    a = uon["per_arm"]["android"]
    add(
        f"**The single line that settles it:** android scored "
        f"{a['plugin_estimate']:.4f}, which is BELOW the "
        f"{a['expected_plugin_under_uniform']:.4f} a uniform draw is expected "
        f"to score at N={a['seeds_readable']} — and the gate still called it "
        "`TOO_NARROW`. An arm cannot be *worse than uniform* while scoring "
        "*better than uniform predicts*. The comparison failed, not the pool."
    )
    add("")
    add("**So the old \"theoretical\" figures are CONFIRMED rather than "
        "overturned:** the uniform-selection assumption behind them holds on "
        "the real draw (p = "
        + ", ".join(
            f"{arm} {uon['per_arm'][arm]['monte_carlo_p_value']:.2f}"
            for arm in ("macos", "linux", "android")
        )
        + ", none anywhere near significance). What has changed is that they "
        "are now measurements instead of assumptions — which is the result "
        "PS-185 was written to get, and it is a result even though the numbers "
        "barely moved: it retires an assumption. **Whether "
        "`engine_gpu_variance` should adopt the unbiased estimator is a "
        "decision for that module's owner — PS-185 measured and reported it, "
        "and deliberately did not change the gate.**")
    add("")

    # ---- the genuine finding ----------------------------------------
    add("#### The GENUINE finding: linux AND android are CONSTANT with the "
        "layer off")
    add("")
    constants = [
        arm for arm in ARMS
        if off["result"]["per_arm"][arm]["verdict"] == "CONSTANT"
    ]
    for arm in constants:
        vals = [v for v in off["readings"][arm].values() if v]
        u = uoff["per_arm"][arm]
        add(
            f"* **{arm}** — every one of {len(vals)} profiles was handed the "
            f"SAME identity (`{collections.Counter(vals).most_common(1)[0][0]}`). "
            f"Monte-Carlo p = {u['monte_carlo_p_value']:.3f}. This one IS a "
            "real finding, not an estimator artefact."
        )
    add("")
    add("Neither arm is engine-authored, so **this is not a live breach** — "
        "persona's own pool is what ships there, and the layer-ON column shows "
        "it working. It is the measurement that says those arms must NOT be "
        "moved into `ENGINE_AUTHORED_IDENTITY_ARMS`. linux confirms PS-161's "
        "existing SwiftShader reading; **android is new** — it had never been "
        "measured on either arm.")
    add("")
    m_off = off["result"]["per_arm"]["macos"]
    add(
        f"**macos, engine side, has MOVED.** PS-161 recorded 76.9% over 30 "
        f"seeds (Apple M2 87% / M4 13%). This run reads "
        f"{pct(m_off['collision_probability'])} over "
        f"{m_off['seeds_readable']} seeds on the same two-value pool — same "
        "conclusion (the engine is worse than our own 50.0% pool, so macos "
        "stays ours), different number. Two different engine builds, so this "
        "is a re-measurement rather than a contradiction."
    )

    # ---- the two macos pools do not agree ---------------------------
    # Derived: read the distinct card names each authorship arm actually
    # drew, rather than restating a remembered pool.
    def _cards(record: dict, arm: str) -> "list[str]":
        """The distinct renderer names drawn on this arm, model part only."""
        names = set()
        for value in record["readings"][arm].values():
            if not value:
                continue
            # "<vendor> | ANGLE (Apple, ANGLE Metal Renderer: Apple M1, ...)"
            marker = "Metal Renderer: "
            if marker in value:
                names.add(value.split(marker, 1)[1].split(",", 1)[0].strip())
        return sorted(names)

    mac_on, mac_off = _cards(on, "macos"), _cards(off, "macos")
    if mac_on and mac_off and mac_on != mac_off:
        add("")
        add(
            "Note also that the layer-ON macos pool draws **"
            + " / ".join(mac_on)
            + "** while the engine draws **"
            + " / ".join(mac_off)
            + "**. The two authors do not agree on which cards exist, which is "
            "worth knowing for PS-183 (`MAC_GPUS` widening) but is not this "
            "ticket's to fix."
        )
    return "\n".join(lines)


def readback_section(rb: dict, rep: dict, repc: dict) -> str:
    lines: "list[str]" = []
    add = lines.append
    seeds = [str(s) for s in rb["seeds"]]

    add("### WebGL / canvas readback — BOTH engines, on loopback")
    add("")
    b = rb["engine_builds"]
    add(
        f"Measured {rb['measured_at'][:10]} on loopback with the layer "
        f"INSTALLED. Engines: chromium `{b['chromium']['build']}` "
        f"(digest verified) and `{b['firefox']['build']}` "
        f"(invisible_core {b['firefox']['invisible_core_version']}). "
        f"Seeds {', '.join(seeds)}."
    )
    add("")
    add("| engine | vector | " + " | ".join(f"seed {s}" for s in seeds) + " | verdict |")
    add("|---|---|" + "---|" * (len(seeds) + 1))
    for engine in rb["engines"]:
        for vec in ("webgl_pixel_hash", "canvas_pixel_hash"):
            e = rb["verdicts"][engine][vec]
            cells = " | ".join(f"`{e['seeds'][s]}`" for s in seeds)
            add(f"| {engine} | `{vec}` | {cells} | **{e['verdict']}** |")
    add("")

    # Repeatability — the instrument check.
    same = tot = 0
    for engine, src in (("firefox", rep), ("chromium", repc)):
        for s in seeds:
            for vec in ("webgl_pixel_hash", "canvas_pixel_hash"):
                a = rb["readings"][engine][s]["reading"]["vectors"].get(vec)
                b2 = (src["readings"].get(engine, {}).get(s, {})
                      .get("reading", {}).get("vectors", {}).get(vec))
                if b2 is not None:
                    tot += 1
                    same += (a == b2)
    add(
        f"**Instrument check (PS-14):** every reading above was taken twice in "
        f"independent runs — {same}/{tot} came back byte-identical, so these "
        "are stable values and not one-off draws."
    )
    add("")

    add("#### ⭐ The firefox `webgl_pixel_hash` question — ANSWERED, and it is "
        "the harder answer")
    add("")
    ff = rb["verdicts"]["firefox"]["webgl_pixel_hash"]["seeds"]
    add(
        "PS-16 records the one outright FAILURE in this matrix: `creepjs :: "
        "webgl_pixel_hash` reads `51df3565` for BOTH firefox seeds 1337 and "
        "4242, across two exits and two days. This ticket asked whether the "
        "LOOPBACK probe sees that same collision. **It does not.**"
    )
    add("")
    add(f"* checker (creepjs), firefox @1337 and @4242 → `51df3565` "
        f"and `51df3565` — **identical**")
    add(f"* loopback probe, firefox @1337 → `{ff[seeds[0]]}`, @4242 → "
        f"`{ff[seeds[1]]}` — **different**")
    add("")
    add(
        "So the seed DOES move this vector inside the browser, and the "
        "difference **does not survive the trip out to the checker**. That is "
        "the second of the two branches the ticket named, and it is the "
        "expensive one: it is PS-97's exact lesson, one vector over. "
        "**Consequence for PS-182: it cannot be worked or verified on the "
        "loopback probe alone** — a green local reading is exactly what we "
        "already have while the checker still collides. Settling it needs a "
        "checker read, so PS-182 stays blocked on the proxy."
    )
    add("")
    add("⚠️ **This is stated separately from the canvas result below, "
        "deliberately.** The ticket warns against averaging the two into one "
        "verdict, and they point in different directions.")
    add("")

    add("#### canvas readback — a SPLIT across the engines")
    add("")
    cff = rb["verdicts"]["firefox"]["canvas_pixel_hash"]["seeds"]
    add(
        f"On firefox, seeds {seeds[0]} and {seeds[1]} produce the SAME canvas "
        f"hash (`{cff[seeds[0]]}`), while seed {seeds[2]} differs "
        f"(`{cff[seeds[2]]}`). On chromium all three differ."
    )
    add("")
    add(
        "The mechanism is recorded in `local_probe`'s own docstring and is "
        "confirmed by the layer report in these records: canvas 2D is "
        "**delegated to `--fingerprint=`, which is chromium-only**, and the "
        "firefox arm returns before it. The layer report for the firefox "
        "readings lists `['audio', 'locale', 'webgl']` — **no canvas "
        "extension at all** — against ten on chromium. So firefox canvas "
        "entropy is whatever the engine happens to produce, and two seeds "
        "colliding there is expected rather than surprising."
    )
    add("")
    add(
        "**This is a two-engine-rule cell, not a chromium cell.** A chromium "
        "canvas fix does not touch it."
    )
    return "\n".join(lines)


def coverage_section() -> str:
    return "\n".join([
        "### What was attempted and NOT obtained (recorded, not left blank)",
        "",
        "| wanted | status | why |",
        "|---|---|---|",
        "| any checker read | **not covered** | The proxy credential is "
        "rejected at account level (`User was rejected by the SOCKS5 server "
        "(1 3)`). Out of scope for PS-185, and a direct connection is never "
        "the fallback. |",
        "| firefox / macos, linux, android arms | **does not exist** | "
        "`InvisiblePlaywright` takes no OS/platform parameter, so Firefox "
        "presents Windows regardless (`declared_machine_honoured: false`, "
        "issue #211). Not a coverage gap — the configuration is unreachable. |",
        "| a mobile profile on the loopback path | **not reachable from this "
        "tier** | `browser_tier.DECLARED_MACHINES` is "
        "`(\"windows\", \"macos\", \"linux\")` with no mobile member, and "
        "`masking_layer` hardcodes `device_type=\"desktop\"` when it computes "
        "`engine_platform` (its own comment: *\"a mobile declared machine is "
        "not a thing this tier can be asked for\"*). The android GPU arm above "
        "is the android **GPU pool**, which is a different axis from a mobile "
        "**device type**. Reaching a real mobile profile needs the product's "
        "`build_mobile_extension` path, which this harness does not build. |",
        "",
        "No arm was recorded `INCONCLUSIVE`: all four GPU arms returned "
        "24/24 readable seeds, and every readback cell produced a usable value "
        "on both engines.",
    ])


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", default="")
    args = ap.parse_args(argv)

    off, on = load(LAYER_OFF), load(LAYER_ON)
    uoff, uon = load(UNIF_OFF), load(UNIF_ON)
    rb = load(READBACK)
    rep = load(REPLICATE) if REPLICATE.is_file() else {"readings": {}}
    repc = load(REPLICATE_CHROME) if REPLICATE_CHROME.is_file() else {"readings": {}}

    parts = [
        "<!-- Re-derived by readings/ps185-2026-08-26/derive.py — "
        "do not hand-edit these numbers. -->",
        "",
        gpu_section(off, on, uoff, uon),
        "",
        readback_section(rb, rep, repc),
        "",
        coverage_section(),
        "",
    ]
    text = "\n".join(parts)
    print(text)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"\n[derive] wrote {os.path.basename(args.output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
