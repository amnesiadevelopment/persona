"""Suite-wide isolation for the app settings store.

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
