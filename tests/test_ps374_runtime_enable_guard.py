"""PS-374: the guard on patch 001 — `Runtime.enable` and the CDP execution-context leak.

WHY THIS FILE EXISTS
════════════════════
`engine/patches/fingerprint/001-disable-runtime.enable.patch` hard-codes
`V8RuntimeAgentImpl::enabled()` to `false` and routes two call sites through it,
so the V8 inspector's `Runtime` domain reports as disabled even when a driver
calls `Runtime.enable`. That is a STRONGER shape than the community fix
(`rebrowser-patches` avoids CALLING enable from the driver side; ours makes the
agent structurally incapable of reporting enabled), and it is one line in a
header, in a file upstream V8 touches often.

**Nothing guarded it.** Before this file:

    git grep -rln "Runtime.enable" -- tests/ .github/   ->  ZERO HITS

A rebase that dropped or mangled the patch would be SILENT — the engine builds,
no test reddens, and every AI-controlled chromium profile starts announcing
execution contexts again. And the exposure is real rather than theoretical:
`connect_over_cdp` is called from `src/api/mcp_server.py`, and
`process.py` appends `--remote-debugging-port=0` + `--remote-allow-origins=*`
whenever `automation_channel.opens_cdp_channel` is true.

⛔ THIS FILE VERIFIES; IT DOES NOT RE-IMPLEMENT. No second mechanism over the
same property is added here, and rebrowser's driver-side approach is not ported
— two mechanisms over one property drift, and the weaker becomes the one people
trust.

TWO SITES, TWO DIFFERENT QUESTIONS, AND CONFLATING THEM IS THE TRAP
════════════════════════════════════════════════════════════════════
Patch 001 covers exactly two `m_enabled` reads. It does NOT cover
`reportExecutionContextCreated`, which still reads the raw field:

    SITE A   Runtime.executionContextCreated announced?
             The LEAK. ⛔ NOT evidence about our patch — the patch never
             covered this site, so a leak here is a SCOPE fact about patch 001
             and not a sign that the patch went missing.

    SITE B   is a console message logged AFTER Runtime.enable reported?
             ⭐ THE PATCH ORACLE. `messageAdded` IS routed through `enabled()`,
             so a binary carrying the patch must SUPPRESS it and an unpatched
             binary reports it. This is the only one of the two that can tell
             "the patch is in this binary" from "the patch was lost".

⭐ THE VERDICT KEYS ON SITE B, DELIBERATELY. Keying the guard on site A would
make it permanently RED against a correctly-patched binary — an assertion no fix
could satisfy, which is the permanently-red gate this project already records as
worse than no gate at all. Site A is measured, recorded and reported; it is not
the pass/fail.

⚠️ AND SITE B IS READ BY PAYLOAD, NEVER BY COUNT. `Runtime.enable` REPLAYS
stored console history on a DIFFERENT code path from `messageAdded`, so a
message logged BEFORE enable arrives on a patched binary too. Comparing
`consoleAPICalled` cardinality gives real numbers and an uninterpretable
reading; two distinctly-marked messages, one before and one after, is what makes
it an oracle. `test_before_marker_alone_does_not_condemn` pins that, because it
is the mistake a future reader is most likely to make in the other direction.

WHAT WAS MEASURED, AND WHAT IS PINNED HERE
═══════════════════════════════════════════
Measured live on 2026-09-09 against the SHIPPED
`personium-152.0.7977.75-linux-x86_64.AppImage` (sha256 `6ddb7bbe…`, matching
`engine/releases/personium-152.0.7977.75.json`), driven over a real
`--remote-debugging-port` with a raw stdlib WebSocket:

                              site A ctxCreated   site B console AFTER enable
    SHIPPED personium               1                    SUPPRESSED
    STOCK chromium 152.0.7977.82    1                    reported

So: **patch 001 is present in the shipped binary and is acting.** The leak at
site A is a scope gap in the patch, not a lost patch. Readings are committed to
`readings/ps374-2026-09-09/artifacts/`.

⚠️ THE LIVE ARM IS SKIPPED, NEVER SILENTLY PASSED, when no engine binary is
present. An absent engine must not read as a clean bill of health — see
`tests/KNOWN_SKIPS.md`. What runs everywhere is the DECISION LOGIC, on readings
shaped exactly as the probe emits them, plus the static half below.

THE STATIC HALF — AND THE FALSE GREEN IT CLOSES
════════════════════════════════════════════════
`scripts/ps307_verify_patches_in_tree.sh` already verifies all 16 patches are in
the tree about to be compiled. For patch 001 it was looking for the WRONG SIX
LINES: the extractor took the first `MAX_PER_FILE` (3) usable added lines per
file in PATCH ORDER, and each of 001's hunks opens with a multi-line
`// persona fingerprint: …` block ahead of its ONE code line. Six claims, all
comments, ZERO pinning `return false` / `if (!enabled())` / `if (enabled())`.

Revert exactly those three code lines to `m_enabled` and leave the comments — the
shape a bad rebase or a careless revert produces — and the guard reported
**6 of 6 claims holding** over a patch that was functionally gone.

PS-374 fixes the extractor by RANKING candidates (code before comment) rather
than by raising the cap: raising it would fix 001 at today's comment lengths and
say nothing about the next patch whose prose runs a line longer.
`scripts/ps374_falsify_patch_evidence.sh` is the demonstration, and
`test_the_falsification_script_reports_the_guard_is_now_sighted` runs it.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
PROBE = REPO / "scripts" / "ps374_runtime_enable_probe.py"
FALSIFY_SH = REPO / "scripts" / "ps374_falsify_patch_evidence.sh"
EVIDENCE_AWK = REPO / "scripts" / "ps307_patch_evidence.awk"
PATCH_001 = REPO / "engine" / "patches" / "fingerprint" / "001-disable-runtime.enable.patch"
ARTIFACTS = REPO / "readings" / "ps374-2026-09-09" / "artifacts"


def _load():
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    spec = importlib.util.spec_from_file_location("ps374_runtime_enable_probe", PROBE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def probe():
    return _load()


def _met(probe, **overrides) -> dict:
    d = dict.fromkeys(probe.PRECONDITIONS, True)
    d.update(overrides)
    return d


# ═══════════════════════════════════════════════════════════════════════════
# THE PATCH ITSELF — the one-line anchor the whole vector rests on
# ═══════════════════════════════════════════════════════════════════════════
def test_patch_001_still_hard_codes_enabled_to_false():
    """The single line that disables the Runtime domain.

    Asserted as TEXT because that is what a rebase damages. If upstream reshapes
    `V8RuntimeAgentImpl` and this line is dropped or reverted to `m_enabled`,
    this fails — which is the whole reason the ticket exists.
    """
    text = PATCH_001.read_text(encoding="utf-8")
    assert "+  bool enabled() const { return false; }" in text, (
        "patch 001 no longer hard-codes enabled() to false. Every profile driven "
        "over CDP would announce its execution contexts again."
    )


def test_patch_001_routes_both_call_sites_through_enabled():
    """The two call sites the patch converts, and the raw reads it replaces.

    Both halves are asserted: an added line proving the route exists, and the
    matching removed line proving the raw `m_enabled` read is gone. A patch that
    added `enabled()` beside a surviving `m_enabled` read would satisfy the
    first alone.
    """
    text = PATCH_001.read_text(encoding="utf-8")
    assert "+  if (!enabled()) return;" in text
    assert "-  if (!m_enabled) return;" in text
    assert "+  if (enabled()) reportMessage(message, true);" in text
    assert "-  if (m_enabled) reportMessage(message, true);" in text


# ═══════════════════════════════════════════════════════════════════════════
# THE VERDICT — every outcome reachable, and the red one asserted BY MECHANISM
# ═══════════════════════════════════════════════════════════════════════════
def test_suppressed_console_after_enable_is_the_patch_present_verdict(probe):
    code, headline, detail = probe.verdict(
        {
            "preconditions": _met(probe),
            "execution_context_created_count": 1,
            "console_before_enable_reported": True,
            "console_after_enable_reported": False,
        }
    )
    assert code == probe.EXIT_PATCH_PRESENT, headline
    assert detail["site_b_console_after_enable_reported"] is False


def test_console_reported_after_enable_condemns_the_binary(probe):
    """THE LOAD-BEARING RED. Asserted by named mechanism, not by exit code alone.

    A test checking only `code == 1` would keep passing if the verdict started
    condemning for some other reason — which is this project's own recorded
    failure mode (PS-343: two tests checked only `run(...) == 2` and passed with
    the fix they guard reverted). So the assertion names the site.
    """
    code, headline, detail = probe.verdict(
        {
            "preconditions": _met(probe),
            "execution_context_created_count": 1,
            "console_before_enable_reported": True,
            "console_after_enable_reported": True,
        }
    )
    assert code == probe.EXIT_PATCH_LOST, headline
    assert detail["site_b_console_after_enable_reported"] is True
    assert "messageAdded" in headline, headline
    assert probe.MARKER_AFTER in headline, headline


def test_before_marker_alone_does_not_condemn(probe):
    """⭐ The payload-not-cardinality rule, pinned so it cannot drift back.

    `Runtime.enable` REPLAYS stored console history on a different code path
    from `messageAdded`, so a message logged BEFORE enable arrives on a PATCHED
    binary too. A guard keyed on "any consoleAPICalled arrived" — or on a COUNT
    — condemns the correctly-patched engine. This is the reading that separates
    the two, and it is asserted here rather than only described in prose.
    """
    reading = {
        "preconditions": _met(probe),
        "execution_context_created_count": 1,
        "console_before_enable_reported": True,
        "console_after_enable_reported": False,
    }
    assert probe.verdict(reading)[0] == probe.EXIT_PATCH_PRESENT

    # ...and the SAME cardinality of console events, differing only in WHICH
    # message arrived, is condemned. One event either way; opposite verdicts.
    reading["console_before_enable_reported"] = False
    reading["console_after_enable_reported"] = True
    assert probe.verdict(reading)[0] == probe.EXIT_PATCH_LOST


def test_site_a_leak_alone_never_condemns_the_patch(probe):
    """⛔ The scope fence. `executionContextCreated` is NOT patch 001's site.

    Keying the guard on the leak would make it permanently RED against a
    correctly-patched binary — an assertion no fix could satisfy. The leak is
    recorded in `detail` and reported in the headline; it is not the verdict.
    """
    code, headline, detail = probe.verdict(
        {
            "preconditions": _met(probe),
            "execution_context_created_count": 7,
            "console_after_enable_reported": False,
        }
    )
    assert code == probe.EXIT_PATCH_PRESENT, headline
    assert detail["leak_present"] is True
    assert detail["site_a_execution_context_created"] == 7
    # ...and it is not silently swallowed either.
    assert "executionContextCreated" in headline


@pytest.mark.parametrize("unmet", ["p5_unsolicited_event_arrived", "p4b_execution_context_real"])
def test_an_unmet_precondition_yields_inconclusive_never_a_pass(probe, unmet):
    """⭐ P5 is the one that gets omitted, and it is why this test is parametrized.

    An absence assertion over a channel passes hardest when the channel was
    never live: "the realm is clean" and "the realm was never reached" are the
    same green, and on this vector the second is the more likely failure. A
    probe that could not measure must say so — INCONCLUSIVE is a THIRD outcome,
    deliberately not folded into either verdict.
    """
    code, headline, detail = probe.verdict(
        {
            "preconditions": _met(probe, **{unmet: False}),
            "execution_context_created_count": 0,
            "console_after_enable_reported": False,
        }
    )
    assert code == probe.EXIT_INCONCLUSIVE, headline
    assert detail["unmet_preconditions"] == [unmet]
    assert "INCONCLUSIVE" in headline


def test_all_preconditions_are_gated_not_merely_recorded(probe):
    """Each of the seven named checks must be able to force INCONCLUSIVE alone.

    A precondition that is collected but never consulted is decoration. This
    asserts every one of them is load-bearing, so a future edit cannot quietly
    drop one from the gate while leaving it in the reading.
    """
    for name in probe.PRECONDITIONS:
        code, _, _ = probe.verdict(
            {
                "preconditions": _met(probe, **{name: False}),
                "console_after_enable_reported": False,
            }
        )
        assert code == probe.EXIT_INCONCLUSIVE, f"{name} does not gate the verdict"


def test_a_dead_console_channel_is_inconclusive_not_a_pass(probe):
    """⛔ THE FALSE GREEN THIS GUARD EXISTS TO REMOVE, pinned as its own case.

    Site B is an ABSENCE assertion: the verdict passes when no console message
    arrives after `Runtime.enable`. An absence assertion passes hardest when the
    channel was never live at all — "site B is suppressed" and "site B was never
    reached" produce the SAME empty reading, and before `p7_console_channel_live`
    existed the second returned exit 0, headline "PATCH 001 PRESENT AND ACTING",
    over a channel the probe had never successfully read.

    ⚠️ `p5_unsolicited_event_arrived` does NOT cover this and the distinction is
    the whole point: `events` is filled from ANY domain and `Page.enable` is
    called before anything console-related, so a lone `Page.frameNavigated`
    satisfies P5. P5 proves the EVENT channel is live; only P7 proves the
    CONSOLE channel is — and site B is read from the console.
    """
    dead_console = {
        "preconditions": _met(probe, p7_console_channel_live=False),
        "execution_context_created_count": 0,
        "console_before_enable_reported": False,
        "console_after_enable_reported": False,
    }
    code, headline, _ = probe.verdict(dead_console)
    assert code == probe.EXIT_INCONCLUSIVE, (
        "a reading whose console channel never produced the BEFORE marker must "
        f"be INCONCLUSIVE, not a pass — got exit {code}: {headline}"
    )
    assert "p7_console_channel_live" in headline

    # ...and the control: the SAME reading with the console channel proven live
    # is a genuine pass. P7 is a liveness gate, not a second verdict — it must
    # never condemn a patched binary, only refuse to speak for a dead channel.
    live_console = {**dead_console, "console_before_enable_reported": True}
    live_console["preconditions"] = _met(probe)
    assert probe.verdict(live_console)[0] == probe.EXIT_PATCH_PRESENT


def test_probe_self_test_reaches_every_verdict(probe, capsys):
    """A guard that can only pass is not a guard."""
    assert probe._self_test() == 0
    out = capsys.readouterr().out
    assert "want exit 0" in out and "want exit 1" in out and "want exit 2" in out


# ═══════════════════════════════════════════════════════════════════════════
# THE COMMITTED READINGS — the measurement, pinned
# ═══════════════════════════════════════════════════════════════════════════
def _reading(name: str) -> dict:
    return json.loads((ARTIFACTS / name).read_text(encoding="utf-8"))


def test_the_shipped_engine_reading_says_patch_001_is_acting(probe):
    """The measured verdict on the binary an operator downloads, re-derived.

    The stored `verdict` block is NOT trusted: the reading's raw fields are fed
    back through `verdict()` here, so a change to the decision logic that
    contradicts the recorded measurement fails rather than silently disagreeing
    with a committed artifact.
    """
    r = _reading("shipped-personium.json")
    assert r["version_string"] == "Chromium 152.0.7977.75"
    assert all(r["preconditions"].values()), r["preconditions"]

    code, headline, _ = probe.verdict(r)
    assert code == probe.EXIT_PATCH_PRESENT, headline
    assert r["console_after_enable_reported"] is False
    # The BEFORE marker DID arrive — which is what proves the console channel
    # was live and the suppression above is a real reading rather than silence.
    assert r["console_before_enable_reported"] is True


def test_the_shipped_engine_still_announces_execution_contexts(probe):
    """The negative half of the finding, recorded rather than smoothed over.

    Patch 001 holds AND the leak is live: `reportExecutionContextCreated` was
    never in the patch's scope. Pinned so nobody reads "the guard is green" as
    "the leak is closed".
    """
    r = _reading("shipped-personium.json")
    assert r["execution_context_created_count"] >= 1
    assert "Runtime.executionContextCreated" in r["events_seen"]


def test_the_stock_control_is_condemned_by_the_same_instrument(probe):
    """⛔ THE FALSIFICATION, AND IT IS A REAL BINARY RATHER THAN A FIXTURE.

    Stock chromium carries none of our patches, so it is what "patch 001 lost"
    looks like through this exact instrument. The guard must go RED on it. A
    guard that has only ever been seen to pass is decoration — this project has
    a documented history of exactly that.
    """
    r = _reading("stock-chromium.json")
    assert all(r["preconditions"].values()), r["preconditions"]
    code, headline, _ = probe.verdict(r)
    assert code == probe.EXIT_PATCH_LOST, headline
    assert r["console_after_enable_reported"] is True


def test_the_two_arms_differ_only_at_site_b(probe):
    """What makes the reading ATTRIBUTABLE to patch 001 rather than to anything else.

    Both binaries leak at site A and both report the BEFORE marker; they differ
    at exactly one place — the site patch 001 actually covers. A difference
    everywhere would be two different browsers; a difference nowhere would be a
    non-discriminating probe.
    """
    ours = _reading("shipped-personium.json")
    stock = _reading("stock-chromium.json")

    # The probe discriminates: same leak, same replayed history, on both.
    assert ours["execution_context_created_count"] == stock["execution_context_created_count"]
    assert ours["console_before_enable_reported"] == stock["console_before_enable_reported"] is True

    # And differs at the one site the patch owns.
    assert ours["console_after_enable_reported"] is False
    assert stock["console_after_enable_reported"] is True


# ═══════════════════════════════════════════════════════════════════════════
# THE STATIC GUARD — patch 001's tree evidence must pin CODE, not prose
# ═══════════════════════════════════════════════════════════════════════════
def _claims(patch: pathlib.Path, max_per_file: int = 3) -> list[tuple[str, str, str]]:
    out = subprocess.run(  # noqa: S603
        ["awk", "-v", f"MAX_PER_FILE={max_per_file}", "-f", str(EVIDENCE_AWK), str(patch), str(patch)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    rows = []
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[1] != "noevidence":
            rows.append((parts[0], parts[1], parts[2]))
    return rows


def _is_comment(text: str) -> bool:
    return text.startswith(("//", "/*", "*")) or text[:2] in ("# ", "#\t")


def test_patch_001_evidence_now_pins_the_code_lines(probe):
    """THE FIX, asserted by NAMED LINE rather than by "some code was pinned".

    Before PS-374 all six of 001's claims were `// persona fingerprint: …`
    comment lines and none pinned the code — so a tree with the three code lines
    reverted and the comments intact passed 6/6. Both of the lines that carry
    the patch's effect must now be among the claims.
    """
    texts = [t for _, _, t in _claims(PATCH_001)]
    assert "bool enabled() const { return false; }" in texts, (
        "the one line that disables the Runtime domain is not pinned by the "
        "tree-presence guard; a rebase that reverted it would pass"
    )
    assert "if (enabled()) reportMessage(message, true);" in texts


def test_patch_001_is_no_longer_an_all_comment_patch():
    """The property, stated generally, because the specific lines may legitimately move."""
    claims = _claims(PATCH_001)
    assert claims, "the extractor produced no claims at all for patch 001"
    code = [t for _, _, t in claims if not _is_comment(t)]
    assert code, (
        "every claim for patch 001 is a COMMENT. A rebase characteristically "
        "keeps the explanatory prose and loses the code line it describes, so "
        "comment-only evidence is satisfied by a tree where the patch does nothing."
    )


def test_no_fingerprint_patch_is_evidenced_by_comments_alone():
    """The fleet-wide form. 001 was the only offender; this keeps it that way.

    A future patch whose hunks open with prose would silently inherit the same
    blindness, and nothing would say so. This is the check that speaks.
    """
    patch_dir = REPO / "engine" / "patches" / "fingerprint"
    blind = []
    for patch in sorted(patch_dir.glob("*.patch")):
        claims = _claims(patch)
        if claims and not [t for _, _, t in claims if not _is_comment(t)]:
            blind.append(patch.name)
    assert not blind, (
        f"these patches are evidenced ONLY by comment lines: {blind}. "
        "The tree-presence guard would pass over a tree in which their code was "
        "reverted and their comments left in place."
    )


def test_ranking_did_not_change_how_many_claims_are_emitted():
    """The cost fence: PS-374 changed WHICH candidates are taken, never HOW MANY.

    The cap is what bounds this check's runtime over a Chromium-sized tree, so a
    fix that quietly raised it would be a different change than the one made.

    ⚠️ Only `added`/`removed` are counted. `newfile` is emitted once per created
    file and is NOT subject to `MAX_PER_FILE` — 011-gpu-info legitimately reads
    4 rows for each file it creates (its `newfile` plus three added lines), and
    that is true before and after this change. Counting it here would fail on a
    pre-existing, correct shape.
    """
    for patch in sorted((REPO / "engine" / "patches" / "fingerprint").glob("*.patch")):
        per_file: dict[str, int] = {}
        for rel, kind, _ in _claims(patch):
            if kind not in ("added", "removed"):
                continue
            per_file[rel] = per_file.get(rel, 0) + 1
        for rel, n in per_file.items():
            assert n <= 3, f"{patch.name}:{rel} emitted {n} claims, above MAX_PER_FILE=3"


def test_a_code_candidate_is_taken_even_when_prose_precedes_it(tmp_path):
    """The mechanism, on a synthetic patch, so the rule is pinned independently
    of whether OUR patches happen to exercise it today.

    Four comment lines ahead of one code line, and a cap of 3: in patch order
    the code line is unreachable. It must still be claimed.
    """
    p = tmp_path / "synthetic.patch"
    p.write_text(
        "diff --git a/foo/bar.cc b/foo/bar.cc\n"
        "--- a/foo/bar.cc\n"
        "+++ b/foo/bar.cc\n"
        "@@ -1,3 +1,8 @@\n"
        "+// an explanatory paragraph that is comfortably over thirty characters\n"
        "+// a second explanatory line that is also comfortably over thirty chars\n"
        "+// a third explanatory line that is also comfortably over thirty chars\n"
        "+// a fourth explanatory line that is likewise over thirty characters\n"
        "+  bool the_actual_code_line() const { return false; }\n"
        " context_line_that_is_long_enough_to_be_usable_evidence();\n",
        encoding="utf-8",
    )
    texts = [t for _, _, t in _claims(p)]
    assert "bool the_actual_code_line() const { return false; }" in texts, texts
    assert len(texts) == 3, f"the cap must still bind: {texts}"


def test_a_comment_only_hunk_still_yields_evidence(tmp_path):
    """The fallback. Ranking is a PREFERENCE, not an exclusion.

    A hunk that genuinely adds only prose must stay checkable — refusing it
    would turn a real patch into an UNVERIFIABLE one, which the ps307 verifier
    treats as a hard failure.
    """
    p = tmp_path / "prose.patch"
    p.write_text(
        "diff --git a/foo/doc.cc b/foo/doc.cc\n"
        "--- a/foo/doc.cc\n"
        "+++ b/foo/doc.cc\n"
        "@@ -1,2 +1,4 @@\n"
        "+// a comment line that is comfortably over the thirty character floor\n"
        "+// a second comment line also comfortably over the thirty char floor\n"
        " some_context_line_that_is_long_enough_to_count();\n",
        encoding="utf-8",
    )
    texts = [t for _, _, t in _claims(p)]
    assert len(texts) == 2, texts
    assert all(_is_comment(t) for t in texts)


def test_preprocessor_directives_are_not_demoted_as_comments(tmp_path):
    """`#define` and `#if` are CODE. Only `# ` (with a space) is a comment here.

    Getting this wrong would demote real code behind prose — the exact inversion
    of the fix — and it would be invisible, because the claim count would not move.
    """
    p = tmp_path / "cpp.patch"
    p.write_text(
        "diff --git a/foo/baz.cc b/foo/baz.cc\n"
        "--- a/foo/baz.cc\n"
        "+++ b/foo/baz.cc\n"
        "@@ -1,2 +1,6 @@\n"
        "+// a leading comment that is comfortably over the thirty char floor\n"
        "+// a second leading comment also over the thirty character floor here\n"
        "+// a third leading comment also over the thirty character floor here\n"
        "+#define PERSONA_FINGERPRINT_GUARD_ENABLED_FLAG 1\n"
        " some_context_line_that_is_long_enough_to_count();\n",
        encoding="utf-8",
    )
    texts = [t for _, _, t in _claims(p)]
    assert "#define PERSONA_FINGERPRINT_GUARD_ENABLED_FLAG 1" in texts, texts


# ═══════════════════════════════════════════════════════════════════════════
# THE FALSIFICATION SCRIPT — run, not merely shipped
# ═══════════════════════════════════════════════════════════════════════════
def test_the_falsification_script_reports_the_guard_is_now_sighted():
    """⛔ Reverting the patch's code lines must be VISIBLE to the tree guard.

    The script builds a genuinely-patched synthetic tree, reverts exactly the
    three code lines (leaving every comment), and runs the ps307 matcher over
    both. Before PS-374 it printed FALSE GREEN DEMONSTRATED; it must now report
    the sabotage as caught.

    Running the script rather than re-implementing it is deliberate — a
    re-implementation here would be a second mechanism over the same property,
    and this file would then be testing itself.
    """
    if os.name == "nt":  # pragma: no cover — the script is POSIX shell
        pytest.skip("POSIX shell script")
    result = subprocess.run(  # noqa: S603
        ["bash", str(FALSIFY_SH)],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "GUARD IS SIGHTED" in result.stdout, result.stdout
    assert "FALSE GREEN DEMONSTRATED" not in result.stdout, (
        "the tree-presence guard is blind to a reverted patch 001 again"
    )


# ═══════════════════════════════════════════════════════════════════════════
# THE SCRIPT'S OWN PORTABILITY — the guard the guard needed
# ═══════════════════════════════════════════════════════════════════════════
#
# `sed -i` is TWO different commands wearing one spelling. GNU sed (Linux, and
# Git Bash on the Windows runner) treats the backup suffix as OPTIONAL; BSD sed
# (every macOS runner) treats it as MANDATORY and POSITIONAL, so `sed -i -e …`
# swallows `-e` as the suffix and dies with `sed: -e: No such file or directory`.
#
# ⛔ THIS IS NOT HYPOTHETICAL. It reddened `tests (macos-latest, main)` on PR
# #310 while ubuntu-24.04 and windows-latest both stayed GREEN — and the test
# above, which RUNS the script, passed on two of the three runners with the bug
# fully present. Running a script is not the same as running it on the dialect
# that breaks it, and the difference is invisible from a Linux container.
#
# ⚠️ THE SHAPE OF THE FAILURE MATTERS MORE THAN THE FIX. The script exits
# NON-ZERO, which is why it was caught at all. Had it exited 0 with the sabotage
# step silently skipped, the demonstration would have printed a verdict about a
# tree it never modified — a false green inside the very script this ticket
# shipped to remove a false green.
#
# So the guard below does not grep for `sed -i`; it EXECUTES the real script
# with a BSD-dialect `sed` shim ahead of it on PATH. A grep would pin today's
# spelling; this pins the BEHAVIOUR, and fires for any other GNU-only construct
# the script grows later.
_BSD_SED_SHIM = r"""#!/bin/sh
# Emulate BSD (macOS) sed's -i: the following argument is a MANDATORY suffix.
args=""
while [ $# -gt 0 ]; do
  if [ "$1" = "-i" ]; then
    shift
    [ $# -eq 0 ] && { echo "sed: option requires an argument -- i" >&2; exit 1; }
    case "$1" in
      -*) echo "sed: $1: No such file or directory" >&2; exit 1 ;;
    esac
    shift
    continue
  fi
  args="$args
$1"
  shift
done
IFS='
'
# shellcheck disable=SC2086
exec {REAL_SED} $args
"""


def _bsd_sed_dir(tmp_path):
    """A directory holding a `sed` that refuses GNU-only `-i` the way BSD does.

    ⚠️ POSIX-ONLY BY CONSTRUCTION, and skipped rather than adapted on Windows.
    The shim is a `#!/bin/sh` file with no extension: on a POSIX host the kernel
    honours the shebang, but Windows has no shebang and resolves `sed` by
    PATHEXT, so an extensionless file is not a candidate at all — the lookup
    falls straight through to Git Bash's real GNU sed and the shim is silently
    NOT IN EFFECT.

    That is not a hypothetical either: it is exactly what
    `test_the_bsd_sed_shim_actually_rejects_the_gnu_only_form` caught on
    `windows-latest` (`assert 0 != 0` — the GNU-only form succeeded). The
    control did its job, which is the reason to keep it: without it the macOS
    guard would have gone GREEN on Windows over a sed it never replaced.

    ⛔ Skipping here is honest and adapting would not be. The dialect this
    models is BSD, which macOS runners have and Windows runners never will;
    a `sed.bat` wrapper would test the wrapper. The POSIX legs (ubuntu and
    macos) are where the guard has to run, and it runs on both.
    """
    if os.name == "nt":  # pragma: no cover — see docstring; PATHEXT, not laziness
        pytest.skip("the BSD-sed shim is a POSIX shebang script; PATHEXT ignores it")
    real = shutil.which("sed")
    if real is None:  # pragma: no cover — no sed at all
        pytest.skip("no sed on PATH")
    shim_dir = tmp_path / "bsdsed"
    shim_dir.mkdir()
    shim = shim_dir / "sed"
    shim.write_text(_BSD_SED_SHIM.replace("{REAL_SED}", real), encoding="utf-8")
    shim.chmod(0o755)
    return shim_dir, real


def test_the_bsd_sed_shim_actually_rejects_the_gnu_only_form(tmp_path):
    """⛔ POSITIVE CONTROL for the shim itself, before anything is concluded from it.

    A shim that silently forwards everything would make the macOS guard below
    pass on a script that is still broken — the shim would be the false green.
    So assert BOTH directions: the GNU-only form must die exactly as BSD sed
    dies, and the portable form must still work.
    """
    shim_dir, _ = _bsd_sed_dir(tmp_path)
    env = {**os.environ, "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"}
    target = tmp_path / "f.txt"

    target.write_text("alpha\n", encoding="utf-8")
    gnu_only = subprocess.run(  # noqa: S603
        ["sed", "-i", "-e", "s/alpha/beta/", str(target)],
        capture_output=True, text=True, encoding="utf-8", env=env, check=False,
    )
    assert gnu_only.returncode != 0, (
        "the shim accepted `sed -i -e`, so it is NOT in effect and every "
        "conclusion drawn from it below would be vacuous"
    )
    assert "No such file or directory" in gnu_only.stderr, gnu_only.stderr
    assert target.read_text(encoding="utf-8") == "alpha\n", "the file was edited anyway"

    portable = subprocess.run(  # noqa: S603
        ["sed", "s/alpha/beta/", str(target)],
        capture_output=True, text=True, encoding="utf-8", env=env, check=False,
    )
    assert portable.returncode == 0, portable.stderr
    assert portable.stdout == "beta\n", portable.stdout


def test_the_falsification_script_runs_on_the_macos_sed_dialect(tmp_path):
    """⛔ The script must reach the SAME verdict under BSD sed as under GNU sed.

    This is the test that would have caught PR #310's macOS-only failure from a
    Linux container. It reproduces the dialect rather than the platform, so it
    runs on every runner instead of only on the one that breaks.

    Asserting the VERDICT, not merely exit 0: a script that dies before the
    sabotage step could still be made to exit 0 by a careless `|| true`, and
    would then report a verdict about an unmodified tree.
    """
    if os.name == "nt":  # pragma: no cover — the script is POSIX shell
        pytest.skip("POSIX shell script")
    shim_dir, _ = _bsd_sed_dir(tmp_path)
    env = {**os.environ, "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"}

    # ⛔ Prove the shim is IN EFFECT before concluding anything from a green run.
    # If PATH ordering did not take, `sed` is the host's real one and this test
    # would assert the script works under a dialect it never met — passing for
    # the wrong reason, which is the defect class this whole ticket is about.
    #
    # ⚠️ THE PROBE MUST USE A REAL FILE. An earlier draft probed a NONEXISTENT
    # path, which fails on BOTH dialects (GNU sed for the missing file, BSD sed
    # for the swallowed `-e`) — so it passed whether or not the shim was there
    # and proved nothing. Against a file that EXISTS the two genuinely diverge:
    # GNU sed edits it and exits 0, BSD sed dies. That divergence IS the check.
    canary = tmp_path / "canary.txt"
    canary.write_text("alpha\n", encoding="utf-8")
    engaged = subprocess.run(  # noqa: S603
        ["sed", "-i", "-e", "s/alpha/beta/", str(canary)],
        capture_output=True, text=True, encoding="utf-8", env=env, check=False,
    )
    assert engaged.returncode != 0, (
        "the BSD-sed shim is not in effect — `sed -i -e` succeeded, which is "
        f"GNU behaviour. This run would prove nothing: {engaged!r}"
    )
    assert canary.read_text(encoding="utf-8") == "alpha\n", (
        "the shim edited the file anyway; it is not emulating BSD sed"
    )

    result = subprocess.run(  # noqa: S603
        ["bash", str(FALSIFY_SH)],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )
    assert result.returncode == 0, (
        "the falsification script fails under BSD sed — this is the "
        f"macos-latest failure, reproduced:\n{result.stdout}\n{result.stderr}"
    )
    assert "GUARD IS SIGHTED" in result.stdout, result.stdout
    assert "sabotage applied" in result.stdout, (
        "the script exited 0 without reaching the sabotage step, so its verdict "
        f"describes a tree it never modified:\n{result.stdout}"
    )


def test_no_shell_script_uses_the_gnu_only_in_place_sed_form():
    """Ratchet: `sed -i` must not re-enter the repo's shell scripts.

    The executable guard above covers ONE script. This covers the other eleven,
    at a cost of one regex — and it is the cheaper half, because the defect is
    a spelling with a portable alternative rather than a subtle behaviour.

    ⚠️ Read a green run as "no `sed -i` in tracked shell scripts", never as
    "these scripts are portable". Other GNU-isms (`grep -P`, `readlink -f`,
    `date -d`) are NOT modeled here; the bound is stated rather than implied.
    """
    offenders = []
    for script in sorted((REPO / "scripts").glob("*.sh")):
        for lineno, line in enumerate(
            script.read_text(encoding="utf-8").splitlines(), start=1
        ):
            code = line.split("#", 1)[0]
            if re.search(r"(?:^|[|;&(\s])sed\s+(?:-[a-zA-Z]+\s+)*-i(?:\s|$)", code):
                offenders.append(f"{script.relative_to(REPO)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "`sed -i` is GNU-only in this form and dies on macOS runners. Write "
        "through a temp file and `mv` it, as scripts/ps374_falsify_patch_evidence.sh "
        "does with sed_inplace().\n" + "\n".join(offenders)
    )


# ═══════════════════════════════════════════════════════════════════════════
# THE EXPOSED POPULATION — narrower than "every profile"
# ═══════════════════════════════════════════════════════════════════════════
def test_cdp_is_opt_in_and_both_conditions_are_required():
    """⚠️ The leak is live on AI-controlled CHROMIUM profiles, not on all of them.

    `opens_cdp_channel` requires BOTH `ai_control` AND an EFFECTIVE engine of
    chromium. Asserting "all profiles are affected" would be wrong, and the
    stored-vs-effective distinction is the half that is easy to get backwards:
    a mobile profile STORING `firefox` is reconciled to chromium and DOES open a
    channel, so reading the stored field would report "closed" over a listening
    port. Both directions are asserted here.
    """
    from src.models.profile import Profile
    from src.services.browser.automation_channel import opens_cdp_channel

    # ai_control off — no channel, whatever the engine.
    assert opens_cdp_channel(
        Profile(name="a", os_type="windows", engine="chromium", ai_control=False)
    ) is False

    # ai_control on + chromium — the exposed population.
    assert opens_cdp_channel(
        Profile(name="b", os_type="windows", engine="chromium", ai_control=True)
    ) is True

    # ai_control on + a genuine firefox — no CDP port at any ai_control value.
    assert opens_cdp_channel(
        Profile(name="c", os_type="windows", engine="firefox", ai_control=True)
    ) is False

    # ⭐ STORED vs EFFECTIVE: an android profile storing "firefox" is reconciled
    # to chromium, so it DOES open a channel. Trusting the stored field here
    # would report a closed channel over a listening port.
    mobile = Profile(name="d", os_type="android", engine="firefox", ai_control=True)
    from src.services.browser.process import effective_engine

    assert effective_engine(mobile) == "chromium"
    assert opens_cdp_channel(mobile) is True


# ═══════════════════════════════════════════════════════════════════════════
# THE LIVE ARM — skipped, never silently passed
# ═══════════════════════════════════════════════════════════════════════════
def _engine_binary() -> str | None:
    """A fingerprint-chromium to measure, or None.

    `PS374_ENGINE_BINARY` is the explicit door. Nothing on PATH is substituted:
    a stock chromium is the CONTROL for this measurement, and running the
    product assertion against it would report a false condemnation.
    """
    candidate = os.environ.get("PS374_ENGINE_BINARY")
    if candidate and pathlib.Path(candidate).is_file():
        return candidate
    return None


@pytest.mark.requires_capability("browser_chromium")
def test_live_shipped_engine_suppresses_console_after_runtime_enable(probe):
    """The behavioural check, against a real binary over a real CDP channel.

    ⚠️ SKIPPED where no engine is provisioned — and that skip is a MESSAGE, not
    a pass. `tests/KNOWN_SKIPS.md` is why: an absent engine must never read as a
    clean bill of health, which is the exact defect this whole ticket is about.
    """
    binary = _engine_binary()
    if binary is None:
        pytest.skip(
            "chromium engine not runnable here: set PS374_ENGINE_BINARY to a "
            "fingerprint-chromium binary to run the live arm"
        )
    reading = probe.measure(binary, "live")
    code, headline, _ = probe.verdict(reading)
    assert code != probe.EXIT_INCONCLUSIVE, f"probe could not measure: {headline}"
    assert code == probe.EXIT_PATCH_PRESENT, headline
