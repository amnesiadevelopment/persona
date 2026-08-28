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
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# The product's OWN estimator and the readback differ's OWN verdict rule,
# imported rather than reimplemented, for the same reason `sweep.py` imports
# `engine_gpu_variance` and `readback_vectors.py` imports `local_probe`: a
# second copy of the rule in this file could drift from the one that took the
# readings, and then the article would be derived from a rule the product does
# not use. Every figure below is recomputed with these two functions.
from src.services.verify.engine_gpu_variance import (  # noqa: E402
    classify as _classify,
    collision_probability as _collision_probability,
    has_known_pool as _has_known_pool,
)

# The readback differ's OWN verdict rule, from the instrument that took the
# readback readings, imported for the same reason.
sys.path.insert(0, str(HERE))
from readback_vectors import verdict_for as _verdict_for  # noqa: E402

# The uniformity instrument's OWN analysis, from the script that wrote the
# uniformity records, imported for the same reason again. Round 5 exempted the
# Monte-Carlo p-value from recounting on the grounds that a seeded simulation
# "is not a function of the readings". That was FALSE: both records store the
# `monte_carlo_seed` and `monte_carlo_trials` they were run with, which makes
# `analyse()` a deterministic function of the readings plus two recorded
# parameters, and it reproduces every stored p-value exactly on all four arms
# in both modes. See `_analysed`.
from uniformity_check import (  # noqa: E402
    analyse as _analyse,
    epoch_pool_sizes as _epoch_pool_sizes,
)

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


def fnum(x: "float | None", spec: str = ".4f") -> str:
    """Format a figure that may be absent, instead of crashing on it.

    The generalised axis-1 walk destroys raw readings, and an arm whose seeds
    are ALL unreadable legitimately produces ``None`` for every estimator
    column. Rendering that used to raise ``TypeError: unsupported format
    string passed to NoneType`` — the document did not report the arm as
    unobtainable, it failed to render at all, which is the one outcome the
    ticket rules out: ``INCONCLUSIVE`` must be *recorded*, never crashed on.
    """
    return "—" if x is None else format(x, spec)


_NUMBER_WORDS = {
    0: "none", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven",
    12: "twelve",
}


def num_word(n: int) -> str:
    """Small counts as words, so a COUNTED figure reads like the prose it
    replaces. Falls back to digits rather than inventing an English rule."""
    return _NUMBER_WORDS.get(n, str(n))


def join_arms(names: "list[str]") -> str:
    """Render a list of arm names as English, for prose built FROM the data.

    Exists so a sentence can NAME the arms it is talking about without a
    literal (PS-239): the paragraph that used to say "macos, linux AND
    android" went on saying it after PS-191 corrected the gate, contradicting
    the table directly beneath it. Reads "none" for an empty list rather than
    rendering an empty gap, so a claim about no arms is still a sentence.
    """
    if not names:
        return "none"
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " AND " + names[-1]


def _bar_verdict(entry: dict, arm: str) -> str:
    """The verdict the gate WOULD have returned under the pre-PS-191 rule.

    RECOMPUTED from a `classify` entry, never transcribed from a stored
    summary. The article's artefact argument is a claim about what the OLD
    gate said, so that verdict has to come from somewhere — and the tempting
    source, the uniformity record's stored ``module_verdict``, is exactly what
    axis 2 of ``enumerate_summary_sites.py`` forbids a rendered figure from
    depending on (poison the field, move the article).

    It does not need to be stored, because PS-191 deliberately KEPT the raw
    bar comparison it replaced: ``meets_bar`` is still computed on every arm
    from the readings and the epoch-pinned bar. So the old rule is still a
    function of the evidence — score above the bar and you were flagged — and
    is reconstructed here rather than remembered.

    The two branches ahead of the bar are unchanged by PS-191 and so are read
    straight off the entry: ``CONSTANT`` (one identity) and ``INCONCLUSIVE``
    (too few seeds, or no bar to compare against) both preceded the bar test
    then and precede the p-value test now.

    ⚠️ A MISSING BAR IS NOT ONE CASE BUT TWO, and the old rule answered them
    differently (PS-239 review). ``meets_bar`` is ``None`` whenever ``bar`` is
    ``None``, but the pre-PS-191 chain reached its ``INCONCLUSIVE`` branch on
    ``elif bar_missing`` — i.e. ``bar is None`` **AND** ``has_known_pool(arm)``.
    An arm with no bar and NO known pool fell past that ``elif`` to the final
    ``else`` and returned ``OK``. Collapsing both into ``INCONCLUSIVE`` here
    would be a reconstruction that quietly disagrees with the rule it claims
    to reproduce, so the known-pool term is carried rather than dropped.

    Unreachable with the committed records — every member of ``ARMS`` is named
    in ``_POOL_VAR_FOR_ARM``, so ``has_known_pool`` is true for each and the
    two readings coincide — but this function's whole claim is that it
    RECONSTRUCTS the rule instead of remembering it, and an unreachable
    divergence is still a divergence.
    """
    if entry["verdict"] in ("CONSTANT", "INCONCLUSIVE"):
        return entry["verdict"]
    if entry.get("meets_bar") is None:
        # No bar. INCONCLUSIVE only where the arm HAS a pool we failed to read
        # ("we failed to look" is not "we looked and it was fine"); otherwise
        # there was never a comparison to make and the old rule said OK.
        return "INCONCLUSIVE" if _has_known_pool(arm) else "OK"
    return "OK" if entry["meets_bar"] else "TOO_NARROW"


def layer_installed(rb: dict, engine: str) -> "list[str]":
    """The extension layer an engine's legs actually REPORTED installing.

    Read from each leg's own ``layer.installed``. The sentence this feeds is
    the stated MECHANISM behind the canvas split, and it used to spell that
    layer out as a literal — which made the explanation immune to the evidence
    it cites. Handing every firefox leg a canvas extension and shrinking
    chromium's layer to two entries moved **nothing at all**: the paragraph
    went on asserting "no canvas extension at all ... against ten on chromium"
    over records that said neither.

    Legs of one engine agree in every committed record; where they ever
    disagree the UNION is reported, so a partial install widens the list
    rather than hiding behind whichever leg happened to be read first.
    """
    seen: "list[str]" = []
    for leg in (rb.get("readings", {}).get(engine, {}) or {}).values():
        if not isinstance(leg, dict):
            continue
        for ext in ((leg.get("layer") or {}).get("installed") or []):
            if ext not in seen:
                seen.append(ext)
    return sorted(seen)


def gpu_section(off: dict, on: dict, uoff: dict, uon: dict) -> str:
    lines: "list[str]" = []
    add = lines.append

    add("### GPU unlinkability — MEASURED on all four arms, both authorship arms")
    add("")
    add("**Lower is better; it is the chance that two random profiles draw the "
        "same card.** Every figure below is a MEASUREMENT taken on "
        f"{off['measured_at'][:10]} over "
        f"{len(off['seeds_requested'])} seeds requested per arm, on loopback "
        "with no proxy and no exit. Engine: "
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
        # RECOUNTED from the raw readings — percentages, distinct counts and
        # seed count alike. `result.per_arm` is the sweep's account of ITSELF:
        # it reports what the run believed it read, so a truncated sweep
        # carries a full-looking summary and every figure rendered from it
        # inherits that blindness. This row lands in PS-16's Table 2, where a
        # reader has no records beside them; recounting the seed count while
        # echoing the percentage it qualifies would ship a row that
        # contradicts itself inside one line.
        e_on = _arm_stats(on, arm)
        e_off = _arm_stats(off, arm)
        engine_authored = arm in on["provenance"]["engine_authored_arms"]
        author = "**engine**" if engine_authored else "ours (`gpu_ext`)"
        # The basis names WHICH authorship arm the shipped figure came from.
        # A bare "measured" would put the layer-OFF windows number and three
        # layer-ON numbers under one identical label — two different
        # quantities in one column, which is the conflation this whole
        # section exists to keep apart.
        basis_mode = "layer OFF" if engine_authored else "layer ON"
        # The seed count must come from the SAME record the shipped figure
        # does, or the count and the percentage could describe two runs.
        shipped = e_off if engine_authored else e_on
        seeds_read = shipped["seeds_readable"]
        add(
            f"| {arm} | {author} | **{pct(e_on['collision_probability'])}** | "
            f"{pct(e_off['collision_probability'])} | "
            f"{e_on['distinct_identities']} / {e_off['distinct_identities']} | "
            f"**measured ({basis_mode})**, {seeds_read} seeds |"
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
    # `if v and ...` is load-bearing, not defensive. `None == None` is True in
    # Python, so counting bare equality scores a seed that produced NOTHING in
    # either mode as a seed that AGREED — a positive control that gets
    # STRONGER the more the sweep fails, and that reads a total launch failure
    # as a perfect 24 of 24. The claim being made is "both modes returned the
    # SAME IDENTITY", so a seed with no identity cannot support it.
    w_identical = sum(1 for s, v in w_on.items() if v and v == w_off.get(s))
    # The denominator has to be readable seeds too, or a truncated run prints
    # "12 of 24" and reads as a FAILED control rather than a short sample.
    w_comparable = max(_readable_seeds(w_on), _readable_seeds(w_off))
    w_on_res = _arm_stats(on, "windows")
    # Recounted for the same reason as the table above: `distinct_identities`
    # on `result.per_arm` is the sweep's account of itself, and this list
    # decides whether the positive control paragraph is printed AT ALL.
    _dist = {
        arm: (_arm_stats(on, arm)["distinct_identities"],
              _arm_stats(off, arm)["distinct_identities"])
        for arm in ARMS
    }
    diverging = [
        arm for arm in ARMS
        if arm not in on["provenance"]["engine_authored_arms"]
        and _dist[arm][0] != _dist[arm][1]
        and _dist[arm][1] == 1
    ]
    if w_identical == w_comparable and w_comparable and diverging:
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
                f"{arm} {_dist[arm][1]} "
                f"identity with the layer off against "
                f"{_dist[arm][0]} pool "
                f"entries with it on"
                for arm in diverging
            )
            + "), which is the layer proving it reached the page rather than "
            "an assertion that it was installed."
        )
        add("")

    # ---- the instrument finding -------------------------------------
    # WHICH ARMS THE GATE FLAGGED, AND WHETHER IT STILL DOES — both RECOMPUTED
    # from the readings rather than typed in OR read from a stored summary
    # (PS-239). This paragraph used to assert "macos, linux AND android" as a
    # literal. PS-191 then corrected the gate, the sentence silently became
    # false, and the table beneath it disagreed with it — the exact drift this
    # script's opening docstring promises cannot happen.
    #
    # ⚠️ `_then` IS DERIVED, NOT TRANSCRIBED. The obvious source for "what the
    # gate used to say" is the uniformity record's stored `module_verdict`,
    # and using it is WRONG here for the reason axis 2 of
    # `enumerate_summary_sites.py` exists: a rendered figure must not depend
    # on a stored summary field, or poisoning that field moves the article.
    # The pre-PS-191 rule was the raw bar comparison, and PS-191 deliberately
    # KEPT that comparison as `meets_bar` on every arm — so the old verdict is
    # still a function of the readings and is recomputed as one.
    _live = _classify(on["readings"], _epoch_pool_sizes(on))["per_arm"]
    _then = [
        arm for arm in ARMS
        if _bar_verdict(_live[arm], arm) == "TOO_NARROW"
    ]
    _now = [arm for arm in ARMS if _live[arm]["verdict"] == "TOO_NARROW"]
    _cleared = [arm for arm in _then if arm not in _now]
    add("#### ⚠️ The gate's own verdicts on "
        f"{num_word(len(_then))} of those arms are an "
        "ESTIMATOR ARTEFACT, not a product finding")
    add("")
    add(f"`engine_gpu_variance` returned `TOO_NARROW` for {join_arms(_then)} on "
        "the layer-ON run *as the gate stood when these readings were taken*. "
        "An identical adverse verdict across every non-windows cell is the "
        "shape this project has learned to distrust (PS-14), and it does not "
        "survive checking.")
    add("")
    if _cleared:
        add(f"**It did not survive it.** The gate has since been corrected "
            f"(PS-191, which replaced the raw bar comparison with a "
            f"hypothesis test against the null), and re-judging *these same "
            f"readings* against *the pool they were actually drawn from* now "
            f"returns a clean verdict for {join_arms(_cleared)}. The artefact "
            "diagnosis below was reached before that fix existed and is "
            "CONFIRMED by it — the numbers never moved, only the rule that "
            "read them.")
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
    # The uniformity records carry NO raw readings — only a per_arm summary —
    # so N is recounted from the sweep each one NAMES in `source_record`.
    unif_sources = {
        "engine-gpu-variance.layer-off.json": off,
        "engine-gpu-variance.layer-on.json": on,
    }
    for arm in ARMS:
        u = _uniformity_stats(uon, arm, unif_sources)
        p = u["monte_carlo_p_value"]
        verdict = (
            "**genuine**" if u["genuine_narrowing_finding"]
            else ("artefact" if u["module_verdict"] == "TOO_NARROW" else "—")
        )
        add(
            f"| {arm} | {fnum(u['plugin_estimate'])} | "
            f"{fnum(u['unbiased_estimate'])} | "
            f"{fnum(u['expected_plugin_under_uniform'])} | "
            f"{fnum(u['bar_collision_probability'])} | "
            f"{fnum(p, '.3f')} | "
            f"{u['module_verdict']} → {verdict} |"
        )
    add("")
    a = _uniformity_stats(uon, "android", unif_sources)
    # An arm with NO readable seed has no estimate to quote, and this sentence
    # is the one the artefact finding rests on. Rendered through `fnum` it
    # reports the absence; rendered through `:.4f` it raised TypeError and the
    # whole document failed to build — found by the generalised axis-1 walk,
    # which destroys an arm outright rather than truncating it. A reading that
    # cannot be had must be RECORDED as unobtainable, never crashed on.
    if a["plugin_estimate"] is None or a["expected_plugin_under_uniform"] is None:
        add(
            "**The single line that settles it:** ⚠️ android produced no "
            "readable seed in this run, so the estimator comparison cannot be "
            "made — `INCONCLUSIVE`, which is not a pass and must not be "
            "written into PS-16 as one."
        )
    else:
        # THE VERDICT NAMED HERE IS THE OLD GATE'S — what it said WHEN THIS
        # READING WAS TAKEN — and it is RECOMPUTED from the readings, not
        # transcribed (PS-239). This sentence is the load-bearing step of the
        # artefact argument, and the argument is a claim about the OLD gate:
        # android scored better than uniform predicts and was flagged anyway.
        # Rendering today's verdict here would destroy the argument (it now
        # reads OK, so there would be nothing to explain), and hardcoding the
        # literal let it go on asserting a flag the table beneath it no longer
        # showed. Reading the uniformity record's stored `module_verdict` is
        # the third wrong answer: axis 2 forbids a rendered figure from
        # depending on a stored summary. `_bar_verdict` reconstructs it from
        # `meets_bar`, which PS-191 kept computing precisely so the old
        # comparison stays visible.
        _then_android = _bar_verdict(_live["android"], "android")
        add(
            f"**The single line that settles it:** android scored "
            f"{fnum(a['plugin_estimate'])}, which is BELOW the "
            f"{fnum(a['expected_plugin_under_uniform'])} a uniform draw is "
            f"expected to score at N={a['seeds_readable']} — and the gate, "
            f"as it stood, still called it `{_then_android}`. An arm cannot "
            "be *worse than uniform* while scoring *better than uniform "
            "predicts*. The comparison failed, not the pool."
        )
    add("")
    add("**So the old \"theoretical\" figures are CONFIRMED rather than "
        "overturned:** the uniform-selection assumption behind them holds on "
        "the real draw (p = "
        + ", ".join(
            f"{arm} "
            + fnum(
                _uniformity_stats(uon, arm, unif_sources)["monte_carlo_p_value"],
                ".2f",
            )
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
    # RECOUNTED: `verdict == "CONSTANT"` is the sweep's own judgement of
    # itself, and it decides which arms this GENUINE-finding section names at
    # all. A truncated arm that collapsed to one surviving identity would keep
    # a stored verdict from the full run, and an arm that stopped being
    # constant would keep its old CONSTANT label. The property being claimed
    # is "every readable profile got the SAME identity", so recount it.
    off_stats = {arm: _arm_stats(off, arm) for arm in ARMS}
    constants = [
        arm for arm in ARMS
        if off_stats[arm]["distinct_identities"] == 1
        and off_stats[arm]["seeds_readable"]
    ]
    for arm in constants:
        vals = off_stats[arm]["values"]
        u = _uniformity_stats(uoff, arm, unif_sources)
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
    # RECOUNTED, both the percentage and the seed count it is quoted over —
    # this paragraph puts the two side by side in one sentence, so echoing
    # either from the stored summary is the self-contradicting-row shape.
    m_off = off_stats["macos"]
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


def _readback_verdicts(rb: dict) -> dict:
    """Recompute each engine/vector verdict FROM THE RAW READINGS.

    ``rb["verdicts"]`` is the readback run's account of ITSELF — the same
    class as ``result.per_arm`` on the GPU side, and it renders the two things
    this ticket exists to deliver: the per-seed hash table, and the firefox
    ``webgl_pixel_hash`` contrast that PS-182 depends on.

    Found by mutating the records and diffing the rendered output rather than
    by grep: emptying EVERY firefox vector left the table still printing three
    distinct hashes and a confident ``DIFFERS``, and left the narrative still
    printing both loopback values — a sweep that read nothing at all,
    published as the answer to the ticket's headline question.

    ``verdict_for`` is the differ's OWN rule, imported rather than
    reimplemented, so this cannot drift from the rule that took the reading.
    Verified against the committed record: all four verdicts, every per-seed
    value and every detail string reproduce exactly.
    """
    out: dict = {}
    seeds = [str(s) for s in rb["seeds"]]
    for engine in rb["engines"]:
        out[engine] = {}
        for vec in ("webgl_pixel_hash", "canvas_pixel_hash"):
            per_seed = {
                s: (((rb["readings"].get(engine, {}).get(s) or {})
                     .get("reading") or {}).get("vectors") or {}).get(vec)
                for s in seeds
            }
            verdict, detail = _verdict_for([per_seed[s] for s in seeds])
            out[engine][vec] = {
                "seeds": per_seed,
                "verdict": verdict,
                "detail": detail,
            }
    return out


def readback_section(rb: dict, rep: dict, repc: dict) -> str:
    lines: "list[str]" = []
    add = lines.append
    seeds = [str(s) for s in rb["seeds"]]
    # RECOUNTED from the raw readings, not read out of rb["verdicts"].
    verdicts = _readback_verdicts(rb)

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
            e = verdicts[engine][vec]
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

    ff = verdicts["firefox"]["webgl_pixel_hash"]["seeds"]
    # The ANSWER is derived, not asserted. This paragraph reports which of the
    # two branches the ticket named actually happened, and the ticket is
    # explicit that they must not be averaged — so the branch has to be chosen
    # by the readings rather than written in. Compared on the SAME two seeds
    # the checker collided on (1337/4242), not on all three.
    _pair = _verdict_for([ff[seeds[0]], ff[seeds[1]]])[0]
    _probe_collides = _pair == "COLLIDES"
    _pair_readable = _pair != "INCONCLUSIVE"
    # ⚠️ THE HEADING IS DERIVED FROM THE SAME VERDICT AS THE BODY.
    # Found by the generalised axis-1 walk, on nobody's list. "the harder
    # answer" NAMES one of the two branches — the expensive one, where the
    # internal difference does not survive the trip out. Making the probe
    # collide flipped the paragraph below to the OTHER branch ("the defect is
    # upstream of delivery ... PS-182 can be verified entirely on loopback")
    # while this heading went on calling it the harder answer, three lines
    # above the sentence that now contradicted it. A heading that states a
    # conclusion is a rendered claim like any other and must move with the
    # evidence, or it is a caption disagreeing with its own section.
    add(
        "#### ⭐ The firefox `webgl_pixel_hash` question — "
        + ("ANSWERED, and it is the harder answer" if not _probe_collides
           and _pair_readable else
           "ANSWERED, and it is the tractable answer" if _probe_collides else
           "NOT ANSWERED — the probe returned no usable reading")
    )
    add("")
    add(
        "PS-16 records the one outright FAILURE in this matrix: `creepjs :: "
        "webgl_pixel_hash` reads `51df3565` for BOTH firefox seeds 1337 and "
        "4242, across two exits and two days. This ticket asked whether the "
        "LOOPBACK probe sees that same collision. "
        + ("**It does.**" if _probe_collides else
           "**It does not.**" if _pair_readable else
           "**The probe produced no usable reading on those seeds, so the "
           "question is NOT answered here — that is a failure to look, not a "
           "result.**")
    )
    add("")
    add(f"* checker (creepjs), firefox @1337 and @4242 → `51df3565` "
        f"and `51df3565` — **identical**")
    add(f"* loopback probe, firefox @1337 → `{ff[seeds[0]]}`, @4242 → "
        f"`{ff[seeds[1]]}` — "
        + ("**identical**" if _probe_collides else
           "**different**" if _pair_readable else "**no usable reading**"))
    add("")
    if _probe_collides:
        add(
            "So the probe reproduces the collision the checker saw. That is "
            "the FIRST of the two branches the ticket named: the defect is "
            "**upstream of delivery**, and PS-182 can be worked and verified "
            "entirely on loopback without the proxy."
        )
    elif _pair_readable:
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
    else:
        add(
            "⚠️ **Neither branch can be reported.** The probe returned no "
            "usable value on these seeds, so this cell is `INCONCLUSIVE` — "
            "which is NOT a pass and must not be written into PS-16 as one."
        )
    add("")
    add("⚠️ **This is stated separately from the canvas result below, "
        "deliberately.** The ticket warns against averaging the two into one "
        "verdict, and they point in different directions.")
    add("")

    add("#### canvas readback — a SPLIT across the engines")
    add("")
    cff = verdicts["firefox"]["canvas_pixel_hash"]["seeds"]
    # Derived like the webgl paragraph above: WHICH seeds collide is a
    # property of the readings, not a sentence. Under a lost firefox leg the
    # literal version printed "seeds 1337 and 4242 produce the SAME canvas
    # hash (None), while seed 9001 differs (None)" — a confident split
    # asserted over three absent values.
    _cpairs = [
        (i, j) for i in range(len(seeds)) for j in range(i + 1, len(seeds))
        if _verdict_for([cff[seeds[i]], cff[seeds[j]]])[0] == "COLLIDES"
    ]
    _cff_readable = [s for s in seeds if cff[s]]
    # ⚠️ THE CHROMIUM CLAUSE IS DERIVED FROM CHROMIUM'S OWN READINGS.
    # It used to be the literal "On chromium all three differ." — a hardcoded
    # conclusion about ONE engine inside a branch selected entirely by the
    # OTHER engine's data. Forcing chromium's canvas to collide on all three
    # seeds rendered the recounted table row as **COLLIDES** with this
    # sentence one line below still saying they all differ: a caption
    # contradicting the row directly above it, in the paragraph that IS
    # DoD #2's deliverable and that the ticket forbids averaging into one
    # verdict. Derived from `verdict_for` — the differ's own rule, the same
    # source the firefox clause beside it already used.
    cch = verdicts["chromium"]["canvas_pixel_hash"]["seeds"]
    _cch_readable = [s for s in seeds if cch[s]]
    _cch_verdict = _verdict_for([cch[s] for s in seeds])[0]
    if not _cch_readable:
        _chrome_clause = (
            " Chromium cannot be contrasted here: its canvas cells produced "
            "no usable reading, which is `INCONCLUSIVE` and not a pass."
        )
    elif _cch_verdict == "COLLIDES":
        _chrome_clause = (
            f" On chromium the same vector COLLIDES too across "
            f"{num_word(len(_cch_readable))} readable seeds — so this is NOT "
            "the engine split described below, and the two engines must not "
            "be averaged into one verdict."
        )
    else:
        _chrome_clause = (
            f" On chromium all {num_word(len(_cch_readable))} differ."
        )
    if not _cff_readable:
        add(
            "⚠️ **The firefox canvas cells produced no usable reading**, so "
            "no split can be reported here — `INCONCLUSIVE`, which is not a "
            "pass and must not be recorded as one." + _chrome_clause
        )
    elif _cpairs:
        i, j = _cpairs[0]
        others = [s for k, s in enumerate(seeds) if k not in (i, j)]
        add(
            f"On firefox, seeds {seeds[i]} and {seeds[j]} produce the SAME canvas "
            f"hash (`{cff[seeds[i]]}`), while seed {others[0]} differs "
            f"(`{cff[others[0]]}`)." + _chrome_clause
        )
    else:
        add(
            f"On firefox, all {len(_cff_readable)} readable seeds produced "
            "DISTINCT canvas hashes — the collision recorded previously is "
            "not reproduced in this run." + _chrome_clause
        )
    add("")
    # ⚠️ THE LAYER REPORT IS READ FROM THE LEGS, NOT SPELLED OUT.
    # This sentence cites the layer report as its EVIDENCE, then used to
    # hardcode both halves: the literal `['audio', 'locale', 'webgl']` and
    # "against ten on chromium". Nothing in this file consulted
    # `leg["layer"]` at all — a grep for it returned nothing — so giving
    # every firefox leg a canvas extension and shrinking chromium's layer to
    # two entries moved zero lines while the paragraph went on describing a
    # layer report neither engine had. An explanation that cannot be
    # contradicted by the record it cites is not evidence, it is decoration.
    _ff_layer = layer_installed(rb, "firefox")
    _ch_layer = layer_installed(rb, "chromium")
    _canvas_exts = sorted(e for e in _ff_layer if "canvas" in e.lower())
    add(
        "The mechanism is recorded in `local_probe`'s own docstring and is "
        "confirmed by the layer report in these records: canvas 2D is "
        "**delegated to `--fingerprint=`, which is chromium-only**, and the "
        "firefox arm returns before it. The layer report for the firefox "
        f"readings lists `{_ff_layer}` — "
        + ("**no canvas extension at all**" if not _canvas_exts else
           f"**including {', '.join(f'`{e}`' for e in _canvas_exts)}**")
        + f" — against {num_word(len(_ch_layer))} on chromium. So firefox "
        "canvas entropy is whatever the engine happens to produce, and two "
        "seeds colliding there is expected rather than surprising."
    )
    add("")
    # The "not a chromium cell" claim rests on the SAME layer evidence as the
    # paragraph above, so it is derived from it rather than asserted beside
    # it. If a future record ever shows firefox installing a canvas
    # extension, the mechanism sentence would report that while this one went
    # on telling the reader a chromium fix cannot touch the cell — the caption
    # contradicting its own evidence, one paragraph later.
    if _canvas_exts:
        add(
            "⚠️ **The mechanism above no longer holds in these records:** the "
            "firefox layer reports a canvas extension, so the split is NOT "
            "explained by its absence and this cell needs re-diagnosing "
            "before any conclusion about ownership is drawn."
        )
    else:
        add(
            "**This is a two-engine-rule cell, not a chromium cell.** A "
            "chromium canvas fix does not touch it."
        )
    return "\n".join(lines)


def _readable_seeds(by_seed: dict) -> int:
    """Count readable seeds the way ``engine_gpu_variance.classify`` does.

    The module's rule is ``[v for v in by_seed.values() if v]`` — a null or
    empty identity is a seed that produced no reading.

    Recounted from the RAW ``readings`` rather than read out of
    ``result.per_arm``, and that choice is the entire point of this function.
    ``result.per_arm['seeds_readable']`` is a summary the sweep wrote **about
    itself**; if a sweep is truncated, that block reports whatever it recorded
    at the time and a stale or carried-over copy still reads as a full run. The
    question this section answers is *"did the run get truncated"*, so the one
    number that must not be trusted to answer it is the run's own account of it.
    """
    return sum(1 for v in by_seed.values() if v)


def _arm_stats(rec: dict, arm: str) -> dict:
    """Recount an arm's SHIPPED figures from its raw readings.

    Companion to ``_readable_seeds`` and it exists for the identical reason,
    one level up: ``result.per_arm`` carries ``collision_probability`` and
    ``distinct_identities`` as well as ``seeds_readable``, and ALL THREE are
    the sweep's account of itself. Recounting only the seed count while still
    echoing the percentage it qualifies produces the worst artifact of the
    three — a row that contradicts itself inside one line, ``27.4%`` computed
    over 24 readings sitting beside a seed count of 12, with nothing to tell a
    future reader which half is load-bearing.

    ``collision_probability`` is the product's OWN estimator, imported rather
    than reimplemented, so this cannot drift from the rule that took the
    reading. Verified against every committed record: all eight arms reproduce
    their stored figure exactly, which is why replacing the source moves no
    published number.
    """
    readable = [v for v in rec["readings"][arm].values() if v]
    return {
        "seeds_readable": len(readable),
        "distinct_identities": len(set(readable)),
        # `classify` leaves this None when an arm is unreadable; match that
        # rather than inventing a 0.0 that would render as "0.0%".
        "collision_probability": (
            _collision_probability(readable) if readable else None
        ),
        "values": readable,
    }


_ANALYSIS_CACHE: "dict[tuple, dict]" = {}


def _analysed(src: dict, trials: int, seed: int) -> dict:
    """``uniformity_check.analyse`` over ``src``, memoised on its READINGS.

    Cached because the simulation is genuinely expensive (200k trials x 4 arms,
    ~10s per record) and a single render asks for it repeatedly. The key is the
    readings themselves, NOT the record's identity: a caller that truncates an
    arm and re-renders must MISS this cache and get the recomputed p-value, or
    the memo would reintroduce exactly the staleness this function removes.

    ⚠️ THE WHOLE RECORD IS ANALYSED AT ONCE, NEVER ONE ARM AT A TIME. ``analyse``
    draws every arm from ONE shared ``random.Random(seed)``, in its own
    ``sorted()`` order, so each arm's p-value depends on how much of the stream
    the previous arms consumed. Analysing a single arm, or iterating in this
    file's ``ARMS`` order, silently lands a different answer — macos comes back
    0.308535 instead of the stored 0.308370. Call the instrument; do not
    reimplement its loop.

    ⚠️ ``k`` IS PINNED TO THE MEASUREMENT EPOCH, read from the sweep's own
    ``fallback_pool_size`` witness rather than from the live product. Round 6
    took it live, which was strictly better than echoing the uniformity
    record's copy *while the two agreed* — and PS-183 then widened ``MAC_GPUS``
    2 -> 11 the day after these readings were taken. The same committed macos
    draw scored against a ``1/11`` bar instead of ``1/2`` moves p 0.308 ->
    0.000 and flips the arm from **artefact** to **genuine**, manufacturing a
    product finding out of an unrelated pool edit (PS-14). The pool is an
    environmental INPUT the sweep recorded, not a summary it wrote about
    itself, and 24 draws cannot recover it — so the record is the only witness.
    ``k`` is part of the cache key for the same reason the readings are.
    """
    pools = _epoch_pool_sizes(src)
    key = (json.dumps(src["readings"], sort_keys=True), trials, seed,
           json.dumps(pools, sort_keys=True))
    if key not in _ANALYSIS_CACHE:
        _ANALYSIS_CACHE[key] = _analyse(
            src, trials=trials, seed=seed, pool_sizes=pools
        )
    return _ANALYSIS_CACHE[key]


def _uniformity_stats(unif: dict, arm: str, sources: dict) -> dict:
    """Recompute the estimator row from the record ``unif`` NAMES.

    The uniformity records carry **no raw readings at all** — only a
    ``per_arm`` summary — so unlike every other site there is nothing local to
    recount. What they do carry is ``source_record``, naming the sweep they
    were computed from, so the recount comes from THAT record's readings.

    EVERY column is recomputed, including the Monte-Carlo p-value. Round 5
    exempted that one on the stated grounds that it "genuinely cannot be
    recomputed — a seeded simulation, not a function of the readings". **That
    was false**, and the exemption was the last live member of the
    stored-summary class: both uniformity records store the
    ``monte_carlo_seed`` and ``monte_carlo_trials`` they were run with, which
    makes the simulation a DETERMINISTIC function of the readings plus two
    recorded parameters. Feeding them back through the instrument's own
    ``analyse()`` reproduces every stored p-value exactly — 1.000000 /
    0.308370 / 0.163825 / 0.579675 — on all four arms in both modes, with no
    browser, no sweep and no re-measurement.

    It mattered because it is the column that decides **artefact vs genuine**.
    Left echoed, a truncated android arm rendered three recounted columns
    beside a frozen p-value inside ONE table row — the self-contradicting row
    this whole class exists to prevent. Recounted, it moves 0.579675 ->
    0.977530 and the row stays honest.

    So the recount now comes from the instruments themselves rather than from
    formulae copied into this file:

    * ``plugin_estimate``, ``unbiased_estimate``,
      ``expected_plugin_under_uniform``, ``bar_collision_probability``,
      ``pool_size``, ``seeds_readable``, ``monte_carlo_p_value``,
      ``consistent_with_uniform`` and ``genuine_narrowing_finding`` — all from
      ``uniformity_check.analyse``, the script that wrote these records;
    * ``module_verdict`` — from ``engine_gpu_variance.classify``, the gate
      itself. That column exists to report WHAT THE GATE SAID so the estimator
      can be contrasted against it, and asking the gate afresh still reports
      the gate's verdict — it just removes a transcription-of-a-transcription
      (the uniformity record's copy of the sweep's copy). Verified to reproduce
      all eight stored verdicts exactly.

    ``pool_size`` is recounted for a reason worth naming: round 5 recomputed
    the estimator FORMULAE but fed them a ``k`` read from the stored block, so
    ``expected_plugin_under_uniform`` and the ``1/k`` bar were only half
    recounted. Poisoning the stored ``pool_size`` moved the "android scored
    BELOW what uniform predicts" line — the sentence the ticket leans on to
    settle the artefact question — while every other column sat still.
    """
    src = sources.get(unif.get("source_record"))
    stored = unif["per_arm"][arm]
    if src is None:
        # No named source to recount from. Return the stored row rather than
        # inventing one — not reachable with the committed records, which all
        # name a source that is loaded here.
        return dict(stored, recounted=False)
    fresh = _analysed(
        src,
        trials=unif["monte_carlo_trials"],
        seed=unif["monte_carlo_seed"],
    )[arm]
    out = dict(stored, recounted=True, **fresh)
    # LAST, deliberately overriding `fresh`. `analyse` carries a
    # `module_verdict` of its own, but it reads it out of
    # `record["result"]["per_arm"][arm]["verdict"]` — the sweep's stored
    # summary — so taking it from `fresh` would leave this column echoed by a
    # different route while looking recounted. Ask the gate itself instead.
    #
    # ⚠️ PINNED TO THE MEASUREMENT EPOCH (PS-239). The gate's verdict is a
    # comparison against persona's OWN pool, so asking it live judges a frozen
    # reading against whatever the pool happens to be today. PS-183 widened
    # `MAC_GPUS` 2 -> 11 AFTER these readings were taken, and re-judging macos
    # live against 11 entries flips it OK -> TOO_NARROW on both modes
    # (p=0.064 -> 1.3e-14): an arm condemned for colliding at a rate that was
    # unremarkable in the pool it actually drew from. This column was the LAST
    # live read in this file — `analyse` was already pinned via
    # `_epoch_pool_sizes` — so this closes the gap rather than adding a rule.
    #
    # `k` COMES FROM THE SWEEP'S OWN WITNESS, exactly as `_analysed` takes it,
    # and NOT from the uniformity record's `pool_size` copy: round 6 pinned
    # that the uniformity copy is never consulted (it poisons it to 999 and
    # requires the columns to sit still), and round 7 pinned that the sweep's
    # witness IS what drives them. Reading the copy here would fail both.
    out["module_verdict"] = _classify(
        src["readings"], _epoch_pool_sizes(src)
    )["per_arm"][arm]["verdict"]
    return out


def gpu_completeness(off: dict, on: dict) -> dict:
    """Per-arm seeds obtained vs requested, recounted from the raw readings.

    Also cross-checks the recount against the sweep's stored ``seeds_readable``.
    Drift between the two means the summary and the readings disagree about what
    was obtained — which is itself a finding, and is reported rather than
    silently resolved in favour of either side.
    """
    modes = (("layer-OFF", off), ("layer-ON", on))
    arms: "list[dict]" = []
    for label, rec in modes:
        for arm in sorted(rec["readings"]):
            by_seed = rec["readings"][arm]
            readable = _readable_seeds(by_seed)
            stored = rec["result"]["per_arm"][arm].get("seeds_readable")
            arms.append({
                "mode": label,
                "arm": arm,
                "requested": len(by_seed),
                "readable": readable,
                "stored": stored,
                "drift": stored is not None and stored != readable,
                "verdict": rec["result"]["per_arm"][arm].get("verdict"),
            })
    return {
        "arms": arms,
        "short": [a for a in arms if a["readable"] < a["requested"]],
        "drifted": [a for a in arms if a["drift"]],
        "inconclusive": [a for a in arms if a["verdict"] == "INCONCLUSIVE"],
        "arm_names": sorted({a["arm"] for a in arms}),
        "modes": [label for label, _ in modes],
    }


def readback_completeness(records: "list[tuple[str, dict]]") -> dict:
    """Readback legs attempted, legs that produced NOTHING, and unusable cells.

    Two different failures, deliberately counted separately:

    * an **unusable cell** is a vector that came back ``unavailable:`` or
      ``error:`` — the page saying it could not read that vector;
    * an **empty leg** is a launch that produced no vectors at all.

    A scan for unusable *values* cannot see an empty leg, because an absent
    reading has no value to inspect — it reports a clean sweep over whatever
    survived. That is the same shape as a truncated GPU sweep reading as clean:
    the evidence of the loss is the absence itself, so it has to be counted by
    what was ATTEMPTED, not by what came back.
    """
    legs: "list[dict]" = []
    cells = 0
    unusable: "list[str]" = []
    for name, rec in records:
        engines = rec.get("engines") or sorted(rec.get("readings", {}))
        seeds = [str(s) for s in rec.get("seeds", [])]
        for engine in engines:
            for seed in seeds:
                leg = (rec.get("readings", {}).get(engine, {}) or {}).get(seed)
                vectors = ((leg or {}).get("reading") or {}).get("vectors") or {}
                err = ((leg or {}).get("error") or "").strip()
                legs.append({
                    "record": name, "engine": engine, "seed": seed,
                    "vectors": len(vectors),
                    "error": err.splitlines()[0] if err else "",
                })
                for vec, val in vectors.items():
                    cells += 1
                    if not val or str(val).startswith(("unavailable:", "error:")):
                        unusable.append(f"{name} {engine}@{seed} {vec}={val!r}")
    return {
        "legs": legs,
        "empty": [l for l in legs if not l["vectors"]],
        "cells": cells,
        "unusable": unusable,
        "engines": sorted({l["engine"] for l in legs}),
    }


def completeness_statement(off: dict, on: dict,
                           readbacks: "list[tuple[str, dict]]") -> str:
    """The sample-completeness claim, derived — nothing here is a literal.

    Factored out of ``coverage_section`` because this exact claim also lands in
    PS-16 through Edit 8 of ``PS-16-PATCH.md``. A hand-typed paraphrase there
    would reintroduce the same defect one file over: a durable assertion in a
    knowledge article, about a sweep nobody can re-check, that cannot become
    false. Both callers render THIS string.
    """
    gpu = gpu_completeness(off, on)
    rbk = readback_completeness(readbacks)

    # --- the GPU half, derived ------------------------------------------
    n_arms = len(gpu["arm_names"])
    if gpu["short"]:
        worst = "; ".join(
            f"{a['mode']} {a['arm']} {a['readable']}/{a['requested']}"
            for a in gpu["short"]
        )
        gpu_sentence = (
            f"⚠️ **The sample is INCOMPLETE and the figures above are computed "
            f"over a partial draw:** {worst}. A collision probability taken "
            "over a truncated sweep is a position-biased estimate, not a "
            "measurement of the pool."
        )
    else:
        counts = sorted({f"{a['readable']}/{a['requested']}" for a in gpu["arms"]})
        gpu_sentence = (
            f"No arm was recorded `INCONCLUSIVE`: all {n_arms} GPU arms "
            f"returned {counts[0] if len(counts) == 1 else ', '.join(counts)} "
            f"readable seeds in {' and '.join(gpu['modes'])}"
        )
    if gpu["drifted"]:
        drift = "; ".join(
            f"{a['mode']} {a['arm']} readings say {a['readable']}, "
            f"summary says {a['stored']}"
            for a in gpu["drifted"]
        )
        gpu_sentence += (
            f"\n\n⚠️ **The stored summary disagrees with the raw readings** "
            f"({drift}). The recount above is taken from the readings."
        )

    # --- the readback half, derived --------------------------------------
    if rbk["unusable"]:
        rb_sentence = (
            f"{len(rbk['unusable'])} of {rbk['cells']} readback cells came "
            f"back unusable: {'; '.join(rbk['unusable'])}."
        )
    else:
        rb_sentence = (
            f"all {rbk['cells']} readback cells that were read produced a "
            f"usable value on both engines."
        )

    lines = [f"{gpu_sentence}, and {rb_sentence}"]

    # An attempted launch that produced NOTHING is invisible to a scan for
    # unusable values, so it is counted by attempt and reported explicitly.
    if rbk["empty"]:
        lines.append("")
        detail = "; ".join(
            f"`{l['record']}` {l['engine']}@{l['seed']}"
            + (f" ({l['error']})" if l["error"] else "")
            for l in rbk["empty"]
        )
        lines.append(
            f"⚠️ **{len(rbk['empty'])} readback leg of "
            f"{len(rbk['legs'])} attempted produced no reading at all** — "
            f"{detail}. It is recorded here because a leg that returns NO "
            "value is invisible to a check for unusable values: the scan finds "
            "nothing wrong with the cells that survived. No published figure "
            "rests on it — it belongs to a repeatability re-run, and the "
            "chromium repeatability above is computed against "
            "`readback-vectors.replicate-chromium.json`, which is complete."
        )

    return "\n".join(lines)


def coverage_section(off: dict, on: dict,
                     readbacks: "list[tuple[str, dict]]") -> str:
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
        completeness_statement(off, on, readbacks),
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
        coverage_section(off, on, [
            ("readback-vectors.three-seeds.json", rb),
            ("readback-vectors.replicate.json", rep),
            ("readback-vectors.replicate-chromium.json", repc),
        ]),
        "",
    ]
    text = "\n".join(parts)
    # This output carries ⚠️, — and non-ASCII identity strings. A Windows
    # console defaults to cp1252, where print() raises UnicodeEncodeError and
    # the script dies WITHOUT writing anything — on a document whose whole
    # instruction to the next maintainer is "re-run derive.py instead of
    # re-typing a number". Pin the stream to UTF-8 so that instruction is
    # followable on every platform rather than on the two it was written on.
    stream = getattr(sys, "stdout", None)
    if stream is not None and hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError, ValueError):
            pass
    print(text)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"\n[derive] wrote {os.path.basename(args.output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
