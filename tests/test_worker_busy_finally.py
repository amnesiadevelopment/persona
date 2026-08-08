"""#8: every download/update worker that sets a busy flag must reset it in a
finally, so a transient raise can't wedge the flag True and dead-end all later
update/engine actions for the session."""
import inspect
import re

import src.ui.app as app_mod


def _worker_bodies():
    src = inspect.getsource(app_mod)
    # split into 'def work' blocks (the threaded workers) — crude but effective
    return re.split(r"\n(?=\s*def work\(\))", src)


def test_busy_flags_reset_in_finally():
    src = inspect.getsource(app_mod)
    # For each busy flag that a worker sets True, there must be a finally: that
    # resets it False somewhere in the module (the guard against a wedged flag).
    for flag in ("_update_in_progress", "_engine_busy", "_engine2_busy"):
        assert f"self.{flag} = True" in src, flag
        # a finally-reset exists
        assert re.search(
            r"finally:\s*\n\s*self\." + flag + r" = False", src
        ), f"{flag} not reset in a finally block"
