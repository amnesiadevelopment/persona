"""Destroying an identity must drop its DURABLE session record too (PS-278).

THE DEFECT, in one sentence: ``running_sessions.json`` holds one record per
profile NAME so the launch guard survives a restart (PS-223), and no
identity-destruction path dropped it — so an operator who deleted a profile and
recreated the name met a brand-new profile reported as "already open", whose
only [ close ] affordance signals a process group belonging to a profile that no
longer exists.

WHY THE REGISTRY'S OWN FAIL-OPEN DESIGN CANNOT COVER IT. ``session_registry.py``
is emphatic that A RECORD IS NOT EVIDENCE: every read is grounded in a liveness
probe, so a record whose process died is dropped as it is read. That is exactly
right for the axis it measures. Here the recorded process is GENUINELY ALIVE and
``liveness_of`` correctly answers ALIVE — what changed is not the process but the
SUBJECT of the name, which no probe measures. Hence these tests stub psutil into
the ALIVE branch: the fix is only interesting in the case the probe gets right.

WHY IT IS WORSE THAN THE IN-MEMORY TWIN (PS-53, ``tests/test_refusal_on_profile``
:520-658, whose four-door + counterweight + falsification shape this file
mirrors on purpose):

  * ``_last_refusal`` dies with the process; THIS record is on disk and is
    re-adopted by ``scan_survivors()`` at every subsequent startup;
  * a stale verdict renders a wrong chip; a stale record REFUSES THE LAUNCH.

WHAT IS STUBBED, AND WHAT IS NOT. Only psutil, and only so the probe answers what
a real live browser would (psutil is a declared dependency, ``requirements.txt``
:36, but is absent from some agent containers). The registry persistence, the
four destruction doors, the hook wiring and the launcher are the shipped code,
unmodified — a test that stubbed the launcher could not tell a fix from a wiring
that the app does not actually perform.

The assertions are on OBSERVABLE BEHAVIOUR — the bytes in the registry file via
``registry.load()``, AND what ``bl.is_running`` / ``bl.survivor_for`` answer for
the name — never that some method was called.

ONE TEST HERE IS DELIBERATELY NOT BEHAVIOURAL:
``test_the_app_installs_forget_identity_on_the_hook`` reads ``src/ui/app.py``'s
AST. Every other test builds its own wiring in ``_manager``, which is right for
what they measure and is precisely why none of them can see the composition
root — reverting app.py's single ``set_forget_identity_hook`` argument restores
the durable lockout in full with the whole suite green. That line is acceptance
criterion 3, so it gets its own witness, in the idiom this repo already uses for
composition-root pins (``tests/test_ps271_version_panel_rail.py``:295-330).
"""

import sys

import pytest

from src.services.browser.launcher import BrowserLauncher
from src.services.browser.session_registry import SessionRecord, SessionRegistry

#: A pid nothing in this test process resolves. It is never signalled: no test
#: here reaches ``terminate_record``, and the stubbed probe answers about the
#: record rather than about the host.
_PID = 424242
#: Recorded create time, echoed by the stub so the pid-reuse discriminator in
#: ``liveness_of`` agrees and the probe reaches ALIVE.
_CREATE_TIME = 1234.5


class _AlivePsutil:
    """Just enough psutil for ``liveness_of`` to answer ALIVE.

    A live browser is what makes this defect visible, and it is the ONE thing an
    agent container without psutil cannot supply. Everything the probe branches
    on is answered the way a running process would answer it: the pid resolves,
    the status is not a zombie, and the create time matches what was recorded.
    """

    STATUS_ZOMBIE = "zombie"

    class NoSuchProcess(Exception):
        pass

    class Process:
        def __init__(self, pid):
            self.pid = pid

        def status(self):
            return "running"

        def create_time(self):
            return _CREATE_TIME


def _alive(monkeypatch):
    monkeypatch.setitem(sys.modules, "psutil", _AlivePsutil)


def _manager(tmp_path, monkeypatch):
    """A real ProfileManager on a temp store, wired to a real BrowserLauncher
    exactly as ``src/ui/app.py`` wires them, over a temp registry file.

    Both hooks, and the identity hook is the production one — so these tests
    cannot pass against a wiring the app does not perform.
    """
    import src.core.config as cfg
    import src.services.profile.manager as mod

    pf, dd = tmp_path / "profiles.json", tmp_path / "data"
    for m in (cfg, mod):
        monkeypatch.setattr(m, "PROFILES_FILE", str(pf), raising=False)
        monkeypatch.setattr(m, "DATA_DIR", str(dd), raising=False)
    from src.services.profile.manager import ProfileManager

    registry = SessionRegistry(str(tmp_path / "running_sessions.json"))
    pm = ProfileManager()
    bl = BrowserLauncher(registry=registry)
    pm.set_stop_hook(bl.stop_profile)
    pm.set_forget_identity_hook(bl.forget_identity)
    return pm, bl, registry


def _seed_survivor(bl, registry, name):
    """Persist a live-looking session for ``name`` and adopt it as a survivor.

    This is the state a persona meets at startup after an unclean exit: a record
    on disk whose process is still running. ``scan_survivors`` is the shipped
    adoption path, so the survivor is established the way production establishes
    it rather than by writing ``_survivors`` by hand.
    """
    registry.record(
        SessionRecord(
            profile=name,
            pid=_PID,
            create_time=_CREATE_TIME,
            pgid=None,
            engine="chromium",
            started_at=0.0,
            owner_pid=1,
        )
    )
    bl.scan_survivors()
    assert bl.is_running(name) is True, f"precondition: {name} reads as running"
    assert bl.survivor_for(name) is not None, f"precondition: {name} has a survivor"


def _refuse(bl, monkeypatch, name):
    """Drive a real ``start_thread`` to a REFUSAL, synchronously.

    The shipped recording path (``classify_refusal`` inside ``start_thread``'s
    handler), mirrored from ``tests/test_refusal_on_profile.py``'s ``_refuse``
    — a refusal written into ``_last_refusal`` by hand would prove nothing about
    the store the app's hook actually has to clear.
    """
    import src.services.browser.launcher as launcher_mod
    from src.models.profile import Profile
    from src.services.proxy.errors import GeographyUnknownError

    def boom(profile):
        raise GeographyUnknownError("no geography")

    monkeypatch.setattr(launcher_mod, "spawn_browser", boom)
    bl.start_thread(Profile(name=name), lambda *a, **k: None)


def _names(registry):
    return sorted(r.profile for r in registry.load())


def _assert_clean_for_a_recreated_name(pm, bl, registry, name):
    """The operator's actual next move: take the freed name and try to open it."""
    pm.add_profile(name, "", "windows")
    assert _names(registry) == [], (
        f"the durable record for {name!r} outlived the profile it described and "
        "is now on disk against a different profile of the same name"
    )
    assert bl.survivor_for(name) is None, (
        f"a brand-new {name!r} inherited a survivor record — its card renders "
        "[ close ], a gesture that reaps a process group belonging to a profile "
        "that no longer exists"
    )
    assert bl.is_running(name) is False, (
        f"a profile created seconds ago and never launched is reported as "
        f"already running, and its launch is refused ({name!r})"
    )


# --------------------------------------------------------------------------
# The four identity-destruction doors
# --------------------------------------------------------------------------


def test_a_deleted_profile_does_not_hand_its_session_record_to_its_replacement(
    tmp_path, monkeypatch
):
    """THE defect. Delete, recreate the name, and the new profile must be
    launchable — not blocked by a record describing a browser that belonged to
    the profile that was deleted.

    Note ``stop_profile`` cannot reach this on its own: a survivor is in neither
    ``_active_sessions`` nor ``_starting``, so the stop hook delete already fires
    returns False early and touches nothing. The disposal has to ride the
    IDENTITY event.
    """
    _alive(monkeypatch)
    pm, bl, registry = _manager(tmp_path, monkeypatch)
    pm.add_profile("acme", "", "windows")
    _seed_survivor(bl, registry, "acme")

    assert pm.delete_profile("acme") is True

    _assert_clean_for_a_recreated_name(pm, bl, registry, "acme")


def test_a_wipe_leaves_no_session_record_for_a_recreated_name(
    tmp_path, monkeypatch
):
    """The wipe frees every name at once, so it is the delete case multiplied.
    Its typed "this cannot be undone" has to be true of the records too."""
    _alive(monkeypatch)
    pm, bl, registry = _manager(tmp_path, monkeypatch)
    pm.add_profile("acme", "", "windows")
    pm.add_profile("beta", "", "windows")
    _seed_survivor(bl, registry, "acme")
    _seed_survivor(bl, registry, "beta")
    assert _names(registry) == ["acme", "beta"], "precondition: both recorded"

    assert pm.wipe_all_profiles() == 2

    for name in ("acme", "beta"):
        _assert_clean_for_a_recreated_name(pm, bl, registry, name)


def test_a_rename_frees_the_name_without_its_session_record(tmp_path, monkeypatch):
    """A rename re-keys ``self.profiles`` and leaves the launcher holding a
    record under the ORIGINAL name — invisible to the operator (the card now
    looks under the new name) while sitting on a key another profile can take.

    Dropped rather than re-keyed on purpose: the record names a pid and a
    process group, and moving it would attach a live browser's teardown to a
    profile the operator has just renamed out from under it. The renamed profile
    keeps its browser; what it does not keep is a guard under the freed name.
    """
    _alive(monkeypatch)
    pm, bl, registry = _manager(tmp_path, monkeypatch)
    pm.add_profile("acme", "", "windows")
    _seed_survivor(bl, registry, "acme")

    assert pm.update_profile("acme", "acme-eu") is True

    _assert_clean_for_a_recreated_name(pm, bl, registry, "acme")


def test_an_overwriting_import_does_not_pass_the_record_to_the_arriving_profile(
    tmp_path, monkeypatch
):
    """An overwrite REPLACES the record holding the name — the same
    identity-is-gone event as a delete, reached by a different door.

    (The NON-overwriting import arm is correctly absent: it takes a name that was
    already free, so there is no identity to destroy — the same reasoning
    ``manager.py`` records for firing ``_forget_identity`` on this arm only.)
    """
    _alive(monkeypatch)
    pm, bl, registry = _manager(tmp_path, monkeypatch)
    pm.add_profile("acme", "", "windows")

    outdir = tmp_path / "exports"
    outdir.mkdir()
    ok, archive = pm.export_profile("acme", str(outdir), include_data=False)
    assert ok, f"precondition: the export succeeded ({archive})"

    # Seed AFTER the export so the record describes the profile being replaced.
    _seed_survivor(bl, registry, "acme")

    ok, msg = pm.import_profile(archive, overwrite=True)
    assert ok, f"precondition: the overwriting import succeeded ({msg})"

    assert _names(registry) == [], (
        "the replaced profile's session record was inherited by the imported "
        "one, which has never been launched"
    )
    assert bl.survivor_for("acme") is None
    assert bl.is_running("acme") is False


# --------------------------------------------------------------------------
# The counterweight — what must NOT clear the record
# --------------------------------------------------------------------------


def test_an_ordinary_edit_and_a_session_teardown_leave_the_record_intact(
    tmp_path, monkeypatch
):
    """The counterweight, and the reason the disposal rides the identity hook
    instead of a line in ``_forget_session_facts``.

    Both arms are PS-223 itself. An ordinary edit does not destroy an identity,
    and a teardown does not either — and the teardown arm is the sharper one:
    ``stop_profile`` finds no session for a survivor and ``shutdown_all`` reaps
    only ``_active_sessions``, so NEITHER of them kills the browser this record
    names. A fix that made the four tests above pass by forgetting on any
    profile touch would erase the record of a browser that is still on screen and
    hand back the double launch.

    That the disposal still works when explicitly asked for is asserted at the
    end, so this test cannot pass by the disposal being broken outright.
    """
    _alive(monkeypatch)
    pm, bl, registry = _manager(tmp_path, monkeypatch)
    pm.add_profile("keep", "", "windows")
    _seed_survivor(bl, registry, "keep")

    # Arm 1: an ordinary edit — same name in, same name out.
    assert pm.update_profile("keep", "keep", new_notes="edited") is True
    assert _names(registry) == ["keep"], (
        "an ordinary edit dropped the record — the disposal degenerated into "
        "'forget on any profile touch' and re-opened the PS-223 lockout"
    )
    assert bl.is_running("keep") is True

    # Arm 2: a session teardown, neither half of which can kill a survivor.
    assert bl.stop_profile("keep") is False, (
        "precondition: a survivor is in no session dict, so the stop path has "
        "nothing to stop"
    )
    bl.shutdown_all()
    assert _names(registry) == ["keep"], (
        "a teardown that did not kill the browser erased its record — the guard "
        "is gone across a clean restart while the browser is still running"
    )
    assert bl.is_running("keep") is True

    # And the disposal is genuinely reachable, so the two asserts above are
    # pinning the EVENT rather than a dead code path.
    bl.forget_survivor("keep")
    assert _names(registry) == []


# --------------------------------------------------------------------------
# Falsification — the tests above pin a mechanism, not a tautology
# --------------------------------------------------------------------------


def test_falsification_no_identity_hook_hands_the_record_to_the_replacement(
    tmp_path, monkeypatch
):
    """The pre-fix wiring, reproduced: delete stops the browser but never tells
    the launcher the identity is gone. Asserts the defect is REAL, so a green run
    above means the mechanism is present rather than that nothing was ever at
    stake.
    """
    _alive(monkeypatch)
    pm, bl, registry = _manager(tmp_path, monkeypatch)
    # Un-wire ONLY the identity hook; the stop hook stays, exactly as before.
    pm.set_forget_identity_hook(None)
    pm.add_profile("acme", "", "windows")
    _seed_survivor(bl, registry, "acme")

    assert pm.delete_profile("acme") is True
    pm.add_profile("acme", "", "windows")

    assert _names(registry) == ["acme"], (
        "the un-wired build must keep the orphaned record, or these tests prove "
        "nothing"
    )
    assert bl.survivor_for("acme") is not None
    assert bl.is_running("acme") is True, (
        "the un-wired build must lock the recreated name out, or these tests "
        "prove nothing"
    )


def test_the_hook_the_app_installs_is_the_one_that_disposes(tmp_path, monkeypatch):
    """The wiring itself, at the composition root's own level of abstraction.

    ``src/ui/app.py`` installs ONE bare method on ``set_forget_identity_hook``,
    and for as long as that method was ``forget_refusal`` the durable registry
    was simply never on the event. Asserting the launcher's identity method
    drops BOTH name-keyed stores is what stops a future name-keyed dict being
    added beside them and silently missed.

    BOTH STORES ARE GENUINELY OCCUPIED FIRST, and that is the whole point of the
    ordering below. An earlier revision of this test asserted
    ``last_refusal(...) is None`` over a name that had never been refused, so the
    assertion was already true before the call and could not fail — "emptiness
    rendered as success", under a docstring claiming it proved the opposite. The
    refusal is therefore driven through the real ``start_thread`` (the shipped
    recording path), and it is driven BEFORE the survivor is seeded: with a
    survivor present ``start_thread`` refuses the launch as a duplicate and
    returns before it can ever classify a spawn failure.
    """
    _alive(monkeypatch)
    pm, bl, registry = _manager(tmp_path, monkeypatch)
    pm.add_profile("acme", "", "windows")

    _refuse(bl, monkeypatch, "acme")
    assert bl.last_refusal("acme") is not None, (
        "precondition: the refusal store must be OCCUPIED, or the assertion "
        "below cannot fail"
    )
    _seed_survivor(bl, registry, "acme")

    bl.forget_identity("acme")

    assert _names(registry) == []
    assert bl.survivor_for("acme") is None
    assert bl.last_refusal("acme") is None, (
        "the identity event dropped the durable record but left the in-memory "
        "refusal behind — the PS-53 disposal is silently no longer on the hook"
    )


# --------------------------------------------------------------------------
# The INSTALLATION, at src/ui/app.py — the one line the tests above cannot see
# --------------------------------------------------------------------------
#
# Every test above builds its own wiring in _manager, which is right for what
# they measure (the launcher method, against a real ProfileManager) and is
# exactly why none of them can see the composition root. Reverting app.py's
# single ``set_forget_identity_hook`` argument to ``forget_refusal`` restores the
# durable lockout in full — delete, recreate the name, launch refused — with the
# whole suite green. That line is acceptance criterion 3 of PS-278 ("the
# disposal is reached through the existing set_forget_identity_hook event"), so
# it gets a witness.
#
# AN AST WALK RATHER THAN A CONSTRUCTED App: importing flet and building an App
# through a Container is a heavy, environment-dependent way to observe one
# argument, and the repo already reads app.py's source for exactly this kind of
# composition-root pin (tests/test_ps271_version_panel_rail.py:295-330). What
# this measures is honest and bounded: that the argument installed on the hook
# is ``self.bl.forget_identity``. It does NOT prove the hook is ever fired —
# that is what the four door tests above establish, through the real manager.


def _forget_identity_hook_arguments() -> list[str]:
    """The attribute name of every argument passed to
    ``set_forget_identity_hook`` in ``src/ui/app.py``, read out of the AST.

    A list rather than a single value so a second installation (or a removed
    one) is visible as a count rather than silently taking the first match.
    """
    import ast
    import inspect
    from pathlib import Path

    import src.ui.app as app_mod

    source = Path(inspect.getsourcefile(app_mod)).read_text(encoding="utf-8")
    tree = ast.parse(source)

    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "set_forget_identity_hook"):
            continue
        if not node.args:
            found.append("<no argument>")
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Attribute):
            found.append(arg.attr)
        elif isinstance(arg, ast.Constant) and arg.value is None:
            found.append("None")
        else:
            # A lambda or a call — the shape the ticket explicitly asked us not
            # to install, named so the failure message says what it found.
            found.append(f"<{type(arg).__name__}>")
    return found


def test_the_app_installs_forget_identity_on_the_hook():
    """The composition root actually installs the method the tests above prove.

    Without this, app.py:348 can be reverted to ``self.bl.forget_refusal`` and
    every other test in this file stays green while the durable lockout returns
    in full: delete a profile, recreate the name, and its launch is refused by a
    record naming a browser that belonged to a profile which no longer exists.
    """
    installed = _forget_identity_hook_arguments()

    assert installed == ["forget_identity"], (
        "src/ui/app.py must install BrowserLauncher.forget_identity on "
        "set_forget_identity_hook (the single event that says the name has "
        f"stopped meaning what it meant); found {installed!r}. Installing a "
        "per-store method here puts only that store on the event — which is "
        "the PS-278 defect exactly."
    )


# --------------------------------------------------------------------------
# 9. THE ISOLATION IS ASSERTED, NOT ASSUMED.
#
# Round 2 of this ticket shipped a helper that reached PAST its temp store and
# deleted records from the operator's real ~/.persona/running_sessions.json.
# Nothing caught it, for a reason that generalises: that file is
# `{"sessions": []}` on a clean container and in CI, so the leak is green
# everywhere it is measured and only bites a host that actually runs persona.
#
# So the isolation gets a witness of its own. A leak of this shape is invisible
# by construction; the only thing that makes it visible is a test that asks
# where the default registry actually points.
# --------------------------------------------------------------------------


def test_a_default_launcher_does_not_touch_the_operators_real_registry():
    """``BrowserLauncher()`` with no registry must resolve INSIDE the test's
    tmp_path, never ``~/.persona/running_sessions.json``.

    Nine test files construct a bare ``BrowserLauncher()``. Any of them can grow
    a registry-touching path tomorrow — and two already have one: a successful
    ``start_thread`` calls ``_registry.record`` (this predates PS-278; it is
    reached by ``test_refusal_on_profile.py``'s "a new attempt supersedes"
    case), and PS-278's own ``forget_identity`` calls ``_registry.forget``.

    What that leak destroys is the PS-223 guard itself: a developer or CI runner
    with persona open loses the record of a browser that is still ALIVE, which
    is the double-launch lockout inversion PS-223 exists to prevent — produced
    by the test suite.

    Asserted on the RESOLVED PATH rather than by writing and checking, because
    the failure this pins is precisely that a write goes somewhere the test did
    not look. A path assertion cannot be satisfied by an empty file.
    """
    import os

    bl = BrowserLauncher()
    path = bl._registry.path
    real = os.path.expanduser("~/.persona/running_sessions.json")

    assert os.path.abspath(path) != os.path.abspath(real), (
        f"a default BrowserLauncher resolved the REAL session registry ({path}). "
        "tests/conftest.py's _isolate_sessions_file exists to make that "
        "unreachable; a suite run would now reap records for browsers that are "
        "genuinely still alive on the developer's machine."
    )
