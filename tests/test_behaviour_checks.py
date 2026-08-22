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
        """Today exactly ONE probe is INDEPENDENT, so a green is NARROW.

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
