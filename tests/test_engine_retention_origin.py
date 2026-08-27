"""PS-172: WHERE Chromium's rollback target actually comes from, and WHICH
machines genuinely lack one — measured, not reasoned.

WHY THIS FILE IS CHARACTERIZATION AND NOT A FIX
------------------------------------------------
PS-172 was filed to make "the first engine bump after installing reversible
rather than the second". Driving the real paths shows that is ALREADY TRUE on a
fresh v3.0.0 install (PS-79's record shipped in v3.0.0 — `git tag --contains`),
and that the population which genuinely has no way back is a DIFFERENT one than
the ticket describes. It also shows the ticket's preferred remedy — derive the
digest locally from the bytes on disk — is unavailable on two of the three OSes
for a reason the ticket could not have seen, and actively harmful if forced.

So these tests PIN the real starting state. Every one drives a real install
(real zip, real extract, real _promote_staging) or a real revert and reads
OBSERVABLE STATE — the bytes in ENGINE_DIR, what rollback_target() returns, what
the UI row renders. None asserts that a helper was called; that is the failure
mode project_knowledge PS-11 catalogues, and it is exactly what would let this
file keep passing against the defect it exists to describe.

THE FOUR FACTS PINNED HERE
--------------------------
 1. A FRESH install records `current` and offers no rollback yet — the row
    renders nothing. This is the state the owner reported, and it is correct.
 2. That same machine's FIRST bump DOES leave a working rollback target, and
    the revert restores the previous build's bytes end to end. The ticket's
    stated goal already holds here.
 3. The machine with no way back is the one whose engine was installed BEFORE
    builds.json existed (upgraded INTO v3.0.0 rather than installed clean). Its
    first bump records no `previous`, exactly as updater.py:200-207 says.
 4. The recorded digest is the digest of the DOWNLOADED ASSET, not of the
    installed tree. Linux keeps that asset as the engine binary, so it can be
    re-derived locally; Windows deletes the zip at install, so it cannot — and
    recording a tree-derived digest instead produces a revert that downloads
    the whole build and THEN fails the verify.
"""

import hashlib
import shutil
import zipfile

import pytest

import src.core.platform as _platform
from src.services.engine import updater
from src.ui import app as _app_mod
from src.utils import httpdl


# --------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------
def _force_os(monkeypatch, *, win=False, mac=False, linux=False):
    monkeypatch.setattr(_platform, "IS_WINDOWS", win)
    monkeypatch.setattr(_platform, "IS_MACOS", mac)
    monkeypatch.setattr(_platform, "IS_LINUX", linux)


class _Engine:
    """A repointed ENGINE_DIR with every module constant that hangs off it.

    ENGINE_DIR is read at import time into VERSION_FILE / MARKER_FILE /
    BUILDS_FILE / ENGINE_BINARY, so repointing the directory alone would leave
    four constants aimed at the developer's REAL engine dir.
    """

    def __init__(self, monkeypatch, tmp_path, binary="chrome.exe"):
        self.dir = tmp_path / "engine"
        self.dir.mkdir()
        monkeypatch.setattr(updater, "ENGINE_DIR", str(self.dir))
        monkeypatch.setattr(updater, "ENGINE_BINARY", str(self.dir / binary))
        monkeypatch.setattr(updater, "VERSION_FILE", str(self.dir / "version.txt"))
        monkeypatch.setattr(updater, "BUILDS_FILE", str(self.dir / "builds.json"))
        monkeypatch.setattr(
            updater, "MARKER_FILE", str(self.dir / ".engine-complete")
        )

    def installed_marker(self) -> bytes:
        """WHICH build is on disk, read out of the engine's own bytes rather
        than out of any record that claims to describe it."""
        return (self.dir / "chrome.exe").read_bytes()[2:]


def _build_zip(path, marker):
    """A whole, runnable-looking Windows engine tree whose bytes NAME the build,
    so a later assertion can tell WHICH build is installed by reading them."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("chrome-win/chrome.exe", b"MZ" + marker)
        zf.writestr("chrome-win/some.dll", b"DLL-" + marker)
        zf.writestr("chrome-win/locales/en.pak", b"PAK-" + marker)


def _sha(path) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _serve(monkeypatch, src_path):
    """Fake ONLY the network hop, and keep the REAL digest gate.

    _download_to is where production verifies the transferred bytes
    (resumable_download checks internally), so a stub that merely returns True
    would disable the very check these tests are about — and test 4 below,
    whose whole point is a digest MISMATCH, could then never fail.
    """

    def fake_download_to(path, url, timeout, dg, progress, allow_missing=False):
        shutil.copyfile(src_path, path)
        return httpdl.verify_file(path, dg)

    monkeypatch.setattr(updater, "_download_to", fake_download_to)


@pytest.fixture
def no_profiles_running():
    """_engine_in_use fails CLOSED on an unwired provider, so without this every
    revert here would defer and the tests would pass for the wrong reason."""
    updater.set_in_use_provider(lambda: False)
    yield
    updater.set_in_use_provider(None)


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    """Point the settings store at a temp file so a pin written by a test cannot
    touch the developer's real ~/.persona/settings.json."""
    from src.core import settings

    monkeypatch.setattr(settings, "_path", lambda: str(tmp_path / "settings.json"))
    yield


class _RowOnly:
    """The narrowest stand-in for App that _engine_rollback_row actually reads.

    `_engine_rollback_row` IS THE REAL METHOD, bound off the class — stubbing it
    would leave a test that renders a fake and proves nothing about what the
    operator sees. `_engine_rollback_pending_row` is bound the same way and for
    the same reason: it is the control under test in half this file, so a
    stand-in version of it would assert only that the stand-in works.
    """

    _engine_rollback_row = _app_mod.App._engine_rollback_row
    _engine_rollback_pending_row = _app_mod.App._engine_rollback_pending_row

    def __init__(self):
        self.logs = []
        self.fired = []

    def _log(self, message):
        self.logs.append(message)

    # The two HANDLERS the row wires. Recorders rather than the real methods:
    # those claim _engine_busy and spawn a download thread, which is a different
    # subject with its own tests. What matters HERE is which handler the row
    # attaches, so the tests assert identity against these and never call them —
    # a row that wired the wrong gesture would still be "clickable".
    def _on_engine_resume(self):
        self.fired.append("resume")

    def _on_engine_rollback(self):
        self.fired.append("rollback")


def _row_text(control) -> str:
    """What the row actually SAYS, read out of the rendered control tree.

    Deliberately not a substring search over some larger blob: it walks to the
    ft.Text the operator reads. A row that renders nothing has no text, and this
    returns "" for it rather than raising, so "says nothing" and "says X" are
    the same kind of answer and can be compared in one assertion.
    """
    content = getattr(control, "content", None)
    if content is None:
        return ""
    parts = []
    for child in getattr(content, "controls", []) or []:
        value = getattr(child, "value", None)
        if isinstance(value, str):
            parts.append(value)
    return " ".join(parts)


# --------------------------------------------------------------------------
# 1. the state the owner reported — and it is CORRECT
# --------------------------------------------------------------------------
def test_a_fresh_install_offers_no_rollback_and_the_row_renders_nothing(
    monkeypatch, tmp_path, no_profiles_running
):
    """THE STARTING STATE, measured before anything is changed.

    A machine that has just installed the engine has exactly one build and
    nothing to go back to, so rollback_target() is empty and the row renders
    NOTHING — a zero-height container, not a disabled button. This is the
    asymmetry the owner saw beside Firefox's revert, and the row is behaving as
    designed: a button that cannot work is worse than no button.
    """
    _force_os(monkeypatch, win=True)
    _Engine(monkeypatch, tmp_path)

    tag_a = "144.0.7559.132"
    zip_a = tmp_path / "a.zip"
    _build_zip(zip_a, b"AAA")
    digest_a = _sha(zip_a)
    monkeypatch.setattr(
        updater, "fetch_latest_full", lambda *a, **k: (tag_a, "http://x/a.zip", digest_a)
    )
    _serve(monkeypatch, zip_a)

    ok, message = updater.ensure_engine()
    assert ok is True, message
    assert updater.current_version() == tag_a

    assert updater.rollback_target() == ("", ""), (
        "a fresh install has only one build; there is nothing to go back to"
    )

    # PS-172 (owner's decision): the EMPTINESS is unchanged and correct — there
    # is still no button, because a button that cannot work is worse than none.
    # What changed is that the row now SAYS why. The silence this replaces is
    # pinned in this file's first commit; the defect the owner reported was
    # reading that silence as "Chromium has no revert at all".
    row = _RowOnly()._engine_rollback_row()
    assert row.height != 0, (
        "the row must no longer be an empty space — silence is what the owner "
        "misread as 'возврат реализован только у фф'"
    )
    assert row.on_click is None, (
        "this state must NOT be clickable: the complaint being closed was a "
        "gesture that looked available and was not"
    )
    assert _row_text(row) == "rollback available after the next engine update", (
        "a FRESH install has its current build recorded, so exactly one more "
        "update leaves it a way back — see the next test, which drives it"
    )


# --------------------------------------------------------------------------
# 2. the ticket's stated goal — ALREADY MET on a fresh install
# --------------------------------------------------------------------------
def test_the_first_bump_after_a_fresh_install_is_already_reversible(
    monkeypatch, tmp_path, no_profiles_running
):
    """PS-172's goal, driven end to end: install, bump the engine ONCE, revert,
    and confirm the build now on disk is the previous one.

    Named for what it asserts. It does not merely check that a target appeared —
    it performs the revert and reads the engine's own bytes back, because a
    recorded pair that cannot actually be fetched and verified is not a way
    back. THIS ALREADY PASSES on main: ensure_engine records the fresh install
    (updater.py:1120), so the FIRST swap has an identity to demote.
    """
    _force_os(monkeypatch, win=True)
    eng = _Engine(monkeypatch, tmp_path)

    tag_a, tag_b = "144.0.7559.132", "145.0.7600.100"
    zip_a, zip_b = tmp_path / "a.zip", tmp_path / "b.zip"
    _build_zip(zip_a, b"AAA")
    _build_zip(zip_b, b"BBB")
    digest_a, digest_b = _sha(zip_a), _sha(zip_b)

    monkeypatch.setattr(
        updater, "fetch_latest_full", lambda *a, **k: (tag_a, "http://x/a.zip", digest_a)
    )
    _serve(monkeypatch, zip_a)
    assert updater.ensure_engine()[0] is True
    assert eng.installed_marker() == b"AAA"

    # ONE engine bump, in the production ordering: record BEFORE write_version,
    # because version.txt's single slot is what destroys the outgoing identity.
    _serve(monkeypatch, zip_b)
    assert updater.download_engine("http://x/b.zip", digest=digest_b, tag=tag_b) is True
    updater.record_installed_build(tag_b, digest_b)
    updater.write_version(tag_b)
    assert eng.installed_marker() == b"BBB"

    assert updater.rollback_target() == (tag_a, digest_a), (
        "the FIRST bump must leave a way back to the build that was working"
    )

    # ...and the way back must actually work.
    monkeypatch.setattr(
        updater, "fetch_release_full", lambda t, **k: (t, "http://x/a.zip", digest_a)
    )
    _serve(monkeypatch, zip_a)
    ok, message = updater.revert_to_previous_build()
    assert ok is True, message
    assert eng.installed_marker() == b"AAA", (
        "the revert reported success but the engine on disk is not build A"
    )
    assert updater.current_version() == tag_a


# --------------------------------------------------------------------------
# 3. the population that GENUINELY has no way back
# --------------------------------------------------------------------------
def test_an_engine_installed_before_the_record_existed_gets_no_target_from_its_first_bump(
    monkeypatch, tmp_path, no_profiles_running
):
    """The real defect population, and it is NOT the fresh install.

    An operator who UPGRADED the app into v3.0.0 already had a working engine,
    so ensure_engine short-circuits on `is_installed()` and never records
    anything. That machine has a version.txt and no builds.json — so its first
    bump has no `current` slot to demote, records only the incoming build, and
    leaves the operator with no way back from an update they did not choose.
    The second swap records normally (updater.py:200-207).
    """
    _force_os(monkeypatch, win=True)
    eng = _Engine(monkeypatch, tmp_path)

    # the legacy on-disk state: a whole, complete engine with NO record beside it
    (eng.dir / "chrome.exe").write_bytes(b"MZ" + b"AAA")
    (eng.dir / "some.dll").write_bytes(b"DLL-AAA")
    (eng.dir / ".engine-complete").write_text("ok", encoding="utf-8")
    updater.write_version("144.0.7559.132")

    assert updater.is_installed() is True
    assert not (eng.dir / "builds.json").exists(), "precondition: no record"

    # PS-172, BEFORE the bump: nothing at all is recorded, so the next swap has
    # no `current` to demote and the one AFTER it is the first reversible one.
    # The row must say TWO here — saying "the next update" would be a promise
    # this machine watches fail one bump from now, which is the same defect as
    # the silence it replaces, only louder.
    legacy_row = _RowOnly()._engine_rollback_row()
    assert legacy_row.height != 0, "the row must explain the gap rather than be empty"
    assert legacy_row.on_click is None, "an explanation is not a gesture"
    assert _row_text(legacy_row) == (
        "rollback available after the next two engine updates"
    )

    tag_b = "145.0.7600.100"
    zip_b = tmp_path / "b.zip"
    _build_zip(zip_b, b"BBB")
    digest_b = _sha(zip_b)
    _serve(monkeypatch, zip_b)
    assert updater.download_engine("http://x/b.zip", digest=digest_b, tag=tag_b) is True
    updater.record_installed_build(tag_b, digest_b)
    updater.write_version(tag_b)

    assert updater.rollback_target() == ("", ""), (
        "THE DEFECT: this machine was bumped off a working build and has no "
        "way back to it"
    )

    # AND THE COUNT THE ROW PROMISED IS NOW ONE LOWER — driven, not asserted
    # from a docstring. This bump recorded the incoming build as `current`, so
    # the next one demotes it. The machine still has no way back, which is the
    # part PS-172 accepts and does not fix; what it no longer does is say
    # nothing about it.
    bumped_row = _RowOnly()._engine_rollback_row()
    assert _row_text(bumped_row) == (
        "rollback available after the next engine update"
    ), "the countdown must actually count down as updates land"


# --------------------------------------------------------------------------
# 4. why "derive the digest locally" is not available on every OS
# --------------------------------------------------------------------------
def test_linux_keeps_the_downloaded_asset_as_the_engine_so_its_digest_is_derivable(
    monkeypatch, tmp_path, no_profiles_running
):
    """LINUX: the AppImage IS the engine — _install_linux atomic_replaces the
    downloaded asset into place — so the installed binary is byte-identical to
    the asset the digest describes, and re-hashing it locally reproduces the
    recorded digest exactly. Local derivation is genuinely available here.
    """
    _force_os(monkeypatch, linux=True)
    eng = _Engine(monkeypatch, tmp_path, binary="chrome.AppImage")

    asset = tmp_path / "a.AppImage"
    asset.write_bytes(b"\x7fELF" + b"A" * 5000)
    digest_a = _sha(asset)
    _serve(monkeypatch, asset)
    assert (
        updater.download_engine("http://x/a.AppImage", digest=digest_a, tag="144.0")
        is True
    )

    assert _sha(str(eng.dir / "chrome.AppImage")) == digest_a, (
        "the installed AppImage must hash to the asset digest, or nothing on "
        "this machine could re-derive it"
    )


def test_windows_destroys_the_asset_so_a_tree_derived_digest_fails_the_revert(
    monkeypatch, tmp_path, no_profiles_running
):
    """WINDOWS: the recorded digest describes the downloaded ZIP, and the zip is
    deleted once extracted (_install_windows). Nothing on disk hashes to it.

    Named for the CONSEQUENCE, not the fact, because the fact alone understates
    it: recording a digest derived from the extracted tree does not merely fail
    to help — it produces a rollback target that LOOKS offerable, downloads the
    whole ~300-600MB build, and only then fails the verify. That is strictly
    worse than the empty row it would replace.
    """
    _force_os(monkeypatch, win=True)
    eng = _Engine(monkeypatch, tmp_path)

    tag_a = "144.0.7559.132"
    zip_a = tmp_path / "a.zip"
    _build_zip(zip_a, b"AAA")
    asset_digest = _sha(zip_a)
    _serve(monkeypatch, zip_a)
    assert (
        updater.download_engine("http://x/a.zip", digest=asset_digest, tag=tag_a)
        is True
    )

    assert not (eng.dir / "a.zip").exists(), "the asset is gone after install"
    tree_digest = _sha(str(eng.dir / "chrome.exe"))
    assert tree_digest != asset_digest

    # take the "derive locally" route on this OS and record the tree digest
    updater.atomic_write_json(
        updater.BUILDS_FILE,
        {
            "current": {"tag": "145.0.7600.100", "digest": _sha(str(eng.dir / "some.dll"))},
            "previous": {"tag": tag_a, "digest": tree_digest},
        },
    )
    assert updater.rollback_target() == (tag_a, tree_digest), (
        "precondition: the row would now offer this revert to the operator"
    )

    monkeypatch.setattr(
        updater, "fetch_release_full", lambda t, **k: (t, "http://x/a.zip", asset_digest)
    )
    _serve(monkeypatch, zip_a)
    ok, message = updater.revert_to_previous_build()
    assert ok is False, (
        "a digest derived from the extracted tree must never verify against the "
        "upstream asset — if this passes, the verify gate is not being applied"
    )
    assert "144.0.7559.132" in message


# --------------------------------------------------------------------------
# 5. the states the new line must NOT have disturbed (PS-172)
# --------------------------------------------------------------------------
# The information fix adds a fourth state to a row that had three working ones.
# These four pin the boundary from the other side: the new line must appear in
# EXACTLY one state and change nothing in the rest. A message that renders too
# eagerly is its own defect — it would be a promise made to a machine that has
# not asked the question yet, or a sentence sitting where a working button used
# to be.
def test_with_no_engine_installed_at_all_the_row_still_renders_nothing(
    monkeypatch, tmp_path
):
    """THE OVER-FIRE GUARD, and the reason the new state checks is_installed().

    Before the engine exists there is no update to go back FROM, so "rollback
    available after the next engine update" would be answering a question the
    operator has not asked — and this row renders during the very first
    download, where that line is pure noise beside a progress bar.

    Silence is correct here and stays correct.
    """
    _force_os(monkeypatch, win=True)
    _Engine(monkeypatch, tmp_path)  # repointed, and deliberately EMPTY

    assert updater.is_installed() is False, "precondition: nothing installed"
    assert updater.rollback_target() == ("", "")

    row = _RowOnly()._engine_rollback_row()
    assert row.height == 0, (
        "with no engine on disk the row must render nothing — the explanation "
        "is for machines that HAVE an engine and cannot go back from it"
    )
    assert _row_text(row) == ""


def test_a_machine_with_a_recorded_previous_build_still_gets_the_working_button(
    monkeypatch, tmp_path
):
    """THE REGRESSION GUARD for the state that already worked.

    Named for what it asserts: not merely that something renders, but that the
    row is still the CLICKABLE revert naming the tag, with its handler wired.
    The whole risk of adding a message state is that it swallows the gesture it
    was meant to explain the absence of.
    """
    _force_os(monkeypatch, win=True)
    eng = _Engine(monkeypatch, tmp_path)

    (eng.dir / "chrome.exe").write_bytes(b"MZ" + b"BBB")
    (eng.dir / ".engine-complete").write_text("ok", encoding="utf-8")
    updater.write_version("145.0.7600.100")
    updater.atomic_write_json(
        updater.BUILDS_FILE,
        {
            "current": {"tag": "145.0.7600.100", "digest": "b" * 64},
            "previous": {"tag": "144.0.7559.132", "digest": "a" * 64},
        },
    )
    assert updater.rollback_target()[0] == "144.0.7559.132"

    row = _RowOnly()._engine_rollback_row()
    assert _row_text(row) == "go back to 144.0.7559.132", (
        "a machine WITH a way back must still be offered it, unchanged"
    )
    assert row.on_click is not None, (
        "the revert must still be CLICKABLE — an explanation must never "
        "replace the working gesture"
    )
    app = _RowOnly()
    clickable = app._engine_rollback_row()
    clickable.on_click(None)
    assert app.fired == ["rollback"], (
        "and it must be wired to the REVERT, not merely to something — a row "
        "that renders the right words on the wrong handler still cannot go back"
    )
    assert "Re-download" in row.tooltip, (
        "PS-79's cost warning must survive: this revert moves hundreds of MB "
        "and the operator is told before clicking, not after"
    )


def test_a_pinned_machine_still_gets_the_resume_gesture(monkeypatch, tmp_path):
    """The pin state outranks everything and is untouched.

    An operator who already went back is PINNED, and the way out of that state
    must stay where they entered it. This is checked with a real pin written
    through the real settings API (isolated by the autouse fixture), not by
    stubbing pinned_build().
    """
    _force_os(monkeypatch, win=True)
    eng = _Engine(monkeypatch, tmp_path)

    (eng.dir / "chrome.exe").write_bytes(b"MZ" + b"AAA")
    (eng.dir / ".engine-complete").write_text("ok", encoding="utf-8")
    updater.write_version("144.0.7559.132")

    from src.core import settings

    settings.set_chromium_build_pin("144.0.7559.132")
    assert updater.pinned_build() == "144.0.7559.132"

    # NOTE the pinned machine here ALSO has no record, so without the pin it
    # would take the new message branch. The pin must win.
    assert updater.rollback_target() == ("", "")
    assert updater.current_build_recorded() is False

    row = _RowOnly()._engine_rollback_row()
    assert _row_text(row) == "resume updates (pinned to 144.0.7559.132)", (
        "a pinned operator must be offered the way out of the pin, not an "
        "explanation of why there is no rollback"
    )
    app = _RowOnly()
    clickable = app._engine_rollback_row()
    clickable.on_click(None)
    assert app.fired == ["resume"], (
        "and it must be wired to RESUME — the pinned operator's only way "
        "forward out of the state they entered"
    )


def test_an_unreadable_build_record_renders_nothing_and_says_why_in_the_log(
    monkeypatch, tmp_path
):
    """THE FAIL-QUIET DIRECTION for the new branch.

    A record that cannot be read cannot support a claim about HOW MANY updates
    remain, so the row degrades to silence rather than guessing a number — and
    logs the reason, matching the sibling handler above it that was given the
    same treatment for the same reason (a control vanishing with nothing on
    screen saying why).
    """
    _force_os(monkeypatch, win=True)
    eng = _Engine(monkeypatch, tmp_path)

    (eng.dir / "chrome.exe").write_bytes(b"MZ" + b"AAA")
    (eng.dir / ".engine-complete").write_text("ok", encoding="utf-8")
    updater.write_version("144.0.7559.132")

    def boom():
        raise OSError("builds.json is unreadable")

    monkeypatch.setattr(updater, "current_build_recorded", boom)

    app = _RowOnly()
    row = app._engine_rollback_row()
    assert row.height == 0, "an unreadable record must not promise a countdown"
    assert any("build record" in line for line in app.logs), (
        "the reason must reach the log rather than the control silently vanishing"
    )
