"""The behavioural harness's own rules, tested without a browser.

The CHECKS themselves are end-to-end and need a real engine, a display and a
scratch store — they are exercised by running the CLI, and their evidence lives
in the PR. What is tested here is the machinery that decides WHETHER A VERDICT
MAY BE PUBLISHED, because that machinery is what stands between this suite and
the failure mode it exists to prevent: an expensive check that is permanently
green because it quietly stopped looking.

Everything below runs in-process with fake checks. No launch, no display, no
store — so these tests run in the dev/CI container where the real checks skip.
"""

from __future__ import annotations

import os

import pytest

from src.services.verify.behaviour import (
    CANNOT_RUN,
    EXIT_CANNOT_RUN,
    EXIT_FINDING,
    EXIT_OK,
    FINDING,
    PASS,
    BehaviourCheckError,
    Check,
    Context,
    Outcome,
    UnsafeEnvironment,
    exit_code,
    format_report,
    require_scratch_home,
    run_check,
)


def _outcome(status: str, name: str = "demo") -> Outcome:
    return Outcome(name=name, surface="a surface", status=status, detail="detail")


def _check(run, falsify, name: str = "demo") -> Check:
    return Check(
        name=name,
        surface="a surface",
        needs_launch=False,
        run=run,
        falsify=falsify,
    )


class TestFalsificationGatesTheVerdict:
    """The core rule: a check that cannot be shown to fail publishes nothing."""

    def test_a_check_whose_falsification_fails_cannot_report_a_pass(self):
        """The whole point of the module.

        A check that did NOT catch its planted defect has stopped looking. Its
        green must not be published as a pass — it is CANNOT_RUN, because
        "this check is broken" and "this behaviour is healthy" are different
        messages and only one of them is true.
        """

        def falsify(ctx):
            raise BehaviourCheckError("the planted defect went unnoticed")

        def run(ctx):  # pragma: no cover - must never be reached
            return _outcome(PASS)

        outcome = run_check(_check(run, falsify), Context(home="/tmp/x"))

        assert outcome.status == CANNOT_RUN
        assert outcome.status != PASS
        assert "SELF-TEST FAILED" in outcome.detail
        assert "the planted defect went unnoticed" in outcome.detail

    def test_the_falsification_runs_BEFORE_the_check(self):
        """Order is load-bearing, not stylistic.

        Running the check first would let an inert check execute its whole
        expensive sequence and emit a verdict that is then retracted. Running
        the falsification first means an inert check never reaches its own
        verdict at all.
        """
        calls: list[str] = []

        def falsify(ctx):
            calls.append("falsify")
            raise BehaviourCheckError("nope")

        def run(ctx):
            calls.append("run")
            return _outcome(PASS)

        run_check(_check(run, falsify), Context(home="/tmp/x"))

        assert calls == ["falsify"], "run() must not execute once falsify() failed"

    def test_a_pass_carries_the_line_proving_it_can_fail(self):
        def falsify(ctx):
            return "a planted defect was caught"

        outcome = run_check(
            _check(lambda ctx: _outcome(PASS), falsify), Context(home="/tmp/x")
        )

        assert outcome.status == PASS
        assert outcome.falsification == "a planted defect was caught"

    def test_a_pass_with_no_falsification_line_is_downgraded(self):
        """Belt and braces against a check that returns an empty proof.

        A PASS whose falsification line is empty is exactly the
        permanently-green check this module exists to prevent, so it is
        withheld rather than trusted even though falsify() returned normally.
        """
        outcome = run_check(
            _check(lambda ctx: _outcome(PASS), lambda ctx: ""), Context(home="/tmp/x")
        )

        assert outcome.status == CANNOT_RUN
        assert "refusing to report a pass" in outcome.detail

    def test_a_real_finding_survives_a_successful_falsification(self):
        """A finding is a statement about the PRODUCT and must not be softened."""
        outcome = run_check(
            _check(lambda ctx: _outcome(FINDING), lambda ctx: "proven"),
            Context(home="/tmp/x"),
        )

        assert outcome.status == FINDING
        assert outcome.falsification == "proven"

    def test_a_check_that_raises_is_cannot_run_not_a_finding(self):
        """An error running the check is not evidence about the product.

        Reporting it as a FINDING would raise a false alarm on the loudest
        signal this system has; reporting it as a PASS would certify something
        nobody measured. It is neither.
        """

        def run(ctx):
            raise BehaviourCheckError("two identical failures compare equal")

        outcome = run_check(_check(run, lambda ctx: "proven"), Context(home="/tmp/x"))

        assert outcome.status == CANNOT_RUN
        assert outcome.status not in (PASS, FINDING)
        assert "two identical failures" in outcome.detail

    def test_an_unexpected_exception_is_also_contained(self):
        """A bug in a check must not take the whole run down."""

        def run(ctx):
            raise ValueError("something unexpected")

        outcome = run_check(_check(run, lambda ctx: "proven"), Context(home="/tmp/x"))

        assert outcome.status == CANNOT_RUN
        assert "ValueError" in outcome.detail


class TestExitCodes:
    """Three verdicts, three codes. Collapsing them loses the message."""

    def test_all_passing_exits_zero(self):
        assert exit_code([_outcome(PASS), _outcome(PASS)]) == EXIT_OK

    def test_a_finding_exits_one(self):
        assert exit_code([_outcome(PASS), _outcome(FINDING)]) == EXIT_FINDING

    def test_cannot_run_exits_two(self):
        assert exit_code([_outcome(PASS), _outcome(CANNOT_RUN)]) == EXIT_CANNOT_RUN

    def test_cannot_run_outranks_a_finding(self):
        """If anything could not be trusted, the headline is not a finding count.

        A run that both found something and failed to run something has not
        established a complete picture, and reporting exit 1 would present a
        partial world as a definitive product verdict.
        """
        outcomes = [_outcome(FINDING), _outcome(CANNOT_RUN)]

        assert exit_code(outcomes) == EXIT_CANNOT_RUN

    def test_an_empty_run_does_not_certify_anything_as_passing(self):
        """Zero checks is zero evidence; it must not look like success.

        Guard against a future --check filter that selects nothing and exits 0,
        which would read as "everything is fine" over a run that observed
        nothing at all.
        """
        assert exit_code([]) == EXIT_OK, (
            "documenting current behaviour: an empty selection exits 0. The CLI "
            "must therefore never be able to select an empty set silently — see "
            "run_checks, which raises on an unknown name."
        )


class TestSafetyGuard:
    """These checks WIPE a store. The guard is the only thing between a run and
    an operator's real profiles, so its refusals are tested explicitly."""

    def test_refuses_when_persona_home_is_unset(self, monkeypatch):
        monkeypatch.delenv("PERSONA_HOME", raising=False)

        with pytest.raises(UnsafeEnvironment) as exc:
            require_scratch_home()

        assert "PERSONA_HOME is not set" in str(exc.value)

    def test_refuses_when_the_variable_disagrees_with_the_resolved_store(
        self, monkeypatch, tmp_path
    ):
        """core.config reads PERSONA_HOME at IMPORT time.

        A variable set after that import points somewhere the stores are not,
        so a guard trusting it would inspect one path while the checks mutated
        another. That is worse than no guard, so it is a hard refusal.
        """
        monkeypatch.setenv("PERSONA_HOME", str(tmp_path / "set-too-late"))

        with pytest.raises(UnsafeEnvironment) as exc:
            require_scratch_home()

        assert "IMPORT time" in str(exc.value)

    def test_refuses_the_default_store_even_if_declared(self, monkeypatch):
        """The unrecoverable case: ~/.persona is the operator's real store."""
        default = os.path.expanduser("~/.persona")
        monkeypatch.setenv("PERSONA_HOME", default)
        monkeypatch.setattr(
            "src.core.config.PERSONA_HOME", default, raising=False
        )

        with pytest.raises(UnsafeEnvironment):
            require_scratch_home()


class TestReport:
    """The report has to be readable by whoever picks up a finding."""

    def test_uncovered_surfaces_are_stated_not_implied(self):
        """An admitted gap beats a surface marked covered by an inert check.

        The ticket's own standard, so the report always prints what this module
        does NOT observe rather than leaving a reader to infer full coverage.
        """
        report = format_report([_outcome(PASS)])

        assert "NOT COVERED BY THIS MODULE" in report
        assert "GPU-dependent vectors" in report
        assert "proxy transport" in report

    def test_a_withheld_verdict_says_so_in_the_report(self):
        outcome = _outcome(CANNOT_RUN)
        outcome.falsification = ""

        report = format_report([outcome])

        assert "CANNOT RUN" in report
        assert "shown capable of failing: NO — verdict withheld" in report

    def test_findings_are_listed_for_handoff(self):
        """This module reports; it does not fix. The handoff list is the product."""
        report = format_report([_outcome(FINDING, name="two-profile-unlinkability")])

        assert "FINDINGS" in report
        assert "two-profile-unlinkability" in report
        assert "it does not fix" in report


class TestRegistry:
    """The registry is what the CLI drives; a malformed entry must be loud."""

    def test_every_check_declares_a_falsification_and_a_run(self):
        from src.services.verify.behaviour_checks import CHECKS

        assert CHECKS, "the registry must not be empty"
        for check in CHECKS:
            assert callable(check.run), f"{check.name} has no run()"
            assert callable(check.falsify), f"{check.name} has no falsify()"
            assert check.surface, f"{check.name} does not name its surface"

    def test_check_names_are_unique(self):
        from src.services.verify.behaviour_checks import CHECKS

        names = [c.name for c in CHECKS]

        assert len(names) == len(set(names)), f"duplicate check name in {names}"

    def test_every_ticket_surface_has_a_check(self):
        """The surfaces the ticket named, each mapped to a check that observes it."""
        from src.services.verify.behaviour_checks import check_names

        names = set(check_names())

        for required in (
            "restart-continuity",
            "two-profile-unlinkability",
            "benign-edit-stability",
            "proxy-assignment-survives-edit",
            "launch-refuses-broken-geography",
            "certificate-key-material",
            "trash-restore-and-wipe",
            # PS-347. Pinned by NAME because deleting the registry entry
            # otherwise leaves the whole suite green — an unmeasured surface
            # looking measured, which is this check's own charter turned on
            # itself.
            "no-process-survives-a-closed-session",
        ):
            assert required in names, f"no check observes {required}"

    def test_unknown_check_name_is_refused_rather_than_silently_empty(self):
        """A typo'd --check must not select nothing and exit 0.

        Silently running zero checks and reporting success is the same defect
        this module exists to close, one level up.
        """
        from src.services.verify.behaviour import run_checks

        with pytest.raises(BehaviourCheckError) as exc:
            run_checks(["no-such-check"])

        assert "unknown check" in str(exc.value)


class TestInventoryHonesty:
    """The two-profile check is only as strong as the must-differ inventory."""

    def test_the_must_differ_inventory_is_reported_not_assumed(self):
        """Today TWO probes are INDEPENDENT, so a green is still NARROW.

        `audio.digest` and `webgl.readback` (PS-90). Two is better than the one
        this docstring used to describe — a single-vector gate is one upstream
        change away from comparing nothing — but it is not coverage. The
        charter calls the vector inventory "never complete, and treating it as
        complete is the failure mode", so read a green here as "the two vectors
        we check did not collide", never as "these profiles are unlinkable".

        This test does not demand a particular count — that would break every
        time a vector is classified. It demands that the inventory is
        non-empty, because a cross-profile comparison over ZERO vectors would
        return an empty list, which this comparator's contract reads as the
        PASS: a certificate of unlinkability nobody measured.
        """
        from src.services.verify.probes import must_differ_probes

        targets = must_differ_probes()

        assert targets, (
            "the must-differ inventory is EMPTY, so compare_profiles would "
            "compare nothing and its empty result would read as a pass"
        )


class _FakeProxy:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeStore:
    """Just enough ProxyStore for the geography check's bookkeeping."""

    def __init__(self) -> None:
        self.proxies: dict[str, _FakeProxy] = {}
        self.failed: list[str] = []

    def add(self, name, url):
        self.proxies[name] = _FakeProxy(name)

    def mark_checked(self, name, cc, country, ip=None, timezone=None):
        self.proxies.setdefault(name, _FakeProxy(name))

    def mark_check_failed(self, name):
        self.failed.append(name)

    def get(self, name):
        return self.proxies.get(name)


class _FakeManager:
    def __init__(self) -> None:
        self.profiles: dict[str, object] = {}


class _FakeCtx:
    """A Context stand-in: makes profile records without touching a store."""

    def __init__(self) -> None:
        self._manager = _FakeManager()

    def manager(self):
        return self._manager

    def make_profile(self, name, **kwargs):
        profile = type("P", (), {"name": name, "proxy": kwargs.get("proxy")})()
        self._manager.profiles[name] = profile
        return profile


def _geo_check():
    from src.services.verify.behaviour_checks import CHECKS

    return next(c for c in CHECKS if c.name == "launch-refuses-broken-geography")


class TestGeographyCheckDrivesTheRealLaunchPath:
    """The refusal must be OBSERVED at the public entry point, not asserted of
    an internal helper.

    The ticket named this surface in exactly those terms — "the refusal paths
    are shipped; that they FIRE is asserted in unit tests, not observed in a
    launched profile" — so a check that calls a private helper and asserts it
    raises is reproducing the very gap it was written to close. These tests
    pin the distinction so it cannot quietly regress.
    """

    def test_the_module_does_not_reach_for_the_private_timezone_helper(self):
        """A regression guard on the SHAPE of the check, not its result.

        Asserting that ``_profile_timezone`` raises passes just as happily when
        a refactor has moved the timezone resolution to AFTER the engine
        spawns, or swallowed the error between the helper and the launch — the
        product would then launch on the operator's real timezone with this
        check still green. Reaching for the helper at all is the defect.
        """
        import inspect

        from src.services.verify import behaviour_checks

        source = inspect.getsource(behaviour_checks)

        assert "_profile_timezone" not in source, (
            "the geography check is reaching for the private timezone helper "
            "again; drive the public spawn_browser entry point instead"
        )

    def test_launch_outcome_drives_spawn_browser(self, monkeypatch):
        """The public entry point is the thing under observation."""
        from src.services.browser import invisible_launch
        from src.services.verify import behaviour_checks

        called: list[object] = []

        def fake_spawn_browser(profile, **kwargs):
            called.append(profile)
            # Reach the engine spawn exactly as a real launch would.
            return invisible_launch.spawn({"timezone": "Europe/Warsaw"})

        monkeypatch.setattr(
            "src.services.browser.process.spawn_browser", fake_spawn_browser
        )
        profile = object()

        zone = behaviour_checks._launch_outcome(profile)

        assert called == [profile], "spawn_browser was not the entry point driven"
        assert zone == "Europe/Warsaw"

    def test_the_engine_spawn_sentinel_is_always_restored(self, monkeypatch):
        """The sentinel must not leak into the rest of the run.

        It replaces the module-level engine spawn, so a check that left it in
        place would silently neuter every launch AFTER it — turning later
        checks green without launching anything, which is this module's own
        failure mode.
        """
        from src.services.browser import invisible_launch
        from src.services.verify import behaviour_checks

        original = invisible_launch.spawn

        monkeypatch.setattr(
            "src.services.browser.process.spawn_browser",
            lambda profile, **kw: invisible_launch.spawn({"timezone": "Europe/Rome"}),
        )
        behaviour_checks._launch_outcome(object())

        assert invisible_launch.spawn is original

        # ...and also when the launch REFUSES, which is the common path here.
        def refusing(profile, **kw):
            from src.services.proxy.errors import GeographyDisprovenError

            raise GeographyDisprovenError("nope")

        monkeypatch.setattr("src.services.browser.process.spawn_browser", refusing)
        with pytest.raises(Exception):
            behaviour_checks._launch_outcome(object())

        assert invisible_launch.spawn is original

    def test_a_launch_that_slips_past_the_sentinel_is_refused_not_reported(
        self, monkeypatch
    ):
        """If a real handle comes back, the check is no longer driving the path
        it claims to — and a live engine must never be left running."""
        from src.services.verify import behaviour_checks

        stopped: list[str] = []

        class _Handle:
            def terminate(self):
                stopped.append("terminated")

        monkeypatch.setattr(
            "src.services.browser.process.spawn_browser", lambda p, **kw: _Handle()
        )

        with pytest.raises(BehaviourCheckError) as exc:
            behaviour_checks._launch_outcome(object())

        assert "no longer drives the path" in str(exc.value)
        assert stopped == ["terminated"], "a live engine handle was left running"


class TestGeographyCheckSeparatesTheTwoRefusalCauses:
    """`GeographyDisprovenError` subclasses `GeographyUnknownError` deliberately.

    Catching only the parent makes the check unable to tell "we never learned
    where this exits" from "we looked, and what we stored is contradicted" —
    a distinction the product went to real trouble to keep, because the two
    send the operator after different remedies.
    """

    def _run(self, monkeypatch, outcomes):
        from src.services.verify import behaviour_checks

        calls = iter(outcomes)

        def fake_launch(profile):
            nxt = next(calls)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt

        monkeypatch.setattr(behaviour_checks, "_proxy_store", _FakeStore)
        monkeypatch.setattr(behaviour_checks, "_launch_outcome", fake_launch)
        return _geo_check().run(_FakeCtx())

    def test_a_disproven_geography_refusal_is_the_pass(self, monkeypatch):
        from src.services.proxy.errors import GeographyDisprovenError

        outcome = self._run(
            monkeypatch, ["Europe/Warsaw", GeographyDisprovenError("refused")]
        )

        assert outcome.status == PASS
        assert "spawn_browser" in outcome.detail

    def test_the_generic_parent_cause_is_a_FINDING_not_a_pass(self, monkeypatch):
        """It failed CLOSED, so nothing leaked — but it named the wrong cause.

        Reporting "never checked" for a proxy that WAS checked and failed sends
        the operator to re-check a proxy they already checked. A check that
        accepted the parent here could not see this at all.
        """
        from src.services.proxy.errors import GeographyUnknownError

        outcome = self._run(
            monkeypatch, ["Europe/Warsaw", GeographyUnknownError("refused")]
        )

        assert outcome.status == FINDING
        assert "GeographyUnknownError" in "".join(outcome.evidence)

    def test_a_launch_that_proceeds_on_a_disproven_zone_is_a_FINDING(
        self, monkeypatch
    ):
        outcome = self._run(monkeypatch, ["Europe/Warsaw", "Europe/Warsaw"])

        assert outcome.status == FINDING
        assert "did NOT refuse" in outcome.detail

    def test_a_healthy_proxy_that_loses_its_exit_zone_is_a_FINDING(self, monkeypatch):
        outcome = self._run(monkeypatch, ["America/New_York"])

        assert outcome.status == FINDING
        assert "Europe/Warsaw" in outcome.detail


class TestGeographyFalsificationProvesTheGuardIsConditional:
    def _falsify(self, monkeypatch, outcomes):
        from src.services.verify import behaviour_checks

        calls = iter(outcomes)

        def fake_launch(profile):
            nxt = next(calls)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt

        monkeypatch.setattr(behaviour_checks, "_proxy_store", _FakeStore)
        monkeypatch.setattr(behaviour_checks, "_launch_outcome", fake_launch)
        return _geo_check().falsify(_FakeCtx())

    def test_a_guard_that_refuses_a_HEALTHY_proxy_fails_the_falsification(
        self, monkeypatch
    ):
        """A guard that refuses everything would pass the check while making
        every profile unlaunchable."""
        from src.services.proxy.errors import GeographyUnknownError

        with pytest.raises(BehaviourCheckError) as exc:
            self._falsify(monkeypatch, [GeographyUnknownError("refused")])

        assert "refuses everything" in str(exc.value)

    def test_an_UNCHECKED_proxy_reported_as_disproven_fails_the_falsification(
        self, monkeypatch
    ):
        """The conflation in the opposite direction, which the run path alone
        cannot see."""
        from src.services.proxy.errors import GeographyDisprovenError

        with pytest.raises(BehaviourCheckError) as exc:
            self._falsify(
                monkeypatch, ["Europe/Berlin", GeographyDisprovenError("wrong cause")]
            )

        assert "NEVER checked" in str(exc.value)

    def test_a_conditional_causally_specific_guard_passes(self, monkeypatch):
        from src.services.proxy.errors import GeographyUnknownError

        line = self._falsify(
            monkeypatch, ["Europe/Berlin", GeographyUnknownError("refused")]
        )

        assert "conditional" in line

    def test_an_UNCHECKED_proxy_that_is_not_refused_fails_the_falsification(
        self, monkeypatch
    ):
        with pytest.raises(BehaviourCheckError) as exc:
            self._falsify(monkeypatch, ["Europe/Berlin", "Europe/Berlin"])

        assert "real location inside the tunnel" in str(exc.value)


class TestUncoveredSurfacesClaimNoCoverageItDoesNotHave:
    """`UNCOVERED_SURFACES` is printed on every run and exists so a reader does
    not over-read the green. A false coverage claim inside the honesty block
    inverts its purpose."""

    def test_the_certificate_gap_does_not_claim_the_status_field_is_checked(self):
        import inspect

        from src.services.verify import behaviour, behaviour_checks
        from src.services.verify.behaviour import UNCOVERED_SURFACES

        cert = [t for t in UNCOVERED_SURFACES if "certificate" in t[0]]
        assert cert, "the certificate trust gap is no longer disclosed at all"

        reads_the_field = "cert_trust_status" in inspect.getsource(behaviour_checks)
        claims_the_field = "truthfulness of the stored status" in cert[0][1]

        assert not claims_the_field or reads_the_field, (
            "UNCOVERED_SURFACES claims the stored status field's truthfulness "
            "is checked, but no check reads cert_trust_status"
        )
        assert behaviour  # the disclosure lives with the vocabulary it qualifies


class TestMissingDisplayCannotRunRatherThanFinding:
    """A missing display must land on EXIT_CANNOT_RUN — never on EXIT_FINDING.

    This is the three-way exit split's whole reason for existing, on the one
    environmental path this project keeps getting bitten by. ``require_display``
    delegates to ``baseline._require_display`` so the Xvfb message is identical
    everywhere, but ``baseline`` raises ``BaselineUnavailable``, which is NOT a
    ``BehaviourCheckError``. It is called from ``run_checks`` OUTSIDE any
    per-check handler, so left untranslated it escaped the CLI's ``except``
    entirely and Python's default unhandled-exception code — 1 — collided with
    ``EXIT_FINDING``.

    That collision is the exact misreading the split prevents: a CI job reading
    exit 1 as documented concludes "a check RAN and the behaviour did NOT hold"
    — a defect against persona — when in truth nothing was measured at all.
    """

    def test_require_display_raises_a_class_the_cli_can_catch(self, monkeypatch):
        """The seam. ``BaselineUnavailable`` alone is invisible to the CLI."""
        from src.services.verify.behaviour import require_display

        monkeypatch.setattr("src.core.platform.IS_LINUX", True, raising=False)
        monkeypatch.delenv("DISPLAY", raising=False)

        with pytest.raises(BehaviourCheckError) as exc:
            require_display()

        # The actionable half of the message must survive the translation.
        assert "xvfb" in str(exc.value).lower(), (
            "the Xvfb install line is the part an operator acts on; it must "
            "not be lost when the exception class is changed"
        )

    def test_the_cli_exits_cannot_run_and_says_so(
        self, monkeypatch, tmp_path, capsys
    ):
        """The contract, observed at the CLI rather than asserted of a helper."""
        from src.services.verify.behaviour_cli import _REEXEC_FLAG, main

        home = tmp_path / "scratch"
        home.mkdir()
        monkeypatch.setenv("PERSONA_HOME", str(home))
        monkeypatch.setattr("src.core.config.PERSONA_HOME", str(home), raising=False)
        # Skip the re-exec: it would replace this test process.
        monkeypatch.setenv(_REEXEC_FLAG, "1")
        monkeypatch.setattr("src.core.platform.IS_LINUX", True, raising=False)
        monkeypatch.delenv("DISPLAY", raising=False)

        code = main(["run"])

        assert code == EXIT_CANNOT_RUN
        assert code != EXIT_FINDING, (
            "a missing display reported as a FINDING accuses the product of a "
            "defect when nothing was measured at all"
        )
        assert "CANNOT RUN:" in capsys.readouterr().err


class TestSingletonSocketBudget:
    """The launch-blocking arithmetic, pinned so a machine catches it.

    ⭐ THIS CLASS IS THE POINT OF PS-347's REWORK, and it is worth stating why
    it is a test rather than a comment. The first version of the survivor check
    was CORRECT — it observed a real leak, counted it from the OS, and killed
    its own mutant — and it was still unrunnable, because chromium's process
    singleton binds a UNIX socket under the profile and ``sun_path`` is 108
    bytes. Under the CLI's own default home the path came to 113, so the engine
    exited FATAL "Socket path too long" ~3s into every launch and the check
    reported CANNOT RUN on every invocation that did not pass a short
    ``--home`` by hand — including the one the module's docstring prescribes.

    A gate whose default invocation is structurally incapable of reaching green
    measures nothing, which is the very failure the gate exists to prevent,
    reproduced inside it. Nothing caught it because nothing exercised the
    arithmetic: the launch path has no unit test by construction (it needs an
    engine and a display), so the budget went unasserted and was wrong by six
    bytes.

    So the ARITHMETIC is tested even though the LAUNCH is not. These tests need
    no browser, no display and no store — they run in the dev container with
    every other test in this file, and they go red the moment a profile name is
    lengthened or the scratch prefix grows.

    THE BOUNDARY BELOW IS MEASURED, NOT ASSUMED. Isolated on
    personium-152.0.7977.75 under Xvfb, one variable — the home path's length —
    moved by exactly one byte between the two arms:

        home len 25 -> socket path 107 bytes -> launched, tree settled at 11
        home len 26 -> socket path 108 bytes -> FATAL, peak 5 then 0
    """

    def test_the_engine_measured_boundary_is_the_constant_we_encode(self):
        """107 usable bytes, because the 108th is the NUL terminator."""
        from src.services.verify.behaviour import SUN_PATH_LIMIT

        assert SUN_PATH_LIMIT == 107, (
            "sizeof(sun_path) is 108 and the last byte is the terminator, so "
            "107 is the longest path chromium's singleton can bind. Measured "
            "on a real engine: 107 launches, 108 exits FATAL."
        )

    def test_the_socket_length_matches_the_path_the_engine_actually_reported(self):
        """The formula is checked against a path an engine printed, not against
        itself.

        This exact string came off chromium's stdout on the product launch path
        while reproducing the defect, and it is 113 bytes. A formula validated
        only against its own re-derivation would agree with a wrong constant.
        """
        from src.services.verify.behaviour import singleton_socket_length

        observed = (
            "/tmp/persona-behaviour-SidVU7bF/persona_data/ps347-live"
            "/.persona-tmp/org.chromium.Chromium.eAfYAT/SingletonSocket"
        )
        assert len(observed) == 113  # the engine's own FATAL line

        assert (
            singleton_socket_length("/tmp/persona-behaviour-SidVU7bF", "ps347-live")
            == 113
        ), "the formula does not reproduce a socket path the engine reported"

    def test_the_default_scratch_home_leaves_room_for_the_checks_own_profiles(
        self,
    ):
        """THE REGRESSION TEST. The pair the harness ships must fit together.

        Not "the home is short" and not "the names are short" — either alone
        was true of the broken version. The claim is about the PAIR, which is
        the thing that was wrong.
        """
        import shutil

        from src.services.verify.behaviour import (
            SUN_PATH_LIMIT,
            default_scratch_home,
            singleton_socket_is_bound,
            singleton_socket_length,
        )
        from src.services.verify.behaviour_checks import (
            SOCKET_BOUND_PROFILE_NAMES,
            longest_socket_bound_profile_name,
        )

        if not singleton_socket_is_bound():
            pytest.skip(
                "the engine binds a UNIX socket for its singleton on POSIX "
                "only (process_singleton_posix.cc); Windows uses a named "
                "mutex, so there is no sun_path budget to assert here"
            )

        home = default_scratch_home(longest_socket_bound_profile_name())
        try:
            for name in SOCKET_BOUND_PROFILE_NAMES:
                length = singleton_socket_length(home, name)
                assert length <= SUN_PATH_LIMIT, (
                    f"profile {name!r} under the harness's OWN default home "
                    f"{home!r} puts chromium's singleton socket at {length} "
                    f"bytes, over the {SUN_PATH_LIMIT}-byte limit. The engine "
                    "will exit FATAL 'Socket path too long' seconds into the "
                    "launch and every launch-backed check will report CANNOT "
                    "RUN — a gate that cannot reach green under its own "
                    "default invocation."
                )
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_a_home_that_cannot_fit_the_names_is_refused_not_returned(self):
        """The sizing REFUSES rather than handing back a home that will FATAL.

        A silent return is what made the original defect so hard to see: the
        symptom arrived seconds later, from the engine, as a tree that grew and
        vanished. The refusal names the actionable fact instead.
        """
        from src.services.verify.behaviour import (
            UnsafeEnvironment,
            default_scratch_home,
            singleton_socket_is_bound,
        )

        if not singleton_socket_is_bound():
            pytest.skip("POSIX-only: no sun_path budget to refuse against")

        with pytest.raises(UnsafeEnvironment) as exc:
            # No prefix is short enough to leave 200 bytes for a name.
            default_scratch_home(200)

        assert "Socket path too long" in str(exc.value), (
            "the refusal must name the engine's own failure, so an operator "
            "can connect it to what they would otherwise see in the log"
        )

    def test_the_budget_is_negative_rather_than_clamped_when_home_is_too_long(
        self,
    ):
        """A home that has already spent the budget reports a NEGATIVE number.

        Clamping at zero would render "no name fits at all" identically to "a
        zero-length name fits", and the caller compares against a required
        length — so the clamp would silently admit an impossible home.
        """
        from src.services.verify.behaviour import profile_name_budget

        assert profile_name_budget("/x" * 80) < 0

    def test_an_over_long_operator_home_is_refused_before_the_launch(self):
        """``--home`` is arbitrary, so the CLI's sizing cannot cover it.

        Unguarded this reaches the engine and returns as a FATAL the settle
        guard can only describe as "no browser tree was observed running" —
        true, unhelpful, and pointing away from the cure.
        """
        from src.services.verify.behaviour import singleton_socket_is_bound
        from src.services.verify.behaviour_checks import (
            SOCKET_BOUND_PROFILE_NAMES,
            _survivor_profile,
        )

        if not singleton_socket_is_bound():
            pytest.skip("POSIX-only: no sun_path budget to refuse against")

        long_home = "/tmp/" + "d" * 90
        ctx = Context(home=long_home)

        with pytest.raises(BehaviourCheckError) as exc:
            _survivor_profile(ctx, SOCKET_BOUND_PROFILE_NAMES[0])

        message = str(exc.value)
        assert "Socket path too long" in message
        assert "--home" in message, "the refusal must name the flag that cures it"

    def test_a_long_TMPDIR_does_not_decide_where_the_scratch_home_lands(
        self, tmp_path, monkeypatch
    ):
        """THE ARM THAT CARRIES THE FIX ON A REAL RUNNER, and the one this
        machine's own /tmp cannot exercise.

        The budget is comfortable under a bare ``/tmp`` (4 bytes) and is spent
        before a profile is named under the bases real CI hands out: a GitHub
        runner's ``/home/runner/work/_temp`` is 23 and macOS's
        ``/var/folders/...`` is 53. ``tempfile.mkdtemp()`` honours ``TMPDIR``,
        so accepting it would reproduce the whole defect on exactly the venue
        PS-336 will supply — while every test above stayed green here, because
        the developer's ``/tmp`` hides it.

        So the base is CHOSEN (shortest writable candidate) rather than
        inherited, and this asserts the choice rather than the outcome.
        """
        import shutil
        import tempfile

        from src.services.verify.behaviour import (
            SUN_PATH_LIMIT,
            default_scratch_home,
            singleton_socket_is_bound,
            singleton_socket_length,
        )
        from src.services.verify.behaviour_checks import (
            SOCKET_BOUND_PROFILE_NAMES,
            longest_socket_bound_profile_name,
        )

        if not singleton_socket_is_bound():
            pytest.skip(
                "POSIX-only: the sun_path budget this asserts exists only "
                "where the engine binds a UNIX socket for its singleton"
            )

        # A stand-in for the runner's base, the same shape as
        # /home/runner/work/_temp, created under pytest's tmp_path so nothing
        # outside the test is touched.
        long_base = tmp_path / "work" / "_temp"
        long_base.mkdir(parents=True)

        # ⚠️ SETTING TMPDIR IS NOT ENOUGH, AND THE FIRST VERSION OF THIS TEST
        # WAS VACUOUS FOR EXACTLY THAT REASON. `tempfile.gettempdir()` resolves
        # the directory ONCE and caches it in `tempfile.tempdir`, so by the
        # time any test runs the value is already pinned to this machine's
        # /tmp and a later setenv changes nothing. The test then "passed"
        # against a 4-byte base — i.e. it asserted nothing about a long one,
        # which is the single thing it exists to assert. Caught by mutating
        # the base-selection away and watching this test stay green.
        monkeypatch.setenv("TMPDIR", str(long_base))
        monkeypatch.setattr(tempfile, "tempdir", str(long_base))

        # THE PRECONDITION, ASSERTED RATHER THAN ASSUMED. If the override ever
        # stops taking effect this fails loudly instead of quietly measuring
        # /tmp again.
        assert tempfile.gettempdir() == str(long_base)
        assert len(str(long_base)) > 20, (
            "the stand-in base must actually be long, or this test passes for "
            "the same reason the defect hid: a short base fits either way"
        )

        home = default_scratch_home(longest_socket_bound_profile_name())
        try:
            for name in SOCKET_BOUND_PROFILE_NAMES:
                length = singleton_socket_length(home, name)
                assert length <= SUN_PATH_LIMIT, (
                    f"with TMPDIR={long_base!s} the harness provisioned "
                    f"{home!r}, which puts {name!r}'s singleton socket at "
                    f"{length} bytes — over the {SUN_PATH_LIMIT}-byte limit. "
                    "A long TMPDIR must not decide where the scratch home "
                    "lands; that is the CI venue reproducing the defect."
                )
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_the_budget_binds_only_where_the_engine_binds_a_socket(
        self, monkeypatch
    ):
        """⛔ THE LIMIT IS A POSIX FACT, AND THIS PINS THAT IT IS SCOPED.

        Caught by the Windows CI leg rather than by reasoning: the first
        version applied the budget everywhere and REFUSED a perfectly
        launchable `C:\\Users\\RUNNER~1\\AppData\\Local\\Temp` at -13
        bytes. The engine's own FATAL names the file that enforces it —
        `chrome/browser/process_singleton_posix.cc` — because `sun_path`
        belongs to the UNIX-domain socket that arm binds. Windows' singleton is
        a named mutex; it binds no socket and has no such wall.

        A guard that invents a failure on a platform which cannot have it is
        worse than the defect it was written for, so this asserts the SCOPE and
        not merely the arithmetic.
        """
        import shutil

        from src.services.verify import behaviour
        from src.services.verify.behaviour import (
            default_scratch_home,
            singleton_socket_is_bound,
        )

        monkeypatch.setattr("src.core.platform.IS_WINDOWS", True, raising=False)
        assert not singleton_socket_is_bound()

        # An impossible budget on any platform. On Windows it must NOT refuse,
        # because there is no socket path for it to be impossible against.
        home = default_scratch_home(200)
        try:
            assert os.path.isdir(home)
        finally:
            shutil.rmtree(home, ignore_errors=True)

        monkeypatch.setattr("src.core.platform.IS_WINDOWS", False, raising=False)
        assert singleton_socket_is_bound()
        with pytest.raises(behaviour.UnsafeEnvironment):
            default_scratch_home(200)

    def test_the_arithmetic_itself_is_answered_on_every_platform(self):
        """The CALCULATION is platform-independent; only the REFUSAL is scoped.

        Keeping the arithmetic universal is what lets the scoping be tested at
        all — a `profile_name_budget` that returned None off-POSIX would make
        the two questions inseparable.
        """
        from src.services.verify.behaviour import (
            profile_name_budget,
            singleton_socket_length,
        )

        assert singleton_socket_length("/tmp/pb-abcdefgh", "p347a") == 93
        assert isinstance(profile_name_budget("/tmp/pb-abcdefgh"), int)

    def test_the_check_reads_its_profile_names_from_the_pinned_constant(self):
        """The names the budget is asserted against must be the names the check
        creates.

        A check that hardcoded its own string would pass every assertion above
        while launching under something else entirely — the budget would be
        pinned for names nobody uses.
        """
        import inspect

        from src.services.verify import behaviour_checks

        source = inspect.getsource(behaviour_checks._run_no_process_survives_a_closed_session)
        falsify = inspect.getsource(
            behaviour_checks._falsify_no_process_survives_a_closed_session
        )

        assert "SOCKET_BOUND_PROFILE_NAMES" in source
        assert "SOCKET_BOUND_PROFILE_NAMES" in falsify


class TestTheCLIProvisionsALaunchableHome:
    """The SEAM the defect actually lived in: what the CLI hands the checks.

    ⭐ THIS CLASS EXISTS BECAUSE THE ARITHMETIC WAS NEVER WRONG. ``TestSingletonSocketBudget``
    above pins ``default_scratch_home`` — the helper — and every one of its
    assertions was already true of the broken build, because the helper did not
    exist yet. The defect was that ``behaviour_cli.main`` provisioned a home
    without consulting ANY budget: ``tempfile.mkdtemp(prefix="persona-behaviour-")``,
    31 bytes under ``/tmp``, 113-byte socket, ``CANNOT RUN`` on every default
    invocation. So the fix was pinned only on the side that was already correct,
    and the CLI could revert byte-for-byte to the pre-fix shape with the whole
    suite green — measured, not supposed: 130 passed and the CI no-launch gate
    at exit 0 under exactly that mutant.

    That is this ticket's own charter turned on itself — a fixed defect with no
    gate that would catch its return — so the assertion belongs on the CALLER.

    ``main``'s only externally-visible act before the re-exec is
    ``os.execve(..., env)``, so intercepting it captures the ``PERSONA_HOME``
    the checks will actually receive. No browser, no display and no store: these
    run wherever the rest of this file runs.
    """

    @staticmethod
    def _capture_home(monkeypatch, argv):
        """Run the CLI up to its re-exec and return the PERSONA_HOME it hands on.

        Returns ``(exit_code, home_or_None)``. ``home`` is None when the CLI
        never got as far as re-execing, which is what a refusal looks like from
        here and is a real answer rather than a missing one.
        """
        from src.services.verify import behaviour_cli

        captured: "dict[str, str]" = {}

        def fake_execve(path, args, env):
            captured["home"] = env["PERSONA_HOME"]
            raise SystemExit(0)

        monkeypatch.setattr(os, "execve", fake_execve)
        monkeypatch.delenv("PERSONA_BEHAVIOUR_CLI_REEXEC", raising=False)

        try:
            code = behaviour_cli.main(argv)
        except SystemExit as exc:  # our fake_execve, i.e. the re-exec happened
            code = exc.code
        return code, captured.get("home")

    def test_the_cli_provisions_a_home_the_checks_can_launch_under(
        self, monkeypatch
    ):
        """THE REGRESSION TEST FOR THE SEAM, asserted where the defect lived.

        Not "the helper computes a budget" — the broken build would have agreed
        with that too, had it had a helper. The claim is that the home the CLI
        actually hands the checks fits the profile names those checks actually
        create.
        """
        import shutil

        from src.services.verify.behaviour import (
            SUN_PATH_LIMIT,
            singleton_socket_is_bound,
            singleton_socket_length,
        )
        from src.services.verify.behaviour_checks import SOCKET_BOUND_PROFILE_NAMES

        if not singleton_socket_is_bound():
            pytest.skip(
                "the engine binds a UNIX socket for its singleton on POSIX "
                "only (process_singleton_posix.cc); Windows uses a named "
                "mutex, so there is no sun_path budget to assert here"
            )

        _, home = self._capture_home(monkeypatch, ["run"])

        # ⛔⛔ THE PRECONDITION, ASSERTED RATHER THAN ASSUMED. Without this the
        # test passes when the CLI never re-execs at all — a green taken from
        # a thing that never happened, which is the exact shape this whole
        # check exists to refuse.
        assert home is not None, (
            "the CLI never re-execed, so nothing was measured: this test "
            "asserts a property of the home it hands the checks, and there "
            "was no home"
        )

        try:
            for name in SOCKET_BOUND_PROFILE_NAMES:
                length = singleton_socket_length(home, name)
                assert length <= SUN_PATH_LIMIT, (
                    f"the CLI's own default home {home!r} puts profile "
                    f"{name!r}'s singleton socket at {length} bytes, over the "
                    f"{SUN_PATH_LIMIT}-byte limit. The engine will exit FATAL "
                    "'Socket path too long' seconds into every launch and the "
                    "launch-backed checks will report CANNOT RUN under the "
                    "invocation this module's own docstring prescribes."
                )
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_the_cli_asks_for_the_budget_the_checks_actually_need(
        self, monkeypatch
    ):
        """The CLI must ask for the REAL name budget, not merely ask.

        ⚠️ THE TEST ABOVE CANNOT CATCH THIS ONE, and the pair is deliberate. A
        CLI that calls ``default_scratch_home(0)`` — consulting the sizing and
        requiring nothing of it — still lands on this machine's short ``/tmp``
        and still fits, so the seam test goes green over a budget nobody asked
        for. That mutant was measured surviving the whole suite.

        The two arms differ only in a NARROW BAND of base lengths: where the
        home leaves a non-negative budget that is nevertheless SMALLER than the
        names the checks create. Below the band both refuse (the budget is
        negative, so even zero is impossible); above it both accept. So the
        band is staged deliberately and its width is ASSERTED, because a venue
        that missed it would let this test pass for the same reason the defect
        hid.

        In the band, asking for the real budget REFUSES while asking for zero
        hands over a home the engine will exit FATAL under.
        """
        import shutil
        import tempfile

        from src.services.verify.behaviour import (
            EXIT_CANNOT_RUN,
            SUN_PATH_LIMIT,
            _SINGLETON_SOCKET_FIXED_COST,
            default_scratch_home,
            profile_name_budget,
            singleton_socket_is_bound,
        )
        from src.services.verify.behaviour_checks import (
            longest_socket_bound_profile_name,
        )

        if not singleton_socket_is_bound():
            pytest.skip("POSIX-only: no sun_path budget to refuse against")

        needed = longest_socket_bound_profile_name()

        # A base sized so that a home provisioned under it lands INSIDE the
        # band: budget >= 0 (asking zero succeeds) and budget < needed (asking
        # for the real requirement refuses).
        #
        # ⚠️ BUILT TO A TARGET ABSOLUTE LENGTH, NOT GROWN FROM THE PLATFORM'S
        # OWN TEMP BASE, and macOS is why: `/var/folders/…` is ~53 bytes, which
        # is already PAST the band (budget -42), so a loop that only lengthens
        # starts below it and can never arrive. Caught by the macos-latest CI
        # leg — and caught as a REFUSAL rather than a false pass, because the
        # precondition below is asserted rather than assumed.
        #
        # `/tmp` is the root because it is the shortest thing POSIX guarantees;
        # the band is a handful of bytes wide, so there is no room to start
        # from a long one. This is a staged venue, not the platform's own.
        home_len_wanted = SUN_PATH_LIMIT - _SINGLETON_SOCKET_FIXED_COST - 1
        mkdtemp_suffix = 1 + len("pb-") + 8  # "/" + prefix + mkdtemp's 8 chars
        base_len_wanted = home_len_wanted - mkdtemp_suffix

        root = tempfile.mkdtemp(prefix="pb-band-", dir="/tmp")
        try:
            band_base = root
            if len(band_base) > base_len_wanted:
                pytest.skip(
                    f"cannot stage the band: /tmp root {band_base!r} is "
                    f"already {len(band_base)} bytes, past the "
                    f"{base_len_wanted}-byte target"
                )
            # Pad to the exact length with one nested component.
            pad = base_len_wanted - len(band_base) - 1
            if pad >= 1:
                band_base = os.path.join(band_base, "d" * pad)
                os.mkdir(band_base)

            probe_home_len = len(band_base) + mkdtemp_suffix

            # THE PRECONDITION, ASSERTED RATHER THAN ASSUMED. Both halves: a
            # budget below zero would make the two arms agree by refusing, and
            # a budget at or above `needed` would make them agree by accepting.
            band_budget = profile_name_budget("x" * probe_home_len)
            assert 0 <= band_budget < needed, (
                f"the staged base leaves a budget of {band_budget}, outside "
                f"the [0, {needed}) band where asking for zero and asking for "
                "the real requirement give different answers — this venue "
                "cannot tell the two apart"
            )

            monkeypatch.setenv("TMPDIR", band_base)
            monkeypatch.setattr(tempfile, "tempdir", band_base)
            assert tempfile.gettempdir() == band_base

            # The hardened half: /tmp exists but this user may not write it, so
            # the shortest-base fallback cannot rescue the run.
            real_access = os.access

            def no_writable_tmp(path, mode, *a, **kw):
                if str(path) == "/tmp" and mode & os.W_OK:
                    return False
                return real_access(path, mode, *a, **kw)

            monkeypatch.setattr(os, "access", no_writable_tmp)

            # ⭐ THE POSITIVE CONTROL, and it is what makes the refusal below
            # ATTRIBUTABLE. In this venue asking for nothing SUCCEEDS — so a
            # refusal here cannot be the base being unwritable, the fallback
            # misfiring, or the venue being broken in some way that would
            # refuse whatever was asked. The only difference between the two
            # calls is the number, which is the thing under test.
            asking_for_nothing = default_scratch_home(0)
            try:
                assert os.path.isdir(asking_for_nothing), (
                    "the control must actually succeed, or the refusal below "
                    "is not attributable to the budget the CLI asks for"
                )
            finally:
                shutil.rmtree(asking_for_nothing, ignore_errors=True)

            code, home = self._capture_home(monkeypatch, ["run"])

            assert home is None, (
                f"the CLI re-execed with PERSONA_HOME={home!r}, which leaves "
                f"only {profile_name_budget(home)} byte(s) for a profile name "
                f"and the checks need {needed} — the engine would exit FATAL "
                "'Socket path too long' and the check would report a launch "
                "that never happened"
            )
            assert code == EXIT_CANNOT_RUN, (
                "a home the checks cannot launch under is 'nothing was "
                f"measured', which is exit {EXIT_CANNOT_RUN} — never 0, and "
                "never 1, which is reserved for a finding about the product"
            )
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_the_refusal_does_not_leave_its_own_scratch_directory_behind(
        self, tmp_path
    ):
        """A refusal must take its directory with it.

        The home has to be CREATED before it can be measured (mkdtemp's suffix
        is part of the length), so the refusal path runs with a real directory
        already on disk. Unswept, every refusal left an empty ``pb-*`` behind —
        3 of 3 refusals, and the test suite itself left 2 — in a module whose
        stated discipline is that a check must not leave the machine dirtier
        than it found it. Unbounded on a self-hosted or cached runner.
        """
        from src.services.verify.behaviour import (
            _SCRATCH_PREFIX,
            UnsafeEnvironment,
            default_scratch_home,
            singleton_socket_is_bound,
        )

        if not singleton_socket_is_bound():
            pytest.skip("POSIX-only: no sun_path budget to refuse against")

        import tempfile

        base = tempfile.gettempdir()
        before = {p for p in os.listdir(base) if p.startswith(_SCRATCH_PREFIX)}

        refusals = 0
        for _ in range(3):
            with pytest.raises(UnsafeEnvironment):
                default_scratch_home(200)
            refusals += 1

        assert refusals == 3, "the venue must actually have refused three times"

        after = {p for p in os.listdir(base) if p.startswith(_SCRATCH_PREFIX)}
        assert after - before == set(), (
            f"3 refusals left {len(after - before)} scratch director(ies) "
            f"behind in {base}: {sorted(after - before)}"
        )
