"""#8: every download/update worker that sets a busy flag must reset it in a
finally, so a transient raise can't wedge the flag True and dead-end all later
update/engine actions for the session.

TWO SHAPES SATISFY #8, AND THIS GUARD ACCEPTS BOTH. It originally knew only
the first, because only the first existed when it was written:

  A. RAW      `self._engine_busy = True` ... `finally: self._engine_busy = False`
  B. ROUTED   `self._set_update_in_progress(True)`
              ... `finally: self._set_update_in_progress(False)`
              where the setter's own body assigns the flag.

PS-152 routed every write of `_update_in_progress` through a single-writer
setter (so a rollback status line cannot outlive the state it describes), which
deleted the literal string `self._update_in_progress = True` that shape A greps
for. The invariant was still satisfied — `finally:` calls the setter and the
setter assigns — but the guard could not see it, and went red on a property the
code still had.

WHY THIS IS A WIDENING AND NOT A WEAKENING. Shape B is checked more strictly
than shape A, not less. It is not enough to find a method whose *name* looks
like a setter and a `finally:` that calls it: `_setters_assigning` parses the
AST and admits a method only if its body really contains `self.<flag> = <one of
its own parameters>`. So a setter that stopped writing the flag — the way this
indirection could genuinely reintroduce the wedged-flag defect — fails to
resolve, and the flag is then judged as having no reset at all. The three
falsifications in PS-152's PR body drive exactly that: gutting the setter body,
and dropping a `finally` reset at each shape, each turn this test red.

A flag must satisfy AT LEAST ONE shape completely, and any shape it *starts*
(a True-write of that shape) it must also *finish* (a False-reset of the same
shape in a finally). A flag matching neither shape is a wedged flag.
"""
import ast
import inspect
import re

import src.ui.app as app_mod

# The busy flags a worker claims for the duration of a download/update. Each
# one dead-ends a different part of the UI for the whole session if it wedges.
FLAGS = ("_update_in_progress", "_engine_busy", "_engine2_busy")


def _setters_assigning(flag: str, src: str) -> list[str]:
    """Names of methods that are a genuine single-writer setter for `flag`.

    Genuine means the body actually contains `self.<flag> = <parameter>`. The
    parameter requirement is what makes this a real check: it admits
    `def _set_x(self, running): self._x = running` and rejects both an empty
    setter and one that hard-codes a constant, either of which would let a
    `finally:` that calls it look like a reset while resetting nothing.
    """
    found: set[str] = set()
    for cls in (n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ClassDef)):
        for fn in (n for n in cls.body if isinstance(n, ast.FunctionDef)):
            params = {a.arg for a in fn.args.args}
            for node in ast.walk(fn):
                if not isinstance(node, ast.Assign):
                    continue
                for target in node.targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and target.attr == flag
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                        and isinstance(node.value, ast.Name)
                        and node.value.id in params
                    ):
                        found.add(fn.name)
    return sorted(found)


def test_busy_flags_reset_in_finally():
    src = inspect.getsource(app_mod)

    for flag in FLAGS:
        # --- shape A: the flag is written directly ------------------------
        raw_true = f"self.{flag} = True" in src
        raw_reset = bool(
            re.search(r"finally:\s*\n\s*self\." + flag + r" = False", src)
        )

        # --- shape B: the flag is written through a single-writer setter --
        setters = _setters_assigning(flag, src)
        routed_true = any(
            re.search(r"self\." + s + r"\(\s*True\s*\)", src) for s in setters
        )
        routed_reset = any(
            re.search(r"finally:\s*\n\s*self\." + s + r"\(\s*False\s*\)", src)
            for s in setters
        )

        # A flag has to be claimed somehow, by one shape or the other.
        assert raw_true or routed_true, (
            f"{flag}: no worker claims this flag (neither `self.{flag} = True` "
            f"nor a `self.<setter>(True)` whose setter assigns it). If the flag "
            f"is now written some third way, teach this guard that shape — do "
            f"not delete the check."
        )

        # Whichever shape claims it must also release it in a finally.
        if raw_true:
            assert raw_reset, (
                f"{flag} is set True directly but never reset in a finally — a "
                f"raise would wedge it True for the session (#8)."
            )
        if routed_true:
            assert setters, (
                f"{flag} is claimed through a setter, but no method assigns "
                f"`self.{flag} = <param>` — the setter does not write the flag, "
                f"so nothing actually resets it (#8)."
            )
            assert routed_reset, (
                f"{flag} is claimed through {setters} but no `finally:` calls "
                f"one of them with False — a raise would wedge it True for the "
                f"session (#8)."
            )


def test_the_routed_setter_really_writes_its_flag():
    """The load-bearing half of shape B, pinned on its own.

    Kept separate from the guard above so a regression here names its own
    cause: if `_update_in_progress` stops being assigned inside its setter,
    every `finally:` that calls that setter becomes a no-op reset, and #8 is
    reintroduced while every call site still *looks* correct.
    """
    src = inspect.getsource(app_mod)
    assert _setters_assigning("_update_in_progress", src) == [
        "_set_update_in_progress"
    ], (
        "the single writer for _update_in_progress is no longer the only method "
        "assigning it from a parameter — re-check that its finally-resets still "
        "reset something"
    )
