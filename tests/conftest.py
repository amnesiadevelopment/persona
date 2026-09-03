"""Suite-wide isolation for the real files under ``~/.persona``.

Two of them, one fixture each: the app SETTINGS store (PS-208, below) and the
durable SESSION REGISTRY (PS-278). They are the same defect on two files — a
test drives a real write path into a real path under the operator's home — and
the second one was added when a new write path was wired through a helper that
had never needed isolation before. Each fixture carries its own account.

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
