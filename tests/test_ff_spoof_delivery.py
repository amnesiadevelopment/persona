"""Every Firefox per-profile spoof is delivered through the ONE registry.

`_install_spoof` (PS-78) is the single delivery point: it registers a script for
documents created LATER (`ctx.add_init_script`) *and* replays it into the tabs
that ALREADY EXIST (`_apply_spoofs_to_open_tabs`). The second half is not
decoration — on a RESTORE launch Firefox has rebuilt the session's tabs before
`__enter__` returns, so an init script alone covers a first launch and misses
every restored tab. PS-73 measured exactly that for audio: 35.749988 on the
first launch, the UNPERTURBED 35.749972 after a restart.

PS-73 closed it for audio with its OWN bespoke replay loop, bypassing the
registry. PS-302 folds that into `_install_spoof`, which is only safe because
the single `on_ctx(_apply_spoofs_to_open_tabs)` call reads `_spoof_scripts` at
CALL time: the invocation had to move BELOW the audio registration. That
ordering is the whole risk of the change, and it fails SILENTLY — nothing
raises, nothing logs, and a first launch looks perfect. So the test that guards
it must observe THE PAGE RECEIVING THE SOURCE, never `_install_spoof` having
been called.
"""
import ast
import inspect
import os
import signal
import sys
import types
from pathlib import Path

import src.services.browser.invisible_launch as il
from src.services.browser.audio_ext import firefox_audio_init_script

_LAUNCH_SRC = Path(inspect.getsourcefile(il))


def _enclosing_defs(tree, lineno):
    """Names of every FunctionDef whose body spans `lineno`."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.lineno <= lineno <= (node.end_lineno or node.lineno):
                out.append(node.name)
    return out


def test_ctx_add_init_script_has_exactly_one_caller_in_the_spoof_region():
    """AC1, by ENUMERATION of the call sites — not by grepping for a literal.

    Grepping for the audio source would answer "audio no longer calls it",
    which is a weaker claim than the one that matters: that `_install_spoof` is
    the only door. A FIFTH vector added tomorrow with its own
    `ctx.add_init_script` reintroduces the hole this registry exists to close,
    and a literal-grep would not see it. This walks the AST instead, so any new
    caller anywhere in the module fails here.
    """
    tree = ast.parse(_LAUNCH_SRC.read_text(encoding="utf-8"))

    call_sites = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_init_script"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "ctx"
    ]

    assert len(call_sites) == 1, (
        "expected exactly ONE ctx.add_init_script call site in "
        f"{_LAUNCH_SRC.name}, found {len(call_sites)} at lines "
        f"{[c.lineno for c in call_sites]} — every spoof must go through "
        "_install_spoof, and a second caller is a vector that silently loses "
        "already-open-tab coverage"
    )
    assert "_install_spoof" in _enclosing_defs(tree, call_sites[0].lineno), (
        "the one ctx.add_init_script call is not inside _install_spoof "
        f"(line {call_sites[0].lineno}) — the registry is being bypassed"
    )


def test_the_bespoke_audio_replay_loop_is_gone():
    """AC2 (first half). PS-73's `_apply_audio_to_open_tabs` duplicated the
    registry's replay for one vector; it must not survive as a template."""
    src = _LAUNCH_SRC.read_text(encoding="utf-8")
    assert "def _apply_audio_to_open_tabs" not in src, (
        "_apply_audio_to_open_tabs still exists — the duplicate replay loop "
        "PS-302 folds into _apply_spoofs_to_open_tabs"
    )


def _spoofs_delivered_by_one_launch(monkeypatch, tmp_path, seed=1):
    """Drive the REAL launch path once against a context that ALREADY HAS A
    TAB, and return what each delivery channel actually carried.

    Shape borrowed from `test_ff_language_override._both_locale_channels_from_one_launch`,
    with the one difference that is the entire point: its `FakeCtx.pages`
    returns `[]`, so the open-tab replay path is exercised by nothing there.
    Here `pages` returns a page that RECORDS every `evaluate`, which is the
    only place the ordering defect is observable.

    Returns ``(init_scripts, evaluated_on_the_open_tab)``.
    """
    captured: dict = {"scripts": [], "evaluated": []}

    class FakePage:
        """A tab that already existed when the launch began."""

        def evaluate(self, js, *_a, **_k):
            captured["evaluated"].append(js)

    class FakeCtx:
        @property
        def pages(self):
            # NOT empty — a restore launch hands back the user's rebuilt tabs.
            return [FakePage()]

        def add_init_script(self, script, *_a, **_k):
            captured["scripts"].append(script)

    class FakeEngine:
        def __init__(self, **kwargs):
            pass

        def _default_context_kwargs(self):
            return {}

        def __enter__(self):
            return FakeCtx()

        def __exit__(self, *a):
            return False

    mod = types.ModuleType("invisible_playwright")
    mod.InvisiblePlaywright = FakeEngine
    monkeypatch.setitem(sys.modules, "invisible_playwright", mod)

    # Fork path, closed immediately: this test is about what the launch
    # DELIVERS, so the window's whole lifetime is out of scope.
    monkeypatch.setattr(il, "_fork_close_watch", lambda d, closed, **k: {10})
    monkeypatch.setattr(
        il, "_kill_profile_firefox", lambda d, pids=None, rescan=True: None
    )
    monkeypatch.setattr(il.os, "_exit", lambda code: None)
    monkeypatch.setattr(il, "_raise_profile_window", lambda *a, **k: None)

    # profile_data_dir is supplied because _child refuses a cfg without it.
    cfg = {
        "profile_dir": str(tmp_path),
        "profile_data_dir": str(tmp_path),
        "profile_name": "t",
        "seed": seed,
    }

    old_term = signal.getsignal(signal.SIGTERM)
    # _child runs IN THIS PROCESS here rather than across a real fork, so its
    # scratch pin writes into pytest's own os.environ; save and restore for the
    # same reason SIGTERM is.
    old_temp = {k: os.environ.get(k) for k in ("TMPDIR", "TMP", "TEMP")}
    r, w = os.pipe()
    try:
        il._child(cfg, w)
    finally:
        signal.signal(signal.SIGTERM, old_term)
        for _k, _v in old_temp.items():
            if _v is None:
                os.environ.pop(_k, None)
            else:
                os.environ[_k] = _v
    os.read(r, 65536)
    os.close(r)

    return captured["scripts"], captured["evaluated"]


def test_the_audio_spoof_REACHES_a_tab_that_was_already_open(monkeypatch, tmp_path):
    """⭐ AC3/AC4. The audio source lands on a PRE-EXISTING page.

    This asserts on the page RECEIVING the script, deliberately, and not on
    `_install_spoof` having been called with it. The two are not the same
    claim, and the difference is the only failure this change can produce:
    `_apply_spoofs_to_open_tabs` reads `_spoof_scripts` at CALL time and is
    invoked exactly ONCE, so registering audio AFTER that invocation still
    calls `_install_spoof`, still lands on `add_init_script`, and still
    delivers nothing to the restored tab. A test written against the call
    would stay green through exactly that regression.

    FALSIFICATION, run and recorded (AC4): moving
    `on_ctx(_apply_spoofs_to_open_tabs)` back ABOVE the audio registration —
    with the rest of PS-302 in place — turns this RED:

        AssertionError: the audio spoof never reached a tab that was already
        open ... 3 script(s) evaluated ... a RESTORE launch therefore keeps the
        UNPERTURBED audio.digest (35.749972)

    while `test_ctx_add_init_script_has_exactly_one_caller_in_the_spoof_region`
    and `test_the_bespoke_audio_replay_loop_is_gone` both stay GREEN under that
    same mutation — which is the point: they cannot see the ordering, and this
    one can.
    """
    seed = 4242
    init_scripts, evaluated = _spoofs_delivered_by_one_launch(
        monkeypatch, tmp_path, seed=seed
    )
    expected_audio = firefox_audio_init_script(seed)

    assert expected_audio in evaluated, (
        "the audio spoof never reached a tab that was already open when the "
        f"launch began ({len(evaluated)} script(s) evaluated on it) — a "
        "RESTORE launch therefore keeps the UNPERTURBED audio.digest "
        "(35.749972) on every rebuilt tab, silently: nothing raises and "
        "nothing logs. Check that on_ctx(_apply_spoofs_to_open_tabs) still "
        "runs BELOW the audio registration."
    )
    # and the new-document path is untouched by the fold
    assert expected_audio in init_scripts, (
        "the audio spoof was not registered for documents created after the "
        "launch — _install_spoof's add_init_script half"
    )


def test_every_registered_spoof_reaches_the_already_open_tab(monkeypatch, tmp_path):
    """The general property the ordering guarantees, stated once.

    Audio is the vector PS-302 moves, but the invariant is registry-wide: a
    spoof registered below the single replay invocation loses open-tab
    coverage. Locale and WebGL are unconditional on this path, so all three
    must land on the pre-existing tab from one launch.
    """
    seed = 7
    init_scripts, evaluated = _spoofs_delivered_by_one_launch(
        monkeypatch, tmp_path, seed=seed
    )
    assert init_scripts, "the launch registered no init scripts at all"
    missing = [s for s in init_scripts if s not in evaluated]
    assert not missing, (
        f"{len(missing)} of {len(init_scripts)} registered spoof(s) never "
        "reached the already-open tab — every spoof must be registered ABOVE "
        "on_ctx(_apply_spoofs_to_open_tabs)"
    )
