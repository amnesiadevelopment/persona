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

    requested = _requested_capabilities(config)
    unknown = [n for n in requested if n not in CAPABILITIES]
    if unknown:
        # Loud, not lenient. A typo'd capability name that was quietly ignored
        # would silently disable the very guard being asked for — which is the
        # defect this file exists to close, one level up.
        known = ", ".join(sorted(CAPABILITIES))
        raise pytest.UsageError(
            f"unknown test capability {', '.join(sorted(unknown))!r} "
            f"(via {REQUIRE_ENV_VAR} or --require-capability). "
            f"Known capabilities: {known}."
        )
    config._persona_required_capabilities = requested  # type: ignore[attr-defined]
    config._persona_capability_failures = {}  # type: ignore[attr-defined]


def _required(config: pytest.Config) -> list[str]:
    return list(getattr(config, "_persona_required_capabilities", []))


def _declared_marker_capabilities(item: pytest.Item) -> set[str]:
    names: set[str] = set()
    for marker in item.iter_markers(name="requires_capability"):
        names.update(str(a) for a in marker.args)
    return names


def capability_for_skip(
    reason: str, declared: set[str] | None = None
) -> Capability | None:
    """Which capability, if any, a skip of this reason belongs to.

    An explicit marker wins over reason matching: a test that names its own
    capability is never re-classified by the text it happened to print.
    """
    for name in sorted(declared or ()):
        cap = CAPABILITIES.get(name)
        if cap is not None:
            return cap
    for cap in CAPABILITIES.values():
        if cap.matches_reason(reason):
            return cap
    return None


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
    cap = capability_for_skip(reason, _declared_marker_capabilities(item))
    if cap is None or cap.name not in required:
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
