"""PS-328: a Windows operator has a way back from a bad app update.

Windows was the last platform with no revert control on the version panel, and
the reason was narrow: the code-only fast path ALREADY retained the previous
`app.zip` + `app.zip.hash` pair, and then deleted it on the boot confirm ~3s
later, so `rollback_target()` had nothing to resolve and `_app_rollback_row` —
which is NOT platform-gated, it asks that function and renders nothing on "" —
silently offered the operator no button at all.

Two halves, one slice: the retention now survives the confirm, and the two
revert functions grow a Windows arm that reads it.

THE EVIDENCE DISCIPLINE IS INHERITED FROM PS-80 AND PS-178 AND IS THE POINT.
Every assertion below is on FILES IN A REAL TEMP INSTALL DIR after a real call,
or on the REAL RENDERED PANEL — never on a constant existing in the source and
never on batch text alone, both of which pass against an implementation that
does not work. test_falsification_* at the bottom pins that by restoring the
deletion and requiring these to go RED.

WHAT COULD NOT BE RUN HERE, stated rather than implied: this container is not
Windows, so the generated .bat is never EXECUTED by cmd. What the script does
to the FILES is executed, through tests/test_fast_update.py's `_run_section`
helpers, which parse the emitted script and perform its move/copy/del lines
against a temp dir honouring the `if exist` guards. `tasklist`, `start`, `ping`
and the goto routing rest on the shared generator's own tests.
"""

import os

import pytest

from src.services.app_update import fast_update as fu
from src.services.app_update import updater as au
from src.ui.app import _RESUME_LABEL, _ROLLBACK_LABEL

# The file-step executor and the emitted-script helpers, reused rather than
# re-written — AC2 is explicit that there must not be a second parser.
from tests.test_fast_update import _install_dir, _run_section


def _force_os(monkeypatch, *, win=False, mac=False, linux=False):
    for mod in (au, fu):
        monkeypatch.setattr(mod._platform, "IS_WINDOWS", win)
        monkeypatch.setattr(mod._platform, "IS_MACOS", mac)
        monkeypatch.setattr(mod._platform, "IS_LINUX", linux)


def _windows_install(monkeypatch, tmp_path):
    """A REAL flet install layout on a simulated Windows host.

    Built at the genuine path shape (`<root>/data/flutter_assets/app`) and
    resolved through the real `install_app_zip_paths` off LOCALAPPDATA rather
    than stubbed — so `can_fast_update()` is a live fixture control below
    instead of a value this test handed itself.
    """
    _force_os(monkeypatch, win=True)
    root = tmp_path / "persona"
    app_dir = root / "data" / "flutter_assets" / "app"
    app_dir.mkdir(parents=True)
    dst_zip = app_dir / "app.zip"
    dst_hash = app_dir / "app.zip.hash"
    dst_zip.write_bytes(b"NEW-BAD-RELEASE")
    dst_hash.write_text("newsha", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(fu.sys, "executable", "")
    exe = root / "persona.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(
        fu.install_env, "installed_windows_exe", lambda: str(exe)
    )
    return dst_zip, dst_hash


def _retain(dst_zip, dst_hash, *, code=b"WORKING-RELEASE-CODE", sha="oldsha"):
    """Leave a retained pair on disk THE WAY A REAL UPDATE DOES — by running
    the actual swap script's file steps and then its confirm route.

    Deliberately NOT two `open(...).write()` calls. Writing the `.prev` files
    by hand would hand this suite the very artifact under test, and the
    falsification control at the bottom (which restores the confirm-time
    deletion) could not then reach it: every assertion here would stay green
    against an implementation that deletes the pair on every successful boot,
    which is precisely the defect. Driving the emitted script means the pair
    exists here for exactly the reason it exists in production, and stops
    existing under the same conditions.
    """
    from tests.test_fast_update import _emit, _section

    # the install dir currently holds `code`; the swap replaces it with what is
    # live in the fixture, retaining `code` as the previous release
    live_zip = dst_zip.read_bytes()
    live_hash = dst_hash.read_text(encoding="utf-8")
    dst_zip.write_bytes(code)
    dst_hash.write_text(sha, encoding="utf-8")
    staged = dst_zip.parent.parent / "staged"
    staged.mkdir(exist_ok=True)
    new_zip = staged / "new.zip"
    new_hash = staged / "new.zip.hash"
    new_zip.write_bytes(live_zip)
    new_hash.write_text(live_hash, encoding="utf-8")

    bat = _emit(dst_zip.parent, dst_zip, dst_hash, new_zip, new_hash)
    _run_section(bat, "swap")
    # THE BOOT CONFIRM. On this branch there is no success arm, so this is a
    # no-op and the pair survives; with the deletion restored it destroys the
    # pair, which is what makes the falsification control reach every test
    # below rather than only the emitter tests.
    try:
        _section(bat, "confirmed")
    except ValueError:
        pass  # no success arm emitted — the state this ticket ships
    else:
        _run_section(bat, "confirmed")

    return fu.retained_paths(str(dst_zip), str(dst_hash))


# --- AC1: the premise, re-derived here rather than inherited ---------------


def test_premise_a_retained_pair_now_resolves_to_a_way_back(
    monkeypatch, tmp_path
):
    # AC1 INVERTED — this is the same reading the ticket's premise inversion
    # took on origin/main, where it answered "". The FIXTURE CONTROLS are what
    # make either answer evidence: a reading with no pair on disk, or on a host
    # the fast path does not even recognise, proves nothing either way.
    dst_zip, dst_hash = _windows_install(monkeypatch, tmp_path)
    prev_zip, prev_hash = _retain(dst_zip, dst_hash)

    # fixture control 1: the fast path genuinely detects this install
    assert fu.can_fast_update() is True
    # fixture control 2: the pair is genuinely on disk
    assert os.path.isfile(prev_zip) and os.path.isfile(prev_hash)

    assert au.rollback_target() == prev_zip


# --- AC4: rollback_target answers, and its required negative ---------------


def test_rollback_target_is_empty_without_a_retained_pair(monkeypatch, tmp_path):
    # THE REQUIRED NEGATIVE. A Windows install whose last update went through
    # the FULL INSTALLER has no `.prev` — Inno upgrades in place under a fixed
    # AppId with [InstallDelete] {app}\* — so this must degrade honestly. A
    # revert button on an install with nothing retained is worse than no
    # button: it promises the machine can undo something it cannot.
    _windows_install(monkeypatch, tmp_path)

    assert fu.can_fast_update() is True  # fixture control: same install, no pair
    assert au.rollback_target() == ""


def test_rollback_target_is_empty_on_a_half_retained_pair(monkeypatch, tmp_path):
    # BOTH halves or nothing. The hash going back WITH the zip is what makes
    # flet re-extract (restore_steps' docstring), so a lone zip is not a revert
    # this can honour — and offering the gesture for one is the dead button.
    dst_zip, dst_hash = _windows_install(monkeypatch, tmp_path)
    prev_zip, _prev_hash = fu.retained_paths(str(dst_zip), str(dst_hash))
    with open(prev_zip, "wb") as f:
        f.write(b"WORKING-RELEASE-CODE")

    assert au.rollback_target() == ""


def test_rollback_target_is_empty_when_the_install_layout_is_absent(
    monkeypatch, tmp_path
):
    # A source run, not a packaged install: no flet asset layout, so there is
    # nothing to resolve and nothing to offer.
    _force_os(monkeypatch, win=True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "nowhere"))
    monkeypatch.setattr(fu.sys, "executable", "")

    assert au.rollback_target() == ""


def test_rollback_target_is_quiet_when_the_install_cannot_be_resolved(
    monkeypatch, tmp_path
):
    # This decides whether to RENDER a control, so it must answer rather than
    # raise — a panel that throws is worse than a missing row.
    _force_os(monkeypatch, win=True)

    def boom():
        raise OSError("LOCALAPPDATA unreadable")

    monkeypatch.setattr(fu, "install_app_zip_paths", boom)

    assert au.rollback_target() == ""


def test_the_other_platforms_are_untouched_by_the_windows_arm(
    monkeypatch, tmp_path
):
    # The Windows arm is FIRST in the function, so it must not shadow the two
    # arms that already shipped. Both are out of scope and stay byte-identical.
    _force_os(monkeypatch, linux=True)
    home = tmp_path / "Applications"
    home.mkdir()
    target = home / "persona.AppImage"
    target.write_bytes(b"v1")
    backup = home / "persona.AppImage.bak"
    backup.write_bytes(b"v0")
    monkeypatch.setattr(au, "installed_appimage_path", lambda: str(target))

    assert au.rollback_target() == str(backup)


# --- AC5: the ROW RENDERS for a Windows operator ---------------------------
#
# Asserted on the RENDERED PANEL, not on the flag — the same model
# tests/test_app_update_linux_retention.py records, for the reason its
# docstring gives: an implementation that resolves a retained artifact but
# never reaches the UI passes a rollback_target() assertion while the operator
# still sees no button.


def _walk_texts(panel):
    """Collect every string the built panel renders."""
    found: list[str] = []

    def walk(c):
        v = getattr(c, "value", None)
        if isinstance(v, str):
            found.append(v)
        for attr in ("content", "controls"):
            child = getattr(c, attr, None)
            if child is None:
                continue
            for k in (child if isinstance(child, list) else [child]):
                walk(k)

    walk(panel)
    return found


def _windows_panel_texts(monkeypatch, *, retained, held=""):
    """Build the REAL version panel on a Windows host whose updater reports
    `retained`, and return every string it renders."""
    from src.ui import app as app_mod
    from tests.test_app_ui import make_app

    monkeypatch.setattr(app_mod._platform, "IS_WINDOWS", True)
    monkeypatch.setattr(app_mod.app_update, "rollback_target", lambda: retained)
    monkeypatch.setattr(app_mod.app_update, "held_version", lambda: held)
    monkeypatch.setattr(
        app_mod.app_settings, "is_auto_update_enabled", lambda: False
    )
    app = make_app(None)
    # make_app goes through __new__, so the fields _build_version_panel reads
    # must be set here exactly as the real __init__ sets them.
    app._app_rollback_status = ""
    app._log = lambda *a, **k: None
    app._refresh_sidebar = lambda *a, **k: None
    app._app_latest = ""
    app._app_update_status = ""
    app._update_staged = ""
    return _walk_texts(app._build_version_panel())


def test_the_go_back_row_renders_on_windows_when_a_pair_is_retained(monkeypatch):
    # AC5 as the operator experiences it. On origin/main this row silently did
    # not exist for ANY Windows operator, because the answer below was always
    # "". Nothing here is stubbed except the service answer.
    texts = _windows_panel_texts(
        monkeypatch,
        retained=r"C:\Users\u\AppData\Local\persona\data\flutter_assets"
                 r"\app\app.zip.prev",
    )

    assert _ROLLBACK_LABEL in texts


def test_the_go_back_row_is_absent_on_windows_when_nothing_is_retained(
    monkeypatch,
):
    # The paired negative control: without it, a row that renders
    # unconditionally would pass the test above.
    texts = _windows_panel_texts(monkeypatch, retained="")

    assert _ROLLBACK_LABEL not in texts


def _real_windows_panel_texts(monkeypatch, hold=None):
    """The version panel built with the REAL rollback_target — nothing about
    the retention stubbed, only the flet host.

    The two tests above stub the service answer, which is right for pinning the
    row's own contract but means the falsification control cannot reach them:
    a stubbed "" and a stubbed path render the same way whatever the retention
    does. This drives the row off whatever the install dir genuinely holds, so
    restoring the confirm-time deletion takes the button off the panel exactly
    as it does in production.

    `hold` exists because the held state is read FIRST and short-circuits the
    row. It defaults to a stub returning "" — right for the retention tests,
    whose subject is the .prev pair and which must not be perturbed by settings
    they never touch. Pass the REAL `au.held_version` when the hold is what is
    under test: without it a caller asking "is the go-back row still there
    after a failed revert?" gets a panel that could not have shown the resume
    row whatever the hold said, i.e. an assertion that passes on the defect.
    """
    from src.ui import app as app_mod
    from tests.test_app_ui import make_app

    monkeypatch.setattr(app_mod._platform, "IS_WINDOWS", True)
    monkeypatch.setattr(
        app_mod.app_update, "held_version", hold or (lambda: "")
    )
    monkeypatch.setattr(
        app_mod.app_settings, "is_auto_update_enabled", lambda: False
    )
    app = make_app(None)
    app._app_rollback_status = ""
    app._log = lambda *a, **k: None
    app._refresh_sidebar = lambda *a, **k: None
    app._app_latest = ""
    app._app_update_status = ""
    app._update_staged = ""
    return _walk_texts(app._build_version_panel())


def test_the_row_renders_off_a_real_install_dir_that_kept_its_pair(
    monkeypatch, tmp_path
):
    # AC5 with NOTHING about the retention stubbed: a real install layout, a
    # real swap, a real boot confirm, the real rollback_target, and the real
    # panel. This is the assertion the falsification control has to be able to
    # turn red, and the one that says the operator gets a button.
    dst_zip, dst_hash = _windows_install(monkeypatch, tmp_path)
    prev_zip, _prev_hash = _retain(dst_zip, dst_hash)
    assert os.path.isfile(prev_zip)  # control: the confirm left the pair

    assert _ROLLBACK_LABEL in _real_windows_panel_texts(monkeypatch)


def test_the_row_is_absent_off_a_real_install_dir_with_no_pair(
    monkeypatch, tmp_path
):
    # The paired negative on the same unstubbed path — the full-installer lane,
    # which retains nothing and must therefore offer nothing.
    _windows_install(monkeypatch, tmp_path)

    assert _ROLLBACK_LABEL not in _real_windows_panel_texts(monkeypatch)


# --- AC6: the revert really puts the previous pair back, on FILES ----------


def _drive_windows_revert(monkeypatch, tmp_path, dst_zip, dst_hash):
    """Run the REAL revert_to_previous_build on a simulated Windows install and
    then execute the file operations the script it scheduled would perform.

    Two seams are replaced and no more: `_spawn_bat` (this container has no
    cmd) captures the script path, and `exit_for_restart` raises instead of
    calling os._exit(0) so the test can inspect the filesystem afterwards. The
    STAGING, the retained-pair resolution, the emitted script and the hold are
    all real.
    """
    spawned: list[str] = []
    monkeypatch.setattr(fu, "_spawn_bat", lambda bat: spawned.append(bat) or True)

    class _Exited(Exception):
        pass

    def _exit():
        raise _Exited()

    monkeypatch.setattr(fu, "exit_for_restart", _exit)

    msgs: list[str] = []
    with pytest.raises(_Exited):
        au.revert_to_previous_build(log=msgs.append)

    assert len(spawned) == 1, "the revert did not schedule exactly one script"
    with open(spawned[0], encoding="ascii", newline="") as f:
        bat = f.read()
    os.remove(spawned[0])
    # what cmd would do to the FILES, executed for real against the temp dir
    _run_section(bat, "restore")
    return bat, msgs


def test_a_windows_revert_puts_both_halves_of_the_previous_pair_back(
    monkeypatch, tmp_path
):
    # AC6. Asserted on the BYTES at the install path afterwards: the operator's
    # next launch reads those two files, and BOTH must move — the hash
    # mismatch is what makes flet re-extract, since its marker records the
    # release being reverted from.
    dst_zip, dst_hash = _windows_install(monkeypatch, tmp_path)
    _retain(dst_zip, dst_hash)

    _bat, msgs = _drive_windows_revert(monkeypatch, tmp_path, dst_zip, dst_hash)

    assert dst_zip.read_bytes() == b"WORKING-RELEASE-CODE", (
        "the revert did not put the previous app.zip back at the install path"
    )
    assert dst_hash.read_text(encoding="utf-8") == "oldsha", (
        "the hash did not go back with the zip — flet keeps the bad extraction"
    )
    # and the operator is told what is about to happen, before it happens
    assert any("go" in m.lower() for m in msgs), msgs


def test_a_windows_revert_does_not_corrupt_the_path_via_the_bak_strip(
    monkeypatch, tmp_path
):
    # TRAP 1, pinned. The shared revert machinery derives the install path as
    # `target[: -len(".bak")]`, which on a `.prev` target yields
    # "…/app.zip." — a path that does not exist, silently, with every rename
    # after it written against that derivation. The Windows arm branches ABOVE
    # that line; this proves the corrupted path is never touched and the real
    # one is.
    #
    # ⚠️ ASSERTED ON THE DIRECTORY LISTING, NOT ON os.path.exists(corrupted),
    # and that is forced rather than stylistic — this test simulates Windows
    # but the CI matrix also RUNS it on a real one, where the two differ.
    # Win32 strips trailing dots during path canonicalisation, so
    # "…/app.zip." resolves to "…/app.zip" and `os.path.exists` on it is True
    # for the perfectly healthy install this test wants to accept: the probe
    # cannot return False on that platform for ANY implementation, correct or
    # broken, so it measured the OS rather than the code. (It duly failed on
    # windows-latest while every other assertion here passed.) A listing
    # entry is a real directory entry, is compared as text, and no platform
    # canonicalises it — so it says the thing meant: nothing named `app.zip.`
    # was ever created beside the live pair.
    #
    # WHICH ASSERTION CATCHES TRAP 1 ON WHICH PLATFORM, since the same
    # canonicalisation splits that too and it is better said than left to be
    # rediscovered. Here (POSIX) `app.zip.` is a distinct name, so a Windows
    # arm that fell through to the `.bak` strip would create a real stray entry
    # and the listing check below names it. On Win32 that same write lands on
    # `app.zip` ITSELF — no stray entry exists to find — and it is the BYTES
    # assertion at the end that fires, because the live pair would then hold
    # the derivation's leavings rather than the restored release. Neither check
    # is redundant; each is the load-bearing one on one platform.
    dst_zip, dst_hash = _windows_install(monkeypatch, tmp_path)
    _retain(dst_zip, dst_hash)
    target = au.rollback_target()
    corrupted = target[: -len(".bak")]
    assert corrupted.endswith("app.zip."), corrupted  # the derivation, shown

    _drive_windows_revert(monkeypatch, tmp_path, dst_zip, dst_hash)

    install_dir = dst_zip.parent
    entries = sorted(os.listdir(install_dir))
    assert os.path.basename(corrupted) not in entries, (
        f"the .bak strip produced a path and something wrote to it: {entries}"
    )
    assert not any(e.endswith(".reverting") for e in entries), (
        f"the macOS/Linux park semantics ran on a Windows revert: {entries}"
    )
    assert dst_zip.read_bytes() == b"WORKING-RELEASE-CODE"


def test_a_windows_revert_with_nothing_retained_refuses_and_returns(
    monkeypatch, tmp_path
):
    # The refusal path RETURNS normally — only a successful handoff exits — so
    # a caller that branches on the return value is still correct for every
    # refusal. Nothing on disk moves.
    dst_zip, dst_hash = _windows_install(monkeypatch, tmp_path)
    exited: list[str] = []
    monkeypatch.setattr(fu, "exit_for_restart", lambda: exited.append("x"))
    monkeypatch.setattr(
        fu, "_spawn_bat", lambda bat: exited.append("spawn") or True
    )

    msgs: list[str] = []
    assert au.revert_to_previous_build(log=msgs.append) == ""

    assert exited == [], "a refused revert scheduled work or exited"
    assert dst_zip.read_bytes() == b"NEW-BAD-RELEASE", "the live pair moved"
    assert any("nothing to go back to" in m.lower() for m in msgs), msgs


def test_a_windows_revert_whose_handoff_fails_leaves_everything_in_place(
    monkeypatch, tmp_path
):
    # The one failure that can happen after the script is written: nothing has
    # moved, because the script does all the work and it was never started.
    # The operator keeps a live install AND the retained pair to retry with.
    #
    # "EVERYTHING IN PLACE" INCLUDES THE SETTINGS FILE, and that is the half
    # this test originally forgot to ask about. The hold is written BEFORE the
    # handoff (there is no "after" on the success path — os._exit(0) is the
    # last statement), so this arm is the only place it can come off again.
    # A hold surviving a revert that did not happen is not an untidy leftover:
    # _app_rollback_row reads the held state first and returns early, so the
    # go-back row the operator would retry from disappears and is replaced by
    # a button offering to RESUME the release they are trying to escape. The
    # panel assertion below is the one that says "they can try again", which
    # is the property actually at stake; the store assertion says why.
    store: dict[str, str] = {}
    from src.core import settings

    monkeypatch.setattr(
        settings, "set_app_update_hold", lambda v: store.__setitem__("held", v)
    )
    monkeypatch.setattr(settings, "app_update_hold", lambda: store.get("held", ""))
    monkeypatch.setattr(au, "APP_VERSION", "9.9.9")

    dst_zip, dst_hash = _windows_install(monkeypatch, tmp_path)
    prev_zip, prev_hash = _retain(dst_zip, dst_hash)

    def boom(bat):
        raise OSError("CreateProcess refused")

    monkeypatch.setattr(fu, "_spawn_bat", boom)
    monkeypatch.setattr(
        fu, "exit_for_restart", lambda: pytest.fail("exited after a failed spawn")
    )

    msgs: list[str] = []
    assert au.revert_to_previous_build(log=msgs.append) == ""

    assert dst_zip.read_bytes() == b"NEW-BAD-RELEASE"
    assert os.path.isfile(prev_zip) and os.path.isfile(prev_hash), (
        "a failed handoff consumed the retained pair"
    )
    # the settings file is part of "in place"
    assert store.get("held", "") == "", (
        f"a revert that never happened left a hold behind: {store}"
    )
    assert au.held_version() == ""
    # …and therefore the operator can still SEE the way back. Asserted on the
    # rendered panel rather than on held_version alone, per AC5: the flag being
    # clear is the mechanism, the row being there is the outcome.
    assert _ROLLBACK_LABEL in _real_windows_panel_texts(
        monkeypatch, hold=au.held_version
    ), "a failed revert took the go-back row off the panel"
    # and the operator is told it failed rather than being left guessing
    assert any("couldn't go back" in m.lower() for m in msgs), msgs
    # the silent-undo contract: restoring the state they were already in is not
    # narrated, so no message claims a gesture they did not make
    assert not any("resumed" in m.lower() for m in msgs), msgs


def test_a_surviving_hold_would_have_taken_the_go_back_row_off_the_panel(
    monkeypatch, tmp_path
):
    # The POSITIVE CONTROL for the panel assertion above, and the reason it is
    # worth anything. That test asserts a row is PRESENT; a presence assertion
    # is only meaningful if something could have made it absent. This drives
    # the same real panel, off the same real install dir with the same real
    # retained pair, changing ONE thing — the hold is set — and shows the row
    # goes away and "resume updates" takes its place.
    #
    # So it measures the defect's blast radius directly: this is exactly what
    # the operator saw after a failed handoff before the undo was added, and it
    # is why leaving a stale hold is worse than the no-button state this ticket
    # started from — the button does not merely fail, it DISAPPEARS, and the
    # one control left offers to reinstall the release being escaped.
    store: dict[str, str] = {}
    from src.core import settings

    monkeypatch.setattr(
        settings, "set_app_update_hold", lambda v: store.__setitem__("held", v)
    )
    monkeypatch.setattr(settings, "app_update_hold", lambda: store.get("held", ""))
    monkeypatch.setattr(au, "APP_VERSION", "9.9.9")

    dst_zip, dst_hash = _windows_install(monkeypatch, tmp_path)
    _retain(dst_zip, dst_hash)

    # control: with no hold, the row is there — same fixture, same panel
    assert _ROLLBACK_LABEL in _real_windows_panel_texts(
        monkeypatch, hold=au.held_version
    )

    store["held"] = "9.9.9"
    texts = _real_windows_panel_texts(monkeypatch, hold=au.held_version)

    assert _ROLLBACK_LABEL not in texts, (
        "the hold did not suppress the go-back row — the assertion it makes "
        "falsifiable is not measuring anything"
    )
    assert _RESUME_LABEL in texts, texts


def test_a_windows_revert_whose_handoff_and_hold_undo_both_fail_still_returns(
    monkeypatch, tmp_path
):
    # The undo has its own failure arm, and an unexercised failure arm is a
    # liability rather than a safeguard. If clearing the hold raises (a
    # read-only settings file — the same condition _set_hold is best-effort
    # about in the other direction), the refusal must still RETURN "" rather
    # than turn into an exception: the whole point of this path is that nothing
    # moved and the operator can retry, and a raise here would propagate out of
    # a gesture that is otherwise safe.
    #
    # It must also SAY so. This is the one case where the operator is genuinely
    # left holding something they cannot see — the silent-undo contract above
    # trades silence for the case where the state is restored, and this is the
    # case where it is not, so the trade is off.
    from src.core import settings

    calls: list[str] = []

    def setter(v):
        calls.append(v)
        if v == "":
            raise OSError("settings.json is read-only")

    monkeypatch.setattr(settings, "set_app_update_hold", setter)
    monkeypatch.setattr(settings, "app_update_hold", lambda: "9.9.9")
    monkeypatch.setattr(au, "APP_VERSION", "9.9.9")

    dst_zip, dst_hash = _windows_install(monkeypatch, tmp_path)
    prev_zip, _prev_hash = _retain(dst_zip, dst_hash)

    monkeypatch.setattr(
        fu, "_spawn_bat", lambda b: (_ for _ in ()).throw(OSError("refused"))
    )
    monkeypatch.setattr(fu, "exit_for_restart", lambda: pytest.fail("exited"))

    msgs: list[str] = []
    assert au.revert_to_previous_build(log=msgs.append) == ""

    # the undo was ATTEMPTED (that is the fix firing), it just could not land
    assert calls == ["9.9.9", ""], calls
    # nothing on disk moved either way — the refusal is still safe to retry
    assert dst_zip.read_bytes() == b"NEW-BAD-RELEASE"
    assert os.path.isfile(prev_zip)
    # and BOTH facts reach the operator: the revert failed, and so did the undo
    assert any("couldn't clear the update hold" in m.lower() for m in msgs), msgs
    assert any("couldn't go back" in m.lower() for m in msgs), msgs


# --- AC7: the hold makes the revert DURABLE --------------------------------


def test_a_windows_revert_writes_the_hold(monkeypatch, tmp_path):
    # AC7, through a REAL revert_to_previous_build call rather than an injected
    # value — the hold is a side effect of the gesture, and stubbing the
    # gesture would remove the thing under test.
    #
    # Without this the restart the revert performs is ITSELF the undo: the
    # fresh process has no memory of the rejected release, the 60s poll sees it
    # as newer than the restored build, and the revert lasts under a minute.
    # That is the PS-208 defect, which this platform must not re-acquire.
    store: dict[str, str] = {}
    from src.core import settings

    monkeypatch.setattr(
        settings, "set_app_update_hold", lambda v: store.__setitem__("held", v)
    )
    monkeypatch.setattr(settings, "app_update_hold", lambda: store.get("held", ""))
    monkeypatch.setattr(au, "APP_VERSION", "9.9.9")

    dst_zip, dst_hash = _windows_install(monkeypatch, tmp_path)
    _retain(dst_zip, dst_hash)
    _drive_windows_revert(monkeypatch, tmp_path, dst_zip, dst_hash)

    # the release being REJECTED is the one this process is running
    assert store.get("held") == "9.9.9"
    # and the rest of the machinery reports it, so the poll cannot re-offer it
    monkeypatch.setattr(au, "APP_VERSION", "9.9.8")
    assert au.held_version() == "9.9.9"
    assert au.update_held("9.9.9") is True


def test_a_refused_windows_revert_writes_no_hold(monkeypatch, tmp_path):
    # The paired negative: a hold written on a revert that did not happen would
    # strand the operator held back from a release they are still running.
    store: dict[str, str] = {}
    from src.core import settings

    monkeypatch.setattr(
        settings, "set_app_update_hold", lambda v: store.__setitem__("held", v)
    )
    monkeypatch.setattr(settings, "app_update_hold", lambda: store.get("held", ""))

    _windows_install(monkeypatch, tmp_path)  # no retained pair
    monkeypatch.setattr(fu, "exit_for_restart", lambda: pytest.fail("exited"))

    assert au.revert_to_previous_build(log=lambda m: None) == ""
    assert store == {}


# --- AC8: the full installer's script stays byte-identical -----------------


def test_the_full_installer_relaunch_script_is_unchanged(tmp_path):
    # PS-80's AC5, re-asserted because this ticket adds a THIRD caller to the
    # shared generator. A caller passing no arms must still get the script it
    # got before any arm existed — the empty `exhausted_jump` is what makes
    # that true, and dropping the fast path's confirm arm changes which branch
    # of it fires for the SWAP script, so the no-arm path is pinned here.
    exe = tmp_path / "persona.exe"
    exe.write_bytes(b"MZ")
    path = au._write_relaunch_bat(
        str(exe), str(tmp_path / "persona-windows-setup.exe"), 4242, 7777
    )
    try:
        with open(path, encoding="ascii", newline="") as f:
            bat = f.read()
    finally:
        os.remove(path)

    from src.services.app_update import relaunch_bat

    checks = relaunch_bat.pid_check(4242) + relaunch_bat.pid_check(7777)
    checks += relaunch_bat.image_snapshot_check(
        ("persona-windows-setup.exe", "persona-windows-setup.tmp", "persona.exe")
    )
    expected = relaunch_bat.build_bat(
        str(exe), wait_checks=checks, stage_label="settle"
    )
    assert bat == expected
    # and it carries neither arm — no recovery, no success cleanup
    assert ":recover" not in bat
    assert ":confirmed" not in bat
    assert "if not errorlevel 1 goto done" in bat


# --- AC9: falsification ----------------------------------------------------


def _restore_the_deletion(monkeypatch):
    """Put the confirm-time deletion back, exactly as origin/main had it, by
    re-wiring the swap emitter's confirm arm.

    The `drop_retained_steps` function itself is gone, so this reconstructs its
    two steps rather than calling it — which also proves the steps are the only
    thing that was removed.
    """
    real = fu._write_appzip_swap_bat

    def with_deletion(exe, new_zip, new_hash, dst_zip, dst_hash, old_pid):
        from src.services.app_update import relaunch_bat

        prev_zip, prev_hash = fu.retained_paths(dst_zip, dst_hash)
        checks = relaunch_bat.pid_check(old_pid) + relaunch_bat.image_check(
            os.path.basename(exe)
        )
        content = relaunch_bat.build_bat(
            exe,
            wait_checks=checks,
            stage_label="swap",
            stage_body=fu.render_steps_bat(
                fu.stage_steps(new_zip, new_hash, dst_zip, dst_hash)
            ),
            recover_body=fu.render_steps_bat(
                fu.restore_steps(dst_zip, dst_hash)
            ),
            confirm_body=fu.render_steps_bat(
                [("del", prev_zip, ""), ("del", prev_hash, "")]
            ),
        )
        return relaunch_bat.write_bat(content, prefix="persona-fastswap-")

    assert real is not with_deletion
    monkeypatch.setattr(fu, "_write_appzip_swap_bat", with_deletion)


def test_falsification_the_way_back_vanishes_when_the_deletion_is_restored(
    tmp_path, monkeypatch
):
    # AC9. With the confirm-time deletion restored, a confirmed-good boot
    # leaves nothing on disk — which is exactly the state origin/main is in,
    # MEASURED here rather than asserted from memory. Everything the Windows
    # arm resolves is then correctly absent, so the tests above go red on a
    # FILE THAT IS NOT THERE rather than on a changed constant.
    dst_zip, dst_hash, new_zip, new_hash = _install_dir(tmp_path)
    _restore_the_deletion(monkeypatch)
    exe = tmp_path / "persona.exe"
    exe.write_bytes(b"MZ")
    path = fu._write_appzip_swap_bat(
        str(exe), str(new_zip), str(new_hash), str(dst_zip), str(dst_hash), 4242
    )
    try:
        with open(path, encoding="ascii", newline="") as f:
            bat = f.read()
    finally:
        os.remove(path)

    _run_section(bat, "swap")
    prev_zip, prev_hash = fu.retained_paths(str(dst_zip), str(dst_hash))
    assert os.path.isfile(prev_zip)  # control: the swap did retain

    _run_section(bat, "confirmed")

    assert not os.path.exists(prev_zip), (
        "the falsification control is not reproducing the defect"
    )
    assert not os.path.exists(prev_hash)

    # AC4's and AC5's evidence is therefore unavailable: with the install dir
    # in this state there is nothing to resolve and no row to render.
    _force_os(monkeypatch, win=True)
    monkeypatch.setattr(
        fu, "install_app_zip_paths", lambda: (str(dst_zip), str(dst_hash))
    )
    assert au.rollback_target() == ""


def test_falsification_the_hold_is_the_only_thing_making_a_revert_durable(
    monkeypatch, tmp_path
):
    # AC9's second half: revert the _set_hold call alone and AC7 goes red on
    # held_version(). Modelled by neutering _set_hold, which is what removing
    # the call amounts to from the revert's point of view.
    store: dict[str, str] = {}
    from src.core import settings

    monkeypatch.setattr(
        settings, "set_app_update_hold", lambda v: store.__setitem__("held", v)
    )
    monkeypatch.setattr(settings, "app_update_hold", lambda: store.get("held", ""))
    monkeypatch.setattr(au, "_set_hold", lambda log=None: None)
    monkeypatch.setattr(au, "APP_VERSION", "9.9.9")

    dst_zip, dst_hash = _windows_install(monkeypatch, tmp_path)
    _retain(dst_zip, dst_hash)
    _drive_windows_revert(monkeypatch, tmp_path, dst_zip, dst_hash)

    # the revert STILL SUCCEEDS on disk — which is the point: the hold is what
    # makes it survive the restart, and nothing else does
    assert dst_zip.read_bytes() == b"WORKING-RELEASE-CODE"
    assert store == {}
    monkeypatch.setattr(au, "APP_VERSION", "9.9.8")
    assert au.held_version() == ""
    assert au.update_held("9.9.9") is False, (
        "the rejected release is not re-offered even without a hold — this "
        "control is not reproducing the defect it models"
    )


# --- TRAP 2: the UI contract, for a call that does not return --------------


def _rollback_app(monkeypatch):
    """The real App with _on_app_rollback reachable, on a Windows host."""
    from src.ui import app as app_mod
    from tests.test_app_ui import make_app

    monkeypatch.setattr(app_mod._platform, "IS_WINDOWS", True)
    app = make_app(None)
    app._update_in_progress = False
    app._update_staged = ""
    app._app_rollback_status = ""
    app._log = lambda *a, **k: None
    app._refresh_sidebar = lambda *a, **k: None
    return app_mod, app


def test_the_status_is_on_screen_before_a_windows_revert_hands_off(monkeypatch):
    # THE DEAD-BUTTON RULE APPLIED TO A CALLER WITH NO "AFTER". On Windows the
    # service call never returns — it spawns a script and calls os._exit(0) —
    # so a status set after it would never be set at all: the operator clicks
    # "go back", the app vanishes, and nothing ever explained why. The status
    # must already be rendered when the handoff happens.
    app_mod, app = _rollback_app(monkeypatch)
    seen: list[str] = []

    def never_returns(log=None):
        # what the operator has on screen AT THE MOMENT OF THE HANDOFF
        seen.append(app._app_rollback_status)
        raise SystemExit(0)

    monkeypatch.setattr(app_mod.app_update, "revert_to_previous_build",
                        never_returns)

    with pytest.raises(SystemExit):
        app._on_app_rollback()

    assert seen == ["going back — persona will restart"], seen


def test_a_refused_windows_revert_still_explains_itself(monkeypatch):
    # Every REFUSAL returns normally, so the arms that re-derive which refusal
    # it was must still run — the pre-set status must not become a permanent
    # lie about a revert that did not happen.
    app_mod, app = _rollback_app(monkeypatch)
    monkeypatch.setattr(
        app_mod.app_update, "revert_to_previous_build", lambda log=None: ""
    )
    monkeypatch.setattr(app_mod.app_update, "rollback_target", lambda: "")

    app._on_app_rollback()

    assert app._app_rollback_status == "nothing to go back to"


def test_a_windows_revert_that_raises_still_explains_itself(monkeypatch):
    app_mod, app = _rollback_app(monkeypatch)

    def boom(log=None):
        raise OSError("nope")

    monkeypatch.setattr(app_mod.app_update, "revert_to_previous_build", boom)

    app._on_app_rollback()

    assert app._app_rollback_status == "couldn't go back — see the log"


def test_the_other_platforms_keep_the_return_value_contract(monkeypatch):
    # Out of scope and unchanged: macOS/Linux still return after the swap and
    # the caller still branches on that return value.
    from src.ui import app as app_mod
    from tests.test_app_ui import make_app

    monkeypatch.setattr(app_mod._platform, "IS_WINDOWS", False)
    app = make_app(None)
    app._update_in_progress = False
    app._update_staged = ""
    app._app_rollback_status = ""
    app._log = lambda *a, **k: None
    app._refresh_sidebar = lambda *a, **k: None
    monkeypatch.setattr(
        app_mod.app_update, "revert_to_previous_build", lambda log=None: "/A/p.app"
    )

    app._on_app_rollback()

    assert app._app_rollback_status == "restart to run the previous version"
