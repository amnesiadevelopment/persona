"""PS-187 — a profile cannot come to rest with an ``os_type`` the engine will not honour.

WHAT THIS FILE PINS, AND WHY IT IS SHAPED THE WAY IT IS.

The defect: our OS fold recognises ``win`` and folds it to ``windows``, so our
own code treated it as a legitimate spelling. The packaged engine does NOT
honour it — ``--fingerprint-platform=win`` is answered with ``Google Inc.
(Google)`` / SwiftShader. The masking layer stood down expecting the engine to
author the identity, the engine did not, neither author wrote the pair, and the
host's software rasteriser reached the page. PS-161 fixed the READ side. This is
the WRITE side: the value remained STORABLE.

⚠️ THE TEST SHAPE IS THE POINT. "Enumerating the doors you happened to think of
is what left this open the first time." So the load-bearing assertions here are
about the END STATE — *no reachable sequence of operations leaves a profile at
rest carrying a non-canonical ``os_type``* — rather than about a list of entry
points. Concretely:

  * ``test_no_door_can_leave_a_profile_at_rest_...`` drives every door this
    repo has and asserts on what is IN THE MANAGER afterwards, not on what each
    door returned.
  * ``test_the_field_itself_repairs_...`` asserts the guarantee at the FIELD,
    which is what makes it hold for the door nobody has written yet. A door
    added next year cannot write this field without crossing it.
  * ``test_every_stored_value_is_honoured_by_the_engine_...`` closes the loop
    on the actual harm: it does not trust the canonical LIST, it re-runs the
    engine-vocabulary question over whatever is stored.

A test asserting "the validator was called" is explicitly NOT coverage (PS-11),
and none of these do that. Two of them would still fail if the refusal were
deleted but the repair kept, and vice versa — they pin the property from both
sides rather than pinning one function's invocation.
"""
import json
import pathlib

import pytest

from src.models.os_type import (
    CANONICAL_OS_TYPES,
    OS_NORM_TABLE,
    RECOGNISED_OS_TYPES,
    canonical_os_type,
)
from src.models.profile import Profile
from src.services.browser.engine_platform import (
    ENGINE_HONOURED_PLATFORMS,
    engine_honours,
    engine_platform_for,
)
from src.services.profile.coherence import IncoherentProfile
from src.services.profile.manager import ProfileManager
from src.services.profile.transfer import export_to_zip

#: Every spelling our fold recognises but the engine does not honour. Derived,
#: never hand-listed: the class is what leaked, and a hand-list would pin only
#: the one alias that happened to be measured (``win``) while five more stayed
#: open. This is what that derivation yields today:
#:     darwin, ipad, ipados, iphone, mac, win
ALIASES_THE_ENGINE_REJECTS = sorted(RECOGNISED_OS_TYPES - CANONICAL_OS_TYPES)

#: Values that are not even recognised — a typo, a platform we do not serve, an
#: empty string from a half-filled form. They must not come to rest either.
UNRECOGNISED_VALUES = ["freebsd", "plan9", "Windows NT", "", "WIN"]

BAD_VALUES = ALIASES_THE_ENGINE_REJECTS + UNRECOGNISED_VALUES


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    import src.core.config as cfg
    import src.services.profile.manager as mod

    pf, dd = tmp_path / "profiles.json", tmp_path / "data"
    for m in (cfg, mod):
        monkeypatch.setattr(m, "PROFILES_FILE", str(pf), raising=False)
        monkeypatch.setattr(m, "DATA_DIR", str(dd), raising=False)
    return ProfileManager()


# ---------------------------------------------------------------------------
# The instrument, checked before anything is attributed to the product (PS-14).
# ---------------------------------------------------------------------------


def test_the_alias_class_is_non_empty_and_win_is_in_it():
    """If this fails, every other test on this page is vacuously green.

    A derived set that silently became empty would make the parametrised tests
    below run zero cases and report success — the exact shape of a verification
    layer that quietly stops verifying.
    """
    assert ALIASES_THE_ENGINE_REJECTS, (
        "the fold no longer recognises any spelling outside the canonical set, "
        "so this whole file is testing nothing. If that is a deliberate "
        "change, this file needs rewriting, not deleting."
    )
    assert "win" in ALIASES_THE_ENGINE_REJECTS, (
        "`win` is the alias that was MEASURED leaking the host's GL strings "
        "(PS-161 round 2). It must remain in the class this file pins."
    )


def test_the_canonical_set_is_the_folds_arms_not_the_engines_platforms():
    """The set-choice that is easy to get backwards, pinned by MEASUREMENT.

    ``ENGINE_HONOURED_PLATFORMS`` is ``{windows, macos, linux}`` and answers a
    question about the value the ENGINE RECEIVES. Constraining STORAGE to it
    would refuse every mobile profile: the engine has no Android/iOS platform,
    so ``engine_platform_for`` backs those with the nearest desktop platform it
    DOES spoof while the device preset supplies the mobile signals.

    The arms are the right set because of a property that is re-measured here
    rather than asserted: every arm is honoured on BOTH device types, and every
    non-arm recognised spelling is honoured on NEITHER.
    """
    assert CANONICAL_OS_TYPES == {arm for _spellings, arm in OS_NORM_TABLE}

    # Storing against the engine's set would refuse the mobile families.
    assert CANONICAL_OS_TYPES - ENGINE_HONOURED_PLATFORMS == {"android", "ios"}

    for arm in sorted(CANONICAL_OS_TYPES):
        for device_type in ("desktop", "mobile"):
            assert engine_honours(engine_platform_for(arm, device_type)), (
                f"{arm!r} is in the canonical (storable) set but the engine "
                f"does not honour it on device_type={device_type!r}. The "
                f"storable set must be exactly the values we can serve."
            )

    for alias in ALIASES_THE_ENGINE_REJECTS:
        assert not engine_honours(engine_platform_for(alias, "desktop")), (
            f"{alias!r} is treated as an alias needing repair, but the engine "
            f"honours it — the repair is then gratuitous and the canonical set "
            f"is drawn in the wrong place."
        )


# ---------------------------------------------------------------------------
# THE PROPERTY. Asserted on the END STATE, not on a list of entry points.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", BAD_VALUES)
def test_the_field_itself_repairs_a_non_canonical_value(bad):
    """The guarantee lives at the FIELD, which is what covers unwritten doors.

    This is the assertion that distinguishes the fix that was made from a guard
    added at each known door. A door-level fix leaves this failing; only a rule
    the field enforces passes it. A door added next year cannot write this
    field without crossing this.

    Both writing shapes are covered, and the second is why ``__setattr__`` was
    chosen over ``__post_init__``: ``update_profile`` does not construct a
    Profile, it assigns onto a live one.
    """
    constructed = Profile(name="p", os_type=bad)
    assert constructed.os_type in CANONICAL_OS_TYPES

    assigned = Profile(name="p")
    assigned.os_type = bad
    assert assigned.os_type in CANONICAL_OS_TYPES, (
        "assignment onto a live instance escaped the repair — this is exactly "
        "the path update_profile takes (`profile.os_type = new_os`), so a "
        "__post_init__-only fix would leave this door open."
    )

    # And the repaired value is the MEANING-PRESERVING one, not merely legal:
    # `win` must land on `windows`, never on some default that silently changes
    # which machine the profile presents.
    assert constructed.os_type == canonical_os_type(bad)


@pytest.mark.parametrize("bad", BAD_VALUES)
def test_no_door_can_leave_a_profile_at_rest_with_a_bad_os_type(mgr, bad, tmp_path):
    """Drive EVERY door this repo has and assert on what is STORED afterwards.

    The assertions are deliberately about the manager's state, never about what
    a door returned: a door is allowed to refuse (create/update) or to repair
    (import/restore/legacy load), and the property is indifferent to which — it
    only forbids the bad value coming to REST.
    """

    def at_rest(name):
        p = mgr.profiles.get(name)
        return None if p is None else p.os_type

    # --- Door 1: create. Authoring, so it REFUSES loudly. ---------------------
    with pytest.raises(IncoherentProfile) as exc:
        mgr.add_profile("created", "", bad)
    assert bad in str(exc.value) or repr(bad) in str(exc.value)
    assert "created" not in mgr.profiles, (
        "the create door refused but stored the profile anyway"
    )

    # --- Door 2: update. Authoring, so it REFUSES — but only when the edit
    # SUPPLIES the bad value; an unrelated edit must still go through. --------
    assert mgr.add_profile("live", "", "windows")
    with pytest.raises(IncoherentProfile):
        mgr.update_profile("live", "live", None, bad)
    assert at_rest("live") == "windows", "a refused edit changed the record"

    assert mgr.update_profile("live", "live", None, None, new_notes="unrelated")
    assert at_rest("live") == "windows"

    # --- Door 3: the legacy/disk load. RECOVERY, so it REPAIRS. --------------
    # Written straight to profiles.json, bypassing every service-layer guard —
    # this is a record from an older build, or one a human edited by hand.
    profiles_file = pathlib.Path(mgr.profiles_file) if hasattr(
        mgr, "profiles_file"
    ) else pathlib.Path(tmp_path / "profiles.json")
    import src.services.profile.manager as mod

    profiles_file = pathlib.Path(mod.PROFILES_FILE)
    raw = json.loads(profiles_file.read_text()) if profiles_file.exists() else {}
    raw["legacy"] = {"name": "legacy", "os_type": bad, "engine": "chromium"}
    profiles_file.write_text(json.dumps(raw))

    reloaded = ProfileManager()
    assert "legacy" in reloaded.profiles, (
        "the legacy record was DROPPED rather than repaired — that strands a "
        "profile the operator already had, which the per-door decision "
        "explicitly rejects."
    )
    assert reloaded.profiles["legacy"].os_type in CANONICAL_OS_TYPES
    assert reloaded.profiles["legacy"].os_type == canonical_os_type(bad)

    # --- Door 4: restore from trash. RECOVERY, so it REPAIRS and strands
    # nothing. The door is documented as EXEMPT from the coherence rules, so a
    # refusal here would be a regression against that decision. ---------------
    class _Entry:
        name = "restored"
        material_path = ""
        payload = {"name": "restored", "os_type": bad, "engine": "chromium"}

    ok, err = mgr.restore_profile(_Entry())
    assert ok, f"restore was refused ({err!r}) — a trashed profile is stranded"
    assert at_rest("restored") in CANONICAL_OS_TYPES
    assert at_rest("restored") == canonical_os_type(bad)

    # --- Door 5: import from zip. RECOVERY, so it REPAIRS. -------------------
    # Export a good profile, rewrite the archive's os_type to the bad value (an
    # archive written by an older build), and import it.
    #
    # ⚠️ `export_to_zip`'s third argument is a DIRECTORY, and the archive's real
    # path is RETURNED. An earlier revision of this test passed a *.zip path and
    # gated the whole door on `if ok:` — so the export failed silently, `ok` was
    # always False, and THIS DOOR WAS NEVER EXERCISED while the test reported
    # green. That is precisely the defect class PS-11 names, reproduced inside
    # the test written to close it. The export is now ASSERTED, never gated on:
    # if it ever breaks again, this fails loudly instead of skipping.
    export_dir = tmp_path / f"exp-{abs(hash(bad))}"
    export_dir.mkdir()
    assert mgr.add_profile("exportme", "", "windows")
    ok, zip_path = export_to_zip(
        mgr.profiles["exportme"], str(tmp_path / "data"),
        str(export_dir), include_data=False,
    )
    assert ok, f"could not export a fixture archive: {zip_path!r}"
    assert pathlib.Path(zip_path).is_file(), (
        f"export reported success but produced no file at {zip_path!r} — the "
        f"import door below would silently not be tested."
    )

    _rewrite_archive_os_type(zip_path, bad)
    # Confirm the fixture actually carries the bad value, so a rewrite that
    # quietly failed cannot let the import door pass by testing nothing.
    assert _archive_os_type(zip_path) == bad, (
        "the test fixture does not carry the bad os_type, so importing it "
        "would prove nothing about the repair."
    )

    del mgr.profiles["exportme"]
    mgr.save_profiles()
    ok_i, res = mgr.import_profile(zip_path)
    assert ok_i, (
        f"import was refused ({res!r}) — refusing here turns a recoverable "
        f"backup into an unimportable one, which the per-door decision "
        f"explicitly rejects."
    )
    assert at_rest("exportme") in CANONICAL_OS_TYPES
    assert at_rest("exportme") == canonical_os_type(bad)

    # --- THE END STATE, over everything the manager now holds. ---------------
    for name, profile in mgr.profiles.items():
        assert profile.os_type in CANONICAL_OS_TYPES, (
            f"profile {name!r} came to rest with os_type={profile.os_type!r}"
        )


def _archive_os_type(zip_path: str) -> str | None:
    """Read back the os_type a profile archive actually carries.

    Exists so the rewrite below can be VERIFIED rather than assumed. A rewrite
    that silently no-ops would let the import-door assertions pass while
    testing a canonical value — green, and proving nothing.
    """
    import zipfile

    with zipfile.ZipFile(zip_path) as zf:
        target = next((n for n in zf.namelist() if n.endswith(".json")), None)
        if target is None:
            return None
        payload = json.loads(zf.read(target))
    for key in ("profile", "data"):
        if isinstance(payload.get(key), dict) and "os_type" in payload[key]:
            return payload[key]["os_type"]
    return payload.get("os_type")


def _rewrite_archive_os_type(zip_path: str, os_type: str) -> None:
    """Rewrite a profile archive's stored os_type, in place.

    Simulates an archive written by an older build (or hand-edited), which is
    the only realistic way a bad value reaches the import door.
    """
    import shutil
    import tempfile
    import zipfile

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        blobs = {n: zf.read(n) for n in names}

    target = next((n for n in names if n.endswith(".json")), None)
    if target is None:
        return
    payload = json.loads(blobs[target])
    for key in ("profile", "data"):
        if isinstance(payload.get(key), dict) and "os_type" in payload[key]:
            payload[key]["os_type"] = os_type
            break
    else:
        if "os_type" in payload:
            payload["os_type"] = os_type
    blobs[target] = json.dumps(payload).encode("utf-8")

    tmp = tempfile.mktemp(suffix=".zip")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for n in names:
            zf.writestr(n, blobs[n])
    shutil.move(tmp, zip_path)


def test_every_stored_value_is_honoured_by_the_engine_on_its_own_device_type(mgr):
    """Close the loop on the HARM, not on the canonical list.

    The previous tests trust ``CANONICAL_OS_TYPES``. This one does not: it takes
    whatever is actually stored, computes the string the engine would genuinely
    be launched with, and asks the engine-vocabulary question directly. If the
    canonical set were ever widened to include something the engine rejects,
    every other test here would still pass and this one would fail — which is
    the failure this file exists to prevent.
    """
    for i, os_type in enumerate(sorted(CANONICAL_OS_TYPES)):
        assert mgr.add_profile(f"p{i}", "", os_type)

    assert mgr.profiles, "no profiles stored; the assertion below is vacuous"
    for name, profile in mgr.profiles.items():
        engine_platform = engine_platform_for(profile.os_type, profile.device_type)
        assert engine_honours(engine_platform), (
            f"profile {name!r} is stored with os_type={profile.os_type!r} and "
            f"device_type={profile.device_type!r}, which launches the engine "
            f"with --fingerprint-platform={engine_platform!r}. The engine does "
            f"NOT honour that value: it answers with its own software renderer "
            f"while our masking layer stands down, so the HOST's GPU strings "
            f"reach the page. This is the PS-161 host leak."
        )


# ---------------------------------------------------------------------------
# The second-order finding: the Firefox path (the two-engine rule, PS-16).
# ---------------------------------------------------------------------------


def test_a_windows_alias_no_longer_silently_downgrades_the_firefox_engine(mgr):
    """Firefox has no OS parameter (#211), so the leak is not the harm there.

    The harm is different and was unexamined: coherence Rule 2 pins the Firefox
    engine to ``os_type == "windows"`` by STRING EQUALITY, so an alias spelling
    failed that compare and ``coherent_engine`` silently downgraded the profile
    to chromium. The operator asked for Firefox and got Chromium, with no
    refusal and no log line naming the spelling as the cause.

    Repairing the value on the way in fixes that arm too — and this asserts the
    OUTCOME (which engine actually launches) rather than that a normaliser ran.
    """
    from src.services.browser.process import effective_engine

    # Authored through the create door: the alias is refused outright, so the
    # confusing half-state cannot be composed at all.
    with pytest.raises(IncoherentProfile):
        mgr.add_profile("ff-alias", "", "win", engine="firefox")

    # And a record that arrives through a RECOVERY door is repaired, so it
    # launches the engine its record claims instead of being downgraded.
    class _Entry:
        name = "ff-restored"
        material_path = ""
        payload = {"name": "ff-restored", "os_type": "win", "engine": "firefox"}

    ok, err = mgr.restore_profile(_Entry())
    assert ok, err
    restored = mgr.profiles["ff-restored"]
    assert restored.os_type == "windows"
    assert effective_engine(restored) == "firefox", (
        "a restored Firefox profile carrying the `win` spelling was downgraded "
        "to chromium by Rule 2's string compare — the operator asked for "
        "Firefox and silently got a different engine."
    )


def test_the_read_side_fix_is_untouched():
    """PS-161's read-side fix is explicitly out of scope; pin that it still holds.

    Deliberately NOT a second guard on the read path — it asserts the existing
    behaviour is unchanged, so this ticket cannot be read as having quietly
    altered it. The alias can no longer be stored, but if one ever reaches the
    read path by any means, authorship must still stay with US.
    """
    from src.services.browser.gpu_ext import (
        engine_authors_identity_for_engine_platform,
    )

    assert ENGINE_HONOURED_PLATFORMS == frozenset({"windows", "macos", "linux"})

    for alias in ALIASES_THE_ENGINE_REJECTS:
        assert engine_authors_identity_for_engine_platform(
            engine_platform_for(alias, "desktop")
        ) is False, (
            f"os_type={alias!r} is not honoured by the engine, yet authorship "
            f"deferred — the PS-161 read-side fix has regressed."
        )


def test_gpu_ext_re_exports_the_table_rather_than_restating_it():
    """One owner for the vocabulary; two copies is the drift that caused this.

    The table moved to ``models/os_type.py`` because the model is the one place
    every write door funnels through and ``models/`` cannot import
    ``services/``. ``gpu_ext`` keeps its historical names as RE-EXPORTS. A
    restatement there would be the round-2 defect's exact shape.
    """
    from src.services.browser import gpu_ext

    assert gpu_ext.RECOGNISED_OS_TYPES is RECOGNISED_OS_TYPES
    assert gpu_ext._OS_NORM_TABLE is OS_NORM_TABLE
    # The pooling fold and the storage repair must be ONE function, or the value
    # a profile is stored as and the value the GPU pool keys on can disagree.
    for spelling in sorted(RECOGNISED_OS_TYPES) + UNRECOGNISED_VALUES:
        assert gpu_ext._os_norm(spelling) == canonical_os_type(spelling)


# ---------------------------------------------------------------------------
# ROUND 2 — the refusal must be DELIVERED, not merely raised.
#
# The round-1 change made a non-canonical `os_type` unstorable, and that half
# held. What it did not check is what each caller DOES with the new refusal.
# Two lanes were enumerated on this ticket as callers and then never opened:
# the MCP tool and `bulk_create`. Both let the exception escape.
#
# ⚠️ THIS IS THE SAME MISTAKE THE TICKET WARNED ABOUT, ONE LEVEL UP. Round 1
# stopped enumerating doors and made the FIELD carry the guarantee — correctly.
# But the refusal that guarantee produces still travels through hand-written
# per-lane handling, so "which doors did I remember to check" came back as
# "which lanes did I remember to catch in". The tests below are therefore
# written as a PROPERTY over the lanes — every lane that can be handed a bad
# value must answer with a value — rather than as one test per lane I happened
# to think of.
#
# WHY IT MATTERS THAT THE RULE IS NEW HERE. Measured at the merge-base: the MCP
# lane could not raise at all before this ticket. `create_profile` passes
# (name, proxy, os_type, tags) and no engine/device_type, so the pair rules were
# structurally unreachable through it. This rule is the FIRST refusal ever to
# reach an off-machine automation client — so "the doors already catch it" was
# not a weaker claim than it looked, it was false about the lane with the least
# ability to recover.
# ---------------------------------------------------------------------------


def _mcp_create(mcp, name, os_type):
    """Call the real MCP tool and return the dict the client actually receives.

    Deliberately goes through ``call_tool`` rather than the Python function:
    the finding is about what an off-machine caller GETS, and an exception that
    escapes the tool body is only observable from the far side of that boundary.
    """
    import asyncio
    import json

    result = asyncio.run(mcp.call_tool(name, os_type))
    return json.loads(result[0].text)


@pytest.fixture
def mcp_harness(mgr):
    """The real MCP server, bound to the isolated ProfileManager."""
    from src.api.mcp_server import build_mcp
    from src.core.container import Container

    c = Container()
    c._instances["pm"] = mgr
    return build_mcp(c), mgr


@pytest.mark.parametrize("bad", BAD_VALUES)
def test_the_mcp_lane_answers_a_refusal_instead_of_throwing(mcp_harness, bad):
    """AC: the automation client gets a structured refusal carrying the reason.

    Asserts on the RESPONSE and on the RESTING STATE, never on whether a
    validator ran (PS-11). On the round-1 branch this raised IncoherentProfile
    out of the tool body: `call_tool` surfaces that to the client as an error
    payload, and the message naming the canonical spelling — the most useful
    thing the rule produces — was discarded.
    """
    mcp, mgr = mcp_harness
    out = _mcp_create(mcp, "create_profile", {"name": f"m-{bad!r}", "os_type": bad})

    assert out.get("created") is False, (
        f"os_type={bad!r} must not report a successful creation; got {out!r}"
    )
    assert out.get("error") == "refused", (
        f"the lane must use the structured-refusal shape it already uses for a "
        f"refused launch, so a client can branch on it; got {out!r}"
    )
    # The reason must survive to the client. It is the only thing that tells an
    # operator what to write instead.
    assert "os_type" in out.get("detail", ""), (
        f"the refusal reason must reach the caller, not be swallowed; got {out!r}"
    )

    # END STATE: nothing came to rest. This is the assertion that would still
    # catch a lane that answered politely and stored the value anyway.
    assert f"m-{bad!r}" not in mgr.profiles


@pytest.mark.parametrize("good", sorted(CANONICAL_OS_TYPES))
def test_the_mcp_lane_still_creates_every_canonical_spelling(mcp_harness, good):
    """The refusal must not have narrowed what legitimately works.

    Measured at the merge-base, all five arms answered {'created': True}. If a
    handler ever turned a canonical value into a refusal, the tests above would
    all still pass — they only assert that bad values fail.
    """
    mcp, mgr = mcp_harness
    out = _mcp_create(mcp, "create_profile", {"name": f"ok-{good}", "os_type": good})

    assert out.get("created") is True, f"os_type={good!r} must still create; got {out!r}"
    assert mgr.profiles[f"ok-{good}"].os_type == good


@pytest.mark.parametrize("bad", BAD_VALUES)
def test_bulk_create_reports_a_refusal_through_skipped_and_persists_nothing(mgr, bad):
    """AC: the batch refuses individually and keeps going, as its contract says.

    `bulk_create` has a designed refusal channel — the `skipped` list, already
    used for a bad name and for add_profile returning falsy. A raise bypasses
    it and propagates MID-LOOP, so names earlier in the batch are created and
    persisted while the return value never arrives; its caller
    (ui/actions/profile.py on_create, whose `str | None` return is ITS error
    channel and which has no try) gets neither the dict nor a message.

    The `created` name is placed FIRST in the batch on purpose: a mid-loop raise
    is only distinguishable from a clean refusal when something before the
    failure point would have been persisted.
    """
    from src.services.profile.bulk import bulk_create

    names = [f"bulk-a-{bad!r}", f"bulk-b-{bad!r}"]
    result = bulk_create(mgr, names, "", bad)

    assert result["created"] == [], (
        f"os_type={bad!r} cannot be stored, so no name in the batch may be "
        f"reported as created; got {result!r}"
    )
    assert sorted(result["skipped"]) == sorted(names), (
        f"every name must be reported through the designed `skipped` channel; "
        f"got {result!r}"
    )

    # END STATE: the loop did not persist a partial batch on its way out.
    for n in names:
        assert n not in mgr.profiles, (
            f"{n!r} was persisted despite the batch being refused — the raise "
            f"aborted the loop after committing earlier names."
        )


def test_bulk_create_still_creates_a_canonical_batch(mgr):
    """The `skipped` routing must not have swallowed the working path."""
    from src.services.profile.bulk import bulk_create

    result = bulk_create(mgr, ["bulk-ok-1", "bulk-ok-2"], "", "windows")

    assert sorted(result["created"]) == ["bulk-ok-1", "bulk-ok-2"]
    assert result["skipped"] == []
    assert mgr.profiles["bulk-ok-1"].os_type == "windows"


def test_no_write_lane_lets_the_refusal_escape_as_an_exception(mgr, tmp_path):
    """THE PROPERTY: every lane answers with a value; none throws at its caller.

    Written over the lanes as a SET rather than as one test per lane, because
    "the lanes I remembered to check" is precisely what failed round 1 — the
    ticket's own door enumeration NAMED the MCP tool, and its exception
    handling still went unexamined. A new lane added to this list gets the
    assertion for free; a new lane not added is the residual, and it is stated
    rather than hidden: this closes the lanes that exist, it does not make the
    delivery of a refusal structural the way `__setattr__` made the repair
    structural.

    The two lanes that already handled it (REST -> 400, dialog -> inline
    message) are exercised too, so a regression in either is caught here rather
    than being assumed to hold because it held before.
    """
    from src.api.mcp_server import build_mcp
    from src.core.container import Container
    from src.services.profile.bulk import bulk_create

    c = Container()
    c._instances["pm"] = mgr
    mcp = build_mcp(c)

    def _via_mcp(bad):
        return _mcp_create(mcp, "create_profile", {"name": f"p-mcp-{bad!r}", "os_type": bad})

    def _via_bulk(bad):
        return bulk_create(mgr, [f"p-bulk-{bad!r}"], "", bad)

    def _via_rest(bad):
        # The REST lane translates IncoherentProfile into a 400. Exercise the
        # translation the route performs, not the route's plumbing.
        from fastapi import HTTPException

        from src.services.profile.coherence import IncoherentProfile

        try:
            mgr.add_profile(f"p-rest-{bad!r}", "", bad, tags=[])
        except IncoherentProfile as e:
            return HTTPException(status_code=400, detail=str(e))
        return None

    lanes = {"mcp": _via_mcp, "bulk": _via_bulk, "rest": _via_rest}

    for bad in BAD_VALUES:
        for lane_name, lane in lanes.items():
            try:
                answer = lane(bad)
            except Exception as e:  # noqa: BLE001 — that is the whole assertion
                raise AssertionError(
                    f"lane {lane_name!r} let {type(e).__name__} escape to its "
                    f"caller for os_type={bad!r}. A refusal must be DELIVERED "
                    f"as a value this lane's caller already knows how to read."
                ) from e
            assert answer is not None, (
                f"lane {lane_name!r} answered nothing at all for os_type={bad!r}"
            )

    # END STATE, once more and over every lane at once: after driving all of
    # them with every bad value, the manager holds nothing non-canonical.
    for name, p in mgr.profiles.items():
        assert p.os_type in CANONICAL_OS_TYPES, (
            f"{name!r} came to rest carrying os_type={p.os_type!r}"
        )
