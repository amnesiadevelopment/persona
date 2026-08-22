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
from collections.abc import Iterator
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
    """

    name: str
    summary: str
    provisioned_by: str
    reason_patterns: tuple[str, ...] = field(default=())

    def matches_reason(self, reason: str) -> bool:
        low = reason.lower()
        return any(p.lower() in low for p in self.reason_patterns)


CAPABILITIES: dict[str, Capability] = {
    # The one this ticket exists for. BOTH guards on the real-Firefox probes
    # live here, because from an operator's seat they are one question ("can
    # this machine run a real browser?") answered in two places:
    #   * the import guard  — `pytest.importorskip("playwright.sync_api")`
    #   * the launch guard  — `pytest.skip(f"firefox not runnable here: {exc}")`
    # Measured, not assumed: `invisible_playwright` (the engine pinned in
    # pyproject.toml) ships NO `playwright` package of its own and hard-depends
    # on upstream `playwright>=1.55,<=1.61.0`. So wherever `pip install .` has
    # run, the IMPORT guard passes and the LAUNCH guard is the one that fires.
    # Provisioning the pip package alone therefore does NOT make these probes
    # run — the browser BINARY is the missing piece.
    "browser": Capability(
        name="browser",
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
        "a real app and a real browser, so these are slow (~45-60s each) and "
        "are selected/deselected as a group with `-m ui_driver`.",
    )

    requested = _requested_capabilities(config)
    unknown = [n for n in requested if n not in CAPABILITIES]
    if unknown:
        # Loud, not lenient. A typo'd capability name that was quietly ignored
        # would silently disable the very guard being asked for — which is the
        # defect this file exists to close, one level up. The marker path holds
        # the same rule at collection; see pytest_collection_modifyitems.
        raise _unknown_capabilities_error(
            unknown, f"via {REQUIRE_ENV_VAR} or --require-capability"
        )
    config._persona_required_capabilities = requested  # type: ignore[attr-defined]
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
    causes rather than overlapping, and a reason is one cause.
    """
    if declared:
        for name in sorted(declared):
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
