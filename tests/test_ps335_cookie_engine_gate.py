"""PS-335 — cookie import/export must REFUSE on a profile whose engine cannot
use the Chromium-shaped cookie store, instead of reporting a false success.

THE DEFECT THIS FILE GUARDS. ``src/services/cookie/store.py`` is Chromium-shaped
end to end and, before this ticket, received no engine at all: ``import_cookies``
and ``export_cookies`` take a bare ``profile_dir`` string, and
``process.py`` computes that dir BEFORE it branches on the engine, so both
engines share it. On a Firefox profile the old import therefore:

* ``mkdir``-ed a ``Default/`` tree and wrote a ``Cookies`` DB the engine never
  opens (Firefox's jar is ``cookies.sqlite`` at the profile ROOT -- this
  project's own ``behaviour_checks`` names it so),
* returned a NON-ZERO count for those rows, and
* had that count persisted by ``set_cookie_status`` and re-rendered as
  ``last import: creep.json · 11 cookies`` on every subsequent dialog open.

Measured at the implementation base before the fix, on a profile dir holding a
real ``cookies.sqlite``::

    import_cookies returned : 1
    tree                    : ['/Default/Cookies', '/cookies.sqlite']
    firefox jar md5         : UNCHANGED
    export on a firefox dir : []

⭐ WHY THE FALSE REPORT IS THE DEFECT, NOT THE MISSING FEATURE. An operator who
imports session cookies into a Firefox profile, is told it worked, and then logs
in by hand has produced exactly the fresh-session-on-a-warm-identity mismatch
that cookie import exists to PREVENT. A refusal costs them one dialog; a false
success costs them the identity.

SCOPE. These tests pin arm (a) -- refuse and say so. Implementing the Firefox
arm (``moz_cookies`` in ``cookies.sqlite``, plaintext values) is deliberately
out of scope, and a half-done arm would re-create the same false report.

WHAT THESE TESTS ASSERT ON. The value the handler RETURNS and the state that is
PERSISTED on the profile -- never that a branch exists, and never a substring of
a generated message (the standing shape from the project's green-test article).
The one place a substring IS asserted is deliberate and inverted: the refusal
must NOT contain ``imported``/``exported``, because the dialog colours by
substring and a refusal containing either would render as a success.
"""

import asyncio
import pathlib
from types import SimpleNamespace

import pytest

from src.models.profile import Profile
from tests.test_app_ui import make_app


class _StubManager:
    """Minimal ProfileManager stand-in: the real one loads and saves JSON.

    ``set_cookie_status`` RECORDS rather than no-ops, because "was the false
    status persisted?" is the question half these tests exist to answer -- a
    stub that silently dropped the write would pass them while the product
    still wrote the lie.
    """

    def __init__(self, profiles: dict[str, Profile]):
        self.profiles = profiles
        self.cookie_status_writes: list[tuple[str, str]] = []

    def set_cookie_status(self, name: str, status: str) -> bool:
        if name not in self.profiles:
            return False
        self.profiles[name].cookie_import_status = status
        self.cookie_status_writes.append((name, status))
        return True


class _ExplodingPicker:
    """A file picker that FAILS if it is ever reached.

    The gate is meant to refuse BEFORE the operator is asked to choose a file,
    so on a refused profile these must never be called. This turns "the picker
    was not opened" from an untested claim into an assertion.
    """

    async def pick_files(self, **_kw):
        raise AssertionError(
            "the file picker was opened on a profile whose engine cannot use "
            "the cookie store — the refusal must come BEFORE the picker"
        )

    async def save_file(self, **_kw):
        raise AssertionError(
            "the save dialog was opened on a profile whose engine cannot use "
            "the cookie store — the refusal must come BEFORE the picker"
        )


def _app_with(profile: Profile, profile_dir: str = "/nonexistent"):
    app = make_app(None)
    app.pm = _StubManager({profile.name: profile})
    app.refs = SimpleNamespace(file_picker=_ExplodingPicker())
    app._log = lambda *a, **k: None
    app._profile_dir = lambda name: profile_dir
    return app


FIREFOX = Profile(name="ff", engine="firefox", os_type="windows")
CHROMIUM = Profile(name="cr", engine="chromium", os_type="windows")


# --- AC2: import refuses, reports no count, and persists nothing ------------


def test_import_on_a_firefox_profile_returns_a_refusal_not_a_count():
    """THE PRIMARY ASSERTION. The old handler returned ``imported <file> · N
    cookies`` with N non-zero. The refusal must carry no count at all."""
    app = _app_with(Profile(name="ff", engine="firefox", os_type="windows"))

    msg = asyncio.run(app._import_cookies_file("ff"))

    assert msg is not None, "the handler must SAY something, not silently no-op"
    assert "firefox" in msg.lower()
    assert "cookie import" in msg.lower()
    # No count may appear: the whole defect is a number the engine cannot honour.
    assert not any(ch.isdigit() for ch in msg), (
        f"the refusal carries a number, which is what the false report was: {msg!r}"
    )


def test_import_on_a_firefox_profile_persists_no_cookie_status():
    """The status is what SURVIVES: ``set_cookie_status`` writes it to the
    profile and the dialog re-renders it as ``last import: …`` on every open.
    A refusal that still wrote the status would leave the lie on screen."""
    profile = Profile(name="ff", engine="firefox", os_type="windows")
    app = _app_with(profile)

    asyncio.run(app._import_cookies_file("ff"))

    assert app.pm.cookie_status_writes == []
    assert profile.cookie_import_status is None


def test_import_on_a_firefox_profile_leaves_the_profile_dir_untouched(tmp_path):
    """No stray ``Default/Cookies``. The old path ``mkdir``-ed a Chromium tree
    into a Firefox profile, and the directory survived as debris even though
    nothing would ever read it."""
    jar = tmp_path / "cookies.sqlite"
    jar.write_bytes(b"FIREFOX-JAR")
    app = _app_with(
        Profile(name="ff", engine="firefox", os_type="windows"), str(tmp_path)
    )

    asyncio.run(app._import_cookies_file("ff"))

    assert not (tmp_path / "Default").exists(), (
        "a Chromium cookie tree was created inside a Firefox profile"
    )
    assert jar.read_bytes() == b"FIREFOX-JAR"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cookies.sqlite"]


# --- AC3: export refuses rather than reporting an empty read as success -----


def test_export_on_a_firefox_profile_returns_a_refusal_not_a_zero_count():
    """``exported 0 cookies`` was logged and returned as a SUCCESS on a profile
    whose jar may be full -- the empty list came from reading a Chromium path
    that does not exist, not from an empty browser."""
    app = _app_with(Profile(name="ff", engine="firefox", os_type="windows"))

    msg = asyncio.run(app._export_cookies_file("ff"))

    assert msg is not None
    assert "firefox" in msg.lower()
    assert "cookie export" in msg.lower()
    assert not any(ch.isdigit() for ch in msg), (
        f"the refusal carries a count: {msg!r}"
    )


# --- the colouring trap, pinned so a reword cannot silently re-break it -----


@pytest.mark.parametrize("verb", ["import", "export"])
def test_the_refusal_cannot_render_in_the_success_colour(verb):
    """⚠️ THE TRAP THIS TICKET NAMES, AS AN EXECUTABLE CHECK.

    ``dialogs/profile.py``'s handlers choose the colour by SUBSTRING::

        _set_status(msg, ok="imported" in msg.lower())
        _set_status(msg, ok="exported" in msg.lower())

    So a refusal worded "cookies are not imported on Firefox profiles" CONTAINS
    ``imported`` and renders in the SUCCESS colour -- the exact false
    affirmative this ticket exists to remove, re-introduced by its own fix. The
    existing failure path escapes only by luck (``import failed: …`` happens
    not to contain ``imported``).

    This asserts the CONSEQUENCE by replaying the renderer's own predicate, so
    a future reword that reads perfectly well in prose still fails here.
    """
    app = _app_with(Profile(name="ff", engine="firefox", os_type="windows"))
    handler = app._import_cookies_file if verb == "import" else app._export_cookies_file

    msg = asyncio.run(handler("ff"))

    assert msg is not None
    renders_as_success = ("imported" if verb == "import" else "exported") in msg.lower()
    assert not renders_as_success, (
        f"the {verb} refusal {msg!r} contains the participle the dialog "
        f"colours on, so it would render GREEN as a success"
    )


# --- the working arm must be completely unaffected -------------------------


def test_a_chromium_profile_is_not_refused_and_reaches_the_picker():
    """The positive control. Without it every assertion above is satisfied by a
    gate that refuses EVERYTHING, which would break the arm that works.

    Reaching the picker is the observable: ``_ExplodingPicker`` raises, so this
    asserts the gate let the handler through rather than asserting a branch.
    """
    app = _app_with(Profile(name="cr", engine="chromium", os_type="windows"))

    with pytest.raises(AssertionError, match="file picker was opened"):
        asyncio.run(app._import_cookies_file("cr"))


def test_a_chromium_export_is_not_refused_and_reaches_the_save_dialog():
    app = _app_with(Profile(name="cr", engine="chromium", os_type="windows"))

    with pytest.raises(AssertionError, match="save dialog was opened"):
        asyncio.run(app._export_cookies_file("cr"))


# --- the resolver: effective_engine, not the raw stored field ---------------


def test_an_incoherent_firefox_record_that_will_LAUNCH_chromium_is_not_refused():
    """⭐ WHY ``effective_engine`` AND NOT ``profile.engine``.

    Stealth-Firefox reports Windows regardless of the record, so a
    ``macos`` + ``firefox`` pair is incoherent and the coherence rules
    reconcile it toward chromium -- which HONORS ``os_type`` and so presents
    the OS the record claims. Such a profile LAUNCHES Chromium, so its cookie
    store IS the Chromium one, and refusing it would break a working arm.

    Reading the raw ``engine`` field would refuse it. This test is the
    difference between the two resolvers, and it is why the gate must not be
    ``if profile.engine != "chromium"``.

    ⚠️ The pair was MEASURED, not assumed: an earlier draft of this test used
    ``device_type="mobile"``, which does NOT reconcile -- ``coherent_engine``
    deliberately takes no ``device_type`` (Rule 3 has no engine remedy, and is
    answered by ``coherent_device_type`` instead). The premise assertion below
    is what caught that, and it stays so the test cannot go vacuous if the
    reconciliation table ever moves.
    """
    from src.services.browser.process import effective_engine

    profile = Profile(name="mac", engine="firefox", os_type="macos")
    # State the premise rather than assuming it: if coherence ever stops
    # reconciling this pair, the test below is no longer testing anything.
    assert profile.engine == "firefox", "premise: the STORED field says firefox"
    assert effective_engine(profile) == "chromium", (
        "premise: a macos+firefox record is reconciled toward chromium"
    )

    app = _app_with(profile)
    with pytest.raises(AssertionError, match="file picker was opened"):
        asyncio.run(app._import_cookies_file("mac"))


def test_the_legacy_camoufox_engine_name_is_refused_too():
    """``camoufox`` is the retired Firefox engine name, mapped FORWARD by
    ``normalize_engine`` so an old profile keeps launching. It resolves to
    firefox, so it must be refused — and the refusal must NAME firefox, not
    the stored string, because that is the engine the operator's profile
    actually runs.

    Worth a test of its own rather than folding into the firefox case: a gate
    reading the raw ``profile.engine`` would see an unrecognised string here
    and could fall either way, so this is a second, independent reason the
    resolver has to be ``effective_engine``.
    """
    from src.services.browser.process import effective_engine

    profile = Profile(name="old", engine="camoufox", os_type="windows")
    assert effective_engine(profile) == "firefox", (
        "premise: the retired engine name is mapped forward to firefox"
    )

    app = _app_with(profile)
    msg = asyncio.run(app._import_cookies_file("old"))

    assert msg is not None
    assert "firefox" in msg.lower(), (
        "the refusal should name the engine that LAUNCHES, not the stored "
        f"legacy string: {msg!r}"
    )
    assert "camoufox" not in msg.lower()


def test_the_refusal_does_not_delete_debris_left_by_an_earlier_import(tmp_path):
    """A profile that was imported into BEFORE this fix carries a real
    ``Default/Cookies``. It must still be refused — the gate is about the
    engine, not about whether debris happens to exist — and the refusal must
    NOT tidy up behind itself.

    Deleting a file the operator may want to recover is a destructive act this
    ticket did not authorise, and cleanup of pre-fix debris is a separate
    decision with its own evidence. Pinning the restraint so a later 'helpful'
    addition has to argue for itself.
    """
    default = tmp_path / "Default"
    default.mkdir()
    (default / "Cookies").write_bytes(b"OLD-CHROMIUM-DB")
    (tmp_path / "cookies.sqlite").write_bytes(b"FIREFOX-JAR")
    app = _app_with(
        Profile(name="ff", engine="firefox", os_type="windows"), str(tmp_path)
    )

    msg = asyncio.run(app._import_cookies_file("ff"))

    assert msg is not None
    assert (default / "Cookies").read_bytes() == b"OLD-CHROMIUM-DB"
    assert (tmp_path / "cookies.sqlite").read_bytes() == b"FIREFOX-JAR"


def test_an_unknown_profile_name_is_not_refused_by_this_gate():
    """Not-found belongs to the manager, which already answers False from
    ``set_cookie_status``. Minting a second, differently-worded not-found here
    would put two answers in the tree for one question, so the gate abstains
    and the existing path handles it."""
    app = _app_with(Profile(name="cr", engine="chromium", os_type="windows"))

    with pytest.raises(AssertionError, match="file picker was opened"):
        asyncio.run(app._import_cookies_file("no-such-profile"))
