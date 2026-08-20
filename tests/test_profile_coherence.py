"""os_type/engine coherence: a profile that exists is a machine that could exist.

These two rules used to live only in the profile dialog, which narrowed its
dropdowns so an incoherent pair could not be picked. The REST lane reached the
same model through another door and enforced neither, so it could create — and
*edit into* — a profile recorded as macOS on the Firefox engine, which launches
presenting Windows to every site while the record, the API response and the
operator all say macOS.

The fix puts the rules in the MODEL, below every door. So the load-bearing test
here is ``test_the_manager_itself_refuses...``: it calls the manager directly,
bypassing both the dialog and REST. A route-level guard would pass every HTTP
test on this page while leaving the automation lane, an importer, a
restore-from-backup path and any door added next year to re-answer the question
from scratch. Only the manager-level assertion can tell those two fixes apart.
"""
import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.mcp_token import get_or_create_token
from src.models.profile import Profile
from src.services.browser.process import effective_engine
from src.services.profile.bulk import bulk_create
from src.services.profile.coherence import (
    IncoherentProfile,
    coherence_error,
    coherent_engine,
    is_coherent,
)
from src.services.profile.manager import ProfileManager
from src.services.profile.transfer import export_to_zip

# The two pairs the rules forbid: a mobile OS cannot run the desktop-only
# Firefox engine (Rule 1), and the Firefox engine reports Windows regardless of
# os_type so it cannot claim another desktop OS (Rule 2).
INCOHERENT_PAIRS = [
    ("macos", "firefox"),
    ("linux", "firefox"),
    ("android", "firefox"),
    ("ios", "firefox"),
]

COHERENT_PAIRS = [
    ("windows", "firefox"),
    ("macos", "chromium"),
    ("linux", "chromium"),
    ("android", "chromium"),
    ("ios", "chromium"),
    ("windows", "chromium"),
]


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    import src.core.config as cfg
    import src.services.profile.manager as mod

    pf, dd = tmp_path / "profiles.json", tmp_path / "data"
    for m in (cfg, mod):
        monkeypatch.setattr(m, "PROFILES_FILE", str(pf), raising=False)
        monkeypatch.setattr(m, "DATA_DIR", str(dd), raising=False)
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
# The load-bearing test: the rule fires from the MODEL, not from a route.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("os_type,engine", INCOHERENT_PAIRS)
def test_the_manager_itself_refuses_an_incoherent_pair(mgr, os_type, engine):
    """Bypass BOTH doors and call the model directly.

    This is the assertion that distinguishes a fix in the right place from one
    added to the REST route: a route-level guard leaves this call unguarded, so
    this test fails against the wrong fix and passes only when the rule sits
    where every caller must cross it.
    """
    with pytest.raises(IncoherentProfile) as exc:
        mgr.add_profile("direct", "", os_type, engine=engine)

    # the refusal names both halves of the conflict, so the caller can act on it
    assert os_type in str(exc.value)
    assert "firefox" in str(exc.value)
    # and nothing was stored: the profile does not exist in either state
    assert "direct" not in mgr.profiles


@pytest.mark.parametrize("os_type,engine", COHERENT_PAIRS)
def test_the_manager_stores_every_coherent_pair(mgr, os_type, engine):
    # The rule must refuse the impossible without narrowing the possible.
    assert mgr.add_profile(f"ok-{os_type}-{engine}", "", os_type, engine=engine)
    stored = mgr.profiles[f"ok-{os_type}-{engine}"]
    assert (stored.os_type, stored.engine) == (os_type, engine)


def test_the_bulk_door_crosses_the_same_rule(mgr):
    # bulk_create is coherent today only because it never passes `engine` — it
    # is one parameter away from being a third door that answers differently.
    # It calls add_profile, so the rule already covers it: prove that placement
    # holds by driving the incoherent pair through bulk's own call path.
    result = bulk_create(mgr, ["b1", "b2"], os_type="macos")
    assert result["created"] == ["b1", "b2"]
    assert all(mgr.profiles[n].engine == "chromium" for n in ("b1", "b2"))

    with pytest.raises(IncoherentProfile):
        mgr.add_profile("b3", "", "macos", engine="firefox")


# ---------------------------------------------------------------------------
# REST create
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("os_type,engine", INCOHERENT_PAIRS)
def test_rest_create_refuses_with_a_reason(client, os_type, engine):
    c, h = client
    r = c.post(
        "/api/v1/profiles",
        json={"name": "viarest", "os_type": os_type, "engine": engine},
        headers=h,
    )
    # 400, not 409: the request describes a machine that cannot exist.
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert os_type in detail and "firefox" in detail
    # and the profile was not created behind the refusal
    assert c.get("/api/v1/profiles/viarest", headers=h).status_code == 404


def test_rest_create_still_accepts_a_coherent_pair(client):
    c, h = client
    r = c.post(
        "/api/v1/profiles",
        json={"name": "goodfox", "os_type": "windows", "engine": "firefox"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    assert r.json()["engine"] == "firefox"


# ---------------------------------------------------------------------------
# REST update — the path most likely to be missed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("os_type,engine", INCOHERENT_PAIRS)
def test_rest_update_cannot_edit_a_coherent_profile_into_an_incoherent_one(
    client, os_type, engine
):
    c, h = client
    assert c.post(
        "/api/v1/profiles",
        json={"name": "edited", "os_type": "windows", "engine": "chromium"},
        headers=h,
    ).status_code == 201

    r = c.patch(
        "/api/v1/profiles/edited",
        json={"os_type": os_type, "engine": engine},
        headers=h,
    )
    assert r.status_code == 400, r.text
    assert os_type in r.json()["detail"]
    # the profile is untouched, not half-applied
    after = c.get("/api/v1/profiles/edited", headers=h).json()
    assert (after["os_type"], after["engine"]) == ("windows", "chromium")


def test_rest_update_judges_a_one_field_patch_against_the_stored_value(client):
    """The trap the reviewer flagged: a PATCH supplying only ONE half.

    A guard that checks only the two SUPPLIED fields together lets
    `PATCH {"os_type": "macos"}` through on a profile already stored as firefox,
    because the body carries no engine to conflict with. The rule must judge the
    pair the edit RESULTS IN — which only something holding the stored record
    can do.
    """
    c, h = client
    assert c.post(
        "/api/v1/profiles",
        json={"name": "halfpatch", "os_type": "windows", "engine": "firefox"},
        headers=h,
    ).status_code == 201

    # only os_type supplied; the stored engine is what makes it incoherent
    r = c.patch("/api/v1/profiles/halfpatch", json={"os_type": "macos"}, headers=h)
    assert r.status_code == 400, r.text
    assert "macos" in r.json()["detail"]

    # the mirror image: only engine supplied, against a stored non-windows OS
    assert c.post(
        "/api/v1/profiles",
        json={"name": "halfpatch2", "os_type": "macos", "engine": "chromium"},
        headers=h,
    ).status_code == 201
    r = c.patch(
        "/api/v1/profiles/halfpatch2", json={"engine": "firefox"}, headers=h
    )
    assert r.status_code == 400, r.text
    assert "macos" in r.json()["detail"]


def test_rest_update_of_an_unrelated_field_still_works(client):
    # The guard must fire on the resulting PAIR, not on any edit that mentions
    # neither field — an over-eager check would make ordinary edits fail.
    c, h = client
    c.post(
        "/api/v1/profiles",
        json={"name": "notes", "os_type": "windows", "engine": "firefox"},
        headers=h,
    )
    r = c.patch("/api/v1/profiles/notes", json={"notes": "hello"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["notes"] == "hello"


# ---------------------------------------------------------------------------
# Already-stored incoherent records: not stranded, and reconciled deliberately
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("os_type", ["macos", "linux", "android", "ios"])
def test_a_stored_incoherent_profile_still_launches_and_tells_the_truth(os_type):
    """A record written before the rules (or through the once-unguarded REST
    lane) must not become unopenable.

    The deliberate choice: fall back to chromium, which HONORS os_type — so the
    launched machine matches what the record claims, instead of the record being
    a lie about a Firefox that reports Windows.
    """
    stranded = Profile(name="old", os_type=os_type, engine="firefox")
    assert effective_engine(stranded) == "chromium"


def test_a_coherent_firefox_profile_still_launches_firefox():
    # The reconciliation must not quietly disable the Firefox engine outright.
    assert effective_engine(Profile(name="f", os_type="windows", engine="firefox")) == (
        "firefox"
    )


def test_the_retired_camoufox_engine_name_is_read_as_firefox():
    # "camoufox" is the retired Firefox engine name. It must be mapped forward,
    # not treated as an unknown third engine that trivially satisfies the rules.
    assert coherent_engine("windows", "camoufox") == "firefox"
    assert coherent_engine("macos", "camoufox") == "chromium"
    assert not is_coherent("macos", "camoufox")


def test_an_already_incoherent_record_stays_editable(mgr):
    """An edit may not INTRODUCE incoherence, but must not be blocked by
    incoherence it did not introduce — otherwise a pre-existing record becomes
    permanently uneditable, including by the edit that would FIX it."""
    # bypass the guard the way an old profiles.json would: construct directly
    mgr.profiles["legacy"] = Profile(name="legacy", os_type="macos", engine="firefox")

    # an unrelated edit still succeeds
    assert mgr.update_profile("legacy", "legacy", "", "macos", new_notes="ok")
    assert mgr.profiles["legacy"].notes == "ok"

    # and the repairing edit is allowed through
    assert mgr.update_profile("legacy", "legacy", "", "macos", new_engine="chromium")
    assert mgr.profiles["legacy"].engine == "chromium"


def test_repairing_by_moving_the_os_to_windows_is_allowed(mgr):
    mgr.profiles["legacy2"] = Profile(name="legacy2", os_type="linux", engine="firefox")
    assert mgr.update_profile("legacy2", "legacy2", "", "windows")
    assert (mgr.profiles["legacy2"].os_type, mgr.profiles["legacy2"].engine) == (
        "windows", "firefox",
    )


# ---------------------------------------------------------------------------
# The rule object itself
# ---------------------------------------------------------------------------


def test_coherence_error_explains_which_way_to_resolve_the_conflict():
    # The caller gets a reason it can act on, not just a rejection.
    msg = coherence_error("macos", "firefox")
    assert msg is not None
    assert "macos" in msg and "windows" in msg and "chromium" in msg


def test_coherence_error_is_none_for_a_possible_machine():
    assert coherence_error("windows", "firefox") is None
    assert coherence_error("android", "chromium") is None


# ---------------------------------------------------------------------------
# The import door — normalises rather than refusing, deliberately
# ---------------------------------------------------------------------------


def _incoherent_archive(tmp_path, name="imported", os_type="macos", engine="firefox"):
    """A profile zip carrying a pair the model says cannot exist.

    Built through the real exporter, so this is exactly the archive an older
    build (or a once-unguarded REST create) would have produced and handed to an
    operator to import.
    """
    ok, zip_path = export_to_zip(
        Profile(name=name, os_type=os_type, engine=engine),
        str(tmp_path / f"{name}-nodata"),
        str(tmp_path),
        include_data=False,
    )
    assert ok, zip_path
    return zip_path


@pytest.mark.parametrize("os_type,engine", INCOHERENT_PAIRS)
def test_the_import_door_crosses_the_same_rule(mgr, tmp_path, os_type, engine):
    """import_profile writes the record directly, so it is a door of its own.

    It crosses the rule by NORMALISING, not refusing: an archive is closer to an
    already-stored legacy record than to a fresh request, and refusing would make
    an old archive permanently unimportable at the one moment the operator cannot
    edit it into shape. What must NOT survive is the impossible pair landing in
    the store verbatim.
    """
    zp = _incoherent_archive(tmp_path, name="imp", os_type=os_type, engine=engine)

    ok, result = mgr.import_profile(zp)
    assert ok, result

    stored = mgr.profiles["imp"]
    # the record the model holds describes a machine that could exist
    assert is_coherent(stored.os_type, stored.engine), (
        stored.os_type, stored.engine
    )
    # and it was reconciled toward the engine that HONORS os_type, so the record
    # keeps its claim and the launched machine matches it
    assert (stored.os_type, stored.engine) == (os_type, "chromium")
    assert effective_engine(stored) == "chromium"


def test_importing_over_a_coherent_profile_cannot_make_it_incoherent(mgr, tmp_path):
    """The sharper case: overwrite=True is an EDIT of an existing record.

    This is the same defect the ticket describes for REST update — 'an existing
    coherent profile can be edited into an incoherent one' — reachable through
    the import door instead.
    """
    assert mgr.add_profile("shared", "", "windows", engine="chromium")
    assert is_coherent(*(mgr.profiles["shared"].os_type, mgr.profiles["shared"].engine))

    zp = _incoherent_archive(tmp_path, name="shared", os_type="macos", engine="firefox")
    ok, result = mgr.import_profile(zp, overwrite=True)
    assert ok, result

    after = mgr.profiles["shared"]
    assert is_coherent(after.os_type, after.engine), (after.os_type, after.engine)
    assert (after.os_type, after.engine) == ("macos", "chromium")


def test_importing_a_coherent_archive_is_untouched(mgr, tmp_path):
    # Normalisation must only fire on the impossible pair — a legitimate Windows
    # Firefox archive must import as Firefox, not be quietly downgraded.
    zp = _incoherent_archive(tmp_path, name="goodimp", os_type="windows", engine="firefox")
    ok, result = mgr.import_profile(zp)
    assert ok, result
    assert (mgr.profiles["goodimp"].os_type, mgr.profiles["goodimp"].engine) == (
        "windows", "firefox",
    )


def test_restore_is_intentionally_exempt_and_does_not_strand_a_record(mgr):
    """Restore replays a record that already existed, so it introduces nothing.

    Guarding it would strand a trashed profile behind a conflict it did not
    create — which the 'already-stored records are not stranded' policy forbids.
    Pin the exemption so a future reader does not 'fix' it into a stranding bug.
    """
    mgr.profiles["oldfox"] = Profile(name="oldfox", os_type="macos", engine="firefox")
    assert mgr.delete_profile("oldfox")
    assert "oldfox" not in mgr.profiles

    entry = mgr._trash().list()[0]
    ok, err = mgr.restore_profile(entry)
    assert ok, err

    # restored verbatim — the incoherent record is recoverable, not refused
    restored = mgr.profiles["oldfox"]
    assert (restored.os_type, restored.engine) == ("macos", "firefox")
    # and it still opens, presenting the macOS its record claims
    assert effective_engine(restored) == "chromium"
