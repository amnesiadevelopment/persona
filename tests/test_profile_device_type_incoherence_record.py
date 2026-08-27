"""Rule 3 at the doors that do NOT refuse it: the pair is recorded, not silent.

PS-188. ``coherence.py`` Rule 3 (``device_type == "mobile"`` requires a mobile
``os_type``) is refused at the two AUTHORING doors — ``add_profile`` and
``update_profile`` — and evaluated at NONE of the three RECOVERY doors:
``import_profile`` normalises the pair only (Rule 3 has no engine remedy),
``restore_profile`` is intentionally exempt, and the legacy disk load predates
the rule. The module's own docstring said so in writing, which is what made this
a ticket rather than a discovery.

The decision recorded on PS-188 is ACCEPT AND RECORD at all three, and the
behaviour at those doors is deliberately UNCHANGED: refusing turns a recoverable
backup into an unimportable one, and Rule 3 has no honest normalisation (which of
``os_type`` and ``device_type`` is the lie is not knowable from the record). What
WAS wrong is that the pair was **silent** — a recovered ``windows`` + ``mobile``
record was indistinguishable from a coherent one on every surface.

WHAT THESE TESTS PIN, AND WHAT NO TEST HERE CAN PIN
---------------------------------------------------

Enumerating entry points is exactly what left this open (twice: PS-161's
follow-up list, then PS-187's). A test that walks the doors *this* worker
happened to find would fail the same way. So the load-bearing assertion here is
a property of the RECORD:

    for any profile that has come to rest in the store, by any route:
        the pair is coherent  OR  the record itself says it is not.

``test_no_stored_profile_is_silently_incoherent`` states that over the store
after every door has been driven, and its assertion names none of them.

⚠️ BE PRECISE ABOUT WHAT THAT DOES AND DOES NOT BUY, because the first version of
this file overclaimed it in writing and a reviewer had to disprove it by
mutation. The verdict is DERIVED ON READ (``Profile.device_type_incoherence`` is
a function of the two fields), so "the record says so" is true BY CONSTRUCTION
for every record that can ever exist. No assertion over stored records can
therefore distinguish a door that records from a door that stays silent — a
sweep that asks the record to confirm its own derivation is ``f(x) == f(x)`` and
cannot fail. **So this file does NOT claim that a door added next year fails a
test here.** The record half is guaranteed by DERIVATION, not by a test, and
that is a stronger guarantee than a door list — but it is a design property, and
saying a test enforces it would be false.

What the sweep below does buy, and why it is still worth running: it checks the
derived verdict against an oracle written INDEPENDENTLY of the implementation
(``_independently_incoherent``, Rule 3 restated from its prose), so a
``device_type_error`` that stopped flagging the pair — or started flagging the
coherent ``android``+``desktop`` default — is caught over whatever the doors
actually landed. That is ``f(x) == g(x)`` with two authors, not one.

⚠️ THE SECOND HALF OF THAT SENTENCE HAD TO BE EARNED, AND THE WAY IT WAS EARNED
IS THE INTERESTING PART. It was first written when every record the sweep landed
carried ``device_type="mobile"`` — so the store held ZERO instances of the
coherent default, and a ``device_type_error`` that started flagging
``android``+``desktop`` sailed through the sweep untouched (a reviewer proved it
by mutation; it was caught only by ``test_a_coherent_record_reports_nothing``).
The store now contains that shape, landed through a door like everything else.

Which door is NOT arbitrary, and getting it wrong re-breaks the claim silently:
it is landed at the LEGACY DISK LOAD, not at ``add_profile``. The authoring door
REFUSES, so under that mutation the sweep dies on the door's own ``assert`` and
never reaches the oracle at all — a door assertion dressed up as a property. The
legacy load has no guard, so the record actually COMES TO REST in the store and
the independent oracle is the thing that judges it. Both directions have been
mutation-verified to fail AT THE ORACLE rather than at a door.

What a future silent door WOULD break is the DISCLOSURE half — the warning log
and the REST field — and that half is inherently per-door, so it is asserted
per-door (``test_the_recovery_doors_say_so_in_the_log``) and by the
door-agnostic ``flagged == {"imported", "trashed"}`` census, which pins that
import and restore land the pair verbatim AND flagged.

And per PS-11: no test here asserts that a validator was CALLED. They assert what
an operator or a downstream reader can actually observe — the stored record's own
verdict, and the REST row that carries it.
"""
import logging

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.mcp_token import get_or_create_token
from src.models.profile import Profile
from src.services.profile.coherence import device_type_error
from src.services.profile.manager import ProfileManager

# The pair Rule 3 refuses: device_type says phone, os_type says desktop.
INCOHERENT_PAIRS = [
    ("windows", "mobile"),
    ("macos", "mobile"),
    ("linux", "mobile"),
]

# Pairs that must keep reading as coherent. "desktop" beside a mobile os_type is
# the model's DEFAULT and the only thing the dialog can produce, so every android
# profile the UI has ever created carries it — a property that flagged it would
# flag the normal case.
COHERENT_PAIRS = [
    ("windows", "desktop"),
    ("macos", "desktop"),
    ("linux", "desktop"),
    ("android", "mobile"),
    ("ios", "mobile"),
    ("android", "desktop"),
    ("ios", "desktop"),
]


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    import src.core.config as cfg
    import src.services.profile.manager as mod

    pf, dd = tmp_path / "profiles.json", tmp_path / "data"
    for m in (cfg, mod):
        monkeypatch.setattr(m, "DATA_DIR", str(dd), raising=False)
        monkeypatch.setattr(m, "PROFILES_FILE", str(pf), raising=False)
    return ProfileManager()


@pytest.fixture
def client(tmp_path, monkeypatch):
    import src.api.mcp_token as tok
    import src.core.config as cfg
    import src.services.profile.manager as pm

    data_dir = str(tmp_path / "data")
    profiles_file = str(tmp_path / "profiles.json")
    monkeypatch.setattr(tok, "_path", lambda: str(tmp_path / "mcp_token"))
    for m in (cfg, pm):
        monkeypatch.setattr(m, "DATA_DIR", data_dir, raising=False)
        monkeypatch.setattr(m, "PROFILES_FILE", profiles_file, raising=False)

    from src.core.container import Container

    app = create_app(Container())
    c = TestClient(app, base_url="http://127.0.0.1")
    return c, {"authorization": f"Bearer {get_or_create_token()}"}


# ---------------------------------------------------------------------------
# The record half: a stored profile can be ASKED, and it answers about itself.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("os_type,device_type", INCOHERENT_PAIRS)
def test_an_incoherent_record_reports_its_own_incoherence(os_type, device_type):
    """The verdict is a property of the FIELDS, so it holds however the record
    was built — including by a door nobody has written yet."""
    p = Profile(name="p", os_type=os_type, device_type=device_type)

    reason = p.device_type_incoherence
    assert reason is not None
    # It names both fields and the offending value, because the point is that a
    # reader can ACT on it, not merely detect a boolean.
    assert device_type in reason
    assert os_type in reason


@pytest.mark.parametrize("os_type,device_type", COHERENT_PAIRS)
def test_a_coherent_record_reports_nothing(os_type, device_type):
    """Including a mobile os_type beside the "desktop" DEFAULT, which is what
    every android profile the UI has ever created carries. A property that
    flagged it would flag the normal case."""
    p = Profile(name="p", os_type=os_type, device_type=device_type)
    assert p.device_type_incoherence is None


def test_the_verdict_follows_the_fields_rather_than_being_stamped_once():
    """A stored flag would go stale the moment either field moved; a derived one
    cannot. Pinned because "just add a column" is the obvious alternative."""
    p = Profile(name="p", os_type="windows", device_type="mobile")
    assert p.device_type_incoherence is not None

    # repair by moving os_type -> the verdict clears, with nothing to update
    p.os_type = "android"
    assert p.device_type_incoherence is None

    # repair by moving device_type instead -> same
    p.os_type = "windows"
    assert p.device_type_incoherence is not None
    p.device_type = "desktop"
    assert p.device_type_incoherence is None


def test_the_verdict_is_not_persisted():
    """It is a property, not a field: `asdict` skips it. So this adds nothing to
    profiles.json, needs no migration for records already on disk, and cannot be
    written to disk in a state that disagrees with the two fields beside it."""
    p = Profile(name="p", os_type="windows", device_type="mobile")
    assert "device_type_incoherence" not in p.to_dict()
    # and the round trip through the store's own serialisation still reports it
    assert Profile(**p.to_dict()).device_type_incoherence is not None


def test_the_verdict_is_rule_3s_own_message_not_a_second_wording():
    """`coherence.device_type_error` stays the single owner, so the property and
    the REFUSING doors cannot come to disagree about what Rule 3 says."""
    p = Profile(name="p", os_type="windows", device_type="mobile")
    assert p.device_type_incoherence == device_type_error("windows", "mobile")


# ---------------------------------------------------------------------------
# THE PROPERTY. Stated over the store, not over a list of doors.
# ---------------------------------------------------------------------------


def _independently_incoherent(os_type, device_type) -> bool:
    """Rule 3, restated from its PROSE rather than called.

    ⚠️ THE POINT OF THIS FUNCTION IS THAT IT DOES NOT IMPORT THE IMPLEMENTATION.
    Comparing ``profile.device_type_incoherence`` against
    ``device_type_error(...)`` is ``f(x) == f(x)`` — the property is derived on
    read from exactly those two fields, so that assertion cannot fail for any
    record and passes even on a record landed with no guard at all. A reviewer
    proved that by mutation, and it is the PS-11 shape this ticket's own method
    constraints cite.

    Rule 3 in words, from ``coherence.py``: *a ``device_type`` of "mobile"
    requires a mobile ``os_type``*; the rule is one-directional (a mobile
    ``os_type`` beside the "desktop" DEFAULT is fine, and is what every android
    profile the UI ever made carries); and a ``device_type`` of ``None`` means
    the caller had nothing to say, which earns no verdict.

    Written out by hand so that a ``device_type_error`` which stopped flagging
    the pair, or started flagging the coherent default, is CAUGHT rather than
    agreed with.

    ⚠️ THAT SECOND DIRECTION IS ONLY REAL IF THE STORE CONTAINS THE COHERENT
    DEFAULT, and for one round it did not: every record the sweep landed carried
    ``device_type="mobile"``, so an implementation that started flagging
    ``android``+``desktop`` was agreed with by omission. The sweep now lands that
    shape at the LEGACY DISK LOAD — deliberately not at ``add_profile``, which
    refuses and would make the sweep die on the door's own ``assert`` before this
    oracle ever ran. Both directions are mutation-verified to fail HERE.
    """
    if device_type is None:
        return False
    return device_type == "mobile" and os_type not in ("android", "ios")


def _every_stored_profile_is_coherent_or_says_otherwise(manager):
    """The invariant, as a predicate over whatever is in the store.

    Deliberately does NOT know which doors exist — it takes the store as it
    finds it. See this module's docstring for the honest bound on what that
    buys: the record half is guaranteed by DERIVATION, so this cannot catch a
    future door that stays silent, and it does not claim to. What it does catch
    is Rule 3's verdict drifting away from Rule 3 as written, over whatever the
    doors actually landed — which is why the oracle above is independent.
    """
    for profile in manager.profiles.values():
        recorded = profile.device_type_incoherence
        expected = _independently_incoherent(profile.os_type, profile.device_type)
        assert (recorded is not None) == expected, (
            f"profile {profile.name!r} ({profile.os_type}/"
            f"{profile.device_type}) reports {recorded!r}, but Rule 3 as "
            f"written says incoherent={expected}"
        )
        # and when it does report, it reports something an operator can act on
        if recorded is not None:
            assert profile.device_type in recorded
            assert profile.os_type in recorded


def test_no_stored_profile_is_silently_incoherent(mgr, tmp_path):
    """Drive EVERY door, then state the property over the resulting store.

    The doors are driven to populate the store, not to be individually asserted
    on: the assertion at the end names none of them. This is the difference
    between pinning the property and pinning the enumeration.
    """
    from src.services.profile.transfer import export_to_zip

    # --- door 1: add_profile (authoring) — REFUSES, so it contributes only a
    # coherent record. That it refuses is pinned elsewhere; here it is a door
    # that must not leave a silent record behind.
    assert mgr.add_profile("authored", None, "android", device_type="mobile")

    # --- door 2: the legacy disk load. A record already on disk from an older
    # build, loaded by a fresh manager.
    import json

    import src.services.profile.manager as mod

    with open(mod.PROFILES_FILE, encoding="utf-8") as f:
        on_disk = json.load(f)
    on_disk["legacyphone"] = {
        "name": "legacyphone",
        "os_type": "windows",
        "device_type": "mobile",
        "engine": "chromium",
    }
    # ...and beside it the COHERENT DEFAULT, which is the shape MOST records
    # actually have: "desktop" beside a mobile os_type is what every android
    # profile the dialog has ever made carries.
    #
    # ⚠️ IT IS LANDED HERE, AT A RECOVERY DOOR, AND THAT IS NOT ARBITRARY. The
    # authoring door REFUSES, so a `device_type_error` that started flagging
    # this shape would make `add_profile` raise and the sweep would die at the
    # door — a door assertion, not the property. The legacy load has no guard at
    # all, so the record REACHES THE STORE and is judged by the independent
    # oracle below. That is the direction the docstring claims, so that is the
    # direction it has to be able to fail in.
    on_disk["legacydefault"] = {
        "name": "legacydefault",
        "os_type": "android",
        "device_type": "desktop",
        "engine": "chromium",
    }
    with open(mod.PROFILES_FILE, "w", encoding="utf-8") as f:
        json.dump(on_disk, f)
    reloaded = ProfileManager()
    assert "legacyphone" in reloaded.profiles
    # loaded, not dropped — a recovery door must not lose a profile the
    # operator already owns...
    assert reloaded.profiles["legacyphone"].device_type == "mobile"
    # ...and not silently, either.
    assert reloaded.profiles["legacyphone"].device_type_incoherence is not None
    _every_stored_profile_is_coherent_or_says_otherwise(reloaded)

    # --- door 3: import_profile
    ok, zip_path = export_to_zip(
        Profile(
            name="imported", os_type="windows", engine="chromium",
            device_type="mobile",
        ),
        str(tmp_path / "imported-nodata"),
        str(tmp_path),
        include_data=False,
    )
    assert ok, zip_path
    ok, result = mgr.import_profile(zip_path)
    assert ok, result

    # --- door 4: restore_profile
    mgr.profiles["trashed"] = Profile(
        name="trashed", os_type="macos", engine="chromium", device_type="mobile"
    )
    mgr.save_profiles()
    assert mgr.delete_profile("trashed")
    entry = [e for e in mgr._trash().list() if e.name == "trashed"][0]
    ok, err = mgr.restore_profile(entry)
    assert ok, err

    # Every door has now run. The property is stated over the store as a whole.
    assert {"authored", "imported", "trashed"} <= set(mgr.profiles)
    _every_stored_profile_is_coherent_or_says_otherwise(mgr)

    # And the incoherent ones really are present and really are flagged — so the
    # property above is not passing vacuously on an empty or coherent store.
    flagged = {
        n for n, p in mgr.profiles.items()
        if p.device_type_incoherence is not None
    }
    assert flagged == {"imported", "trashed"}


def test_the_recovery_doors_still_recover_rather_than_refusing(mgr, tmp_path):
    """The decision was ACCEPT and record, not refuse. Pinned so a later reader
    does not "fix" the recording into a guard — which would turn a recoverable
    backup into an unimportable one, the exact cost the exemption exists to
    avoid."""
    from src.services.profile.transfer import export_to_zip

    ok, zip_path = export_to_zip(
        Profile(name="arch", os_type="windows", engine="chromium",
                device_type="mobile"),
        str(tmp_path / "arch-nodata"),
        str(tmp_path),
        include_data=False,
    )
    assert ok, zip_path

    ok, result = mgr.import_profile(zip_path)
    assert ok, result
    # landed VERBATIM: recorded, never rewritten. Which of the two fields is the
    # lie is not knowable from the record, so a repair would be a guess.
    assert (mgr.profiles["arch"].os_type, mgr.profiles["arch"].device_type) == (
        "windows", "mobile",
    )
    # and it is still editable, i.e. not stranded
    assert mgr.update_profile("arch", "arch", "", None, new_notes="editable")
    assert mgr.profiles["arch"].notes == "editable"


def test_restore_replays_the_record_exactly_and_still_records_the_pair(mgr):
    """Restore's contract is "exactly as it was" — to the point of refusing a
    rename. So it may not repair. The exemption is from the GUARD, not from the
    RECORD."""
    mgr.profiles["oldphone"] = Profile(
        name="oldphone", os_type="windows", engine="chromium",
        device_type="mobile",
    )
    mgr.save_profiles()
    assert mgr.delete_profile("oldphone")
    entry = [e for e in mgr._trash().list() if e.name == "oldphone"][0]

    ok, err = mgr.restore_profile(entry)
    assert ok, err

    restored = mgr.profiles["oldphone"]
    assert (restored.os_type, restored.device_type) == ("windows", "mobile")
    assert restored.device_type_incoherence is not None


@pytest.mark.parametrize("door", ["import", "restore"])
def test_the_recovery_doors_say_so_in_the_log(mgr, tmp_path, caplog, door):
    """The operator-visible half at the moment the record lands. Asserted on the
    LOG the door emitted, which is a thing that happens, rather than on a
    validator having been invoked."""
    from src.services.profile.transfer import export_to_zip

    with caplog.at_level(logging.WARNING):
        if door == "import":
            ok, zip_path = export_to_zip(
                Profile(name="noisy", os_type="windows", engine="chromium",
                        device_type="mobile"),
                str(tmp_path / "noisy-nodata"),
                str(tmp_path),
                include_data=False,
            )
            assert ok, zip_path
            ok, result = mgr.import_profile(zip_path)
            assert ok, result
        else:
            mgr.profiles["noisy"] = Profile(
                name="noisy", os_type="windows", engine="chromium",
                device_type="mobile",
            )
            mgr.save_profiles()
            assert mgr.delete_profile("noisy")
            entry = [e for e in mgr._trash().list() if e.name == "noisy"][0]
            ok, err = mgr.restore_profile(entry)
            assert ok, err

    incoherence_warnings = [
        r.getMessage() for r in caplog.records
        if r.levelno >= logging.WARNING
        and "os_type/device_type" in r.getMessage()
    ]
    assert incoherence_warnings, (
        f"the {door} door let an incoherent pair through silently"
    )
    assert "noisy" in incoherence_warnings[0]


def test_a_coherent_recovery_says_nothing(mgr, tmp_path, caplog):
    """The counterpart, so the warning above means something: a coherent record
    recovers quietly. A log line on every import would be noise, and noise is
    read as normal."""
    from src.services.profile.transfer import export_to_zip

    ok, zip_path = export_to_zip(
        Profile(name="fine", os_type="android", engine="chromium",
                device_type="mobile"),
        str(tmp_path / "fine-nodata"),
        str(tmp_path),
        include_data=False,
    )
    assert ok, zip_path

    with caplog.at_level(logging.WARNING):
        ok, result = mgr.import_profile(zip_path)
        assert ok, result

    assert not [
        r for r in caplog.records if "os_type/device_type" in r.getMessage()
    ]
    assert mgr.profiles["fine"].device_type_incoherence is None


# ---------------------------------------------------------------------------
# The read surface: an operator can FIND the incoherent profiles they hold.
# ---------------------------------------------------------------------------


def test_the_rest_row_carries_the_verdict(client):
    """Previously impossible from ANY surface: the row was identical to a
    coherent one, so an operator could not locate the profiles this affects."""
    c, h = client
    import src.services.profile.manager as pm

    # compose the pair the only way still possible — as a stored record, which
    # is exactly how it reaches an operator (import / restore / legacy). The
    # app's own manager is built lazily on first request, so it loads this.
    mgr = pm.ProfileManager()
    mgr.profiles["phoney"] = Profile(
        name="phoney", os_type="windows", engine="chromium",
        device_type="mobile",
    )
    mgr.profiles["fine"] = Profile(
        name="fine", os_type="android", engine="chromium", device_type="mobile"
    )
    mgr.save_profiles()

    r = c.get("/api/v1/profiles/phoney", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["os_type"] == "windows"
    assert body["device_type"] == "mobile"
    assert body["device_type_incoherence"] is not None
    assert "mobile" in body["device_type_incoherence"]

    r = c.get("/api/v1/profiles/fine", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["device_type_incoherence"] is None


# ---------------------------------------------------------------------------
# THE BLAST RADIUS, PINNED. These are the measurements that make "accept and
# record" a defensible decision rather than a shrug — if any of them changes,
# the decision recorded at the doors needs re-taking, and this is what says so.
#
# Pinned on BOTH engines deliberately (PS-16's two-engine rule): `device_type`
# reaches both launch paths, and examining only the engine where the original
# leak was measured is how PS-97's Firefox half went undelivered.
# ---------------------------------------------------------------------------


def test_the_gpu_authorship_leak_really_is_closed_on_this_pair():
    """PS-161 round 4, restated as the premise this ticket builds on rather than
    as a claim inherited from its description.

    `engine_platform` is ONE computation over both fields, so on windows+mobile
    the engine is told `linux` and our layer does NOT stand down expecting a
    `windows` identity nobody wrote. That is why this ticket adds no guard on
    the GPU vector.
    """
    from src.services.browser.engine_platform import engine_platform_for
    from src.services.browser.gpu_ext import (
        engine_authors_identity_for_engine_platform,
    )

    engine_platform = engine_platform_for("windows", "mobile")
    assert engine_platform == "linux"
    # our layer keeps authorship -> the host's rasteriser cannot reach the page
    assert engine_authors_identity_for_engine_platform(engine_platform) is False

    # the desktop counterpart, so the above is a real distinction
    assert engine_platform_for("windows", "desktop") == "windows"
    assert engine_authors_identity_for_engine_platform("windows") is True


def test_the_pair_still_contradicts_on_vectors_that_read_os_type_alone():
    """"None, because the GPU one is fixed" is the reasoning that left this
    open, so it is pinned as FALSE.

    `engine_platform` reads both fields; the GPU POOL ARM and the voice roster
    still read `os_type` alone. So a windows+mobile record launches an Android
    device preset while its GPU pool and voices are Windows ones.
    """
    import inspect

    from src.services.browser import process
    from src.services.browser.device_presets import is_mobile_profile
    from src.services.browser.gpu_ext import _os_norm

    # The launch path treats it as a phone. Asserted TWICE, on purpose: once
    # behaviourally against the shared predicate, and once against the launch
    # site's real source.
    #
    # ⚠️ THE FIRST ASSERTION ALONE IS NOT ENOUGH, and the previous version of
    # this test proved it the hard way — it read
    # `is_mobile_os("windows") or "mobile" == "mobile"`, a comparison between
    # two string literals, so it was `True` unconditionally and survived
    # DELETING `device_type` from the launch computation. The predicate being
    # right does not establish that the launch path CALLS it with both fields.
    assert is_mobile_profile("windows", "mobile") is True
    # ...and the desktop counterpart, so the above is a real distinction and
    # not a function that returns True for everything.
    assert is_mobile_profile("windows", "desktop") is False

    # The launch site itself, read as source (the technique the Firefox test
    # below uses correctly). This is what fails if someone drops `device_type`
    # from the gate and re-strands the pair on the vector this ticket measured.
    launch_src = inspect.getsource(process.spawn_browser)
    assert "is_mobile_profile(profile.os_type, profile.device_type)" in launch_src, (
        "the launch gate no longer computes is_mobile from BOTH fields -- a "
        "windows+mobile record would launch as a Windows desktop while its "
        "record claims a phone"
    )

    # ...while the GPU pool arm is still chosen from os_type alone
    assert _os_norm("windows") == "windows"
    # ...as is the voice roster's arm, built from os_type with no device_type
    # parameter to consult at all.
    import inspect

    from src.services.browser.voice_ext import build_voice_extension

    voice_params = inspect.signature(build_voice_extension).parameters
    assert "device_type" not in voice_params
    assert "os_type" in voice_params


def test_the_firefox_path_reads_neither_field(tmp_path):
    """The two-engine half (PS-16), stated rather than assumed.

    Firefox has no OS parameter at all (#211) and presents Windows regardless,
    so the pair rules pin a Firefox profile to os_type "windows" — which makes
    windows + mobile + firefox PAIR-COHERENT. It therefore launches Firefox,
    and the launch path never reads `device_type`: the record claims a phone
    and the browser presents a desktop Windows machine.
    """
    import inspect

    from src.services.profile.coherence import coherent_engine, is_coherent

    # the pair rules do not stop it, so this record really does reach Firefox
    assert is_coherent("windows", "firefox") is True
    assert coherent_engine("windows", "firefox") == "firefox"

    # and the Firefox launch path has no notion of the field at all, so there
    # is nothing there to honour it
    from src.services.browser import invisible_launch

    source = inspect.getsource(invisible_launch)
    assert "device_type" not in source
    assert "is_mobile" not in source

    # which is exactly why the record must SAY so — it is the only place the
    # contradiction survives being launched.
    assert (
        Profile(name="ff", os_type="windows", engine="firefox",
                device_type="mobile").device_type_incoherence
        is not None
    )
