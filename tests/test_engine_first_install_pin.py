"""PS-317: the FIRST-INSTALL lane must honour the operator's revert pin.

THE DEFECT, AS A REACHABLE SEQUENCE
-----------------------------------
1. The operator REVERTS off a bad build. ``revert_to_previous_build`` records
   the reverted-to build in ``builds.json["current"]`` (with the digest those
   bytes verified against), writes ``version.txt``, and sets the pin.
2. A later upgrade FAILS. ``download_engine`` deliberately leaves the
   ``.engine-installing`` sentinel behind, so ``is_installed()`` stays False
   across the crash rather than launching a half-promoted tree.
3. Nothing removes ``version.txt`` or ``builds.json`` on that failure, so the
   pin and the record both survive intact.
4. ``_check_engine_async`` routes a not-installed engine to the FIRST-INSTALL
   lane (``_download_engine_fresh``), NOT to the update lane — so
   ``_engine_update_available()``, the pin's only enforcing consumer before this
   ticket, is never consulted.
5. ``_download_engine_fresh`` calls ``ensure_engine``.
6. ``ensure_engine`` fetched the NEWEST build and installed it.

Net: the build the operator explicitly rejected was re-installed, unattended,
with the pin still on disk — and the panel then told them the engine was
"currently held at OLD" while NEW was what was installed.

WHAT THESE TESTS ASSERT, AND WHAT THEY DELIBERATELY DO NOT
----------------------------------------------------------
Every test drives the REAL ``ensure_engine`` over a REAL install (a real zip, a
real extract, a real ``_promote_staging``) and reads OBSERVABLE STATE — the
engine's own bytes in ENGINE_DIR, what ``current_version()`` reports, what the
real ``_engine_rollback_row`` renders. None asserts that a helper was called;
that is the failure mode project_knowledge PS-11 catalogues, and it is exactly
what would let this file keep passing against the defect it exists to close.

The Windows path is driven end to end over a real multi-file zip (as
tests/test_engine_rollback_redownload.py does) because ``_force_os`` plus a real
zipfile make that reachable in ANY container. macOS shells out to
hdiutil/ditto, which are absent here.

STEP 2'S FAILURE IS A CRASH, NOT A CORRUPT ASSET, and the distinction is
load-bearing rather than incidental — see ``_fail_an_upgrade`` for the full
reasoning. Since PS-245 an install that fails BEFORE promotion is a CONFIRMED
rollback and its sentinel is CLEARED, so a corrupt asset no longer leaves the
engine not-installed and cannot express step 2 at all. The failure this file
needs is the one PS-245 deliberately does not forgive: a process killed
mid-promotion, whose outcome is UNKNOWN and whose sentinel therefore survives.

THE CHOSEN BRANCH (see the PR body for the full argument): ``ensure_engine``
installs the PINNED tag rather than refusing. When the pinned tag is no longer
served upstream it FALLS THROUGH to the newest build, says so, and CLEARS the
pin — because a pin that could not be honoured must not keep claiming on screen
that the engine is held at a build that is not installed.
"""

import os

import pytest

import src.core.platform as _platform
from src.services.engine import policy
from src.services.engine import updater
from src.ui import app as _app_mod
from src.utils.httpdl import normalize_digest


# ---------------------------------------------------------------------------
# harness — the same shape tests/test_engine_rollback_redownload.py uses
# ---------------------------------------------------------------------------


def _force_os(monkeypatch, *, win=False, mac=False, linux=False):
    monkeypatch.setattr(_platform, "IS_WINDOWS", win)
    monkeypatch.setattr(_platform, "IS_MACOS", mac)
    monkeypatch.setattr(_platform, "IS_LINUX", linux)


def _build_zip(path, marker):
    """A whole, runnable-looking Windows engine tree whose bytes NAME the build,
    so a later assertion can tell WHICH build is installed by reading them."""
    import zipfile

    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("chrome-win/chrome.exe", b"MZ" + marker)
        zf.writestr("chrome-win/some.dll", b"DLL-" + marker)
        zf.writestr("chrome-win/locales/en.pak", b"PAK-" + marker)


class _Engine:
    """A repointed ENGINE_DIR with every module constant that hangs off it.

    ENGINE_DIR is read at import time into VERSION_FILE / MARKER_FILE /
    BUILDS_FILE / ENGINE_BINARY, so repointing the directory alone would leave
    four constants aimed at the operator's REAL engine dir.
    """

    def __init__(self, monkeypatch, tmp_path):
        self.dir = tmp_path / "engine"
        self.dir.mkdir()
        monkeypatch.setattr(updater, "ENGINE_DIR", str(self.dir))
        monkeypatch.setattr(updater, "ENGINE_BINARY", str(self.dir / "chrome.exe"))
        monkeypatch.setattr(updater, "VERSION_FILE", str(self.dir / "version.txt"))
        monkeypatch.setattr(updater, "BUILDS_FILE", str(self.dir / "builds.json"))
        monkeypatch.setattr(
            updater, "MARKER_FILE", str(self.dir / ".engine-complete")
        )

    def installed_marker(self) -> bytes:
        """WHICH build is actually on disk, read out of the engine's own bytes
        rather than out of any record that claims to describe it."""
        return (self.dir / "chrome.exe").read_bytes()[2:]

    def entries(self) -> set:
        return {p.name for p in self.dir.iterdir()}


@pytest.fixture
def eng(monkeypatch, tmp_path):
    _force_os(monkeypatch, win=True)
    e = _Engine(monkeypatch, tmp_path)
    # No profile is running. Wired explicitly because _engine_in_use fails
    # CLOSED on an unwired provider, and an unwired oracle would make every
    # revert here defer — the tests would pass for entirely the wrong reason.
    updater.set_in_use_provider(lambda: False)
    yield e
    updater.set_in_use_provider(None)


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    """Point the settings store at a temp file so a pin written by a test cannot
    touch the developer's real ~/.persona/settings.json."""
    from src.core import settings

    monkeypatch.setattr(settings, "_path", lambda: str(tmp_path / "settings.json"))
    yield


@pytest.fixture(autouse=True)
def no_operator_policy_file(monkeypatch, tmp_path):
    """No local engine-policy.json, so policy.check answers OK on every tag and
    a refusal here can only come from the code under test."""
    monkeypatch.setattr(
        policy, "POLICY_FILE", str(tmp_path / "no-such-engine-policy.json")
    )
    yield


class _Upstream:
    """Stands GitHub up: `catalogue` maps tag -> (marker, digest). A tag absent
    from it is a YANKED release — fetch_release_full answers ('','',''), which
    is exactly what the real function does for a deleted release.
    """

    def __init__(self, monkeypatch, tmp_path, catalogue):
        self.catalogue = dict(catalogue)
        self.tmp_path = tmp_path
        self.downloads = []  # (tag, digest_presented_to_the_transfer)
        monkeypatch.setattr(updater, "fetch_release_full", self._fetch_release_full)
        monkeypatch.setattr(updater, "fetch_latest_full", self._fetch_latest_full)
        monkeypatch.setattr(updater, "_download_to", self._download_to)

    def _fetch_release_full(self, tag, timeout=20):
        if tag not in self.catalogue:
            return "", "", ""
        _marker, digest = self.catalogue[tag]
        return tag, f"http://x/{tag}.zip", digest

    def _fetch_latest_full(self, timeout=20):
        if not self.catalogue:
            return "", "", ""
        newest = max(self.catalogue, key=updater.parse_version)
        return self._fetch_release_full(newest)

    def _download_to(self, path, url, timeout, digest, progress, allow_missing=False):
        import shutil as _sh

        tag = url.rsplit("/", 1)[-1][: -len(".zip")]
        self.downloads.append((tag, digest))
        marker, _digest = self.catalogue[tag]
        zip_path = self.tmp_path / f"serve-{tag}.zip"
        _build_zip(zip_path, marker)
        _sh.copyfile(zip_path, path)
        return True

    def yank(self, tag):
        self.catalogue.pop(tag, None)


def _install(eng, upstream, tag):
    """A REAL install of `tag` through download_engine, recorded the way the
    production callers do (record BEFORE write_version — version.txt's single
    slot is what destroys the outgoing identity)."""
    _marker, digest = upstream.catalogue[tag]
    ok = updater.download_engine(f"http://x/{tag}.zip", digest=digest, tag=tag)
    assert ok is True, f"the {tag} install itself failed"
    updater.record_installed_build(tag, digest)
    updater.write_version(tag)


class _ProcessDied(BaseException):
    """The upgrade's process being KILLED the instant promotion begins.

    ⚠️ WHY BaseException — AND AN HONEST NOTE ON HOW FAR THAT ACTUALLY BUYS
    ANYTHING, because the tempting claim here is measurably too strong.

    The claim worth making: production must not be able to swallow this. The
    installer catches `(OSError, zipfile.BadZipFile)` and `_promote_staging`
    catches `Exception` to run its rollback — and a rollback that COMPLETES
    reports INSTALL_OUTCOME_RESTORED, which is exactly what PS-245 clears the
    sentinel for. A catchable exception can therefore be turned into a tidy,
    launchable, sentinel-free outcome, which is the opposite of the state this
    sequence needs. BaseException cannot be absorbed by either handler.

    The claim NOT worth making, because it is false AT THIS SEAM: that the
    choice is load-bearing here. It is not. Measured both ways —

        seam ABOVE the promotion (this one, a stubbed _promote_staging):
          BaseException -> outcome='unknown'   Exception -> outcome='unknown'
        seam INSIDE the promotion (a kill on a mid-tree shutil.move):
          BaseException -> outcome='unknown'   Exception -> outcome='restored'

    — so at this seam the two are indistinguishable today, and a probe that
    swapped the base class here would pass and prove nothing. BaseException is
    kept as defence against the seam or the handlers moving, not because it is
    doing work right now, and this note exists so a later reader does not
    mistake belt for braces.

    The seam is ABOVE the promotion deliberately: it leaves the tree WHOLLY at
    the reverted-to build, which is what the ticket's step 3 describes ("the
    promotion never happened") and what lets every downstream test read
    `installed_marker()` as a clean answer to "was the rejected build
    installed?". Killing mid-move instead leaves a half-swapped tree, and the
    NEW bytes that then sit in chrome.exe would be indistinguishable from the
    silent substitution these tests exist to detect.
    """


def _fail_an_upgrade(upstream, tag):
    """Step 2, driven: a real ``download_engine`` whose install is KILLED part
    way through the promotion, leaving the ``.engine-installing`` sentinel.

    ⚠️ WHY A CRASH AND NOT A CORRUPT ASSET — this is the whole reason the file
    once went red on CI while passing locally, so it is recorded here rather
    than in a commit message nobody will read again.

    This helper used to manufacture the failure by serving a corrupt zip and
    letting the real ``_install_windows`` raise ``BadZipFile``. That failure
    happens BEFORE promotion begins, and PS-245 (``06305ce``, landed on main
    after this branch was cut) gave exactly that arm a new meaning: an install
    that provably wrote nothing outside its staging dir now reports
    ``INSTALL_OUTCOME_RESTORED``, and ``download_engine`` CLEARS the sentinel
    for it. So the corrupt asset stopped producing a failed install and started
    producing a *cleanly rolled back* one — ``is_installed()`` answered True and
    the premise of this whole sequence evaporated.

    That was correct behaviour in PS-245 and a broken premise here. The fix is
    to manufacture the failure PS-245 deliberately does not forgive, which is
    also the one the sequence is actually about: a genuine crash mid-promotion,
    where the tree is left part-old/part-new and the outcome is UNKNOWN. Unknown
    keeps the sentinel, by that slice's own fail-closed rule.

    The assertions below pin that as a CONTRACT at the step that establishes it,
    rather than letting a later test die at an assertion that names something
    else — a harness that stops manufacturing a real failure must fail loudly
    HERE.
    """
    _marker, digest = upstream.catalogue[tag]

    # THE SEAM IS ABOVE THE PROMOTION — see _ProcessDied for why. The tree is
    # left wholly at the reverted-to build, which is the state step 3 of the
    # sequence describes.
    real_promote = updater._promote_staging

    def dying_promote(_staging):
        raise _ProcessDied("the process was killed the instant promotion began")

    updater._promote_staging = dying_promote
    try:
        updater.download_engine(f"http://x/{tag}.zip", digest=digest, tag=tag)
    except _ProcessDied:
        pass  # the crash escaping IS the manufactured failure
    else:  # pragma: no cover - a guard, not a path
        raise AssertionError(
            "the upgrade was supposed to be KILLED mid-promotion, but "
            "download_engine returned normally — the crash seam has moved"
        )
    finally:
        updater._promote_staging = real_promote

    # THE FAILURE CONTRACT, asserted where it is ESTABLISHED rather than
    # inherited. Each of these says something the others do not, and a harness
    # that silently stops producing a real failure trips HERE, naming the
    # actual cause, instead of reddening every downstream test at an assertion
    # about the pin.
    assert os.path.exists(updater._installing_file()), (
        "the killed upgrade must LEAVE the .engine-installing sentinel — that "
        "is what makes is_installed() False across a crash"
    )
    assert not updater._previous_build_restored(), (
        "the install outcome must be UNKNOWN after a crash: a crash cannot "
        "confirm its own rollback, and PS-245 clears the sentinel for a "
        "CONFIRMED restore. If this reads restored, the failure being "
        "manufactured is a tidy rollback and not a crash"
    )


class _RowOnly:
    """The narrowest stand-in for App that _engine_rollback_row actually reads.

    ⚠️ `_engine_rollback_row` IS THE REAL METHOD, bound off the class — stubbing
    it would leave a test that renders a fake and proves nothing about what the
    operator sees. Same for `_engine_rollback_pending_row`, which it delegates
    to.
    """

    _engine_rollback_row = _app_mod.App._engine_rollback_row
    _engine_rollback_pending_row = _app_mod.App._engine_rollback_pending_row

    def __init__(self):
        self.logs = []
        self.fired = []

    def _log(self, message):
        self.logs.append(message)

    def _on_engine_resume(self):
        self.fired.append("resume")

    def _on_engine_rollback(self):
        self.fired.append("rollback")


def _row_text(control) -> str:
    """What the row actually SAYS, read out of the rendered control tree — it
    recurses, because PS-229 nests the label under a cost line."""
    import flet as ft

    out = []

    def walk(c):
        if isinstance(c, ft.Text) and c.value:
            out.append(str(c.value))
        for attr in ("content", "controls"):
            child = getattr(c, attr, None)
            if child is None:
                continue
            if isinstance(child, list):
                for x in child:
                    walk(x)
            else:
                walk(child)

    walk(control)
    return " ".join(out)


OLD = "148.0.7778.215"
NEW = "149.0.8000.10"
OLD_DIGEST = "sha256:" + "a" * 64
NEW_DIGEST = "sha256:" + "b" * 64


def _revert_and_fail_an_upgrade(eng, monkeypatch, tmp_path):
    """Steps 1-3 of the sequence, driven end to end over real installs.

    Leaves: OLD on disk and recorded as `current` with its verified digest, the
    pin naming OLD, the .engine-installing sentinel from the failed NEW upgrade,
    and is_installed() False.
    """
    upstream = _Upstream(
        monkeypatch,
        tmp_path,
        {OLD: (b"OLD", OLD_DIGEST), NEW: (b"NEW", NEW_DIGEST)},
    )
    # a real swap OLD -> NEW, so there is genuinely something to revert
    _install(eng, upstream, OLD)
    _install(eng, upstream, NEW)
    assert eng.installed_marker() == b"NEW"

    # 1. the operator reverts
    ok, message = updater.revert_to_previous_build()
    assert ok is True, message
    assert eng.installed_marker() == b"OLD"
    assert updater.current_version() == OLD
    assert updater.pinned_build() == OLD
    # the conversion's load-bearing fact: the pinned tag sits in the record
    # TOGETHER with the digest those bytes verified against
    rec_tag, rec_digest = updater.current_build_target()
    assert rec_tag == OLD
    assert normalize_digest(rec_digest) == normalize_digest(OLD_DIGEST)

    # 2-3. a later upgrade to NEW is KILLED mid-promotion, leaving the sentinel
    _fail_an_upgrade(upstream, NEW)

    assert updater.is_installed() is False, (
        "the failed upgrade must leave is_installed() False — that is the "
        "sentinel's whole purpose and the premise of this sequence"
    )
    # ...and the pin and the record both survived the failure untouched
    assert updater.pinned_build() == OLD
    assert updater.current_version() == OLD
    # the engine tree on disk is still the reverted-to build (the promotion
    # never happened), which is what makes the re-install a silent substitution
    assert eng.installed_marker() == b"OLD"
    return upstream


# ---------------------------------------------------------------------------
# AC1 — the sequence, reproduced against the real ensure_engine
# ---------------------------------------------------------------------------


def test_a_failed_upgrade_after_a_revert_must_not_reinstall_the_rejected_build(
    eng, monkeypatch, tmp_path
):
    """THE DEFECT, end to end. RED on main.

    On `main` ensure_engine reaches fetch_latest_full() and installs NEW — the
    exact build the operator rejected — with the pin still sitting on disk.
    Asserted on the ENGINE'S OWN BYTES, not on a call count: a test that
    asserted "pinned_build was called" would pass against an implementation
    that read the pin and then installed the newest anyway.
    """
    upstream = _revert_and_fail_an_upgrade(eng, monkeypatch, tmp_path)

    logs = []
    ok, message = updater.ensure_engine(attempts=1, log=logs.append)

    assert ok is True, f"the first-install lane must still produce an engine: {logs}"
    assert eng.installed_marker() == b"OLD", (
        "the first-install lane re-installed the build the operator explicitly "
        "reverted away from — the pin was on disk the whole time"
    )
    assert updater.current_version() == OLD
    assert message == OLD
    # the engine really is complete and launchable again, not merely named
    assert updater.is_installed() is True
    assert (eng.dir / "some.dll").read_bytes() == b"DLL-OLD"
    assert (eng.dir / "locales" / "en.pak").read_bytes() == b"PAK-OLD"
    # and the operator's instruction still stands afterwards
    assert updater.pinned_build() == OLD


def test_the_pinned_first_install_asks_upstream_for_the_pinned_tag_only(
    eng, monkeypatch, tmp_path
):
    """The substitution must not happen at the TRANSFER either.

    A weaker implementation could read the pin, log about it, and still hand the
    newest build's URL to download_engine. This reads what was actually fetched.
    """
    upstream = _revert_and_fail_an_upgrade(eng, monkeypatch, tmp_path)
    upstream.downloads.clear()

    ok, _message = updater.ensure_engine(attempts=1, log=lambda m: None)

    assert ok is True
    fetched = [tag for tag, _digest in upstream.downloads]
    assert fetched == [OLD], (
        f"the pinned build is the only thing that should have been fetched: "
        f"{fetched}"
    )


def test_the_pinned_install_verifies_against_the_recorded_digest(
    eng, monkeypatch, tmp_path
):
    """PS-49's discipline, applied to this lane: the digest comes off the DISK
    RECORD, never off whatever upstream advertises for that tag today.

    Upstream is made to advertise a DIFFERENT digest for the pinned tag than the
    one those bytes were verified against when they were installed. The install
    must present the RECORDED digest to the transfer — that is the whole reason
    revert_to_previous_build writes record_installed_build BEFORE _set_pin.
    """
    upstream = _revert_and_fail_an_upgrade(eng, monkeypatch, tmp_path)
    upstream.downloads.clear()
    upstream_now_claims = "sha256:" + "f" * 64
    upstream.catalogue[OLD] = (b"OLD", upstream_now_claims)

    ok, _message = updater.ensure_engine(attempts=1, log=lambda m: None)

    assert ok is True
    assert upstream.downloads, "nothing was fetched at all"
    tag, presented = upstream.downloads[0]
    assert tag == OLD
    assert normalize_digest(presented) == normalize_digest(OLD_DIGEST), (
        "the pinned install trusted a FRESH API response for the digest — the "
        "recorded pair exists precisely so it does not have to"
    )
    assert normalize_digest(presented) != normalize_digest(upstream_now_claims)


# ---------------------------------------------------------------------------
# AC4 — the yanked pinned tag must never dead-end the first install
# ---------------------------------------------------------------------------


def test_a_yanked_pinned_build_falls_through_to_the_newest_and_says_so(
    eng, monkeypatch, tmp_path
):
    """⛔ THE TRAP THIS TICKET MUST NOT FALL INTO: a pin naming a build upstream
    no longer serves must not leave the operator with no engine.

    Firefox's own rule is the precedent — "a pin naming a build that is NOT
    installed is IGNORED rather than honoured-into-nothing" — and ui/app.py says
    an app with no engine is worse than one with an unwanted engine.
    """
    upstream = _revert_and_fail_an_upgrade(eng, monkeypatch, tmp_path)
    upstream.yank(OLD)  # the pinned release is gone from upstream

    logs = []
    ok, message = updater.ensure_engine(attempts=1, log=logs.append)

    assert ok is True, f"the operator was left with no engine at all: {logs}"
    assert eng.installed_marker() == b"NEW"
    assert updater.current_version() == NEW
    assert message == NEW
    assert updater.is_installed() is True
    joined = " ".join(logs)
    assert OLD in joined and "could not get" in joined, (
        f"persona substituted a different build in silence: {logs}"
    )
    assert "newest build instead" in joined, (
        f"the operator is told the pin failed but not what happened: {logs}"
    )


def test_a_yanked_pinned_build_clears_the_hold_so_the_row_cannot_lie(
    eng, monkeypatch, tmp_path
):
    """AC6, THE USER-VISIBLE HALF, on the one path that can still produce the
    mismatch: the pin could not be honoured, so it must not survive claiming the
    engine is held at a build that is not installed.

    Driven through the REAL _engine_rollback_row, so this asserts what the
    operator would actually read.
    """
    upstream = _revert_and_fail_an_upgrade(eng, monkeypatch, tmp_path)
    upstream.yank(OLD)

    logs = []
    ok, _message = updater.ensure_engine(attempts=1, log=logs.append)
    assert ok is True
    assert eng.installed_marker() == b"NEW"

    assert updater.pinned_build() == "", (
        "the hold outlived the build it named: the panel would tell the "
        f"operator the engine is held at {OLD} while {NEW} is installed"
    )

    row = _RowOnly()._engine_rollback_row()
    text = _row_text(row)
    tooltip = row.tooltip or ""
    assert "currently held at" not in tooltip, (
        f"the RESUME row still claims a hold that no longer exists: {tooltip!r}"
    )
    assert "resume updates" not in text, (
        f"a machine with no pin must not be offered the way out of one: {text!r}"
    )


def test_the_honoured_pin_leaves_the_row_telling_the_truth(
    eng, monkeypatch, tmp_path
):
    """AC6, the POSITIVE control for the same invariant — and the reveal control
    for the test above.

    When the pin IS honoured the hold must SURVIVE, and the row's "currently
    held at {pin}" must name the build that is genuinely installed. An
    implementation that cleared the pin unconditionally would satisfy the yanked
    test above and fail here, which is why both are written.
    """
    _revert_and_fail_an_upgrade(eng, monkeypatch, tmp_path)

    ok, _message = updater.ensure_engine(attempts=1, log=lambda m: None)
    assert ok is True
    assert eng.installed_marker() == b"OLD"

    pin = updater.pinned_build()
    assert pin == OLD
    assert updater.current_version() == pin, (
        "the pin names a build other than the one installed — the exact false "
        "statement AC6 forbids"
    )

    row = _RowOnly()._engine_rollback_row()
    tooltip = row.tooltip or ""
    assert f"currently held at {pin}" in tooltip
    assert NEW not in tooltip


# ---------------------------------------------------------------------------
# AC5 — the refuse/failed vocabulary is not regressed by any of this
# ---------------------------------------------------------------------------


def test_a_known_bad_pinned_build_is_refused_in_policys_own_words(
    eng, monkeypatch, tmp_path
):
    """The pin chooses WHICH tag is the candidate; persona's governance still
    rules on it, and it does so in the vocabulary that already exists.

    A pinned build persona knows to be broken must not be installed just because
    it was pinned — and the refusal must NOT be dressed up as a download
    failure, because retrying cannot change persona's decision. The operator's
    local remedy is the RESUME row, which clears the pin.
    """
    _revert_and_fail_an_upgrade(eng, monkeypatch, tmp_path)
    monkeypatch.setattr(
        policy, "KNOWN_BAD_VERSIONS", frozenset({OLD})
    )
    monkeypatch.setattr(
        updater.policy, "KNOWN_BAD_VERSIONS", frozenset({OLD})
    )

    logs = []
    ok, message = updater.ensure_engine(attempts=1, log=logs.append)

    assert ok is False
    assert OLD in message and "known-bad" in message
    assert any("known-bad" in m for m in logs), (
        "the refusal must reach the operator through `log` — the onboarding "
        "caller discards the returned message entirely"
    )
    assert not any("download failed" in m.lower() for m in logs), (
        "a governance refusal must never be worded as a failed download"
    )
    # nothing was substituted: the rejected build was NOT installed instead
    assert eng.installed_marker() == b"OLD"
    assert updater.is_installed() is False


def test_a_transient_failure_on_the_pinned_build_is_still_a_download_failure(
    eng, monkeypatch, tmp_path
):
    """The counterpart, and the reason both live in ensure_engine: a genuine
    transfer failure on the pinned build keeps the retryable wording."""
    upstream = _revert_and_fail_an_upgrade(eng, monkeypatch, tmp_path)
    monkeypatch.setattr(
        updater, "_download_to",
        lambda path, url, timeout, digest, progress, allow_missing=False: False,
    )

    logs = []
    ok, message = updater.ensure_engine(attempts=1, log=logs.append)

    assert ok is False
    assert message == "download failed"
    assert any("Engine download failed" in m for m in logs)


def test_a_failed_pinned_transfer_does_not_silently_take_the_newest_instead(
    eng, monkeypatch, tmp_path
):
    """The pinned build EXISTS and would not transfer — that is a retryable
    failure on the operator's own choice, not permission to install a different
    build.

    Substituting here would re-open the whole defect through a narrower door:
    the operator would end up on the build they rejected because their network
    blipped once.
    """
    upstream = _revert_and_fail_an_upgrade(eng, monkeypatch, tmp_path)
    monkeypatch.setattr(
        updater, "_download_to",
        lambda path, url, timeout, digest, progress, allow_missing=False: False,
    )

    ok, _message = updater.ensure_engine(attempts=1, log=lambda m: None)

    assert ok is False
    assert eng.installed_marker() == b"OLD", "the rejected build was installed"
    assert updater.is_installed() is False
    assert updater.pinned_build() == OLD, (
        "a transfer failure must not release the operator's hold"
    )


def test_the_yanked_fall_through_does_not_also_claim_the_download_failed(
    eng, monkeypatch, tmp_path
):
    """AC5's vocabulary, on the path that most easily breaks it.

    The run ENDS WITH AN ENGINE INSTALLED. Telling the operator "Engine download
    failed" on the way there would be false, would be the retryable wording for
    something retrying cannot change, and would bury the one accurate sentence
    (persona could not get the pinned build, and here is what it did instead).
    """
    upstream = _revert_and_fail_an_upgrade(eng, monkeypatch, tmp_path)
    upstream.yank(OLD)

    logs = []
    ok, _message = updater.ensure_engine(attempts=1, log=logs.append)

    assert ok is True
    assert not any("Engine download failed" in m for m in logs), (
        f"a run that installed an engine reported a failed download: {logs}"
    )


def test_a_total_outage_keeps_the_pin_rather_than_releasing_it(
    eng, monkeypatch, tmp_path
):
    """THE REVEAL CONTROL for the pin release, and the reason it is keyed on a
    successful install of something ELSE rather than on the resolution failing.

    fetch_release_full answers ('','','') for a yanked release AND for a network
    outage, and cannot tell them apart. On an outage nothing installs — so the
    operator's standing instruction must survive for the next check to honour.
    An implementation that released the pin the moment resolution failed would
    pass the yanked test above and fail here.
    """
    upstream = _revert_and_fail_an_upgrade(eng, monkeypatch, tmp_path)
    upstream.catalogue.clear()  # nothing resolves at all: the whole API is down

    logs = []
    ok, _message = updater.ensure_engine(attempts=1, log=logs.append)

    assert ok is False
    assert updater.pinned_build() == OLD, (
        "an outage released the operator's hold — the pinned build may be "
        "perfectly fine and reachable again in an hour"
    )
    assert updater.is_installed() is False
    # and the operator IS told something went wrong, rather than nothing
    assert logs


# ---------------------------------------------------------------------------
# the unpinned lane is untouched
# ---------------------------------------------------------------------------


def test_an_unpinned_first_install_still_takes_the_newest_build(
    eng, monkeypatch, tmp_path
):
    """The steady state, and the guard against over-reach: with no pin the
    first install must behave exactly as it always has."""
    upstream = _Upstream(
        monkeypatch,
        tmp_path,
        {OLD: (b"OLD", OLD_DIGEST), NEW: (b"NEW", NEW_DIGEST)},
    )
    assert updater.pinned_build() == ""

    ok, message = updater.ensure_engine(attempts=1, log=lambda m: None)

    assert ok is True
    assert message == NEW
    assert eng.installed_marker() == b"NEW"
    assert updater.current_version() == NEW


def test_a_pin_naming_the_newest_build_installs_it_once(eng, monkeypatch, tmp_path):
    """The degenerate case: the pin and "latest" agree, so nothing is
    substituted and nothing is fetched twice."""
    upstream = _Upstream(monkeypatch, tmp_path, {NEW: (b"NEW", NEW_DIGEST)})
    from src.core import settings

    settings.set_chromium_build_pin(NEW)

    ok, message = updater.ensure_engine(attempts=1, log=lambda m: None)

    assert ok is True
    assert message == NEW
    assert eng.installed_marker() == b"NEW"
    assert [t for t, _d in upstream.downloads] == [NEW]
    assert updater.pinned_build() == NEW, "an honoured pin must survive"


# ---------------------------------------------------------------------------
# AC7 — the two engines' pins stay independent
# ---------------------------------------------------------------------------


def test_the_firefox_pin_does_not_steer_the_chromium_first_install(
    eng, monkeypatch, tmp_path
):
    """PS-79 made these separate settings keys deliberately. A Firefox revert
    must not reach into the Chromium install path — reading the wrong key here
    would resolve a `firefox-NN` tag against the Chromium release API."""
    upstream = _Upstream(
        monkeypatch,
        tmp_path,
        {OLD: (b"OLD", OLD_DIGEST), NEW: (b"NEW", NEW_DIGEST)},
    )
    from src.core import settings

    settings.set_engine_build_pin("firefox-140")
    assert updater.pinned_build() == "", "the Chromium pin must still be empty"

    ok, message = updater.ensure_engine(attempts=1, log=lambda m: None)

    assert ok is True
    assert message == NEW
    assert eng.installed_marker() == b"NEW"
    assert [t for t, _d in upstream.downloads] == [NEW]


# ---------------------------------------------------------------------------
# FALSIFICATION (non-waivable)
# ---------------------------------------------------------------------------


def test_falsification_without_the_pin_read_the_rejected_build_comes_back(
    eng, monkeypatch, tmp_path
):
    """WITH THE PIN READ NEUTERED AND THE REST OF THE DIFF IN PLACE, THE DEFECT
    MUST RETURN.

    This is the test that proves the others are not passing for some incidental
    reason — that the pin read is genuinely what redirects the install, and not
    (say) the ordering of the catalogue or a lucky cache hit. `pinned_build` is
    made to answer "" (the state of a machine that never reverted); everything
    else — the record, version.txt, the sentinel, upstream, the whole install
    path — is exactly as the tests above leave it.

    That is the state of `main` today, reproduced deliberately, and it is
    asserted on the engine's own bytes.
    """
    _revert_and_fail_an_upgrade(eng, monkeypatch, tmp_path)
    monkeypatch.setattr(updater, "pinned_build", lambda: "")

    ok, message = updater.ensure_engine(attempts=1, log=lambda m: None)

    assert ok is True
    assert message == NEW
    assert eng.installed_marker() == b"NEW", (
        "with the pin read removed the first install should have re-installed "
        "the rejected build — if it did not, the tests above are not measuring "
        "the pin at all"
    )
