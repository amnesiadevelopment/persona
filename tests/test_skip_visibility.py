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
        timeout=120, encoding="utf-8",
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
    (sandbox / "test_probe.py").write_text(_UNSEEN_BROWSER_TEST, encoding="utf-8")

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
    (sandbox / "test_probe.py").write_text(_UNSEEN_BROWSER_TEST, encoding="utf-8")

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
    (sandbox / "test_probe.py").write_text(_UNSEEN_BROWSER_TEST, encoding="utf-8")

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
    (sandbox / "test_probe.py").write_text(_UNSEEN_BROWSER_TEST, encoding="utf-8")

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
    (sandbox / "test_probe.py").write_text(_UNSEEN_BROWSER_TEST, encoding="utf-8")

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

    tree = ast.parse(CONFTEST.read_text(encoding="utf-8"))
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
        "    pytest.skip('conditions not met')\n", encoding="utf-8"
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
        "    pytest.skip('node not available')\n", encoding="utf-8"
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
        "    pytest.skip('firefox not runnable here: no binary')\n", encoding="utf-8"
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
        "    pytest.skip('chromium engine not runnable here: no build')\n", encoding="utf-8"
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
        "    pytest.skip('conditions not met')\n", encoding="utf-8"
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
        "    assert True\n", encoding="utf-8"
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
        "    assert False\n", encoding="utf-8"
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
    (sandbox / "test_ok.py").write_text("def test_fine():\n    assert True\n", encoding="utf-8")

    result = _run_pytest(
        sandbox, "-q", env_extra={persona_conftest.REQUIRE_ENV_VAR: "browser"}
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "declared capabilities" in result.stdout
    assert "ok browser" in result.stdout


def test_the_cli_flag_and_the_env_var_are_the_same_declaration(sandbox: Path):
    """Two spellings, one meaning — CI sets an env var, a human types a flag."""
    (sandbox / "test_probe.py").write_text(_UNSEEN_BROWSER_TEST, encoding="utf-8")

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
        (sandbox / "test_probe.py").write_text(_UNSEEN_BROWSER_TEST, encoding="utf-8")

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
        (sandbox / "test_probe.py").write_text(_UNSEEN_BROWSER_TEST, encoding="utf-8")

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
            "    pytest.skip('conditions not met')\n", encoding="utf-8"
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
        (sandbox / "test_probe.py").write_text(_UNSEEN_CHROMIUM_ENGINE_TEST, encoding="utf-8")

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
        (sandbox / "test_ok.py").write_text("def test_fine():\n    assert True\n", encoding="utf-8")

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
        (sandbox / "test_ok.py").write_text("def test_fine():\n    assert True\n", encoding="utf-8")

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
        (sandbox / "test_probe.py").write_text(_UNSEEN_CHROMIUM_ENGINE_TEST, encoding="utf-8")

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
        config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
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
            # PyYAML, BOTH wordings (PS-389). These are not two spellings of
            # one pattern — they share no substring, so a single pattern
            # cannot reach both, and an entry covering only the first would
            # leave the module-level guards dark WHILE REPORTING SUCCESS.
            ("could not import 'yaml': No module named 'yaml'", "yaml"),
            ("PyYAML is needed to parse the workflow", "yaml"),
            # The STEM, not one guard's sentence: a future guard that words its
            # own detail differently must still classify, on the `engine`
            # precedent that this repo has already been bitten by twice.
            ("PyYAML is needed to read the release manifest", "yaml"),
            # genuinely unrelated skips must stay unclassified: a capability
            # that swept these in would fail runs for reasons no operator
            # declared anything about.
            ("root bypasses directory permissions; can't make a dir unlistable", None),
            ("SO_PEERCRED not exposed on this platform", None),
            ("no real AppImage available", None),
            ("exercises the real Windows PowerShell/WMI pid query path", None),
            # ⚠️ THE COLLISION THE `yaml` PATTERNS COULD HAVE CAUSED, pinned in
            # the direction that actually threatened. "could not import 'yaml"
            # is a substring of nothing else here, but a careless widening to a
            # bare "yaml" would swallow every skip that merely MENTIONS a
            # workflow file — including these, which are about something else
            # entirely and must keep classifying as nothing.
            ("the workflow yaml under test is not in this checkout", None),
            ("this reading needs a live GitHub API token", None),
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


# ---------------------------------------------------------------------------
# PS-371: an importorskip that supplies its OWN reason must still classify
# ---------------------------------------------------------------------------

#: The engine fence's guard, verbatim in SHAPE: `importorskip` with a custom
#: `reason=`. That keyword REPLACES the wording this table matches on, which is
#: why this is a distinct failure mode from the bare form below and not a
#: paraphrase of it.
#:
#: ⚠️ THE MODULE NAME IS DELIBERATELY UNIMPORTABLE, AND IT MUST STAY THAT WAY.
#: An earlier draft of this file named the REAL package, `invisible_core` —
#: which made these tests pass in a bare container and FAIL in CI, where
#: `pip install .` supplies it: the importorskip would succeed, no skip would
#: occur, and the `assert False` below would fire. The claim under test is
#: about the SHAPE of the guard (a custom `reason=`) and about how conftest
#: CLASSIFIES the resulting skip — it is not about any particular package, and
#: it must not silently become a test of what happens to be installed. The
#: reason string is what carries the engine meaning, and that is the input the
#: classifier actually reads.
_UNSEEN_ENGINE_TEST_WITH_CUSTOM_REASON = '''
import pytest

def test_a_brand_new_engine_probe():
    pytest.importorskip(
        "persona_no_such_engine_module",
        reason="the engine driver is not installed in this environment",
    )
    assert False, "must never execute: the importorskip skips first"
'''

#: The SAME absence, guarded the bare way, so importorskip writes the reason
#: itself. Here the reason is generated FROM the module name, so this one names
#: `invisible_core` of necessity — the pattern it must match is
#: "could not import 'invisible_core". It is wrapped so the module is
#: unimportable in EVERY environment (including CI, where the real package is
#: installed) while importorskip still writes that exact wording.
_UNSEEN_ENGINE_TEST_BARE = '''
import pytest

def test_a_bare_engine_probe():
    # A submodule that cannot exist, under the real top-level name: the skip
    # reason importorskip writes still begins "could not import
    # 'invisible_core", which is the string the capability table matches on,
    # and it reads that way whether or not the real package is installed.
    pytest.importorskip("invisible_core.no_such_submodule_ps371")
    assert False, "must never execute: the importorskip skips first"
'''


class TestACustomImportorskipReasonIsStillPoliced:
    """A guard written more HELPFULLY must not thereby become invisible.

    ``pytest.importorskip(mod, reason=...)`` replaces importorskip's own
    wording, and this file classifies skips BY that wording. So the two
    spellings of one absence took different paths: the bare form matched
    ``"could not import 'invisible_core"`` and was policed, while a custom
    reason matched nothing and was silently tolerated even where the engine
    was declared.

    PS-371 MEASURED THAT ASYMMETRY INSIDE A SINGLE FILE, which is what makes
    it worth pinning rather than merely noting.
    ``tests/test_engine_driver_platform_support.py`` is the ONLY assertion in
    this repo that the pinned ``invisible_core`` still supports every OS
    ``release.yml`` builds for — the guard holding macOS. Its fence uses the
    custom-reason form; the self-test beside it (``test_the_probe_can_actually_fail``)
    uses the bare form. Same module, same missing package, and before this the
    IMPORTANT one was the unclassified one.

    Both spellings are driven here through a real pytest process, because the
    claim is about the hook wiring and not about a helper's return value.
    """

    def test_the_custom_reason_form_fails_where_the_engine_is_declared(
        self, sandbox: Path
    ):
        """THE REGRESSION THIS CLASS EXISTS FOR — observed red, not assumed."""
        (sandbox / "test_probe.py").write_text(
            _UNSEEN_ENGINE_TEST_WITH_CUSTOM_REASON, encoding="utf-8"
        )

        result = _run_pytest(
            sandbox, "-q", env_extra={persona_conftest.REQUIRE_ENV_VAR: "engine"}
        )

        assert result.returncode != 0, (
            "a custom importorskip reason classified as nothing, so the engine "
            "declaration policed it not at all and the run reported green: "
            + result.stdout
            + result.stderr
        )
        assert "test_a_brand_new_engine_probe" in result.stdout
        # The message names the capability and how to supply it, rather than
        # merely reporting a red.
        assert "engine" in result.stdout
        assert "pip install ." in result.stdout

    def test_the_bare_form_is_policed_identically(self, sandbox: Path):
        """The other spelling of the same absence, so the two cannot drift
        apart again: whichever way a guard is written, the same declaration
        catches it."""
        (sandbox / "test_probe.py").write_text(
            _UNSEEN_ENGINE_TEST_BARE, encoding="utf-8"
        )

        result = _run_pytest(
            sandbox, "-q", env_extra={persona_conftest.REQUIRE_ENV_VAR: "engine"}
        )

        assert result.returncode != 0, result.stdout + result.stderr
        assert "test_a_bare_engine_probe" in result.stdout

    def test_an_undeclared_run_still_skips_and_still_passes(self, sandbox: Path):
        """The quiet path, for the capability this ticket widened.

        A contributor who has not installed the project must keep getting a
        green run. Widening a pattern is only safe if it changes nothing where
        nothing was declared — otherwise the loud path was bought by making an
        ordinary checkout red.
        """
        (sandbox / "test_probe.py").write_text(
            _UNSEEN_ENGINE_TEST_WITH_CUSTOM_REASON, encoding="utf-8"
        )

        result = _run_pytest(sandbox, "-q", "-rs")

        assert result.returncode == 0, result.stdout + result.stderr
        assert "1 skipped" in result.stdout
        assert "the engine driver is not installed" in result.stdout

    def test_declaring_browser_does_not_police_the_engine(self, sandbox: Path):
        """`browser` does NOT cover `engine`, and this is why the workflow had
        to name it.

        ``expand_capabilities(["browser"])`` is ``["browser", "browser_firefox"]``
        — the engine packages are not in that umbrella. So the pattern widened
        above buys nothing on its own: without ``engine`` in the declaration
        the fence could still go dark silently. Pinned as a real run so the
        two halves of this change are known to BOTH be load-bearing.
        """
        (sandbox / "test_probe.py").write_text(
            _UNSEEN_ENGINE_TEST_WITH_CUSTOM_REASON, encoding="utf-8"
        )

        result = _run_pytest(
            sandbox, "-q", env_extra={persona_conftest.REQUIRE_ENV_VAR: "browser"}
        )

        assert result.returncode == 0, (
            "declaring 'browser' policed an engine skip — the umbrella has "
            "silently grown to cover the engine packages, and the workflow's "
            "separate 'engine' declaration is no longer the thing enforcing "
            "this: " + result.stdout
        )

    def test_the_pattern_is_the_stem_not_one_guards_exact_sentence(self):
        """A guard that appends its own detail must still classify.

        Matching the fence's full sentence would make this pattern a private
        arrangement with ONE call site — the next guard, worded slightly
        differently, would be unclassified again and nobody would know. The
        stem is the environment-independent part.
        """
        for reason in (
            "the engine driver is not installed in this environment",
            "the engine driver is not installed (bare checkout, no pip install)",
            "the engine driver is not installed",
        ):
            cap = persona_conftest.capability_for_skip(reason)
            assert cap is not None and cap.name == "engine", (
                f"{reason!r} did not classify as the engine capability"
            )

    def test_every_engine_guard_in_this_repo_writes_a_reason_that_classifies(self):
        """THE SWEEP, NOT ONE MORE EXAMPLE — because the failure recurred.

        The tests above prove the MECHANISM polices a custom reason. They
        cannot see a guard in some other file whose wording quietly misses the
        pattern, and that is exactly what happened twice:

        * PS-371 found `test_engine_driver_platform_support.py`'s macOS fence
          unclassified, and widened the table to a stem to fix it;
        * PS-353 then wrote "the PINNED engine driver is not installed" in
          `tests/test_app_egress.py` — ONE WORD off that new stem — and six
          egress tests, three of them security-relevant routing assertions,
          declined silently while the summary printed "ok engine: no test
          declined to run".

        A per-site example cannot catch the third occurrence. This walks the
        AST of every test module, finds each `pytest.importorskip` whose
        MODULE is an engine package, and requires the reason it writes to
        classify as `engine`. A guard is in scope because of what it GUARDS,
        so a new file gets this for free and nobody has to remember.

        ⛔ SCOPED TO THE ENGINE ON PURPOSE, and the scope is the honest part.
        Some reasons in this repo legitimately classify as nothing, and
        inventing a capability to make this sweep pass would be the reverse
        defect. The claim here is narrow and true: where a capability EXISTS
        and is DECLARED in CI, a guard for it must be reachable by it.

        ⚠️ THIS NOTE USED TO NAME THE `PyYAML` GUARDS AS ITS EXAMPLE OF THE
        FIRST SENTENCE, AND THAT EXAMPLE IS NOW FALSE (PS-389). It read: "the
        `PyYAML` guards name no capability because none is declared for them".
        A `yaml` capability now exists in conftest's table and ci.yml declares
        it, so the condition that sentence rested on — *none is declared for
        them* — no longer holds, and leaving the sentence would put a note in
        the tree that reads as contradicted by the file next to it.

        THE POSITION IT RECORDED IS UNCHANGED AND STILL BINDING. PS-389 did
        NOT add that capability to green this sweep — this sweep does not look
        at yaml guards and STILL DOES NOT, deliberately: `engine_modules`
        below is unchanged, and widening it is a separate judgement nobody has
        made. The capability was added on its own evidence, measured three
        ways, because 89 workflow-shape tests were declining silently under
        ci.yml's own full declaration — 37 of them the guards in
        tests/test_ci_verification_gates.py that assert the OTHER capability
        declarations exist. That is the opposite of the reverse defect: the
        name was earned by a measured absence, not by a red sweep.
        """
        import ast

        engine_modules = ("invisible_playwright", "invisible_core")
        repo_tests = Path(persona_conftest.__file__).parent / "tests"
        unclassified: list[str] = []
        checked = 0

        for path in sorted(repo_tests.rglob("test_*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
                continue
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "importorskip"
                ):
                    continue
                if not (node.args and isinstance(node.args[0], ast.Constant)):
                    continue
                module = node.args[0].value
                if not isinstance(module, str):
                    continue
                if not any(module.split(".")[0] == m for m in engine_modules):
                    continue
                reason = next(
                    (
                        kw.value.value
                        for kw in node.keywords
                        if kw.arg == "reason"
                        and isinstance(kw.value, ast.Constant)
                        and isinstance(kw.value.value, str)
                    ),
                    None,
                )
                # No `reason=` at all is SAFE: importorskip writes its own
                # wording, which is the first pattern in the table.
                if reason is None:
                    continue
                checked += 1
                cap = persona_conftest.capability_for_skip(reason)
                if cap is None or cap.name != "engine":
                    unclassified.append(
                        f"{path.relative_to(repo_tests.parent)}:{node.lineno} "
                        f"guards {module!r} with reason {reason!r} -> "
                        f"{cap.name if cap else None}"
                    )

        assert checked, (
            "this sweep found NO engine importorskip with a custom reason to "
            "check, which means it is asserting nothing — re-point it before "
            "trusting a green"
        )
        assert not unclassified, (
            "an engine guard writes a skip reason the 'engine' capability "
            "cannot classify, so on a runner that DECLARES engine these tests "
            "decline silently and the summary reports 'no test declined to "
            "run'. Carry the stem 'the engine driver is not installed' in the "
            "reason, or drop `reason=` and let importorskip write its own:\n  "
            + "\n  ".join(unclassified)
        )


# ---------------------------------------------------------------------------
# PS-389: PyYAML — the capability the workflow-shape guards had no name for
# ---------------------------------------------------------------------------

#: The BARE form: `pytest.importorskip("yaml")`, which is what 6 of this repo's
#: guards use and what produces 78 of the 89 dark skips.
#:
#: ⚠️ THE MODULE NAME IS DELIBERATELY UNIMPORTABLE, AND IT MUST STAY THAT WAY —
#: the same discipline the engine probes above are written under, and for the
#: same reason. Naming the REAL `yaml` here would make these tests pass in a
#: bare container and FAIL everywhere PyYAML is installed (which is CI, and is
#: this repo's own dev image): the importorskip would succeed, no skip would
#: occur, and the `assert False` would fire. The claim under test is about the
#: SHAPE of the guard and about how conftest CLASSIFIES the resulting skip, not
#: about what happens to be installed. A submodule that cannot exist under the
#: real top-level name still makes importorskip write "could not import 'yaml",
#: which is the string the capability table matches on.
_UNSEEN_YAML_TEST_BARE = '''
import pytest

def test_a_bare_yaml_probe():
    pytest.importorskip("yaml.no_such_submodule_ps389")
    assert False, "must never execute: the importorskip skips first"
'''

#: The CUSTOM-REASON form, VERBATIM as 8 module-level guards in this repo write
#: it. This wording shares NO SUBSTRING with the bare form's, which is the whole
#: reason the `yaml` capability carries two patterns instead of one.
_UNSEEN_YAML_TEST_WITH_CUSTOM_REASON = '''
import pytest

def test_a_yaml_probe_with_a_custom_reason():
    pytest.importorskip(
        "yaml.no_such_submodule_ps389",
        reason="PyYAML is needed to parse the workflow",
    )
    assert False, "must never execute: the importorskip skips first"
'''

#: The THIRD form, which is not an importorskip at all:
#: `tests/test_ps372_firefox_major_watch.py` guards from a FIXTURE with a bare
#: `pytest.skip()`, deliberately — an importorskip at its module level would
#: skip the entire file at collection. 11 skips ride this shape, and a sweep
#: looking only for `importorskip` would miss every one. The table matches on
#: the reason TEXT rather than on the call that wrote it, so this must classify
#: identically; that is a claim worth driving through a real run rather than
#: asserting about the matcher.
_UNSEEN_YAML_TEST_BARE_SKIP_CALL = '''
import pytest

@pytest.fixture
def workflow():
    pytest.skip("PyYAML is needed to parse the workflow")

def test_a_yaml_probe_guarded_by_a_plain_skip(workflow):
    assert False, "must never execute: the fixture skips first"
'''


class TestThePyYAMLGuardsAreReachableByADeclaration:
    """PS-389 — both arms, and the quiet one is the one that discriminates.

    THE DEFECT, measured at 9dad467 with a `sys.meta_path` blocker raising a
    genuine ``ModuleNotFoundError`` (an ``ImportError`` stub takes a different
    path and produces errors, not skips):

    * PyYAML absent, nothing declared          -> 89 tests SILENTLY SKIPPED
    * PyYAML absent, ``browser,engine``        -> 89 tests SILENTLY SKIPPED
      (ci.yml's OWN declaration, verbatim)        BYTE-IDENTICAL to declaring
                                                  nothing at all

    ci.yml's full declaration bought nothing, because the table had no name for
    PyYAML. 37 of those 89 live in ``tests/test_ci_verification_gates.py`` and
    include the guards asserting that the OTHER capability declarations exist —
    so the failure mode is a suite that stops policing its own policing and
    reports green while doing it.

    Driven through real pytest processes, on the same reasoning as every other
    end-to-end test in this file: a test written against ``capability_for_skip``
    alone would keep passing if the hooks were never registered.
    """

    def test_the_bare_form_fails_where_yaml_is_declared(self, sandbox: Path):
        """LOUD, form 1 of 3 — `importorskip("yaml")`, 78 of the 89."""
        (sandbox / "test_probe.py").write_text(
            _UNSEEN_YAML_TEST_BARE, encoding="utf-8"
        )

        result = _run_pytest(
            sandbox, "-q", env_extra={persona_conftest.REQUIRE_ENV_VAR: "yaml"}
        )

        assert result.returncode != 0, (
            "a PyYAML guard declined to run in an environment DECLARING yaml "
            "and the run still reported green: " + result.stdout + result.stderr
        )
        assert "test_a_bare_yaml_probe" in result.stdout
        # The failure names the dependency and how to supply it, rather than
        # merely going red — the whole point of the table's third field.
        assert "yaml" in result.stdout
        assert "requirements-dev.txt" in result.stdout

    def test_the_custom_reason_form_is_policed_identically(self, sandbox: Path):
        """LOUD, form 2 of 3 — and the form a single-pattern entry would MISS.

        The two wordings share no substring. An entry written against
        `importorskip`'s default text alone would leave these guards dark while
        the change reported success — this file's own subject matter re-created
        inside the fix for it, which is why this arm is not a paraphrase of the
        one above.
        """
        (sandbox / "test_probe.py").write_text(
            _UNSEEN_YAML_TEST_WITH_CUSTOM_REASON, encoding="utf-8"
        )

        result = _run_pytest(
            sandbox, "-q", env_extra={persona_conftest.REQUIRE_ENV_VAR: "yaml"}
        )

        assert result.returncode != 0, (
            "the custom-reason PyYAML guard classified as nothing, so a yaml "
            "declaration polices it not at all: " + result.stdout + result.stderr
        )
        assert "test_a_yaml_probe_with_a_custom_reason" in result.stdout

    def test_a_plain_skip_call_is_policed_identically(self, sandbox: Path):
        """LOUD, form 3 of 3 — the fixture-level `pytest.skip()`, 11 of the 89.

        Not an importorskip at all, so anything keyed off the CALL rather than
        off the reason TEXT would miss it. This is the shape
        `tests/test_ps372_firefox_major_watch.py` uses for a stated reason, and
        it must not be a hole.
        """
        (sandbox / "test_probe.py").write_text(
            _UNSEEN_YAML_TEST_BARE_SKIP_CALL, encoding="utf-8"
        )

        result = _run_pytest(
            sandbox, "-q", env_extra={persona_conftest.REQUIRE_ENV_VAR: "yaml"}
        )

        assert result.returncode != 0, result.stdout + result.stderr
        assert "test_a_yaml_probe_guarded_by_a_plain_skip" in result.stdout

    def test_an_undeclared_run_still_skips_and_still_passes(self, sandbox: Path):
        """QUIET — and this is the arm that proves the guard DISCRIMINATES.

        The loud arms above prove only that it can fire. A contributor with no
        PyYAML, declaring nothing, must keep getting a green run: buying the
        loud path by making an ordinary checkout red would be a worse bug than
        the one being fixed. This project has shipped the one-armed version
        twice (PS-299's probe printing "81/81 hunks, 0 rejects" against an
        empty directory; PS-341's "8 of 8 moved"), which is why this is not
        optional.
        """
        for source in (
            _UNSEEN_YAML_TEST_BARE,
            _UNSEEN_YAML_TEST_WITH_CUSTOM_REASON,
            _UNSEEN_YAML_TEST_BARE_SKIP_CALL,
        ):
            (sandbox / "test_probe.py").write_text(source, encoding="utf-8")

            result = _run_pytest(sandbox, "-q", "-rs")

            assert result.returncode == 0, result.stdout + result.stderr
            assert "1 skipped" in result.stdout

    def test_declaring_browser_or_engine_does_not_police_yaml(self, sandbox: Path):
        """THE DEFECT ITSELF, pinned — ARM C of the research falsification.

        This is the assertion that would have caught the original bug: ci.yml's
        own declaration, verbatim as it stood before this change, must NOT
        reach a PyYAML guard. If some later widening quietly folded yaml into
        `browser` or `engine`, the specific failure an operator reads would
        stop naming PyYAML and start naming a browser — and the reverse, a
        `yaml` pattern broad enough to swallow an engine skip, is the collision
        the two stem patterns are deliberately narrow to avoid.
        """
        (sandbox / "test_probe.py").write_text(
            _UNSEEN_YAML_TEST_BARE, encoding="utf-8"
        )

        result = _run_pytest(
            sandbox,
            "-q",
            "-rs",
            env_extra={persona_conftest.REQUIRE_ENV_VAR: "browser,engine"},
        )

        assert result.returncode == 0, (
            "declaring browser+engine policed a PyYAML skip, so the capability "
            "names have blurred into each other and the failure an operator "
            "reads no longer says what is actually missing: "
            + result.stdout
            + result.stderr
        )
        assert "1 skipped" in result.stdout

    def test_the_yaml_capability_the_workflow_declares_actually_exists(self):
        """A declaration naming a capability the harness does not know is a
        hard UsageError at startup, so a typo in ci.yml would take every tests
        job down rather than silently doing nothing. Assert the name resolves
        AND that it classifies the real guards' own wordings — the table entry
        and the declaration are two halves of one guard, and either alone
        enforces nothing.
        """
        assert "yaml" in persona_conftest.CAPABILITIES

        for reason in (
            # tests/test_ci_verification_gates.py:45 and :51, among others.
            "could not import 'yaml': No module named 'yaml'",
            # tests/test_ps306_toolchain_retry.py:54 and 7 siblings, and
            # tests/test_ps372_firefox_major_watch.py:706's plain skip.
            "PyYAML is needed to parse the workflow",
        ):
            cap = persona_conftest.capability_for_skip(reason)
            assert cap is not None and cap.name == "yaml", (
                f"{reason!r} — a real guard's own wording, taken from the tree "
                f"— classifies as {cap.name if cap else None}, so declaring "
                "'yaml' in ci.yml polices that guard not at all"
            )

    def test_pyyaml_is_declared_in_the_file_ci_installs(self):
        """NAMING THE GAP IS NOT CLOSING IT — the other half of PS-389.

        A capability declared but not PROVISIONED fails for want of
        provisioning rather than for want of correctness, which is the mistake
        `browser_chromium` is deliberately left out of the umbrella to avoid.
        ci.yml's tests job installs `requirements-dev.txt` before running
        pytest, so that file is where the declaration is honoured.

        ⚠️ THIS IS NOT BOOKKEEPING. Before PS-389 PyYAML was declared in NO
        dependency file at all and reached the tests job only as a transitive
        dependency of `uvicorn[standard]`. That route works today and is an
        accident: nothing pins it for this purpose, and the day the extra drops
        it the 89 guards go quiet — with a `yaml` declaration in force, they
        would instead go RED for want of provisioning. The declaration and the
        dependency have to land together, so this pins that they stay together.
        """
        text = (REPO_ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
        declarations = [
            line for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")
            and "yaml" in line.split("#", 1)[0].lower()
        ]
        assert declarations, (
            "PyYAML is not declared in requirements-dev.txt, but ci.yml "
            "declares the 'yaml' capability — so on any runner where the "
            "transitive route stops supplying it, every workflow-shape test "
            "goes RED for want of provisioning rather than for want of "
            "correctness"
        )


#: A MODULE-LEVEL guard: `importorskip` at import time, which is how 8 files in
#: this repo guard PyYAML. It raises `Skipped` during COLLECTION — the file is
#: dropped whole and NO test item is ever created — so `pytest_runtest_makereport`
#: is never called for it and cannot police it however loudly the environment
#: declares the capability.
_UNSEEN_MODULE_LEVEL_YAML_TEST = '''
import pytest

yaml = pytest.importorskip(
    "yaml.no_such_submodule_ps389",
    reason="PyYAML is needed to parse the workflow",
)

def test_one():
    assert False, "must never execute: the module skipped at import"

def test_two():
    assert False, "must never execute: the module skipped at import"

def test_three():
    assert False, "must never execute: the module skipped at import"
'''


class TestAModuleLevelGuardIsPolicedToo:
    """PS-389 — the hole a declaration could not reach, and the one that would
    have made this whole capability a half-fix reporting success.

    THE TWO HOOKS ARE TWO ENTRY POINTS, NOT TWO OPINIONS.
    ``pytest_runtest_makereport`` sees a skip that happened while RUNNING a
    test — a guard in the body, or in a fixture it takes. A MODULE-LEVEL
    ``importorskip`` never gets that far: it raises during COLLECTION, the
    whole file is dropped, and there is no item for that hook to be called
    with. So the run-time hook alone polices the fixture-level guards and is
    structurally blind to the module-level ones.

    MEASURED DURING PS-389, NOT REASONED ABOUT, and it is the reason this class
    exists rather than a comment. With the ``yaml`` capability wired only to
    the run-time hook, eight module-level files reported this verbatim::

        ok yaml: no test declined to run
        SKIPPED [1] tests/test_ps306_toolchain_retry.py:54: PyYAML is needed...

    **222 tests vanished and the summary said nothing had** — while printing a
    green "ok" line about the exact capability that had just failed. That is
    strictly worse than having no capability at all, because a reader is now
    reassured rather than merely uninformed.

    ⛔ THIS IS CAPABILITY-BLIND AND MUST STAY THAT WAY. Nothing below mentions
    yaml except the fixture text: the hook checks the same table every other
    path checks, so a module-level guard for ANY capability, in any file
    written from now on, is covered without anyone remembering to wire it.
    """

    def test_a_module_level_guard_fails_where_the_capability_is_declared(
        self, sandbox: Path
    ):
        """LOUD — and the failure names the MODULE, because there are no test
        ids to name. They were never created, and for the same reason the
        message states no COUNT: the module never imported, so how many tests
        were lost is unknowable at that point and printing a figure would mean
        inventing one."""
        (sandbox / "test_probe.py").write_text(
            _UNSEEN_MODULE_LEVEL_YAML_TEST, encoding="utf-8"
        )

        result = _run_pytest(
            sandbox, "-q", env_extra={persona_conftest.REQUIRE_ENV_VAR: "yaml"}
        )

        assert result.returncode != 0, (
            "a module-level guard skipped the WHOLE FILE in an environment "
            "declaring the capability, and the run still reported green — the "
            "declaration was structurally unable to see it: "
            + result.stdout
            + result.stderr
        )
        assert "test_probe.py" in result.stdout
        assert "yaml" in result.stdout
        # The message must say what happened — that a whole module went, not
        # that one test declined — or the reader has to work that out for
        # themselves from a count that is not shown.
        assert "MODULE" in result.stdout or "module" in result.stdout

    def test_the_summary_does_not_report_ok_for_a_capability_that_just_failed(
        self, sandbox: Path
    ):
        """THE MEASURED REGRESSION, pinned in the exact shape it appeared.

        The half-fix did not merely miss the skip — it printed
        ``ok yaml: no test declined to run`` beside it. This asserts the
        summary line is the FAILED one, so a future change that reintroduces
        the blindness cannot do it quietly.
        """
        (sandbox / "test_probe.py").write_text(
            _UNSEEN_MODULE_LEVEL_YAML_TEST, encoding="utf-8"
        )

        result = _run_pytest(
            sandbox, "-q", "-rs", env_extra={persona_conftest.REQUIRE_ENV_VAR: "yaml"}
        )
        out = result.stdout + result.stderr

        assert "ok yaml: no test declined to run" not in out, (
            "the summary reported the yaml capability held, in a run where a "
            "whole module declined to collect for want of it — the exact "
            "false green this mechanism exists to remove:\n" + out
        )
        assert "FAILED yaml" in out, out

    def test_an_undeclared_run_still_drops_the_module_and_still_passes(
        self, sandbox: Path
    ):
        """QUIET — the arm that proves the collection hook DISCRIMINATES.

        A contributor with no PyYAML, declaring nothing, must still get a green
        run with the module honestly skipped. A collection hook that failed
        unconditionally would make every ordinary checkout red, which is a
        worse bug than the one it was added to fix.
        """
        (sandbox / "test_probe.py").write_text(
            _UNSEEN_MODULE_LEVEL_YAML_TEST, encoding="utf-8"
        )
        # ⚠️ AN ORDINARY TEST BESIDE IT, DELIBERATELY. A sandbox holding only
        # the skipped module exits 5 ("no tests collected"), which is neither
        # the green under test nor a red — it would make this arm assert
        # nothing while looking like it passed.
        (sandbox / "test_ok.py").write_text(
            "def test_fine():\n    assert True\n", encoding="utf-8"
        )

        result = _run_pytest(sandbox, "-q", "-rs")

        assert result.returncode == 0, (
            "an undeclared run went red on a module-level skip, so the loud "
            "path was bought by making an ordinary checkout fail: "
            + result.stdout
            + result.stderr
        )
        assert "PyYAML is needed" in result.stdout

    def test_declaring_a_different_capability_does_not_police_it(
        self, sandbox: Path
    ):
        """The collection hook checks the SAME table as every other path, so a
        declaration of something else must leave this skip alone. Without this,
        a hook that fired on any collection skip at all would convert honest
        platform-bound module guards into failures across the suite."""
        (sandbox / "test_probe.py").write_text(
            _UNSEEN_MODULE_LEVEL_YAML_TEST, encoding="utf-8"
        )
        # See the note on the arm above: without this the run exits 5 and the
        # assertion below would be checking nothing.
        (sandbox / "test_ok.py").write_text(
            "def test_fine():\n    assert True\n", encoding="utf-8"
        )

        result = _run_pytest(
            sandbox,
            "-q",
            "-rs",
            env_extra={persona_conftest.REQUIRE_ENV_VAR: "browser,engine"},
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert "PyYAML is needed" in result.stdout

    def test_an_unclassifiable_module_skip_is_left_alone(self, sandbox: Path):
        """A module that skips for a reason no capability claims must stay a
        skip, even on a fully-declared run. Platform-bound module guards are
        real and honest; sweeping them in would fail runs for reasons nobody
        declared anything about."""
        (sandbox / "test_probe.py").write_text(
            'import pytest\n'
            'pytest.skip(\n'
            '    "exercises the real Windows Toolhelp process scan",\n'
            '    allow_module_level=True,\n'
            ')\n'
            '\n'
            'def test_never():\n'
            '    assert False\n',
            encoding="utf-8",
        )
        # See the note on the first quiet arm: without this the run exits 5.
        (sandbox / "test_ok.py").write_text(
            "def test_fine():\n    assert True\n", encoding="utf-8"
        )

        result = _run_pytest(
            sandbox,
            "-q",
            "-rs",
            env_extra={
                persona_conftest.REQUIRE_ENV_VAR: "browser,engine,yaml,node"
            },
        )

        assert result.returncode == 0, (
            "a module skip that classifies as NO capability was converted into "
            "a failure on a declared run — the collection hook is firing on "
            "the skip rather than on the classification: "
            + result.stdout
            + result.stderr
        )
        assert "Windows Toolhelp" in result.stdout
