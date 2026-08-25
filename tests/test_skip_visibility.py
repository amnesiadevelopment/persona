"""The skip-visibility mechanism, tested against its own failure modes.

This suite exists because the thing being built is itself a verification
layer, and a verification layer that has only ever been observed staying
quiet has not been observed at all. So every test here drives an OUTCOME —
a real pytest process, a real exit code — rather than asserting that a
helper returns the string it was handed.

The two paths that must both hold, and which pull in opposite directions:

* QUIET — a contributor with no browser declares nothing, the browser probes
  skip, and the run PASSES. Making the loud path work by making an ordinary
  laptop run red would be a worse bug than the one being fixed.
* LOUD — a machine that declares it supplies the browser and then skips a
  browser probe FAILS, naming what was missing.

Most tests run pytest as a subprocess against a throwaway directory holding a
copy of the real conftest.py. That is deliberate: it exercises the actual hook
wiring end to end, and a test written directly against the helper functions
would keep passing if the hooks were never registered at all.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import conftest as persona_conftest

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFTEST = REPO_ROOT / "conftest.py"


def _run_pytest(cwd: Path, *args: str, env_extra: dict[str, str] | None = None):
    import os

    env = dict(os.environ)
    env.pop(persona_conftest.REQUIRE_ENV_VAR, None)
    # A parent run that declared a capability must not leak into the child and
    # silently decide the outcome of these tests.
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:randomly", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    """A throwaway pytest project carrying the REAL conftest under test."""
    shutil.copy(CONFTEST, tmp_path / "conftest.py")
    return tmp_path


# A stand-in for the real firefox_probe: it skips with the SAME reason text the
# launch guard produces, but it is a test this mechanism has never seen. If it
# is caught, the mechanism is keying off the skip reason rather than off a
# hardcoded list of known test names — which is what makes the NEXT real-browser
# test inherit the behaviour with nobody remembering to wire it.
_UNSEEN_BROWSER_TEST = '''
import pytest

@pytest.fixture
def probe():
    pytest.skip("firefox not runnable here: Executable doesn't exist at /nope/firefox")

def test_a_brand_new_browser_probe(probe):
    assert False, "must never execute: the fixture skips first"

def test_unrelated_pure_test():
    assert 1 == 1
'''


def test_an_undeclared_run_still_skips_and_still_passes(sandbox: Path):
    """The quiet path: a laptop without a browser is not punished.

    Guards the regression that would make this whole change a net loss —
    turning every contributor's green run red because they lack a browser
    nobody asked them to install.
    """
    (sandbox / "test_probe.py").write_text(_UNSEEN_BROWSER_TEST)

    result = _run_pytest(sandbox, "-q", "-rs")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
    assert "1 skipped" in result.stdout
    # ...and the skip is VISIBLE, with the real reason, which is the other half
    # of the deliverable: a run says what it did not do.
    assert "firefox not runnable here" in result.stdout


def test_a_declared_environment_turns_a_browser_skip_into_a_failure(sandbox: Path):
    """The loud path, observed red — not assumed.

    Declares browser support in an environment that plainly lacks it and
    asserts the run FAILS and names the offending test.
    """
    (sandbox / "test_probe.py").write_text(_UNSEEN_BROWSER_TEST)

    result = _run_pytest(
        sandbox, "-q", env_extra={persona_conftest.REQUIRE_ENV_VAR: "browser"}
    )

    assert result.returncode != 0, result.stdout + result.stderr
    assert "test_a_brand_new_browser_probe" in result.stdout
    # The unrelated test is untouched — the mechanism fails the probes that
    # declined to run, not the run as a whole.
    assert "1 passed" in result.stdout


def test_the_failure_carries_the_real_exception_not_a_paraphrase(sandbox: Path):
    """"No browser installed" and "the browser refused to start" are not the
    same problem, and the message has to keep them apart.

    The launch guard interpolates the actual exception; a mechanism that
    replaced it with a generic "browser capability missing" would destroy the
    one piece of information that says which failure this is.
    """
    (sandbox / "test_probe.py").write_text(_UNSEEN_BROWSER_TEST)

    result = _run_pytest(
        sandbox, "-q", env_extra={persona_conftest.REQUIRE_ENV_VAR: "browser"}
    )

    assert "Executable doesn't exist at /nope/firefox" in result.stdout
    # and it tells the reader how to fix the environment
    assert "playwright install firefox" in result.stdout


def test_declaring_one_capability_does_not_police_another(sandbox: Path):
    """A machine with node but no browser declares `node` and stays green.

    Without this, the declaration would be all-or-nothing and an operator
    would be pushed back toward declaring nothing at all.
    """
    (sandbox / "test_probe.py").write_text(_UNSEEN_BROWSER_TEST)

    result = _run_pytest(
        sandbox, "-q", env_extra={persona_conftest.REQUIRE_ENV_VAR: "node"}
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 skipped" in result.stdout


def test_a_misspelled_capability_is_a_hard_error_not_a_silent_no_op(sandbox: Path):
    """The original defect, one level up — and the reason this is loud.

    `PERSONA_REQUIRED_CAPABILITIES=browsers` (or `brower`, or a renamed
    capability) that was quietly ignored would report a confident green while
    enforcing nothing at all: exactly the "looks like success, verified
    nothing" failure this whole ticket exists to remove. It must refuse to run.
    """
    (sandbox / "test_probe.py").write_text(_UNSEEN_BROWSER_TEST)

    result = _run_pytest(
        sandbox, "-q", env_extra={persona_conftest.REQUIRE_ENV_VAR: "browsers"}
    )

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "unknown test capability" in output
    # and it names the valid options rather than leaving the operator guessing
    assert "browser" in output and "node" in output


def test_nothing_infers_support_from_the_thing_being_checked(sandbox: Path):
    """The trap the ticket names: a guard that decides "playwright imported,
    therefore this machine should run browser tests" concludes "not supported
    here" on exactly the machine where support broke.

    Asserted structurally, because it is an absence: the capability layer must
    make no import attempt and consult nothing but the operator's declaration.
    A future edit that "helpfully" probes for playwright fails here.

    Parsed as an AST rather than grepped as text, deliberately. The conftest
    NAMES `importorskip` repeatedly in prose — it documents the very guards it
    polices — so a substring check over the source would either fail on the
    documentation or force the documentation to be deleted to stay green. What
    must be absent is the CALL, not the word.
    """
    import ast

    tree = ast.parse(CONFTEST.read_text())
    probing = {"importorskip", "import_module", "find_spec", "which", "__import__"}

    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name:
                called.add(name)
        # a bare `import playwright` anywhere in the capability layer is the
        # same inference by another route
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = getattr(node, "module", None) or ""
            names = [a.name for a in node.names] + [mod]
            assert not any(
                n.split(".")[0] in {"playwright", "invisible_playwright", "invisible_core"}
                for n in names
                if n
            ), f"capability layer must not import the thing it checks: {names}"

    leaked = probing & called
    assert not leaked, (
        f"conftest.py calls {sorted(leaked)} — support must be DECLARED, never "
        "inferred from the presence of the thing being checked, or the guard "
        "goes quiet on exactly the machine where support broke"
    )

    # The single input is the declaration.
    assert persona_conftest.REQUIRE_ENV_VAR == "PERSONA_REQUIRED_CAPABILITIES"


def test_an_explicit_marker_beats_reason_matching(sandbox: Path):
    """A test may name its own capability instead of relying on its wording.

    Reason matching is the net that catches unwired tests; the marker is the
    precise statement for a test whose skip reason says nothing recognisable.
    """
    (sandbox / "test_marked.py").write_text(
        "import pytest\n"
        "@pytest.mark.requires_capability('browser')\n"
        "def test_marked_but_cryptic_reason():\n"
        "    pytest.skip('conditions not met')\n"
    )

    declared = _run_pytest(
        sandbox, "-q", env_extra={persona_conftest.REQUIRE_ENV_VAR: "browser"}
    )
    assert declared.returncode != 0, declared.stdout
    assert "test_marked_but_cryptic_reason" in declared.stdout

    # ...and it is still an honest skip where nothing is declared.
    quiet = _run_pytest(sandbox, "-q")
    assert quiet.returncode == 0, quiet.stdout
    assert "1 skipped" in quiet.stdout


def test_a_marker_is_policed_on_every_capability_it_names(sandbox: Path):
    """A multi-capability marker must be checked against ALL its names.

    The regression: the classifier once returned only the FIRST declared
    capability it recognised, alphabetically, and the caller tested that single
    name against the declaration. So a test marked ("browser", "node"), skipping
    because node was missing, on a machine that declares `node`, reported GREEN
    — "browser" sorted first, was not in the declaration, and ended the search.
    A guard whose firing depends on the alphabetical order of its own arguments
    is a guard that silently declines to fire, which is this file's subject
    matter one level up.

    `node` is chosen deliberately: it does NOT sort first. A fix that still
    considers only one name cannot pass this test.
    """
    (sandbox / "test_both.py").write_text(
        "import pytest\n"
        "@pytest.mark.requires_capability('browser', 'node')\n"
        "def test_needs_browser_and_node():\n"
        "    pytest.skip('node not available')\n"
    )

    result = _run_pytest(
        sandbox, "-q", env_extra={persona_conftest.REQUIRE_ENV_VAR: "node"}
    )

    assert result.returncode != 0, (
        "a skip for want of node, on a machine declaring node, reported green: "
        + result.stdout
    )
    assert "test_needs_browser_and_node" in result.stdout
    # ...and it names the capability the operator actually declared, with THAT
    # capability's provisioning advice — telling them to install a browser here
    # would send them after the wrong missing thing.
    assert "Node.js" in result.stdout

    # The first-listed name still works, so the fix widened the check rather
    # than swapping which single name wins.
    other = _run_pytest(
        sandbox, "-q", env_extra={persona_conftest.REQUIRE_ENV_VAR: "browser"}
    )
    assert other.returncode != 0, other.stdout

    # ...and an undeclared run is still an honest skip.
    quiet = _run_pytest(sandbox, "-q")
    assert quiet.returncode == 0, quiet.stdout
    assert "1 skipped" in quiet.stdout


def test_a_marker_adds_to_reason_matching_instead_of_replacing_it(sandbox: Path):
    """A skip is classified by BOTH what the test declared and what was missing.

    The regression, from PS-174: a two-armed test needs chromium AND firefox,
    but only chromium GATES it — the firefox arm is unreachable unless chromium
    ran first — so its marker names `browser_chromium` alone. Under the old
    rule a marker SUPPRESSED reason matching entirely, which made that honest,
    precise marker strictly WEAKER than a vaguer one: a genuine
    "firefox not runnable here" skip classified as `browser_chromium` only,
    which no environment declares, so the firefox guard went silent on exactly
    the machine provisioned to catch it.

    The marker states what a test NEEDS; the reason states what was actually
    MISSING. They are different facts and a skip belongs to both. The union can
    only ever police MORE, never less, so no guard can go quiet because of it.
    """
    (sandbox / "test_two_armed.py").write_text(
        "import pytest\n"
        "@pytest.mark.requires_capability('browser_chromium')\n"
        "def test_two_armed_probe():\n"
        "    pytest.skip('firefox not runnable here: no binary')\n"
    )

    # CI's actual declaration. The marker does not name firefox; the REASON
    # does, and that must still be caught.
    result = _run_pytest(
        sandbox, "-q", env_extra={persona_conftest.REQUIRE_ENV_VAR: "browser"}
    )

    assert result.returncode != 0, (
        "a firefox-absent skip went unpoliced on a machine declaring browser, "
        "because the test's marker happened to name a different engine: "
        + result.stdout
    )
    assert "test_two_armed_probe" in result.stdout
    # ...and it names FIREFOX — the thing that was missing — not the engine the
    # marker happened to mention.
    assert "browser_firefox" in result.stdout, result.stdout
    assert "playwright install firefox" in result.stdout, result.stdout

    # The other half of the same rule: a skip whose reason names the engine
    # NOTHING provisions stays an honest inconclusive skip under that same
    # declaration. This is the arm that was reporting a self-contradictory
    # failure — "declares browser_firefox, so this test must RUN" printed above
    # a skip reason naming chromium.
    (sandbox / "test_two_armed.py").write_text(
        "import pytest\n"
        "@pytest.mark.requires_capability('browser_chromium')\n"
        "def test_two_armed_probe():\n"
        "    pytest.skip('chromium engine not runnable here: no build')\n"
    )
    gated = _run_pytest(
        sandbox, "-q", env_extra={persona_conftest.REQUIRE_ENV_VAR: "browser"}
    )
    assert gated.returncode == 0, (
        "a chromium-absent skip was reported as a failure naming firefox, "
        "which was never the missing thing: " + gated.stdout
    )
    assert "1 skipped" in gated.stdout

    # ...but whoever DOES provision the engine gets a real gate on it.
    declared = _run_pytest(
        sandbox,
        "-q",
        env_extra={persona_conftest.REQUIRE_ENV_VAR: "browser_chromium"},
    )
    assert declared.returncode != 0, declared.stdout
    assert "browser_chromium" in declared.stdout


def test_a_marker_naming_an_unknown_capability_is_a_hard_error(sandbox: Path):
    """The typo that disables the guard, on the marker path this time.

    `PERSONA_REQUIRED_CAPABILITIES=browserr` has always been a hard error.
    `@pytest.mark.requires_capability("browserr")` was silently ignored, which
    left the test entirely UNGUARDED while the marker sat in the source looking
    exactly like protection. Both spellings of the declaration must fail closed;
    a guard that a misspelling turns off, quietly, is the original defect
    wearing a new hat.
    """
    (sandbox / "test_typo.py").write_text(
        "import pytest\n"
        "@pytest.mark.requires_capability('browserr')\n"
        "def test_marked_with_a_typo():\n"
        "    pytest.skip('conditions not met')\n"
    )

    result = _run_pytest(
        sandbox, "-q", env_extra={persona_conftest.REQUIRE_ENV_VAR: "browser"}
    )

    assert result.returncode != 0, result.stdout + result.stderr
    output = result.stdout + result.stderr
    assert "unknown test capability" in output
    assert "browserr" in output
    # it locates the offender rather than leaving the reader to grep for it,
    # and offers the valid names
    assert "test_marked_with_a_typo" in output
    # It offers the VALID NAMES — asserted as the whole known set, computed
    # from the table rather than hardcoded. A literal list here went stale the
    # moment `browser` was split by engine, and the tempting repair (drop the
    # assertion, or pin one name) would stop checking the thing that matters:
    # an operator who typo'd a capability must be shown what they COULD have
    # typed, including any capability added after this test was written.
    for known in persona_conftest.CAPABILITIES:
        assert known in output, (
            f"the error omits the valid capability {known!r}, so an operator "
            "who mistyped is not shown the name they wanted"
        )
    # The run must not have proceeded to report a confident green around it.
    assert "1 skipped" not in result.stdout

    # It is loud even when nothing is declared: the marker is wrong in the
    # source, and it is wrong on every machine — not only on a provisioned one.
    undeclared = _run_pytest(sandbox, "-q")
    assert undeclared.returncode != 0, undeclared.stdout + undeclared.stderr
    assert "unknown test capability" in undeclared.stdout + undeclared.stderr


def test_a_valid_marker_still_collects_normally(sandbox: Path):
    """The negative control for the collection-time validator.

    Without this, a validator that rejected EVERY marker — or that crashed on
    the happy path — would still make the typo test above pass. It must refuse
    the unknown name and nothing else.
    """
    (sandbox / "test_ok_marker.py").write_text(
        "import pytest\n"
        "@pytest.mark.requires_capability('browser', 'node')\n"
        "def test_well_formed_marker():\n"
        "    assert True\n"
    )

    result = _run_pytest(sandbox, "-q")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
    assert "unknown test capability" not in result.stdout + result.stderr


def test_an_xfail_is_not_mistaken_for_a_declined_test(sandbox: Path):
    """An xfail reports as skipped internally but is a RESULT, not an absence
    of one — converting it would produce noise nobody can rank."""
    (sandbox / "test_xfail.py").write_text(
        "import pytest\n"
        "@pytest.mark.xfail(reason='firefox not runnable here: known')\n"
        "def test_expected_to_fail():\n"
        "    assert False\n"
    )

    result = _run_pytest(
        sandbox, "-q", env_extra={persona_conftest.REQUIRE_ENV_VAR: "browser"}
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 xfailed" in result.stdout


def test_the_summary_states_the_capability_held_when_nothing_declined(sandbox: Path):
    """A green declared run must say so positively.

    Silence would leave a reader unable to tell "the browser probes ran" from
    "the mechanism was never active" — the same ambiguity, relocated.
    """
    (sandbox / "test_ok.py").write_text("def test_fine():\n    assert True\n")

    result = _run_pytest(
        sandbox, "-q", env_extra={persona_conftest.REQUIRE_ENV_VAR: "browser"}
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "declared capabilities" in result.stdout
    assert "ok browser" in result.stdout


def test_the_cli_flag_and_the_env_var_are_the_same_declaration(sandbox: Path):
    """Two spellings, one meaning — CI sets an env var, a human types a flag."""
    (sandbox / "test_probe.py").write_text(_UNSEEN_BROWSER_TEST)

    result = _run_pytest(sandbox, "-q", "--require-capability", "browser")

    assert result.returncode != 0, result.stdout
    assert "test_a_brand_new_browser_probe" in result.stdout


#: A probe skipping for want of the CHROMIUM ENGINE — the engine
#: `DEFAULT_ENGINE` names and that nothing in CI provisions. Prospective by
#: construction: no such guard exists in `tests/` yet, which is exactly why the
#: behaviour is pinned now rather than after the first one lands.
_UNSEEN_CHROMIUM_ENGINE_TEST = '''
import pytest

@pytest.fixture
def probe():
    pytest.skip("chromium engine not runnable here: no fingerprint-chromium build")

def test_a_brand_new_chromium_engine_probe(probe):
    assert False, "must never execute: the fixture skips first"
'''


class TestTheUmbrellaKeepsTheOldDeclarationMeaningWhatItMeant:
    """`browser` was split by engine. The name it was split OUT of still has to
    work, or this change silently disarmed every gate that uses it."""

    def test_declaring_the_umbrella_still_fails_a_firefox_skip(self, sandbox: Path):
        """THE REGRESSION THIS WHOLE CLASS EXISTS FOR.

        `ci.yml` declares `PERSONA_REQUIRED_CAPABILITIES=browser`, and the
        Firefox reason patterns now live on `browser_firefox`. If declaring the
        umbrella did not expand to its members, that CI job would police
        NOTHING and report a confident green — the exact "looks like success,
        verified nothing" defect this file exists to close, re-created by a
        rename. Asserted end-to-end through a real run, not by inspecting the
        expansion helper, because the helper being right is not the claim.
        """
        (sandbox / "test_probe.py").write_text(_UNSEEN_BROWSER_TEST)

        result = _run_pytest(
            sandbox, "-q", env_extra={persona_conftest.REQUIRE_ENV_VAR: "browser"}
        )

        assert result.returncode != 0, (
            "declaring the umbrella policed nothing and the run reported "
            "green: " + result.stdout + result.stderr
        )
        assert "test_a_brand_new_browser_probe" in result.stdout

    def test_the_failure_names_the_engine_and_not_the_umbrella(self, sandbox: Path):
        """An operator is handed an instruction, not a redirection.

        Both the umbrella and its member qualify when `browser` is declared.
        Reporting the umbrella would print "provision the engines this umbrella
        covers", which tells the reader to go and look something up; the
        engine-specific entry tells them to install the Firefox binary. The
        whole point of splitting the name was to make the message specific.
        """
        (sandbox / "test_probe.py").write_text(_UNSEEN_BROWSER_TEST)

        result = _run_pytest(
            sandbox, "-q", env_extra={persona_conftest.REQUIRE_ENV_VAR: "browser"}
        )

        assert "browser_firefox" in result.stdout, result.stdout
        assert "playwright install firefox" in result.stdout
        assert "provision the engines this umbrella covers" not in result.stdout

    def test_a_marker_naming_the_umbrella_expands_too(self, sandbox: Path):
        """The other spelling of the same declaration.

        `@pytest.mark.requires_capability("browser")` appears in the tree today.
        If the marker path did not expand umbrellas, those markers would become
        decoration the day the capability was split — a guard sitting in the
        source looking exactly like protection while enforcing nothing.
        """
        (sandbox / "test_marked.py").write_text(
            "import pytest\n"
            "@pytest.mark.requires_capability('browser')\n"
            "def test_marked_but_cryptic_reason():\n"
            "    pytest.skip('conditions not met')\n"
        )

        result = _run_pytest(
            sandbox, "-q", env_extra={persona_conftest.REQUIRE_ENV_VAR: "browser"}
        )

        assert result.returncode != 0, result.stdout
        assert "test_marked_but_cryptic_reason" in result.stdout


class TestTheChromiumGapIsNamedAndNotQuietlyCovered:
    """The deliverable is an ADMITTED gap. These tests hold the line on both
    halves of that: it must be stated, and it must not be faked."""

    def test_declaring_the_umbrella_does_not_police_the_chromium_engine(
        self, sandbox: Path
    ):
        """`browser` must NOT quietly start failing chromium-engine skips.

        Nothing provisions that engine — not CI, not anywhere. Folding it into
        the umbrella would turn every declaring job red for want of
        PROVISIONING rather than for want of correctness, which is a gate that
        fails for the wrong reason and teaches its reader to ignore it. The
        capability is opt-in precisely because declaring it today would be a
        promise no machine can keep.
        """
        (sandbox / "test_probe.py").write_text(_UNSEEN_CHROMIUM_ENGINE_TEST)

        result = _run_pytest(
            sandbox, "-q", env_extra={persona_conftest.REQUIRE_ENV_VAR: "browser"}
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert "1 skipped" in result.stdout

    def test_a_declared_run_states_what_the_declaration_does_not_cover(
        self, sandbox: Path
    ):
        """The gap is PRINTED, next to the green line it qualifies.

        "ok browser: no test declined to run" is true and, on its own,
        misleading — a reader takes it as "real browsers are covered here" when
        the engine the product DEFAULTS to is launched by nothing. Leaving that
        to be discovered by diffing the capability table would make the gap
        silent again, which is the whole defect this split removes.
        """
        (sandbox / "test_ok.py").write_text("def test_fine():\n    assert True\n")

        result = _run_pytest(
            sandbox, "-q", env_extra={persona_conftest.REQUIRE_ENV_VAR: "browser"}
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert "ok browser" in result.stdout
        # ...and immediately the honest qualification of that green line
        assert "browser_chromium" in result.stdout, (
            "a declared run reported the umbrella green without stating which "
            "engine it does not cover: " + result.stdout
        )
        assert "does NOT cover" in result.stdout

    def test_the_gap_notice_does_not_claim_the_engine_is_provisioned(
        self, sandbox: Path
    ):
        """Naming a gap is not closing it, and the text must not blur the two.

        The standing directive: a surface that cannot be driven yet is
        "recorded as not covered with the reason — never as covered by a weaker
        check standing in for it". A future edit that softened this into
        "chromium: ok" while nothing launches chromium would be exactly the
        false green the ticket set out to remove.
        """
        (sandbox / "test_ok.py").write_text("def test_fine():\n    assert True\n")

        result = _run_pytest(
            sandbox, "-q", env_extra={persona_conftest.REQUIRE_ENV_VAR: "browser"}
        )

        assert "ok browser_chromium" not in result.stdout
        assert "NOTHING PROVISIONS THIS TODAY" in result.stdout

    def test_the_chromium_capability_can_still_be_declared_deliberately(
        self, sandbox: Path
    ):
        """Opt-in, not inert. Whoever provisions the engine gets a real gate on
        the day they declare it — otherwise this table entry would be a comment
        with no mechanism behind it, which is a different kind of lie."""
        (sandbox / "test_probe.py").write_text(_UNSEEN_CHROMIUM_ENGINE_TEST)

        result = _run_pytest(
            sandbox,
            "-q",
            env_extra={persona_conftest.REQUIRE_ENV_VAR: "browser_chromium"},
        )

        assert result.returncode != 0, result.stdout + result.stderr
        assert "test_a_brand_new_chromium_engine_probe" in result.stdout


class TestSkipReportingIsOnByDefault:
    """The reporting half: no flag, no knowledge required of the reader."""

    def test_pyproject_enables_skip_reasons_without_suppressing_failures(self):
        """`-r` REPLACES the default summary set, so a bare `-rs` prints skip
        reasons and then hides the list of FAILED test names — measured on this
        suite: 30 failures rendered as an aggregate count with no names. The
        `f` and `E` are load-bearing, not decoration."""
        config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
        addopts = config["tool"]["pytest"]["ini_options"]["addopts"]

        flags = addopts.split()
        report_flag = next(f for f in flags if f.startswith("-r"))
        chars = report_flag[2:]
        assert "s" in chars, "skip reasons must print by default"
        assert "f" in chars, "-r must not suppress the failed-test list"
        assert "E" in chars, "-r must not suppress the errored-test list"

    def test_this_repo_prints_skip_reasons_with_no_extra_flags(self):
        """End to end, against the real project config: a reader who knows
        nothing and passes nothing still sees what declined to run."""
        result = _run_pytest(REPO_ROOT, "-q", "--tb=no", "tests/test_assets.py")

        if "skipped" not in result.stdout:
            pytest.skip("PIL is installed here, so this file has no skips to show")
        assert "SKIPPED" in result.stdout
        # the reason, not merely the count
        assert "PIL" in result.stdout

    def test_pytest_config_lives_in_exactly_one_place(self):
        """A flag in a CI invocation and a key here would drift; so would a
        second config file. Keep the single site."""
        for rival in ("pytest.ini", "setup.cfg", "tox.ini"):
            assert not (REPO_ROOT / rival).exists(), (
                f"{rival} is a second pytest config site — fold it into "
                "pyproject.toml's [tool.pytest.ini_options]"
            )


class TestCapabilityClassification:
    """The reason->capability mapping, at the unit level."""

    @pytest.mark.parametrize(
        "reason,expected",
        [
            # Both guards on the real-Firefox probes, in their real wording.
            # They classify as the ENGINE-SPECIFIC capability, not the umbrella:
            # "a browser did not run" is not an actionable sentence, "the
            # Firefox binary is missing" is.
            ("playwright not installed", "browser_firefox"),
            ("could not import 'playwright.sync_api': No module named 'playwright'", "browser_firefox"),
            ("firefox not runnable here: Executable doesn't exist", "browser_firefox"),
            # The chromium engine the product DEFAULTS to. Prospective wording —
            # no probe guards on it yet — pinned here so the partition is
            # checked before the first such probe lands, not after.
            ("chromium engine not runnable here: Executable doesn't exist", "browser_chromium"),
            ("fingerprint-chromium not available", "browser_chromium"),
            # THE COLLISION THIS SPLIT COULD HAVE CAUSED, pinned in both
            # directions. `ui_driver` owns a DIFFERENT chromium — the system
            # browser its UI driver attaches to — and matching is substring
            # matching, so a careless engine pattern would have swallowed this
            # reason (or been swallowed by it) and reported a skip about one
            # chromium as a failure of the other.
            ("chromium not runnable here: /usr/bin/chromium is absent", "ui_driver"),
            ("flet not installed", "ui_driver"),
            ("node not available", "node"),
            ("could not import 'invisible_core': No module named 'invisible_core'", "engine"),
            ("could not import 'invisible_playwright': No module named x", "engine"),
            # genuinely unrelated skips must stay unclassified: a capability
            # that swept these in would fail runs for reasons no operator
            # declared anything about.
            ("root bypasses directory permissions; can't make a dir unlistable", None),
            ("SO_PEERCRED not exposed on this platform", None),
            ("no real AppImage available", None),
            ("exercises the real Windows PowerShell/WMI pid query path", None),
        ],
    )
    def test_a_skip_reason_maps_to_the_capability_that_would_fix_it(
        self, reason, expected
    ):
        cap = persona_conftest.capability_for_skip(reason)
        assert (cap.name if cap else None) == expected

    def test_every_capability_says_how_to_provision_it(self):
        """A failure that names a missing capability without saying how to
        supply it just relocates the dead end.

        An UMBRELLA legitimately carries no reason_patterns — classification
        must land on its engine-specific member, so "a browser did not run"
        never reaches an operator in place of "the Firefox binary is missing".
        The requirement is therefore that no capability is a DEAD END: it
        either catches skips itself, or it delegates to members that do. That
        is the original intent, widened to survive the split — not weakened:
        a leaf with no patterns still fails here, and an umbrella whose
        members do not exist fails too.
        """
        for name, cap in persona_conftest.CAPABILITIES.items():
            assert cap.summary, name
            assert cap.provisioned_by, name
            assert cap.reason_patterns or cap.includes, name
            if cap.includes:
                assert not cap.reason_patterns, (
                    f"{name} is an umbrella AND matches reasons itself, so a "
                    "skip could classify as the umbrella and hand the reader "
                    "a redirection instead of the engine that was missing"
                )
            for member in (*cap.includes, *cap.excludes):
                assert member in persona_conftest.CAPABILITIES, (
                    f"{name} names {member!r}, which is not a capability — a "
                    "declaration would expand to nothing and enforce nothing"
                )
