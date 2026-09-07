"""Windows code-only fast update (#205): most releases change only the app's
Python code (app.zip, ~1MB) — not the 218MB runtime the Inno installer
reinstalls. flet re-extracts app.zip on launch when its sha256 (app.zip.hash)
differs from the extracted marker, so swapping just those two files + a relaunch
is a seconds-fast update. The full installer stays the fallback for releases
that change the runtime/dependencies (gated by the manifest's
requires_full_install).
"""
import json
import os

import pytest

from src.services.app_update import fast_update as fu


def test_should_fast_update_true_when_manifest_allows():
    manifest = {
        "version": "2.6.0",
        "requires_full_install": False,
        "app_zip_sha256": "abc",
    }
    assert fu.should_fast_update(manifest, current="2.5.2") is True


def test_should_fast_update_false_when_requires_full_install():
    manifest = {"version": "2.6.0", "requires_full_install": True, "app_zip_sha256": "abc"}
    assert fu.should_fast_update(manifest, current="2.5.2") is False


def test_should_fast_update_false_when_not_newer():
    manifest = {"version": "2.5.2", "requires_full_install": False, "app_zip_sha256": "abc"}
    assert fu.should_fast_update(manifest, current="2.5.2") is False


def test_should_fast_update_false_without_sha():
    manifest = {"version": "2.6.0", "requires_full_install": False, "app_zip_sha256": ""}
    assert fu.should_fast_update(manifest, current="2.5.2") is False


def test_should_fast_update_false_on_empty_manifest():
    assert fu.should_fast_update({}, current="2.5.2") is False
    assert fu.should_fast_update(None, current="2.5.2") is False


def test_parse_manifest_reads_fields():
    body = json.dumps({
        "version": "2.6.0",
        "requires_full_install": False,
        "app_zip_sha256": "deadbeef",
    })
    m = fu.parse_manifest(body)
    assert m["version"] == "2.6.0"
    assert m["requires_full_install"] is False
    assert m["app_zip_sha256"] == "deadbeef"


def test_parse_manifest_bad_json_returns_none():
    assert fu.parse_manifest("not json") is None
    assert fu.parse_manifest("") is None


def test_install_app_zip_paths_finds_flutter_assets(tmp_path, monkeypatch):
    # the install dir holds data/flutter_assets/app/app.zip + app.zip.hash
    app_dir = tmp_path / "data" / "flutter_assets" / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "app.zip").write_bytes(b"code")
    (app_dir / "app.zip.hash").write_text("h", encoding="utf-8")
    monkeypatch.setattr(fu, "_install_root", lambda: str(tmp_path))
    zip_path, hash_path = fu.install_app_zip_paths()
    assert zip_path.endswith("app.zip")
    assert hash_path.endswith("app.zip.hash")
    assert zip_path != hash_path
    assert os.path.isfile(zip_path) and os.path.isfile(hash_path)


def test_install_app_zip_paths_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(fu, "_install_root", lambda: str(tmp_path))
    assert fu.install_app_zip_paths() == (None, None)


def test_sha256_of_bytes_matches_hashlib():
    # httpdl is where the ONE hashing helper lives (PS-6); assert it there
    # rather than through a re-export on this module, so a dead-import pass over
    # fast_update.py can't quietly break this test.
    import hashlib
    from src.utils import httpdl
    data = b"persona code"
    assert httpdl.sha256_bytes(data) == hashlib.sha256(data).hexdigest()


def test_manifest_and_appzip_urls_from_assets():
    assets = [
        {"name": "persona-windows-setup.exe", "browser_download_url": "u1"},
        {"name": "update-manifest.json", "browser_download_url": "u2"},
        {"name": "app.zip", "browser_download_url": "u3"},
    ]
    murl, zurl = fu.manifest_and_appzip_urls(assets)
    assert murl == "u2"
    assert zurl == "u3"


def test_manifest_and_appzip_urls_empty_when_absent():
    assert fu.manifest_and_appzip_urls([]) == ("", "")


def test_can_fast_update_false_off_windows(monkeypatch):
    monkeypatch.setattr(fu._platform, "IS_WINDOWS", False)
    assert fu.can_fast_update() is False


def test_apply_code_only_noop_off_windows(monkeypatch):
    monkeypatch.setattr(fu._platform, "IS_WINDOWS", False)
    assert fu.apply_code_only_and_restart("u", "sha") is False


def test_try_windows_fast_update_declines_when_requires_full(monkeypatch):
    # updater's decision gate: manifest requires_full_install → fall back to the
    # full installer (return False), never touch the code-only path.
    from src.services.app_update import updater as au
    import src.services.app_update.fast_update as fu2

    monkeypatch.setattr(fu2, "can_fast_update", lambda: True)
    monkeypatch.setattr(au, "APP_REPO", "amnesiadevelopment/persona")
    monkeypatch.setattr(au, "latest_tag", lambda timeout=30: "v9.9.9")
    monkeypatch.setattr(au, "update_available", lambda tag: True)
    # the manifest (fetched via _curl_get) says requires_full_install
    monkeypatch.setattr(
        au, "_curl_get",
        lambda *a, **k: '{"version":"9.9.9","requires_full_install":true,"app_zip_sha256":"x"}',
    )
    assert au._try_windows_fast_update(lambda *a: None) is False


def test_try_windows_fast_update_takes_fast_path(monkeypatch):
    from src.services.app_update import updater as au
    import src.services.app_update.fast_update as fu2

    monkeypatch.setattr(fu2, "can_fast_update", lambda: True)
    monkeypatch.setattr(au, "APP_REPO", "amnesiadevelopment/persona")
    monkeypatch.setattr(au, "latest_tag", lambda timeout=30: "v9.9.9")
    monkeypatch.setattr(au, "update_available", lambda tag: True)
    monkeypatch.setattr(
        au, "_curl_get",
        lambda *a, **k: '{"version":"9.9.9","requires_full_install":false,"app_zip_sha256":"deadbeef"}',
    )
    applied = {}
    def fake_apply(url, sha, log=None):
        applied["url"] = url
        applied["sha"] = sha
        return True  # pretend it swapped + exited
    monkeypatch.setattr(fu2, "apply_code_only_and_restart", fake_apply)
    assert au._try_windows_fast_update(lambda *a: None) is True
    # deterministic app.zip URL for the resolved tag
    assert applied["url"].endswith("/releases/download/v9.9.9/app.zip")
    assert applied["sha"] == "deadbeef"


def test_fastswap_bat_waits_a_real_beat_before_confirming_launch(tmp_path):
    # #229: after `start`, the new persona re-extracts app.zip behind its boot
    # screen before persona.exe registers in tasklist — several seconds. A
    # near-instant recheck saw "not up yet" and re-launched, spawning a second
    # instance that raced the extraction (one won, the other died: "reopened
    # then closed again quickly"). The confirm must sleep a real ~3s beat first,
    # use its OWN bounded counter (not the wait loop's `tries`, already near its
    # cap), and re-launch only a handful of times.
    exe = tmp_path / "persona.exe"
    exe.write_bytes(b"MZ")
    path = fu._write_appzip_swap_bat(
        str(exe),
        str(tmp_path / "new.zip"),
        str(tmp_path / "new.hash"),
        str(tmp_path / "dst.zip"),
        str(tmp_path / "dst.hash"),
        4242,
    )
    try:
        with open(path, encoding="ascii", newline="") as f:
            bat = f.read()
    finally:
        os.remove(path)
    launch = bat.split(":launch")[1]
    # a real ~3s wait before the liveness check, not a near-instant ping
    assert "ping -n 4 " in launch
    # re-launch is bounded by a SEPARATE counter, tightly, so a genuine failure
    # can't spawn a fistful of persona processes
    assert "boots" in bat
    assert "lss 5" in launch
    assert "lss 930" not in bat


def test_swap_bat_purges_flet_extraction(tmp_path):
    # #12 (audit3): the fast-update swap .bat must purge the stale flet\app
    # extraction (like the full installer) so the new persona re-extracts
    # cleanly instead of racing a straggler handle → errno-32 white screen (#195).
    from src.services.app_update.fast_update import _write_appzip_swap_bat

    bat_path = _write_appzip_swap_bat(
        exe=r"C:\Users\x\AppData\Local\persona\persona.exe",
        new_zip=str(tmp_path / "new.zip"),
        new_hash=str(tmp_path / "new.hash"),
        dst_zip=r"C:\dst\app.zip",
        dst_hash=r"C:\dst\app.zip.hash",
        old_pid=1234,
    )
    import pathlib
    content = pathlib.Path(bat_path).read_text(encoding="utf-8")
    assert r"flet\app" in content
    assert "rd /s /q" in content
    # bounded so a never-dying holder can't block the relaunch forever
    assert "purges" in content
    pathlib.Path(bat_path).unlink()


# ---------------------------------------------------------------------------
# PS-80: a way back from a release that verifies but does not boot.
#
# Before the swap the working code exists twice (install-dir app.zip + the flet
# extraction) and the script destroys both in consecutive steps, while the
# updater that would fetch a fix ships INSIDE app.zip. So the swap retains the
# previous pair and the script can put it back.
#
# The emitters are pure string builders, but AC1/AC4 are claims about FILES ON
# DISK, not about script text — so rather than grepping the .bat for a
# filename, these tests parse the REAL emitted script and execute its file-op
# lines against a real temp install dir. Executing the whole script needs
# Windows (cmd, tasklist, start); what it does to the FILES is asserted here.
# ---------------------------------------------------------------------------
import re

_IF_EXIST = re.compile(r'^if exist "([^"]+)"\s+(.*)$', re.I)
_MOVE = re.compile(r'^move /Y "([^"]+)" "([^"]+)"', re.I)
_COPY = re.compile(r'^copy /Y "([^"]+)" "([^"]+)"', re.I)
_DEL = re.compile(r'^del /F /Q "([^"]+)"', re.I)


def _section(bat: str, label: str) -> "list[str]":
    """The emitted lines under :label, up to the next label."""
    lines = bat.replace("\r\n", "\n").split("\n")
    start = lines.index(f":{label}")
    out = []
    for line in lines[start + 1:]:
        if line.startswith(":"):
            break
        out.append(line)
    return out


def _run_section(bat: str, label: str) -> int:
    """Execute the file operations cmd would run under :label, for real.

    Returns how many `start` (launch) lines the section carries. Anything that
    needs Windows itself (tasklist/ping/goto/set) is not modelled — those are
    asserted as script text, not executed.
    """
    launches = 0
    for raw in _section(bat, label):
        line = raw.strip()
        if not line:
            continue
        if line.lower().startswith("start "):
            launches += 1
            continue
        m = _IF_EXIST.match(line)
        if m:
            guard, line = m.group(1), m.group(2).strip()
            if not os.path.exists(guard):
                continue  # `if exist` false — cmd skips the line
        m = _MOVE.match(line)
        if m:
            os.replace(m.group(1), m.group(2))
            continue
        m = _COPY.match(line)
        if m:
            with open(m.group(1), "rb") as src, open(m.group(2), "wb") as dst:
                dst.write(src.read())
            continue
        m = _DEL.match(line)
        if m:
            os.remove(m.group(1))
            continue
    return launches


def _labels(bat: str) -> "list[str]":
    """Every label in the emitted script, in order."""
    return [ln[1:] for ln in bat.replace("\r\n", "\n").split("\n")
            if ln.startswith(":") and not ln.startswith("::")]


def _goto_targets(bat: str, label: str) -> "list[str]":
    """Every label the section under :label can jump to (conditional or not)."""
    out = []
    for line in _section(bat, label):
        m = re.search(r"\bgoto\s+(\w+)", line.strip(), re.I)
        if m:
            out.append(m.group(1))
    return out


def _falls_through(bat: str, label: str) -> bool:
    """True when control can drop off the end of :label into the next label —
    i.e. its last meaningful line is not an unconditional `goto`."""
    body = [ln.strip() for ln in _section(bat, label) if ln.strip()]
    return not (body and re.fullmatch(r"goto\s+\w+", body[-1], re.I))


def _route(bat: str, start: str) -> "list[str]":
    """Every section reachable from :start, in emitted order, stopping at :done.

    A route rather than a single section, because a `start` is no longer
    necessarily in the section a `goto` names — the recovery arm reaches its
    launch through its own purge, exactly as the normal one does.
    """
    labels = _labels(bat)
    seen, stack = set(), [start]
    while stack:
        label = stack.pop()
        if label in seen or label == "done" or label not in labels:
            continue
        seen.add(label)
        stack.extend(_goto_targets(bat, label))
        if _falls_through(bat, label):
            nxt = labels[labels.index(label) + 1:]
            if nxt:
                stack.append(nxt[0])
    return [ln for ln in labels if ln in seen]


def _purge_exits(bat: str) -> "set[str]":
    """The labels that a bounded purge block hands control to. A section is a
    purge block when it carries the `rd /s /q` of the flet extraction."""
    exits = set()
    for label in _labels(bat):
        body = "\n".join(_section(bat, label))
        if "rd /s /q" in body:
            exits.update(t for t in _goto_targets(bat, label) if t != label)
    return exits


def _install_dir(tmp_path):
    """A real install dir holding the CURRENT (working) release, plus a staged
    new release in a temp dir — the state the swap runs against."""
    app = tmp_path / "app"
    app.mkdir()
    dst_zip = app / "app.zip"
    dst_hash = app / "app.zip.hash"
    dst_zip.write_bytes(b"WORKING-RELEASE-CODE")
    dst_hash.write_text("oldsha", encoding="utf-8")
    staged = tmp_path / "staged"
    staged.mkdir()
    new_zip = staged / "persona-fast-app.zip"
    new_hash = staged / "persona-fast-app.zip.hash"
    new_zip.write_bytes(b"NEW-RELEASE-CODE")
    new_hash.write_text("newsha", encoding="utf-8")
    return dst_zip, dst_hash, new_zip, new_hash


def _emit(tmp_path, dst_zip, dst_hash, new_zip, new_hash) -> str:
    exe = tmp_path / "persona.exe"
    exe.write_bytes(b"MZ")
    path = fu._write_appzip_swap_bat(
        str(exe), str(new_zip), str(new_hash),
        str(dst_zip), str(dst_hash), 4242,
    )
    try:
        with open(path, encoding="ascii", newline="") as f:
            return f.read()
    finally:
        os.remove(path)


def test_swap_retains_previous_appzip_in_install_dir(tmp_path):
    # AC1 + AC7: after the swap the NEW pair is live and the PREVIOUS pair is
    # still on disk in the install dir. On main today the stage is two bare
    # `copy /Y` lines, so the old bytes are gone and this is RED.
    dst_zip, dst_hash, new_zip, new_hash = _install_dir(tmp_path)
    bat = _emit(tmp_path, dst_zip, dst_hash, new_zip, new_hash)

    _run_section(bat, "swap")

    # the new release is live
    assert dst_zip.read_bytes() == b"NEW-RELEASE-CODE"
    assert dst_hash.read_text(encoding="utf-8") == "newsha"
    # and the working one it replaced still EXISTS, beside it, on the same volume
    prev_zip, prev_hash = fu.retained_paths(str(dst_zip), str(dst_hash))
    assert os.path.isfile(prev_zip), "previous app.zip was destroyed by the swap"
    assert os.path.isfile(prev_hash)
    assert open(prev_zip, "rb").read() == b"WORKING-RELEASE-CODE"
    assert open(prev_hash, encoding="utf-8").read() == "oldsha"
    # retained INSIDE the install dir (survives a reboot; atomic rename), not %TEMP%
    assert os.path.dirname(prev_zip) == os.path.dirname(str(dst_zip))


def test_exhausted_launch_budget_restores_and_relaunches_previous(tmp_path):
    # AC3: when the post-swap confirm fails its FULL retry budget, the retained
    # pair is put back and a launch is attempted from it — instead of falling
    # through to a dead install whose own updater shipped inside app.zip.
    dst_zip, dst_hash, new_zip, new_hash = _install_dir(tmp_path)
    bat = _emit(tmp_path, dst_zip, dst_hash, new_zip, new_hash)
    _run_section(bat, "swap")

    # the emitted control flow: a spent budget goes to the restore arm
    launch = bat.split(":launch")[1]
    assert "if %boots% lss 5 goto launch" in launch
    assert "goto recover" in launch, "spent launch budget does not reach a restore arm"

    _run_section(bat, "recover")

    # the working release is live again, ON DISK
    assert dst_zip.read_bytes() == b"WORKING-RELEASE-CODE"
    # the hash goes back WITH the zip — that mismatch is what makes flet
    # re-extract, since its marker now records the failed release
    assert dst_hash.read_text(encoding="utf-8") == "oldsha"
    # and a launch is actually attempted from the restored pair. The recovery
    # route reaches its `start` through its own purge (see the launch-purge
    # reachability test below), so count the starts over the whole route.
    launches = sum(_run_section(bat, s) for s in _route(bat, "recover"))
    assert launches == 1, "restored the previous release but never launched it"


def test_confirmed_boot_leaves_the_retained_pair(tmp_path):
    # AC3, and this test is the REWRITE of
    # `test_confirmed_boot_leaves_no_retained_pair`, which asserted the exact
    # opposite. That is deliberate and is the ticket, not a casualty of it.
    #
    # PS-80's AC4 recorded a decision — "a confirmed-good boot leaves no
    # retained pair — one previous version, replaced per update, never
    # accumulating" — and the deletion it pinned is removed here. The argument,
    # searched for a counter-reason at implementation time and found to have
    # none (`git log -S"drop_retained_steps"` and `-S"confirm_body"` each
    # return exactly one commit, the feature's own; every "accumulat" hit in
    # the module states the non-accumulation GOAL and none argues a retained
    # pair is itself harmful):
    #
    #   * AC4's stated goal — NON-ACCUMULATION — is met identically either
    #     way, and the test below this one proves it on files rather than
    #     asserting it. `stage_steps` already `move`s the live pair OVER the
    #     retained one, so the next update replaces it. The bound is DEPTH, not
    #     duration — the same policy updater.py states for the macOS .app and
    #     engine_install.py states for the Firefox engine.
    #   * What the deletion cost was the only artifact a Windows revert could
    #     have used. The recovery arm consumes the pair for a release that will
    #     not come up AT ALL; the success arm then destroyed it for the case
    #     that matters most — a release that boots, registers in tasklist, and
    #     is broken. rollback_target() had no Windows arm because there was
    #     nothing on disk for one to resolve.
    #   * The cost of keeping it is one app.zip + hash (~1MB), against the
    #     ~200MB .app macOS retains and the AppImage Linux retains, both
    #     indefinitely.
    dst_zip, dst_hash, new_zip, new_hash = _install_dir(tmp_path)
    bat = _emit(tmp_path, dst_zip, dst_hash, new_zip, new_hash)
    _run_section(bat, "swap")
    prev_zip, prev_hash = fu.retained_paths(str(dst_zip), str(dst_hash))
    assert os.path.isfile(prev_zip)

    # There is no longer a success arm at all — the confirm goes straight to
    # :done — so the pair survives the boot by construction rather than by a
    # cleanup that happens to be skipped.
    assert "confirmed" not in _labels(bat), (
        "a success arm is emitted again — the retained pair has something that "
        "can delete it"
    )
    assert "if not errorlevel 1 goto done" in bat

    # ON DISK, after the boot the script confirms: the way back is still there.
    assert os.path.isfile(prev_zip), "the confirmed boot destroyed the way back"
    assert os.path.isfile(prev_hash)
    assert open(prev_zip, "rb").read() == b"WORKING-RELEASE-CODE"
    assert open(prev_hash, encoding="utf-8").read() == "oldsha"
    # the release that booted stays live and untouched
    assert dst_zip.read_bytes() == b"NEW-RELEASE-CODE"
    assert dst_hash.read_text(encoding="utf-8") == "newsha"


def test_a_second_update_replaces_the_retained_pair_rather_than_adding_one(
    tmp_path,
):
    # AC3's other half, and the one that actually keeps PS-80's AC4 GOAL. The
    # non-accumulation guarantee is not deleted here, it changes MECHANISM:
    # from delete-on-confirm to replace-on-next-update. Asserted by running two
    # real updates back to back against one install dir and counting what is
    # on disk — never by reading the emitted text.
    dst_zip, dst_hash, new_zip, new_hash = _install_dir(tmp_path)
    app_dir = os.path.dirname(str(dst_zip))

    # update 1: WORKING -> NEW
    bat = _emit(tmp_path, dst_zip, dst_hash, new_zip, new_hash)
    _run_section(bat, "swap")

    # update 2: NEW -> NEWER, from the same install dir the first one left
    newer_zip = tmp_path / "staged" / "newer.zip"
    newer_hash = tmp_path / "staged" / "newer.zip.hash"
    newer_zip.write_bytes(b"NEWER-RELEASE-CODE")
    newer_hash.write_text("newersha", encoding="utf-8")
    bat2 = _emit(tmp_path, dst_zip, dst_hash, newer_zip, newer_hash)
    _run_section(bat2, "swap")

    # EXACTLY ONE retained pair — the previous release's, not the one before it
    retained = sorted(n for n in os.listdir(app_dir)
                      if n.endswith(fu.RETAINED_SUFFIX))
    assert retained == ["app.zip.hash.prev", "app.zip.prev"], (
        f"retention accumulated across updates: {sorted(os.listdir(app_dir))}"
    )
    prev_zip, prev_hash = fu.retained_paths(str(dst_zip), str(dst_hash))
    assert open(prev_zip, "rb").read() == b"NEW-RELEASE-CODE", (
        "the retained pair is not the version this update replaced"
    )
    assert open(prev_hash, encoding="utf-8").read() == "newsha"
    # and the newest release is live
    assert dst_zip.read_bytes() == b"NEWER-RELEASE-CODE"
    assert dst_hash.read_text(encoding="utf-8") == "newersha"


def test_failed_boot_still_reaches_the_restore_arm(tmp_path):
    # THE REWRITE of `test_failed_boot_never_runs_the_confirmed_cleanup`, whose
    # hazard was that :confirmed sat between the confirm and :done and a
    # fall-through would delete exactly what recovery needs. With no success
    # arm that hazard cannot exist — but the ROUTING it guarded must be
    # asserted, not reasoned about, because build_bat's `exhausted_jump`
    # branches on WHICH arms are present and dropping one changes that branch.
    dst_zip, dst_hash, new_zip, new_hash = _install_dir(tmp_path)
    bat = _emit(tmp_path, dst_zip, dst_hash, new_zip, new_hash)

    # a good boot ends the script; nothing runs between the confirm and :done
    assert "if not errorlevel 1 goto done" in bat
    assert "confirmed" not in _labels(bat)
    # a spent launch budget still reaches the restore arm — the property the
    # old test's `goto recover` assertion pinned, unchanged by the removal
    launch = bat.split(":launch")[1]
    assert "if %boots% lss 5 goto launch" in launch
    assert "goto recover" in launch, (
        "the spent launch budget no longer reaches the restore arm"
    )
    # and the restore arm still ends by leaving rather than falling anywhere
    recover = bat.split(":recover")[1]
    assert "goto done" in recover
    # nothing in the script deletes a retained file any more
    assert ".prev" in bat, "the retention itself vanished"
    assert "del /F /Q" not in bat.split(":swap")[1].split(":done")[0], (
        "something still deletes files after the swap"
    )


def test_step_order_is_still_wait_stage_purge_launch(tmp_path):
    # AC6: the retain/restore work must not have reordered the script. Every
    # step of this order was a bug once (build_bat's docstring).
    dst_zip, dst_hash, new_zip, new_hash = _install_dir(tmp_path)
    bat = _emit(tmp_path, dst_zip, dst_hash, new_zip, new_hash)
    assert bat.index(":wait") < bat.index(":swap") < bat.index(":purge") < bat.index(":launch")
    # the purge is still there and still bounded
    assert "rd /s /q" in bat and "purges" in bat


def test_every_launch_is_reached_through_a_purge(tmp_path):
    # THE BLOCKER, as a reachability property rather than an emit order.
    #
    # :launch carries no purge of its own — it gets away with that only because
    # every route into it passes through :purge first. `goto recover` added a
    # SECOND route to a `start`, and a restore-then-start arm bypassed the
    # purge entirely: it handed the extraction to the new persona's bootstrap,
    # whose delete runs before any of our Python and therefore CANNOT retry
    # (#195) — on the one path that exists because everything else has failed.
    #
    # The step-order test above stays green against that bug (it pins where
    # :purge is EMITTED, never whether a launch is REACHED through one), which
    # is exactly why this asserts reachability instead.
    dst_zip, dst_hash, new_zip, new_hash = _install_dir(tmp_path)
    bat = _emit(tmp_path, dst_zip, dst_hash, new_zip, new_hash)

    launch_sections = [lbl for lbl in _labels(bat)
                       if any(ln.strip().lower().startswith("start ")
                              for ln in _section(bat, lbl))]
    # the normal route and the recovery route
    assert len(launch_sections) == 2, f"unexpected launch sections: {launch_sections}"

    # each one is a bounded purge block's exit target, so no `start` can run
    # against an extraction that was never cleared
    exits = _purge_exits(bat)
    for label in launch_sections:
        assert label in exits, (
            f":{label} carries a `start` that no purge block hands control to — "
            "the bootstrap's non-retrying delete gets a stale extraction (#195)"
        )


def test_recovery_purge_is_bounded_on_its_own_counter(tmp_path):
    # The recovery purge must not inherit a counter the normal route already
    # spent: by the time `goto recover` fires, the boots budget is gone and
    # `purges` may well be at its cap, which would make a shared counter fall
    # straight through to the launch without deleting anything.
    dst_zip, dst_hash, new_zip, new_hash = _install_dir(tmp_path)
    bat = _emit(tmp_path, dst_zip, dst_hash, new_zip, new_hash)

    route = _route(bat, "recover")
    purges = [lbl for lbl in route if "rd /s /q" in "\n".join(_section(bat, lbl))]
    assert purges, "the recovery route reaches a launch with no purge on it"

    body = "\n".join(_section(bat, purges[0]))
    counters = set(re.findall(r"set /a (\w+)\+=1", body))
    assert counters, "the recovery purge retries without a bound"
    counter = counters.pop()
    assert counter != "purges", "recovery purge shares the normal route's spent counter"
    # bounded, and reset on entry so it starts from a full budget
    assert re.search(rf"if %{counter}% geq \d+ goto", body), "recovery purge is unbounded"
    assert f"set {counter}=0" in bat, f"{counter} is never reset before the recovery purge"


def test_recovery_route_launches_exactly_once(tmp_path):
    # #229 is a multi-instance race, so the last-resort route must not spawn a
    # fistful either. This is also what rules out the naive fix for the blocker:
    # a bare `goto purge` would re-enter :launch with `boots` already at its cap,
    # fail the confirm, and `goto recover` a SECOND time — restoring nothing
    # (the `if exist` guards no-op) and firing a stray extra `start`.
    dst_zip, dst_hash, new_zip, new_hash = _install_dir(tmp_path)
    bat = _emit(tmp_path, dst_zip, dst_hash, new_zip, new_hash)
    _run_section(bat, "swap")

    route = _route(bat, "recover")
    # the recovery route must not loop back into the exhausted confirm/retry
    assert "launch" not in route, (
        f"recovery re-enters the spent launch loop: {route}"
    )
    assert sum(_run_section(bat, lbl) for lbl in route) == 1

    # and the restore still happened, on disk, on that same route
    assert dst_zip.read_bytes() == b"WORKING-RELEASE-CODE"
    assert dst_hash.read_text(encoding="utf-8") == "oldsha"


# ---------------------------------------------------------------------------
# PS-328: the OPERATOR-DRIVEN restore. The recovery arm above is automatic and
# fires only when a release will not come up AT ALL; this is the way back from
# a release that boots, registers in tasklist, and is broken — the case the
# confirm-time deletion used to make unreachable.
#
# Same discipline as everything above it: assert on FILES in a real temp
# install dir by executing the emitted script's file-op lines, never on batch
# text. Executing the whole script needs Windows (cmd, tasklist, start).
# ---------------------------------------------------------------------------


def _emit_restore(tmp_path, dst_zip, dst_hash) -> str:
    exe = tmp_path / "persona.exe"
    exe.write_bytes(b"MZ")
    path = fu._write_appzip_restore_bat(
        str(exe), str(dst_zip), str(dst_hash), 4242
    )
    try:
        with open(path, encoding="ascii", newline="") as f:
            return f.read()
    finally:
        os.remove(path)


def test_the_restore_script_puts_the_retained_pair_back_on_disk(tmp_path):
    # AC6 at the file level. BOTH halves go back together: the hash mismatch is
    # what makes flet re-extract, since its marker records the release being
    # reverted FROM (restore_steps' own docstring).
    dst_zip, dst_hash, new_zip, new_hash = _install_dir(tmp_path)
    swap = _emit(tmp_path, dst_zip, dst_hash, new_zip, new_hash)
    _run_section(swap, "swap")
    assert dst_zip.read_bytes() == b"NEW-RELEASE-CODE"  # the bad release is live

    bat = _emit_restore(tmp_path, dst_zip, dst_hash)
    _run_section(bat, "restore")

    assert dst_zip.read_bytes() == b"WORKING-RELEASE-CODE", (
        "the revert did not put the previous app.zip back"
    )
    assert dst_hash.read_text(encoding="utf-8") == "oldsha", (
        "the hash did not go back with the zip — flet will not re-extract"
    )
    # the retained pair is CONSUMED (move, not copy), so the reverted install
    # holds exactly one good pair and no leftovers
    prev_zip, prev_hash = fu.retained_paths(str(dst_zip), str(dst_hash))
    assert not os.path.exists(prev_zip)
    assert not os.path.exists(prev_hash)


def test_the_restore_script_launches_exactly_once_through_a_purge(tmp_path):
    # The restore is a THIRD caller of the shared generator, and step 3 of
    # build_bat's order is a property of every route to a launch: the restore
    # has just put the OLD zip + OLD hash back while the extraction on disk is
    # the NEW one, so the bootstrap WILL delete and re-extract — and that
    # delete is precisely the one that cannot retry (#195).
    dst_zip, dst_hash, _nz, _nh = _install_dir(tmp_path)
    bat = _emit_restore(tmp_path, dst_zip, dst_hash)

    launch_sections = [lbl for lbl in _labels(bat)
                       if any(ln.strip().lower().startswith("start ")
                              for ln in _section(bat, lbl))]
    assert len(launch_sections) == 1, launch_sections
    assert launch_sections[0] in _purge_exits(bat), (
        "the restore launches against an extraction no purge block cleared (#195)"
    )
    assert sum(_run_section(bat, lbl) for lbl in _route(bat, "restore")) == 1


def test_the_restore_script_is_stage_only_and_adds_no_arms(tmp_path):
    # TRAP 3: the generator is shared with the full installer, whose script
    # must stay byte-identical (PS-80's AC5). A stage-only caller adds no
    # labels beyond its own stage label — the cheapest shape the generator
    # emits — so this pins the label set rather than trusting it.
    dst_zip, dst_hash, _nz, _nh = _install_dir(tmp_path)
    bat = _emit_restore(tmp_path, dst_zip, dst_hash)

    assert _labels(bat) == [
        "wait", "hold", "restore", "purge", "launch", "done"
    ]
    # no recovery and no success arm: there is nothing to recover TO (the
    # retained pair is what this consumes) and nothing to drop on success
    assert "recover" not in bat
    assert "confirmed" not in bat
    # the wait is on THIS persona only, and the order still holds
    assert 'tasklist /FI "PID eq 4242"' in bat
    assert bat.index(":wait") < bat.index(":restore") < bat.index(":purge") \
        < bat.index(":launch")


def test_the_restore_script_is_ascii_and_silent(tmp_path):
    # cmd reads .bat in the OEM codepage and a live console during an update is
    # a reported defect — the same two constraints every emitted script here
    # carries.
    dst_zip, dst_hash, _nz, _nh = _install_dir(tmp_path)
    bat = _emit_restore(tmp_path, dst_zip, dst_hash)

    bat.encode("ascii")  # raises if a non-ASCII path leaked in
    assert bat.startswith("@echo off\r\n")
    for line in bat.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith(":") or stripped.startswith("if ") \
                or stripped.startswith("goto") or stripped.startswith("set "):
            continue
        if stripped.startswith("start ") or stripped.startswith("(goto)") \
                or stripped == "@echo off":
            continue
        assert ">nul" in stripped, f"unsilenced line: {stripped!r}"


def test_staging_a_restore_refuses_when_nothing_is_retained(tmp_path, monkeypatch):
    # The full-installer lane leaves no `.prev` pair (Inno upgrades in place
    # under a fixed AppId), so the revert must refuse rather than emit a script
    # whose `if exist` guards would all no-op into a relaunch of the SAME build.
    #
    # AND IT SAYS SO ITSELF. This arm used to be the one silent refusal, with
    # its message supplied by the caller — which fired that message on the
    # other three arms too, telling an operator whose %TEMP% was locked that
    # nothing was retained while the pair sat on disk. Each arm owning its own
    # reason is what makes "exactly one reason reaches the operator" true by
    # construction rather than by the caller guessing.
    dst_zip, dst_hash, _nz, _nh = _install_dir(tmp_path)
    monkeypatch.setattr(fu._platform, "IS_WINDOWS", True)
    monkeypatch.setattr(
        fu, "install_app_zip_paths", lambda: (str(dst_zip), str(dst_hash))
    )
    monkeypatch.setattr(fu.install_env, "installed_windows_exe", lambda: "p.exe")

    msgs: list[str] = []
    assert fu.stage_retained_restore(log=msgs.append) == ""
    assert msgs == [
        "Update: nothing to go back to — no previous version is retained."
    ], msgs


def test_staging_a_restore_refuses_on_a_half_retained_pair(tmp_path, monkeypatch):
    # A lone .prev zip is not a revert this can honour: without the hash going
    # back too, flet keeps the extraction of the release being reverted from.
    # The operator hears the same thing they hear when nothing is retained,
    # because from where they stand that is what it is — there is no pair to
    # go back to. Previously this arm was silent and the caller's blanket
    # message covered it by accident.
    dst_zip, dst_hash, _nz, _nh = _install_dir(tmp_path)
    prev_zip, _prev_hash = fu.retained_paths(str(dst_zip), str(dst_hash))
    with open(prev_zip, "wb") as f:
        f.write(b"WORKING-RELEASE-CODE")
    monkeypatch.setattr(fu._platform, "IS_WINDOWS", True)
    monkeypatch.setattr(
        fu, "install_app_zip_paths", lambda: (str(dst_zip), str(dst_hash))
    )
    monkeypatch.setattr(fu.install_env, "installed_windows_exe", lambda: "p.exe")

    msgs: list[str] = []
    assert fu.stage_retained_restore(log=msgs.append) == ""
    assert any("nothing to go back to" in m.lower() for m in msgs), msgs
