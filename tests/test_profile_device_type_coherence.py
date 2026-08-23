"""``device_type`` coherence: a profile that says "phone" must have a phone's OS.

The launch path derives "is this a phone?" from BOTH fields
(``services.browser.process``: ``is_mobile_os(os_type) or device_type ==
"mobile"``) while every other half of the same launch reads ``os_type`` alone.
``coherence.py`` — the module that exists to refuse machines that cannot exist —
had never heard of ``device_type``, so a stored ``windows`` + ``mobile`` profile
was accepted by the model and launched one machine answering "what OS am I?"
four different ways: an Android device preset drove the UA and screen, while the
GPU renderer (Direct3D11), the voice roster (Microsoft desktop voices) and
``--fingerprint-platform`` were each built from a different value.

The field is reachable only through doors that inherit none of the dialog's
narrowing: ``ui/dialogs/profile.py`` has no ``device_type`` control at all, and
``api/schemas/profiles.py`` declares it as a bare ``str`` with no ``Literal`` and
no validator. So the load-bearing tests here are the MANAGER ones — a route-level
guard would pass the HTTP tests on this page while leaving the automation lane,
an importer and any door added next year to re-answer the question from scratch.

Which way it reconciles: ``os_type`` WINS. That is the principle
``coherent_engine`` already applies for the pair ("in favour of the record rather
than in favour of the engine"), and the one ``process.py`` itself already claims
two lines above the code that breaks it.
"""
import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.mcp_token import get_or_create_token
from src.models.profile import Profile
from src.services.profile.coherence import (
    IncoherentProfile,
    coherence_error,
    is_coherent,
    is_device_type_coherent,
)
from src.services.profile.manager import ProfileManager

# The triples Rule 3 forbids: device_type says phone, os_type says desktop.
INCOHERENT_TRIPLES = [
    ("windows", "chromium", "mobile"),
    ("macos", "chromium", "mobile"),
    ("linux", "chromium", "mobile"),
]

# Coherent triples, including the two that MUST stay legal:
#  * a mobile os_type with the "desktop" default — the model's default value and
#    the only thing the dialog can produce, so every android profile the UI has
#    ever created carries it. Refusing it would refuse the normal case.
#  * a mobile os_type with an explicit "mobile" — the two fields agreeing.
COHERENT_TRIPLES = [
    ("android", "chromium", "mobile"),
    ("ios", "chromium", "mobile"),
    ("android", "chromium", "desktop"),
    ("ios", "chromium", "desktop"),
    ("windows", "chromium", "desktop"),
    ("macos", "chromium", "desktop"),
    ("linux", "chromium", "desktop"),
    ("windows", "firefox", "desktop"),
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
# AC2 — the premise, stated as a test rather than as a claim in the PR.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("os_type,engine,device_type", INCOHERENT_TRIPLES)
def test_the_pair_rules_alone_accept_the_triple_this_ticket_refuses(
    os_type, engine, device_type
):
    """The premise inversion: this is what the module said BEFORE this change.

    Called with the two-argument shape every pre-existing caller uses, the bad
    triple is still judged coherent — because on the os_type/engine pair alone it
    genuinely is. That is not a bug being preserved; it is the measurement that
    proves the new refusal comes from the NEW input and not from a rule that was
    already there. Without this, a green Rule 3 test could be passing for the
    wrong reason.
    """
    assert coherence_error(os_type, engine) is None
    assert is_coherent(os_type, engine)


def test_an_omitted_device_type_is_not_judged():
    """None means "not supplied", which is not the same as "desktop".

    This is what keeps the change additive across a signature shared by
    ``is_coherent``, ``assert_coherent`` and ``coherent_engine``: a caller that
    does not have the field in hand gets exactly its old verdict.
    """
    assert coherence_error("windows", "chromium", None) is None
    assert coherence_error("windows", "chromium") is None


# ---------------------------------------------------------------------------
# AC1 — the load-bearing test: the rule fires from the MODEL, not from a route.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("os_type,engine,device_type", INCOHERENT_TRIPLES)
def test_the_manager_itself_refuses_a_mobile_device_type_on_a_desktop_os(
    mgr, os_type, engine, device_type
):
    """Calls the manager directly, bypassing both the dialog and REST.

    Asserts on the refusal ACTUALLY RAISED and on the store being untouched —
    not that a rule, a branch or a constant exists (PS-11: an assertion that a
    mechanism exists passes against an implementation that does not work).
    """
    with pytest.raises(IncoherentProfile) as excinfo:
        mgr.add_profile(
            "phoney", "", os_type, engine=engine, device_type=device_type
        )

    # the reason names BOTH fields and how to resolve it, per the module's
    # wording convention (see the two shipped messages).
    msg = str(excinfo.value)
    assert "device_type" in msg and "os_type" in msg
    assert repr(os_type) in msg
    assert "android" in msg and "ios" in msg, "must say which way to resolve it"

    # and nothing was stored — the refusal is the point, not the message
    assert "phoney" not in mgr.profiles


@pytest.mark.parametrize("os_type,engine,device_type", COHERENT_TRIPLES)
def test_the_manager_stores_every_coherent_triple(mgr, os_type, engine, device_type):
    assert mgr.add_profile(
        "ok", "", os_type, engine=engine, device_type=device_type
    )
    stored = mgr.profiles["ok"]
    assert (stored.os_type, stored.device_type) == (os_type, device_type)


def test_the_default_device_type_on_a_mobile_os_is_not_refused(mgr):
    """The asymmetry, pinned so it is not "fixed" for symmetry later.

    ``desktop`` is the model's default and the profile dialog has no device_type
    control, so EVERY android profile the UI has ever created is stored
    android + desktop. It is also not a lie: os_type already flips is_mobile on
    its own, so the record launches as the phone its os_type claims and the
    defaulted field makes no competing claim. Only an explicit "mobile" does.
    """
    assert mgr.add_profile("droid", "", "android", engine="chromium")
    assert mgr.profiles["droid"].device_type == "desktop"
    assert is_coherent("android", "chromium", "desktop")


# ---------------------------------------------------------------------------
# AC3 — the REST lane, through the `except IncoherentProfile` already in place
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("os_type,engine,device_type", INCOHERENT_TRIPLES)
def test_rest_create_refuses_with_a_reason_and_stores_nothing(
    client, os_type, engine, device_type
):
    c, h = client
    r = c.post(
        "/api/v1/profiles",
        json={
            "name": "restphone",
            "os_type": os_type,
            "engine": engine,
            "device_type": device_type,
        },
        headers=h,
    )
    assert r.status_code == 400, r.text
    assert "device_type" in r.json()["detail"]

    # AC3 asserts on the STORE, not merely on the status code: a 400 that still
    # wrote the record would satisfy the status assertion and defeat the ticket.
    listing = c.get("/api/v1/profiles", headers=h)
    assert listing.status_code == 200, listing.text
    assert not [
        p for p in listing.json()["profiles"] if p["name"] == "restphone"
    ]
    assert c.get("/api/v1/profiles/restphone", headers=h).status_code == 404


def test_rest_create_still_accepts_a_coherent_mobile_profile(client):
    c, h = client
    r = c.post(
        "/api/v1/profiles",
        json={
            "name": "realphone",
            "os_type": "android",
            "engine": "chromium",
            "device_type": "mobile",
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    assert r.json()["device_type"] == "mobile"


# ---------------------------------------------------------------------------
# AC4 — update refuses only when the edit INTRODUCES it, judged on the
#       RESULTING triple (an omitted field read from the stored record)
# ---------------------------------------------------------------------------


def test_update_judges_a_one_field_patch_against_the_stored_os(mgr):
    """The door this rule was added to close, in its PATCH form.

    A patch carrying only device_type must be judged against the os_type already
    stored, or `PATCH {"device_type": "mobile"}` sails through on a windows
    profile — the same defect, reached one edit later.
    """
    assert mgr.add_profile("desk", "", "windows", engine="chromium")

    with pytest.raises(IncoherentProfile):
        mgr.update_profile("desk", "desk", "", None, new_device_type="mobile")

    # the store is unchanged — the refusal happens before any field is applied
    assert mgr.profiles["desk"].device_type == "desktop"
    assert mgr.profiles["desk"].os_type == "windows"


def test_update_judges_a_one_field_os_patch_against_the_stored_device_type(mgr):
    """The mirror case: moving a coherent android+mobile profile onto windows.

    The supplied field is os_type and the conflicting one is stored, so a check
    that only looked at what the caller SENT would miss it.
    """
    assert mgr.add_profile("droid", "", "android", engine="chromium",
                           device_type="mobile")

    with pytest.raises(IncoherentProfile):
        mgr.update_profile("droid", "droid", "", "windows")

    assert mgr.profiles["droid"].os_type == "android"


def test_rest_update_cannot_edit_a_coherent_profile_into_an_incoherent_one(client):
    c, h = client
    assert c.post(
        "/api/v1/profiles",
        json={"name": "u", "os_type": "windows", "engine": "chromium"},
        headers=h,
    ).status_code == 201

    r = c.patch("/api/v1/profiles/u", json={"device_type": "mobile"}, headers=h)
    assert r.status_code == 400, r.text
    assert "device_type" in r.json()["detail"]

    # and the stored record did not take the edit
    assert c.get("/api/v1/profiles/u", headers=h).json()["device_type"] == "desktop"


def test_a_repairing_edit_is_allowed_through(mgr):
    """Moving the os_type to a mobile family fixes the triple, so it must pass."""
    mgr.profiles["legacy"] = Profile(
        name="legacy", os_type="windows", engine="chromium", device_type="mobile"
    )
    assert mgr.update_profile("legacy", "legacy", "", "android")
    stored = mgr.profiles["legacy"]
    assert (stored.os_type, stored.device_type) == ("android", "mobile")
    assert is_coherent(stored.os_type, stored.engine, stored.device_type)


def test_repairing_by_moving_the_device_type_back_to_desktop_is_allowed(mgr):
    """The other repair direction — the edit that resolves it the os_type's way."""
    mgr.profiles["legacy2"] = Profile(
        name="legacy2", os_type="windows", engine="chromium", device_type="mobile"
    )
    assert mgr.update_profile("legacy2", "legacy2", "", None,
                              new_device_type="desktop")
    stored = mgr.profiles["legacy2"]
    assert (stored.os_type, stored.device_type) == ("windows", "desktop")
    assert is_coherent(stored.os_type, stored.engine, stored.device_type)


# ---------------------------------------------------------------------------
# AC5 — THE TRAP: an already-stored incoherent record stays editable
# ---------------------------------------------------------------------------


def test_an_already_incoherent_record_stays_editable(mgr):
    """An edit may not INTRODUCE incoherence, but must never be blocked by
    incoherence it did not introduce.

    Otherwise a record written before this rule (or through the unguarded REST
    lane) becomes permanently uneditable — including by the edit that would FIX
    it. The module solved this once for the pair; Rule 3 must not be stricter
    than the two rules it sits beside.
    """
    # bypass the guard the way an old profiles.json would: construct directly
    mgr.profiles["legacy"] = Profile(
        name="legacy", os_type="windows", engine="chromium", device_type="mobile"
    )

    # an unrelated edit — a note — still succeeds
    assert mgr.update_profile("legacy", "legacy", "", None, new_notes="ok")
    assert mgr.profiles["legacy"].notes == "ok"

    # and so does an unrelated edit to a tag
    assert mgr.update_profile("legacy", "legacy", "", None, new_tags=["keep"])
    assert mgr.profiles["legacy"].tags == ["keep"]

    # the bad triple is still there — it was tolerated, not silently rewritten
    assert mgr.profiles["legacy"].device_type == "mobile"
    assert mgr.profiles["legacy"].os_type == "windows"


def test_rest_update_of_an_unrelated_field_on_a_stored_bad_triple_still_works(client):
    """The same trap through the REST door, which is how it would be hit."""
    c, h = client
    # compose the bad triple the only way that is still possible: directly in
    # the store, exactly as a record predating this rule would sit there.
    from src.api.app import create_app  # noqa: F401  (app already built)
    import src.services.profile.manager as pm

    mgr = pm.ProfileManager()
    mgr.profiles["old"] = Profile(
        name="old", os_type="windows", engine="chromium", device_type="mobile"
    )
    mgr.save_profiles()

    r = c.patch("/api/v1/profiles/old", json={"notes": "hello"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["notes"] == "hello"


# ---------------------------------------------------------------------------
# AC5, the uncovered quadrant — PRE-EXISTING *PAIR* INCOHERENCE x A device_type
# EDIT. Every case above composes windows+chromium+mobile: a COHERENT pair with
# only Rule 3 violated, so Rules 1 and 2 have nothing to say and a gate that
# wrongly submitted every field to every rule would still look green. These two
# put a violated PAIR under the record and then touch device_type — the only
# shape that can tell a per-rule-family gate from a single "something changed"
# one. Both go RED against a single-flag gate.
# ---------------------------------------------------------------------------


def test_a_device_type_edit_is_not_refused_by_the_pair_it_did_not_touch(mgr):
    """The regression this quadrant exists to catch.

    A stored macos + firefox record violates Rule 2 and predates these rules
    (reachable via `restore_profile`, exempt by design, and via the unguarded
    REST lane). Its device_type is the innocent default. Editing ONLY
    device_type — to a value Rule 3 does not refuse at all — introduces nothing,
    so it must APPLY.

    A gate that fires `assert_coherent(os, engine, device_type)` on "any field
    changed" refuses this with `engine 'firefox' cannot be combined with os_type
    'macos'`: a conflict the operator did not create and cannot even see in the
    field they touched. That is the "never blocked by incoherence it did not
    introduce" invariant, broken.
    """
    mgr.profiles["legacypair"] = Profile(
        name="legacypair", os_type="macos", engine="firefox",
        device_type="desktop",
    )

    assert mgr.update_profile("legacypair", "legacypair", "", None,
                              new_device_type="tablet")

    stored = mgr.profiles["legacypair"]
    assert stored.device_type == "tablet"
    # the pre-existing pair was tolerated, not repaired and not refused
    assert (stored.os_type, stored.engine) == ("macos", "firefox")


def test_the_rule_3_repair_is_allowed_on_a_record_that_also_violates_the_pair(mgr):
    """The stranding case: BOTH families violated, and the edit REPAIRS Rule 3.

    macos + firefox + mobile is routinely produced by the import door — which by
    design does not refuse, and whose normaliser (`coherent_engine`) answers
    "which engine?" and so leaves Rule 3 unreconciled — and by `restore_profile`,
    which is exempt (AC6). Moving device_type back to the default is the edit
    that FIXES Rule 3.

    Under a single gate that edit is refused by Rule 2, so the record is
    permanently stuck in its worst state: the repair is blocked by the other
    rule, arriving through the exact door the exemption keeps open. The manager
    docstring promises the opposite in as many words — "leave the profile
    permanently uneditable — including the edit that would FIX the pair".
    """
    mgr.profiles["stuck"] = Profile(
        name="stuck", os_type="macos", engine="firefox", device_type="mobile",
    )

    assert mgr.update_profile("stuck", "stuck", "", None,
                              new_device_type="desktop")

    stored = mgr.profiles["stuck"]
    # Rule 3 is genuinely repaired...
    assert is_device_type_coherent(stored.os_type, stored.device_type)
    assert stored.device_type == "desktop"
    # ...and the pair it did not touch is still the pair it did not touch
    assert (stored.os_type, stored.engine) == ("macos", "firefox")


def test_an_os_edit_that_introduces_rule_3_is_still_refused_on_such_a_record(mgr):
    """The gates overlap on os_type, and splitting them must not lose that.

    Rule 3 reads (os_type, device_type), so an os_type-only edit can INTRODUCE
    it. Per-family gating is about not judging fields the edit did not touch —
    not about judging less. A stored android + mobile record moved onto windows
    newly contradicts itself and must still be refused.
    """
    mgr.profiles["phone"] = Profile(
        name="phone", os_type="android", engine="chromium", device_type="mobile",
    )

    with pytest.raises(IncoherentProfile) as exc:
        mgr.update_profile("phone", "phone", "", "windows")

    # refused by RULE 3 — the reason names the fields the edit actually moved
    assert "device_type" in str(exc.value)
    assert mgr.profiles["phone"].os_type == "android"


def test_a_pair_edit_that_introduces_rule_2_is_still_refused(mgr):
    """The other half of the same guard: splitting the gates keeps Rule 2 armed.

    A coherent windows + firefox profile moved onto macos newly violates Rule 2,
    and the device_type family has nothing to say about it. The pair gate must
    still fire on its own.
    """
    assert mgr.add_profile("ff", "", "windows", engine="firefox")

    with pytest.raises(IncoherentProfile) as exc:
        mgr.update_profile("ff", "ff", "", "macos")

    assert "firefox" in str(exc.value)
    assert mgr.profiles["ff"].os_type == "windows"


# ---------------------------------------------------------------------------
# AC6 — the two decided asymmetries: restore is EXEMPT, import does not REFUSE
# ---------------------------------------------------------------------------


def test_restore_is_intentionally_exempt_and_does_not_strand_a_record(mgr):
    """Restore replays a record that already existed, so it introduces nothing.

    Guarding it would strand a trashed profile behind a conflict it did not
    create. Pinned so a future reader does not 'fix' it into a stranding bug.
    """
    mgr.profiles["oldphone"] = Profile(
        name="oldphone", os_type="windows", engine="chromium", device_type="mobile"
    )
    mgr.save_profiles()
    assert mgr.delete_profile("oldphone")
    assert "oldphone" not in mgr.profiles

    entry = mgr._trash().list()[0]
    ok, err = mgr.restore_profile(entry)
    assert ok, err

    # restored verbatim — the incoherent record is recoverable, not refused
    restored = mgr.profiles["oldphone"]
    assert (restored.os_type, restored.device_type) == ("windows", "mobile")


def test_the_import_door_does_not_refuse_the_triple(mgr, tmp_path):
    """Import NORMALISES rather than refusing, and Rule 3 does not change that.

    An archive is closer to an already-stored legacy record than to a fresh
    request: refusing would make an old archive permanently unimportable at the
    one moment the operator cannot edit it into shape.

    Stated honestly, because it is a real residual rather than a clean win: the
    normaliser is ``coherent_engine``, which answers "which engine?" — and Rule 3
    has NO engine remedy, since a windows + mobile profile is contradictory on
    chromium and on firefox alike. So the triple survives import verbatim, landing
    as exactly the kind of already-stored record the module tolerates and keeps
    editable (above). Reconciling it would mean rewriting a field at launch, which
    is ``process.py``'s job and deliberately out of this slice. What this test
    pins is the decided asymmetry: import does not REFUSE.
    """
    from src.services.profile.transfer import export_to_zip

    ok, zip_path = export_to_zip(
        Profile(name="imp", os_type="windows", engine="chromium",
                device_type="mobile"),
        str(tmp_path / "imp-nodata"),
        str(tmp_path),
        include_data=False,
    )
    assert ok, zip_path

    ok, result = mgr.import_profile(zip_path)
    assert ok, result
    assert "imp" in mgr.profiles

    # and the record it landed is editable, not stranded
    assert mgr.import_profile is not None
    assert mgr.update_profile("imp", "imp", "", None, new_notes="still editable")
    assert mgr.profiles["imp"].notes == "still editable"
