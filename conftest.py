"""Make a test that declined to run say so — and, where declared, make it fail.

A test that fails is a message. A test that silently declines to run is the
ABSENCE of a message, indefinitely, and it looks exactly like success. For a
product whose defining defect class is a leak — which announces itself to
nobody — a verification layer that can quietly stop verifying is the specific
failure mode that matters most.

Two halves, deliberately separate:

1. REPORTING (always on, no flag, `addopts` in pyproject.toml). Every run
   prints which tests skipped and the reason each gave. This is honest
   everywhere and changes no outcome.

2. DECLARED CAPABILITIES (opt-in, off by default). A machine PROVISIONED to
   run the browser probes says so:

       PERSONA_REQUIRED_CAPABILITIES=browser python -m pytest

   In that environment a browser-probe skip stops being acceptable and becomes
   a FAILURE naming what was missing. An ordinary developer run declares
   nothing, still skips, and still passes.

WHY THE DECLARATION IS EXPLICIT AND NEVER INFERRED. The tempting shortcut is
"playwright imported, therefore this machine should run browser tests". That
re-creates the original defect one level up: it concludes "not supported here"
on exactly the machine where support broke, which is the one case that must be
loud. Nothing here reads the presence of the thing being checked. The only
input is the operator's declaration, and a declaration naming a capability that
does not exist is a hard error rather than a silent no-op — a typo that
disabled the guard would be the original defect wearing a new hat.

The mechanism keys off the SKIP REASON, not off a list of test names, so it is
not specific to any one fixture: the next real-browser test that skips with
"firefox not runnable here: ..." inherits this behaviour with nobody
remembering to wire it. A test may also declare itself precisely with
`@pytest.mark.requires_capability("browser")`, which wins over reason matching.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

import pytest

#: Environment variable an operator uses to declare what this machine supports.
#: Comma- or space-separated capability names from ``CAPABILITIES`` below.
REQUIRE_ENV_VAR = "PERSONA_REQUIRED_CAPABILITIES"


@dataclass(frozen=True)
class Capability:
    """One thing an environment can be provisioned to supply.

    ``reason_patterns`` are matched case-insensitively against the reason a
    skip GAVE. They are how a capability catches tests it was never wired to:
    the reason text is written at the guard, so a new probe that skips for the
    same cause is covered the day it lands.

    ``includes`` makes a capability an UMBRELLA over more specific ones.
    Declaring the umbrella declares every member, so an operator keeps saying
    the broad thing they mean ("this machine runs real browsers") while the
    table stays specific enough to name WHICH engine a skip was about. An
    umbrella carries no ``reason_patterns`` of its own: classification must
    land on the member, because "a browser did not run" is not an actionable
    sentence — "the Firefox binary is missing" is.

    ``excludes`` is the other half of that, and it is the reason this class
    grew two fields instead of one. An umbrella that silently covered less than
    its name suggests would be the original defect wearing a new hat: a job
    declaring "browser" would read as "real browsers are enforced here" while
    one engine went unlaunched and unmentioned. A capability named here is one
    this umbrella deliberately does NOT cover, and every declared run PRINTS
    it — so the gap is stated on every run rather than inferred by a reader who
    thinks to diff the table. Naming a gap is not the same as closing it, and
    this field exists precisely so the two are never confused.
    """

    name: str
    summary: str
    provisioned_by: str
    reason_patterns: tuple[str, ...] = field(default=())
    includes: tuple[str, ...] = field(default=())
    excludes: tuple[str, ...] = field(default=())

    @property
    def is_umbrella(self) -> bool:
        return bool(self.includes)

    def matches_reason(self, reason: str) -> bool:
        low = reason.lower()
        return any(p.lower() in low for p in self.reason_patterns)


CAPABILITIES: dict[str, Capability] = {
    # WHY THIS IS THREE ENTRIES AND NOT ONE. "browser" used to mean, verbatim,
    # "a real Firefox the playwright API can launch" — one name for one engine,
    # with no way to say anything about the other. That is a problem because
    # CHROMIUM IS THE ENGINE THE PRODUCT DEFAULTS TO: `DEFAULT_ENGINE` is
    # "chromium" (src/services/profile/coherence.py:78) and it is the engine
    # every impossible os_type/engine pair is reconciled TOWARD (:99, :162).
    # The engine the product defaults to was the one no gate ever launched.
    #
    # A single engine-blind name made that gap INVISIBLE rather than merely
    # open: a job declaring "browser" reads as "real-browser coverage is
    # enforced here", and it is — for Firefox only. The declaration could not
    # EXPRESS the gap. Splitting the name is what lets the table say which
    # engine is covered and which is not, and the absence of chromium from the
    # umbrella below is now a NAMED gap that an operator can read.
    #
    # THIS CHANGES NO OUTCOME FOR ANY EXISTING DECLARATION. `browser` survives
    # as an UMBRELLA over the engines it covers, so `ci.yml`'s
    # PERSONA_REQUIRED_CAPABILITIES=browser and every existing
    # `@pytest.mark.requires_capability("browser")` police exactly what they
    # policed before — the Firefox probes — and nothing new.
    #
    # An UMBRELLA carries no reason_patterns of its own deliberately —
    # every skip classifies as the engine-specific member, so the failure an
    # operator reads names the engine and the binary, not "a browser".
    "browser": Capability(
        name="browser",
        summary="real browsers this project's probes can launch",
        provisioned_by=(
            "provision the engines this umbrella covers — see "
            "`browser_firefox`. It is an umbrella: declaring it declares "
            "every capability it includes"
        ),
        includes=("browser_firefox",),
        # NOT a TODO and NOT an oversight — the gap, stated. Declaring
        # `browser` today buys Firefox coverage and NOTHING for the engine the
        # product defaults to. Every declared run prints this, so the sentence
        # "real-browser coverage is enforced here" can never again be read as
        # covering both engines. See `browser_chromium` for why closing it is a
        # separate question rather than a line in this table.
        excludes=("browser_chromium",),
    ),
    # Today's `browser`, unchanged in meaning, wording and patterns. BOTH
    # guards on the real-Firefox probes live here, because from an operator's
    # seat they are one question ("can this machine run a real Firefox?")
    # answered in two places:
    #   * the import guard  — `pytest.importorskip("playwright.sync_api")`
    #   * the launch guard  — `pytest.skip(f"firefox not runnable here: {exc}")`
    # Measured, not assumed: `invisible_playwright` (the engine pinned in
    # pyproject.toml) ships NO `playwright` package of its own and hard-depends
    # on upstream `playwright>=1.55,<=1.61.0`. So wherever `pip install .` has
    # run, the IMPORT guard passes and the LAUNCH guard is the one that fires.
    # Provisioning the pip package alone therefore does NOT make these probes
    # run — the browser BINARY is the missing piece.
    "browser_firefox": Capability(
        name="browser_firefox",
        summary="a real Firefox the playwright API can launch",
        provisioned_by=(
            "install the engine (`pip install .`, which pulls upstream "
            "playwright), then download the browser binary "
            "(`python -m playwright install firefox`) and give it a display "
            "or a headless-capable sandbox"
        ),
        reason_patterns=(
            "playwright not installed",          # the import guard's reason=
            "could not import 'playwright",      # importorskip's own wording
            "firefox not runnable here",         # the launch guard
        ),
    ),
    # THE NAMED GAP. Deliberately NOT in the umbrella above, and that absence
    # is the point of this entry: nothing in CI provisions this engine, so
    # folding it into `browser` would turn every declaring job red for a
    # capability no job supplies — a gate that fails for want of provisioning
    # rather than for want of correctness. Declaring it is opt-in, and today it
    # would be a declaration no machine can honour.
    #
    # WHAT RIDES ON IT, measured at this commit: src/services/browser/process.py
    # returns early for firefox (:353-356) BEFORE all 13 `extensions.append`
    # calls (`grep -c "extensions.append" src/services/browser/process.py` → 13)
    # — audio, WebGL, GPU, device, voice, locale, mobile, native-cloak, stealth,
    # canvas-ctx, measuretext, search, geo. None is exercised by any gate on any
    # platform.
    #
    # THE PATTERNS BELOW ARE PROSPECTIVE, AND THAT IS STATED RATHER THAN
    # IMPLIED: no probe in `tests/` guards on this engine today. They are here
    # so the first one that lands is classified the day it lands, which is the
    # same "catches tests it was never wired to" property the other entries
    # rely on. They must NOT read as evidence that a chromium probe exists.
    #
    # DISJOINT FROM `ui_driver` ON PURPOSE. That capability owns "chromium not
    # runnable here" for a DIFFERENT chromium — the system browser its UI
    # driver attaches to (tests/test_ui_driven.py:55). Matching is substring
    # matching, so the wording here is "chromium ENGINE not runnable here":
    # neither string contains the other, and the partition stays disjoint in
    # both directions. tests/test_skip_visibility.py pins that.
    "browser_chromium": Capability(
        name="browser_chromium",
        summary=(
            "a real fingerprint-chromium — the engine DEFAULT_ENGINE names — "
            "that the product's own launch path can start"
        ),
        provisioned_by=(
            "NOTHING PROVISIONS THIS TODAY, IN CI OR ANYWHERE ELSE — that is "
            "the gap this capability exists to name rather than to hide. It is "
            "NOT `python -m playwright install chromium`: the product launches "
            "fingerprint-chromium, not playwright's chromium build. The engine "
            "binary arrives through the `download_engine` route that "
            ".github/workflows/engine-autoupdate.yml:104-115 uses against a "
            "pinned baseline tag, so a chromium equivalent is an OPEN QUESTION "
            "(which build, which pin, which runner), not a promise made here. "
            "Do not declare this capability until that question is answered"
        ),
        reason_patterns=(
            # Prospective — see the note above. Kept distinct from ui_driver's
            # "chromium not runnable here" in both directions.
            "chromium engine not runnable here",
            "fingerprint-chromium not available",
        ),
    ),
    # The node-backed probes cross-check generated JS against a real engine.
    "node": Capability(
        name="node",
        summary="a `node` binary on PATH for the JS cross-check probes",
        provisioned_by="install Node.js and ensure `node` resolves on PATH",
        reason_patterns=("node not available",),
    ),
    # The engine packages themselves. Absent in a bare checkout; present
    # wherever `pip install .` has run, which includes the release pipeline.
    "engine": Capability(
        name="engine",
        summary="the invisible_playwright / invisible_core engine packages",
        provisioned_by="`pip install .` (both are direct pyproject dependencies)",
        reason_patterns=(
            "could not import 'invisible_playwright",
            "could not import 'invisible_core",
        ),
    ),
    # Driving persona's OWN flet UI (tests/test_ui_driven.py). Distinct from
    # "browser" above, which is about launching a browser as the PRODUCT does:
    # this one needs flet installed so the UI can be SERVED in web mode, plus a
    # chromium the driver can attach to. Measured on this container: `flet` is
    # declared in requirements.txt but is NOT present in the dev image, and the
    # playwright browser cache carries firefox only — so both halves genuinely
    # have to be provisioned, and neither can be inferred from the other.
    "ui_driver": Capability(
        name="ui_driver",
        summary="flet plus a chromium binary, so persona's own UI can be driven",
        provisioned_by=(
            "`pip install flet==0.85.3` (to serve the UI in web mode) and "
            "install a chromium at /usr/bin/chromium (the playwright cache "
            "ships firefox only, so the driver uses the system binary)"
        ),
        reason_patterns=(
            "flet not installed",       # the serve-side import guard
            "could not import 'flet",   # importorskip's own wording
            "chromium not runnable here",  # the driver-side binary guard
        ),
    ),
}


def _specificity_key(name: str) -> tuple[bool, str]:
    """Order capability names so an UMBRELLA is considered last.

    Both are checked — this only decides which one gets to write the failure
    message when several qualify. "install the Firefox binary" is an
    instruction; "provision the engines this umbrella covers" is a redirection,
    and handing a reader the redirection when the specific answer was available
    would relocate the dead end this file exists to remove.
    """
    cap = CAPABILITIES.get(name)
    return (bool(cap and cap.is_umbrella), name)


def expand_capabilities(names: Iterable[str]) -> list[str]:
    """Every capability a declaration implies, umbrellas resolved to members.

    Declaring an umbrella declares everything under it. This is what keeps
    `PERSONA_REQUIRED_CAPABILITIES=browser` meaning exactly what it meant
    before the engine split: the reason patterns now live on `browser_firefox`,
    so without this expansion a declaration of `browser` would police NOTHING
    and report a confident green — the precise failure this file exists to
    close, re-created by a rename.

    Unknown names are passed through untouched so the caller's own validation
    still sees them; silently dropping one here would disable that error.
    """
    seen: list[str] = []
    stack = list(names)
    while stack:
        name = stack.pop(0)
        if not name or name in seen:
            continue
        seen.append(name)
        cap = CAPABILITIES.get(name)
        if cap is not None:
            # Transitive, and cycle-safe via `seen` — an umbrella of umbrellas
            # resolves rather than recursing forever.
            stack.extend(cap.includes)
    return seen


def _requested_capabilities(config: pytest.Config) -> list[str]:
    raw = os.environ.get(REQUIRE_ENV_VAR, "")
    raw = raw.replace(",", " ")
    names = raw.split()
    names += list(config.getoption("--require-capability") or [])
    seen: list[str] = []
    for name in names:
        if name and name not in seen:
            seen.append(name)
    return seen


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--require-capability",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "Declare that this environment supplies NAME, so a test that "
            "skips for want of it FAILS instead. Repeatable. Equivalent to "
            f"the {REQUIRE_ENV_VAR} environment variable."
        ),
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "requires_capability(name): this test needs a declared environment "
        "capability; skipping it is a failure where that capability is declared.",
    )
    config.addinivalue_line(
        "markers",
        "ui_driver: drives persona's own flet UI through real controls. Boots "
        "a real app and a real browser per test, so the tier is slow and its "
        "cost is dominated by that fixed boot rather than by the interaction "
        "(driving more controls inside one test is close to free). Measured "
        "per-test and whole-suite figures live in tests/UI_DRIVING.md#cost, "
        "which is their single owner — deliberately not restated here, because "
        "a copied number goes stale when the suite changes. Selected and "
        "deselected as a group with `-m ui_driver`.",
    )

    requested = _requested_capabilities(config)
    unknown = [n for n in requested if n not in CAPABILITIES]
    if unknown:
        # Loud, not lenient. A typo'd capability name that was quietly ignored
        # would silently disable the very guard being asked for — which is the
        # defect this file exists to close, one level up. The marker path holds
        # the same rule at collection; see pytest_collection_modifyitems.
        #
        # Validated on what the OPERATOR TYPED, before umbrellas are resolved,
        # so the error quotes their spelling back to them rather than some
        # expanded name they never wrote.
        raise _unknown_capabilities_error(
            unknown, f"via {REQUIRE_ENV_VAR} or --require-capability"
        )
    # Umbrellas resolve HERE, once, so every consumer downstream sees the same
    # expanded set. Declaring `browser` must keep policing the Firefox probes
    # exactly as it did before those patterns moved to `browser_firefox`.
    config._persona_required_capabilities = expand_capabilities(  # type: ignore[attr-defined]
        requested
    )
    config._persona_capability_failures = {}  # type: ignore[attr-defined]


def _required(config: pytest.Config) -> list[str]:
    return list(getattr(config, "_persona_required_capabilities", []))


def _unknown_capabilities_error(unknown: list[str], via: str) -> pytest.UsageError:
    known = ", ".join(sorted(CAPABILITIES))
    return pytest.UsageError(
        f"unknown test capability {', '.join(sorted(unknown))!r} ({via}). "
        f"Known capabilities: {known}."
    )


def _declared_marker_capabilities(item: pytest.Item) -> set[str]:
    names: set[str] = set()
    for marker in item.iter_markers(name="requires_capability"):
        names.update(str(a) for a in marker.args)
    return names


def pytest_collection_modifyitems(
    session: pytest.Session, config: pytest.Config, items: list[pytest.Item]
) -> None:
    """A marker naming a capability that does not exist refuses to run.

    The same rule the environment declaration already holds, applied to the
    other way of declaring. `@pytest.mark.requires_capability("browserr")`
    that was quietly ignored would leave the test entirely UNGUARDED while the
    marker sat there looking like protection — a guard disabled by a typo,
    reporting green. Unknown marker names were the one path into this file
    that failed open; both now fail closed.

    Validated at collection so the error names the offending tests and arrives
    before any of them runs, rather than at the end of a long suite.
    """
    offenders: dict[str, list[str]] = {}
    for item in items:
        for name in sorted(_declared_marker_capabilities(item)):
            if name not in CAPABILITIES:
                offenders.setdefault(name, []).append(item.nodeid)
    if not offenders:
        return
    located = "; ".join(
        f"{name!r} on {', '.join(nodes)}" for name, nodes in sorted(offenders.items())
    )
    raise _unknown_capabilities_error(
        list(offenders), f"via @pytest.mark.requires_capability — {located}"
    )


def capabilities_for_skip(
    reason: str, declared: set[str] | None = None
) -> Iterator[Capability]:
    """EVERY capability this skip belongs to, not merely the first one found.

    An explicit marker wins over reason matching: a test that names its own
    capability is never re-classified by the text it happened to print.

    ALL of a marker's names are yielded, and that is the whole point. Returning
    only one — say the alphabetically first — would make the outcome depend on
    the order the names happen to sort in: a test marked
    ``("browser", "node")`` that skips for want of node would go unguarded on a
    machine declaring `node`, because "browser" sorted first, did not appear in
    the declaration, and ended the search. A green run that enforced nothing is
    precisely the defect this file exists to close, so the search must exhaust
    the declaration rather than abandon it.

    Reason matching stops at the first match by design: the patterns partition
    causes rather than overlapping, and a reason is one cause. Splitting
    `browser` by engine split that partition too, so the engine-specific
    patterns are kept mutually exclusive — and `browser_chromium`'s wording is
    deliberately not a substring of `ui_driver`'s "chromium not runnable here",
    nor that of it, because these are two different chromiums and a skip about
    one must never be reported as the other.

    A MARKER'S NAMES ARE EXPANDED THROUGH UMBRELLAS, for the same reason the
    environment declaration is: `requires_capability("browser")` has to keep
    meaning "the Firefox probes" now that those patterns live on a member.
    The expansion is ordered most-specific-first, so when both an umbrella and
    its member qualify, the reader is handed "install the Firefox binary"
    rather than "provision the engines this umbrella covers".
    """
    if declared:
        for name in sorted(expand_capabilities(sorted(declared)), key=_specificity_key):
            cap = CAPABILITIES.get(name)
            if cap is not None:
                yield cap
        return
    for cap in CAPABILITIES.values():
        if cap.matches_reason(reason):
            yield cap
            return


def capability_for_skip(
    reason: str, declared: set[str] | None = None
) -> Capability | None:
    """The single best capability for a skip, or None.

    Kept for reason-only classification, where "the cause of this skip" is a
    single answer. Anything deciding whether a skip is POLICED must use
    :func:`capabilities_for_skip` and check every declared name against the
    environment's declaration.
    """
    return next(capabilities_for_skip(reason, declared), None)


def _skip_reason(report: pytest.TestReport) -> str:
    longrepr = getattr(report, "longrepr", None)
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        reason = str(longrepr[2])
    else:
        reason = str(longrepr or "")
    # pytest renders a skip's longrepr as "Skipped: <reason>".
    return re.sub(r"^Skipped:\s*", "", reason).strip()


@pytest.hookimpl(wrapper=True, trylast=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    report = yield

    required = _required(item.config)
    if not required or not report.skipped or hasattr(report, "wasxfail"):
        return report

    reason = _skip_reason(report)
    # EVERY declared capability is checked against the environment's
    # declaration, not just the first one found. See capabilities_for_skip:
    # stopping at the first match would let a test marked ("browser", "node")
    # skip for want of node, on a machine declaring node, and still report
    # green — a guard that silently declines to fire, which is this file's own
    # subject matter one level up.
    cap = next(
        (
            c
            for c in capabilities_for_skip(reason, _declared_marker_capabilities(item))
            if c.name in required
        ),
        None,
    )
    if cap is None:
        return report

    # This environment DECLARED it supplies the capability, and the test
    # declined to run anyway. That is a failure, and the message names what
    # was actually missing — the real exception text, not a paraphrase, so
    # "no browser installed" and "the browser refused to start" stay
    # distinguishable.
    report.outcome = "failed"
    report.longrepr = (
        f"{item.nodeid}\n"
        f"This environment declares the {cap.name!r} capability "
        f"({cap.summary}), so this test must RUN — a skip here means the "
        f"declared support is missing or broken, not that skipping is fine.\n"
        f"\n"
        f"  skip reason: {reason}\n"
        f"\n"
        f"To provision it: {cap.provisioned_by}\n"
        f"If this machine genuinely cannot supply it, drop {cap.name!r} from "
        f"{REQUIRE_ENV_VAR} — do not weaken the guard in the test."
    )
    failures = item.config._persona_capability_failures  # type: ignore[attr-defined]
    failures.setdefault(cap.name, []).append(item.nodeid)
    return report


def pytest_terminal_summary(
    terminalreporter, exitstatus, config: pytest.Config
) -> None:
    required = _required(config)
    if not required:
        return

    failures = getattr(config, "_persona_capability_failures", {})
    write = terminalreporter.write_line
    terminalreporter.write_sep("=", "declared capabilities", bold=True)
    for name in required:
        cap = CAPABILITIES[name]
        offenders = failures.get(name, [])
        if offenders:
            write(
                f"FAILED {name}: {len(offenders)} test(s) skipped in an "
                f"environment that declares {cap.summary}:",
                red=True,
            )
            for nodeid in offenders:
                write(f"    {nodeid}", red=True)
            write(f"  provision it with: {cap.provisioned_by}", red=True)
        else:
            write(f"ok {name}: no test declined to run ({cap.summary})", green=True)

    # WHAT THE DECLARATION DOES NOT BUY, printed on every declared run.
    #
    # An "ok browser" line above is true and, on its own, MISLEADING: it says
    # no test declined to run, and a reader takes that as "real browsers are
    # covered here". One engine is covered. The engine the product DEFAULTS to
    # is not launched by anything. Leaving that to be discovered by diffing the
    # capability table would make the gap silent again, which is the whole
    # defect this split exists to remove — so the run SAYS it, next to the
    # green line it qualifies, every time.
    #
    # This changes no outcome. It is a statement, not a gate: a gap that is
    # named is not a gap that is closed, and printing it must never be mistaken
    # for covering it.
    for name in required:
        for missing in CAPABILITIES[name].excludes:
            gap = CAPABILITIES.get(missing)
            if gap is None:
                continue
            write(
                f"gap {name}: does NOT cover {missing!r} ({gap.summary}) — "
                f"declaring {name!r} enforces nothing about it",
                yellow=True,
            )
            write(f"  {gap.provisioned_by}", yellow=True)
