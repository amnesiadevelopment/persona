"""The self-update must verify the staged installer's sha256 against the
release's published checksum BEFORE running it. Size-only completeness let a
truncated/corrupted download execute as an installer (a broken half install),
and a size check can never tell a truncated download from a SUBSTITUTED one.

The missing-checksum policy is fail-CLOSED (PS-6), shared with the engine
updater: a fetched-and-mismatching checksum refuses, and so does a checksum that
cannot be fetched at all — an older release without one, or a network drop.
fetch_expected_sha256 already retries, so on exhaustion a transient failure is
indistinguishable from an absent digest and both refuse. Availability of an
update is never weighed against the integrity of what gets executed."""

import hashlib
import os
import sys

import pytest

import src.services.app_update.updater as au


def _write_staged(tmp_path, data=b"installer-bytes", tag="v9.9.9"):
    staged = tmp_path / f"persona-update-setup-{tag}.exe"
    staged.write_bytes(data)
    return staged, hashlib.sha256(data).hexdigest()


# --- fetch_expected_sha256: parsing + retry ---


def test_fetch_expected_sha256_parses_checksums_txt(monkeypatch):
    body = (
        "aaaa1111  persona-x86_64.AppImage\n"
        "bbbb2222 *persona-windows-setup.exe\n"
    )
    urls = []
    monkeypatch.setattr(au, "_curl_get", lambda url, **k: urls.append(url) or body)
    got = au.fetch_expected_sha256("v9.9.9", name="persona-windows-setup.exe")
    assert got == "bbbb2222"
    assert "releases/download/v9.9.9/checksums.txt" in urls[0]


def test_fetch_expected_sha256_retries_then_gives_up(monkeypatch):
    calls = {"n": 0}

    def curl(url, **k):
        calls["n"] += 1
        return ""

    monkeypatch.setattr(au, "_curl_get", curl)
    assert au.fetch_expected_sha256("v9.9.9", name="x.exe") == ""
    assert calls["n"] > 1  # a transient fetch failure is retried


def test_fetch_expected_sha256_empty_when_asset_not_listed(monkeypatch):
    # asset not in checksums.txt AND no sidecar → empty
    def curl(url, **k):
        if url.endswith("checksums.txt"):
            return "aaaa  other-file.zip\n"
        return ""  # no sidecar
    monkeypatch.setattr(au, "_curl_get", curl)
    assert au.fetch_expected_sha256("v9.9.9", name="persona-windows-setup.exe") == ""


def test_fetch_expected_sha256_reads_sidecar_for_mac_linux(monkeypatch):
    # #6 (audit4 HIGH): mac dmg / linux AppImage aren't in checksums.txt; their
    # hash lives in a per-asset {asset}.sha256 sidecar. The updater must read it,
    # else verify falls open on mac/linux (RCE via a swapped installer).
    def curl(url, **k):
        if url.endswith("checksums.txt"):
            return "aaaa  persona-windows-setup.exe\n"  # asset not here
        if url.endswith("persona-x86_64.AppImage.sha256"):
            return "deadbeefcafe  persona-x86_64.AppImage\n"
        return ""
    monkeypatch.setattr(au, "_curl_get", curl)
    got = au.fetch_expected_sha256("v9.9.9", name="persona-x86_64.AppImage")
    assert got == "deadbeefcafe"


def test_fetch_expected_sha256_checksums_txt_still_wins_for_windows(monkeypatch):
    # the windows exe hash still comes from the combined checksums.txt
    def curl(url, **k):
        if url.endswith("checksums.txt"):
            return "1234abcd  persona-windows-setup.exe\n"
        return ""
    monkeypatch.setattr(au, "_curl_get", curl)
    got = au.fetch_expected_sha256("v9.9.9", name="persona-windows-setup.exe")
    assert got == "1234abcd"


# --- verify_staged_installer ---


def test_verify_passes_on_matching_sha256(monkeypatch, tmp_path):
    staged, digest = _write_staged(tmp_path)
    monkeypatch.setattr(au, "fetch_expected_sha256", lambda tag, **k: digest)
    msgs = []
    assert au.verify_staged_installer(str(staged), log=msgs.append) is True


def test_verify_refuses_on_mismatching_sha256(monkeypatch, tmp_path):
    staged, _ = _write_staged(tmp_path)
    monkeypatch.setattr(au, "fetch_expected_sha256", lambda tag, **k: "0" * 64)
    msgs = []
    assert au.verify_staged_installer(str(staged), log=msgs.append) is False
    assert any("checksum" in m.lower() for m in msgs)


def test_verify_refuses_when_checksum_unavailable(monkeypatch, tmp_path):
    # PS-6: this used to pass (fall back to the download-time size check and
    # install anyway). A size check proves completeness, never integrity — it
    # cannot tell a truncated download from a substituted installer, which is
    # the fail-OPEN 5e00d66 and bfc7cbf were each reaching for. The policy is
    # now fail-CLOSED and shared with the engine updater: an installer we can't
    # verify never runs, and the refusal is explained rather than silent.
    staged, _ = _write_staged(tmp_path)
    monkeypatch.setattr(au, "fetch_expected_sha256", lambda tag, **k: "")
    msgs = []
    assert au.verify_staged_installer(str(staged), log=msgs.append) is False
    assert any("checksum" in m.lower() for m in msgs)
    # the operator is told WHY, not just that nothing happened
    assert any("refus" in m.lower() for m in msgs)


def test_verify_refuses_when_checksum_fetch_fails_transiently(monkeypatch, tmp_path):
    # PS-6, the accepted consequence stated explicitly so it isn't re-litigated
    # as a bug: fetch_expected_sha256 already retries and returns '' on
    # exhaustion, so a transient network failure is indistinguishable from an
    # absent checksum — and both refuse. Availability of an update is never
    # weighed against the integrity of what gets executed (Invariant #0).
    staged, digest = _write_staged(tmp_path)
    calls = {"n": 0}

    def flaky_fetch(tag, **k):
        calls["n"] += 1
        return ""  # every retry inside fetch_expected_sha256 already failed

    monkeypatch.setattr(au, "fetch_expected_sha256", flaky_fetch)
    assert au.verify_staged_installer(str(staged), log=lambda m: None) is False
    # and the very same staged file verifies once the digest IS reachable, so
    # the refusal is about the missing digest, not about the file
    monkeypatch.setattr(au, "fetch_expected_sha256", lambda tag, **k: digest)
    assert au.verify_staged_installer(str(staged), log=lambda m: None) is True


def test_verify_uses_tag_from_staged_filename(monkeypatch, tmp_path):
    staged, digest = _write_staged(tmp_path, tag="v2.4.0")
    seen = {}

    def fetch(tag, **k):
        seen["tag"] = tag
        return digest

    monkeypatch.setattr(au, "fetch_expected_sha256", fetch)
    assert au.verify_staged_installer(str(staged)) is True
    assert seen["tag"] == "v2.4.0"


def test_tag_recovered_from_linux_appimage_part():
    # audit7 #2: the Linux staged name (.persona-update-<tag>.AppImage.part) must
    # yield the real tag — it used to return '' → checksum lookup skipped → an
    # unverified AppImage ran.
    assert au._tag_from_staged(".persona-update-v2.9.14.AppImage.part") == "v2.9.14"
    assert au._tag_from_staged("/tmp/.persona-update-v2.9.14.AppImage.part") == "v2.9.14"
    # the tagless name still yields '' (no tag baked in)
    assert au._tag_from_staged(".persona-update.AppImage.part") == ""


def test_linux_appimage_verify_actually_checks_checksum(monkeypatch, tmp_path):
    # audit7 #2: a staged Linux AppImage with a recoverable tag must be sha256-
    # verified, not fall through to the fail-OPEN size-only branch. A mismatching
    # checksum must REFUSE (an unverified/substituted AppImage must never run).
    staged = tmp_path / ".persona-update-v2.9.14.AppImage.part"
    staged.write_bytes(b"attacker-substituted-appimage")
    seen = {}

    def fetch(tag, **k):
        seen["tag"] = tag
        return "0" * 64  # a real published checksum that WON'T match

    monkeypatch.setattr(au, "fetch_expected_sha256", fetch)
    msgs = []
    assert au.verify_staged_installer(str(staged), log=msgs.append) is False
    assert seen["tag"] == "v2.9.14", "tag must be recovered so the checksum is fetched"
    assert any("mismatch" in m.lower() for m in msgs)


# --- apply_and_restart (Windows path) must not launch a refused installer ---


def _force_windows(monkeypatch):
    monkeypatch.setattr(au._platform, "IS_WINDOWS", True)
    # These tests exercise the FULL-installer path; keep the #205 code-only fast
    # path out of the way (it would otherwise probe the real installed app.zip on
    # a Windows dev host and add a manifest-fetch curl before the installer).
    monkeypatch.setattr(au, "_try_windows_fast_update", lambda say: False)


def test_apply_refuses_to_launch_on_checksum_mismatch(monkeypatch, tmp_path):
    _force_windows(monkeypatch)
    staged, _ = _write_staged(tmp_path)
    monkeypatch.setattr(au, "fetch_expected_sha256", lambda tag, **k: "0" * 64)
    monkeypatch.setattr(
        au.subprocess, "Popen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("installer launched!")),
    )
    monkeypatch.setattr(
        au.os, "_exit", lambda *a: (_ for _ in ()).throw(AssertionError("exited!"))
    )
    msgs = []
    ok = au.apply_and_restart(str(staged), log=msgs.append)
    assert ok is False
    assert not staged.exists()  # corrupt file dropped so it re-downloads fresh
    assert any("checksum" in m.lower() for m in msgs)


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="exercises the real Windows apply_and_restart os._exit path",
)
def test_apply_launches_on_matching_checksum(monkeypatch, tmp_path):
    _force_windows(monkeypatch)
    staged, digest = _write_staged(tmp_path)
    monkeypatch.setattr(au, "fetch_expected_sha256", lambda tag, **k: digest)
    launched = []
    monkeypatch.setattr(au.subprocess, "Popen", lambda *a, **k: launched.append(a))
    monkeypatch.setattr(au, "_installed_windows_exe", lambda: "")

    class Exit(Exception):
        pass

    monkeypatch.setattr(au.os, "_exit", lambda *a: (_ for _ in ()).throw(Exit()))
    with pytest.raises(Exit):
        au.apply_and_restart(str(staged), log=lambda m: None)
    assert launched and str(staged) in launched[0][0]


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="exercises the real Windows apply_and_restart os._exit path",
)
def test_apply_refuses_to_launch_when_checksum_unavailable(monkeypatch, tmp_path):
    # PS-6: the apply-time twin of the verify flip. This used to LAUNCH the
    # installer when no checksum could be fetched. Under fail-closed the
    # installer must never start, the process must not exit into it, and the
    # unverifiable file is dropped so it re-downloads fresh.
    _force_windows(monkeypatch)
    staged, _ = _write_staged(tmp_path)
    monkeypatch.setattr(au, "fetch_expected_sha256", lambda tag, **k: "")
    monkeypatch.setattr(
        au.subprocess, "Popen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("installer launched!")),
    )
    monkeypatch.setattr(
        au.os, "_exit", lambda *a: (_ for _ in ()).throw(AssertionError("exited!"))
    )
    msgs = []
    assert au.apply_and_restart(str(staged), log=msgs.append) is False
    assert not staged.exists()
    assert any("checksum" in m.lower() for m in msgs)


# --- PS-6: ONE missing-checksum contract, asserted on BOTH sides ---


def test_app_and_engine_share_one_missing_checksum_contract():
    # The whole point of PS-6: the app path installed on a missing checksum
    # while the engine path refused, for the IDENTICAL situation. Both now read
    # the same policy from the same module, so the two can no longer drift —
    # this is the assertion that fails if either side grows its own copy again.
    from src.utils import httpdl
    from src.services.engine import updater as eu

    # no digest -> refuse, on both sides, through the one shared helper
    assert httpdl.verify_bytes(b"anything", "") is False
    assert httpdl.verify_file(__file__, "") is False
    assert eu.sha256_ok(b"anything", "") is False

    # the single opt-in (engine Linux predictable-URL fallback) is explicit,
    # per-call, and covers ONLY "there is no digest" — never "it didn't match"
    assert eu.sha256_ok(b"anything", "", allow_missing=True) is True
    assert eu.sha256_ok(b"anything", "deadbeef", allow_missing=True) is False
    assert httpdl.verify_bytes(b"anything", "deadbeef", allow_missing=True) is False


def test_allow_missing_does_not_cover_a_present_but_unusable_digest():
    # "no digest was published" and "a digest arrived but is unusable" are two
    # different facts, and the opt-in was granted for the FIRST one only.
    # normalize_digest() flattens "sha256:", ":" and "  x  " to "", so a policy
    # written as `if not normalize_digest(d): return allow_missing` hands a
    # malformed digest the opt-in exit and ACCEPTS it — fail-open under a new
    # name, and the same shape as bfc7cbf (a guard and the check it protects
    # disagreeing about the meaning of one word). These forms must be REFUSED
    # even under allow_missing.
    from src.utils import httpdl
    from src.services.engine import updater as eu

    # Whitespace-only is in this list ON PURPOSE. It is the row that makes the
    # obvious fix (`digest is None or not str(digest).strip()`) wrong: that
    # spelling calls "   " missing and hands it the opt-in, but on main "   "
    # fails CLOSED, so it would be a widening smuggled in by the fix for a
    # widening.
    for bad in ("sha256:", ":", "sha256: ", "sha256::", "   ", "\t\n"):
        # None of these is "nothing was published", so none may take the opt-in
        # exit — whatever they normalize to.
        assert httpdl.digest_missing(bad) is False, bad
        assert httpdl.digest_ok("deadbeef", bad, allow_missing=True) is False, bad
        assert httpdl.verify_bytes(b"anything", bad, allow_missing=True) is False, bad
        assert httpdl.verify_file(__file__, bad, allow_missing=True) is False, bad
        assert eu.sha256_ok(b"anything", bad, allow_missing=True) is False, bad

    # The specific trap this guards: these normalize to "" exactly like a digest
    # that was never published, which is why a policy keyed on normalize_digest()
    # hands them the opt-in. ("sha256::" is deliberately NOT here — it splits on
    # the FIRST colon and normalizes to ":", so it reaches the same refusal by
    # the compare instead. Both routes must refuse; only this one is the trap.)
    for flattens in ("sha256:", ":", "sha256: ", "   ", "\t\n"):
        assert httpdl.normalize_digest(flattens) == "", flattens

    # Only a genuinely absent digest keeps the opt-in. No caller in persona
    # passes allow_missing=True any more — PS-49 removed the one gate that did
    # (engine/updater.py's `allow_unverified = not digest and IS_LINUX`), so the
    # engine path now refuses an undigested asset on every OS. The distinction
    # is still pinned here because `digest_missing` is now the predicate the
    # engine REFUSAL itself is written in: the same one word decides whether an
    # operator is told "no digest was published" or an unusable digest is simply
    # rejected.
    for blank in ("", None):
        assert httpdl.digest_missing(blank) is True, blank
        assert httpdl.verify_bytes(b"anything", blank) is False, blank
        assert httpdl.verify_bytes(b"anything", blank, allow_missing=True) is True, blank


def test_app_verify_has_no_allow_missing_escape_hatch():
    # The refusal must not be re-openable by a caller: verify_staged_installer
    # deliberately exposes no allow_missing/force parameter, so there is no way
    # to ask the app path to install an unverified installer. Re-introducing
    # fail-open under a new name is the regression this ticket exists to prevent.
    import inspect

    params = inspect.signature(au.verify_staged_installer).parameters
    assert set(params) == {"staged", "tag", "log"}, params
