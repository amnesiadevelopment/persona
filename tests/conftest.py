"""Suite-wide isolation for real state the suite would otherwise touch.

Three of them, one fixture each: the app SETTINGS store (PS-208, below), the
durable SESSION REGISTRY (PS-278), and the host WORK-AREA reading (PS-352).
The first two are the same defect on two files — a test drives a real write
path into a real path under the operator's home — and the second was added when
a new write path was wired through a helper that had never needed isolation
before. The third is the same finding on a different kind of real state: not a
file the suite WRITES, but a property of the host machine the suite READS, which
made two assertions depend on the desktop size of whatever ran them. Each
fixture carries its own account.

WHY THIS EXISTS (PS-208). `src/core/settings.py` resolves to a REAL file —
`~/.persona/settings.json` — whenever `PERSONA_SETTINGS_FILE` is unset. Any test
that drives a code path which WRITES a setting therefore writes the developer's
(or CI's) actual settings file, and every later test in the same process reads
what it left behind.

That was latent rather than harmless before: the write paths that existed were
reached only by tests which already isolated themselves
(`test_engine_rollback.py` sets this env var precisely BECAUSE the engine revert
writes a pin). PS-208 gives the APP revert the same property — a successful
`revert_to_previous_build` now records a hold-back so the reversal survives the
restart it demands — and the macOS/Linux revert suites drive that real function
without isolating anything. The result was a genuine cross-file leak: running
`test_app_update_macos.py` wrote `app_update_hold` into the real settings file,
and `test_app_ui.py`'s version-panel tests then rendered a "resume updates" row
they never asked for and failed. Each file passed alone; only the combination
failed, which is the signature of shared mutable state rather than a wrong
assertion.

Fixing it HERE rather than in those files is deliberate on two counts:

  * It touches no existing test. The PS-152/PS-164/PS-178 suites this slice must
    keep green pass completely unmodified, which is exactly the guarantee that
    would be destroyed by sprinkling a fixture through them.
  * It is the general fix, not the instance fix. The next write path added to
    settings would reintroduce the same leak in a new pair of files; an
    autouse isolation makes "a test writes real settings" unreachable for the
    whole suite instead of for the two files that happen to collide today.

A test that wants a specific path still wins — `monkeypatch.setenv` inside the
test overrides this, and `monkeypatch.delenv` (test_settings.py's PERSONA_HOME
derivation cases) still sees the variable absent.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_sessions_file(tmp_path, monkeypatch):
    """Point the DURABLE SESSION REGISTRY at this test's own tmp_path (PS-278).

    THE SAME LEAK AS THE SETTINGS ONE ABOVE, ON A SECOND REAL FILE.
    ``BrowserLauncher(registry=None)`` — which is how nine test files construct
    one — falls back to ``default_registry()``, resolving ``config.SESSIONS_FILE``
    to the operator's REAL ``~/.persona/running_sessions.json``. Any test that
    drives a code path which WRITES that registry therefore writes the
    developer's (or CI's) actual file.

    Two such paths are reached today, and they are not the same finding:

      * ``_registry.record`` on a SUCCESSFUL launch — reached by
        ``test_refusal_on_profile.py::test_a_new_attempt_supersedes_the_previous_verdict``,
        which drives a real ``start_thread`` to a spawn that succeeds. This one
        PREDATES PS-278: measured at merge-base ``14c9b24`` it already emptied a
        seeded record, so it is exposed here rather than introduced.
      * ``_registry.forget`` via ``forget_identity`` — introduced by PS-278's own
        change, when the identity hook stopped being the purely in-memory
        ``forget_refusal``. That one is ALSO fixed at its own site (an explicit
        registry in ``test_refusal_on_profile.py``'s ``_manager``), because the
        door tests there must stay isolated on their own terms rather than by a
        fixture a later edit could quietly move.

    WHAT IT DESTROYS IS THE PS-223 GUARD ITSELF. A developer or CI runner with
    persona open runs the suite, and the record of a browser that is still ALIVE
    is silently forgotten — the double-launch lockout inversion PS-223 exists to
    prevent, produced by the test suite. It cannot be reproduced by discipline
    about ordering: it fires on any run of the file.

    AND IT IS INVISIBLE WHERE IT IS MEASURED. The file is ``{"sessions": []}`` on
    a clean container and in CI, so every run there is green and says nothing
    about it. It only bites a host that actually uses persona.

    Set on ``config.SESSIONS_FILE`` rather than via ``PERSONA_SESSIONS_FILE``
    because that constant is bound at IMPORT time (``_under_home`` runs once at
    module load), so an env var set after the first import of ``src.core.config``
    would be read by nothing. ``default_registry()`` deliberately reads the
    attribute at CALL time — its own docstring says so, precisely so a test that
    moves the home gets the registry that goes with it — which is the seam this
    patches. A test wanting a specific path still wins: an explicit
    ``SessionRegistry(...)`` bypasses the default entirely.
    """
    monkeypatch.setattr(
        "src.core.config.SESSIONS_FILE", str(tmp_path / "running_sessions.json")
    )
    yield


@pytest.fixture(autouse=True)
def _isolate_host_work_area(monkeypatch):
    """Pin the ambient HOST WORK-AREA reading, so a launch test measures the
    fix rather than the desktop of whatever machine is running the suite
    (PS-352).

    THE SAME FINDING AS THE TWO FIXTURES ABOVE, ON A THIRD KIND OF REAL STATE.
    Those isolate a real FILE the suite would write; this isolates a real
    PROPERTY OF THE HOST the suite would read. `invisible_launch._work_area()`
    calls Win32 `SPI_GETWORKAREA` and answers with the machine's ACTUAL usable
    desktop, and PS-352 put that reading on the Chromium launch path: a forced
    desktop resolution is now fitted to the work area, because a 2560x1440 pick
    on a 1920x1080 monitor overflows even at scale 1.0.

    WHAT THAT COST, MEASURED RATHER THAN IMAGINED. Two tests assert an exact
    `--window-size`, and the GitHub `windows-latest` runner has a 1024x768 work
    area, so the fitted 1280x720 pick came out as `--window-size=1024,720` —
    `1024` being the runner's desktop width and nothing else:

        tests/test_ps327_outer_size.py::test_a_forced_desktop_resolution_caps_the_window
        tests/test_engine_masking_matrix.py::test_a_forced_desktop_launch_really_carries_the_window_cap

    ⚠️ AND IT WAS INVISIBLE EVERYWHERE IT WAS RUN. `_work_area()` returns
    `(0, 0)` off Windows, which `host_workarea_dip()` translates to `None` —
    "no work-area information", skip the fit. So the arm never fires on Linux
    or macOS: the ubuntu and macOS legs were green, a developer container is
    green, and ONLY the Windows runner ever executed the line. A local run is
    evidence of nothing on this vector, which is why the pin belongs here
    rather than in a note telling people to remember.

    ⛔ WHY `None` AND NOT SOME FIXED DESKTOP SIZE. `None` is the reading that
    means "skip the fit", so every test written before PS-352 sees EXACTLY the
    behaviour it was written against — the fixture cannot quietly change what
    an existing assertion is asserting. Pinning a size instead would make this
    fixture an invisible author of every `--window-size` in the suite.

    ⛔ AND IT DOES NOT MASK THE FIT — the risk worth stating, since a fixture
    that neutralises the feature under test would turn a real regression green.
    `tests/test_ps352_hidpi_window.py` sets its own work-area values INSIDE
    each test, and a test-body monkeypatch overrides an autouse fixture, so
    every guard on the fit still exercises it and still fails if it regresses.
    Any future test wanting a real work area wins the same way.

    Patched at the DEFINITION in `invisible_launch` rather than on
    `launch_policy.host_workarea_dip`, so both the Chromium path and Firefox's
    own `_seed_window_size` reader see one consistent answer.
    """
    monkeypatch.setattr(
        "src.services.browser.invisible_launch._work_area", lambda: (0, 0)
    )
    yield


@pytest.fixture(autouse=True)
def _isolate_settings_file(tmp_path, monkeypatch):
    """Point the settings store at this test's own tmp_path.

    Set unconditionally rather than only when absent: a value inherited from the
    environment the suite was launched in is exactly as shared as the default
    path, so honouring it would leave the leak open for whoever exported it.
    """
    monkeypatch.setenv(
        "PERSONA_SETTINGS_FILE", str(tmp_path / "persona-settings.json")
    )
    yield
