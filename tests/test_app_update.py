import os

import pytest

from src.services.app_update import updater as au


def test_app_version_is_set():
    assert au.APP_VERSION
    assert isinstance(au.APP_VERSION, str)


def test_update_available_true_when_remote_newer():
    assert au.update_available("0.2.0", "0.1.0") is True


def test_update_available_false_when_same_or_older():
    assert au.update_available("0.1.0", "0.1.0") is False
    assert au.update_available("0.1.0", "0.2.0") is False


def test_update_available_false_when_no_remote():
    assert au.update_available("", "0.1.0") is False


def test_release_url_built_from_configured_repo(monkeypatch):
    monkeypatch.setattr(au, "APP_REPO", "someone/persona")
    assert au.releases_api() == (
        "https://api.github.com/repos/someone/persona/releases/latest"
    )


def test_release_url_empty_when_repo_unconfigured(monkeypatch):
    monkeypatch.setattr(au, "APP_REPO", "")
    assert au.releases_api() == ""


def test_check_returns_none_when_repo_unconfigured(monkeypatch):
    # no GitHub repo set yet -> check is a no-op, never crashes
    monkeypatch.setattr(au, "APP_REPO", "")
    assert au.check_for_update() == ("", "", 0)


def _force_os(monkeypatch, *, win=False, mac=False, linux=False):
    monkeypatch.setattr(au._platform, "IS_WINDOWS", win)
    monkeypatch.setattr(au._platform, "IS_MACOS", mac)
    monkeypatch.setattr(au._platform, "IS_LINUX", linux)


_ASSETS = [
    {"name": "persona-windows-setup.exe", "browser_download_url": "uwin"},
    {"name": "persona-x86_64.AppImage", "browser_download_url": "ulin"},
    {"name": "persona-macos.dmg", "browser_download_url": "umac"},
]


def test_pick_asset_linux(monkeypatch):
    _force_os(monkeypatch, linux=True)
    assert au.pick_asset(_ASSETS) == ("ulin", 0)


def test_pick_asset_windows(monkeypatch):
    _force_os(monkeypatch, win=True)
    assert au.pick_asset(_ASSETS) == ("uwin", 0)


def test_pick_asset_macos(monkeypatch):
    _force_os(monkeypatch, mac=True)
    assert au.pick_asset(_ASSETS) == ("umac", 0)


def test_pick_asset_none_when_os_asset_absent(monkeypatch):
    _force_os(monkeypatch, linux=True)
    assert au.pick_asset(
        [{"name": "persona-windows-x64.zip", "browser_download_url": "u1"}]
    ) == ("", 0)


def test_asset_name_per_os(monkeypatch):
    _force_os(monkeypatch, win=True)
    assert au.asset_name() == "persona-windows-setup.exe"
    _force_os(monkeypatch, mac=True)
    assert au.asset_name() == "persona-macos.dmg"
    _force_os(monkeypatch, linux=True)
    assert au.asset_name() == "persona-x86_64.AppImage"


def test_staged_path_windows_uses_temp(monkeypatch):
    # On Windows the staged installer goes to a temp file (there's no $APPIMAGE
    # to sit next to), so downloading has somewhere to land.
    _force_os(monkeypatch, win=True)
    p = au.staged_path()
    assert p
    assert p.endswith(".exe")


def test_staged_path_is_keyed_by_tag(monkeypatch):
    # A per-tag filename keeps one version's download from resuming onto — or
    # being mistaken for — another's. This is the fix for "installed 2.3.4 but
    # stayed 2.3.3": a fixed name reused the stale 2.3.3 installer.
    _force_os(monkeypatch, win=True)
    p3 = au.staged_path("v2.3.3")
    p4 = au.staged_path("v2.3.4")
    assert p3 != p4
    assert "2.3.3" in p3 and "2.3.4" in p4
    assert p3.endswith(".exe") and p4.endswith(".exe")


def test_apply_and_restart_windows_runs_installer_silently(monkeypatch, tmp_path):
    # On Windows apply runs the downloaded setup.exe with NO windows (Inno
    # /VERYSILENT), then WE relaunch persona ourselves afterward (the installer's
    # own relaunch came up to a black window under a lowered token).
    _force_os(monkeypatch, win=True)
    staged = tmp_path / "persona-windows-setup.exe"
    staged.write_bytes(b"MZ")
    exe = tmp_path / "persona.exe"
    exe.write_bytes(b"MZ")
    calls = []

    def fake_popen(args, **kw):
        calls.append(args)

        class P:
            pass

        return P()

    monkeypatch.setattr(au.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(au._platform, "no_window_kwargs", lambda: {})
    monkeypatch.setattr(au.tempfile, "tempdir", str(tmp_path))
    # point the relaunch at our fake exe so the relaunch branch runs
    monkeypatch.setattr(au, "_installed_windows_exe", lambda: str(exe))

    def fake_exit(code):
        raise SystemExit(code)

    monkeypatch.setattr(au.os, "_exit", fake_exit)
    msgs = []
    with pytest.raises(SystemExit):
        au.apply_and_restart(str(staged), log=msgs.append)
    # 1) the installer was launched fully silently (no visible progress window)
    installer_call = next(c for c in calls if str(staged) in c)
    assert any(a.lower() == "/verysilent" for a in installer_call)
    # 2) persona is relaunched by US afterward (the installed exe, via a
    #    waiting cmd running the temp bat)
    relaunch_call = next(c for c in calls if c and c[0] == "cmd")
    assert relaunch_call[2].endswith(".bat")
    with open(relaunch_call[2], encoding="ascii", newline="") as f:
        assert str(exe) in f.read()


def test_windows_relaunch_has_single_merged_creationflags(monkeypatch, tmp_path):
    # The relaunch Popen once passed creationflags= AND spread no_window_kwargs()
    # (which also carries creationflags) — a TypeError at the call site, swallowed
    # by the except, so the app never reopened after an update (#133). The
    # relaunch must reach Popen with ONE merged creationflags: a hidden console
    # (NOT DETACHED_PROCESS — a cmd with no console at all wedges running a
    # batch file) in its own process group.
    _force_os(monkeypatch, win=True)
    NEW_GROUP, NO_WINDOW = 0x00000200, 0x08000000
    monkeypatch.setattr(
        au.subprocess, "CREATE_NEW_PROCESS_GROUP", NEW_GROUP, raising=False
    )
    monkeypatch.setattr(au.subprocess, "CREATE_NO_WINDOW", NO_WINDOW, raising=False)
    monkeypatch.setattr(au.tempfile, "tempdir", str(tmp_path))
    staged = tmp_path / "persona-windows-setup.exe"
    staged.write_bytes(b"MZ")
    exe = tmp_path / "persona.exe"
    exe.write_bytes(b"MZ")
    calls = []

    def fake_popen(args, **kw):
        calls.append((args, kw))

        class P:
            pass

        return P()

    monkeypatch.setattr(au.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(au, "_installed_windows_exe", lambda: str(exe))

    def fake_exit(code):
        raise SystemExit(code)

    monkeypatch.setattr(au.os, "_exit", fake_exit)
    msgs = []
    with pytest.raises(SystemExit):
        au.apply_and_restart(str(staged), log=msgs.append)
    assert not any("couldn't schedule the relaunch" in m for m in msgs), msgs
    relaunch_kw = next(kw for args, kw in calls if args and args[0] == "cmd")
    assert relaunch_kw["creationflags"] == NEW_GROUP | NO_WINDOW
    assert relaunch_kw.get("close_fds") is True


def test_windows_relaunch_waits_for_installer_to_exit(monkeypatch, tmp_path):
    # A fixed sleep before `start` raced slow installs (AV scan, slow disk): when
    # the install outlived it, `start` hit persona.exe mid-replace and died
    # silently — the app never reopened. The relaunch must instead run a temp
    # .bat that polls for the INSTALLER PID to disappear, then starts the exe.
    _force_os(monkeypatch, win=True)
    NEW_GROUP, NO_WINDOW = 0x00000200, 0x08000000
    monkeypatch.setattr(
        au.subprocess, "CREATE_NEW_PROCESS_GROUP", NEW_GROUP, raising=False
    )
    monkeypatch.setattr(au.subprocess, "CREATE_NO_WINDOW", NO_WINDOW, raising=False)
    monkeypatch.setattr(au.tempfile, "tempdir", str(tmp_path))
    staged = tmp_path / "persona-windows-setup.exe"
    staged.write_bytes(b"MZ")
    exe = tmp_path / "persona.exe"
    exe.write_bytes(b"MZ")
    calls = []

    def fake_popen(args, **kw):
        calls.append((args, kw))

        class P:
            pid = 4242

        return P()

    monkeypatch.setattr(au.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(au, "_installed_windows_exe", lambda: str(exe))
    monkeypatch.setattr(au.os, "getpid", lambda: 7777)

    def fake_exit(code):
        raise SystemExit(code)

    monkeypatch.setattr(au.os, "_exit", fake_exit)
    msgs = []
    with pytest.raises(SystemExit):
        au.apply_and_restart(str(staged), log=msgs.append)
    assert not any("couldn't schedule the relaunch" in m for m in msgs), msgs
    relaunch = next(
        (args, kw) for args, kw in calls if args and args[0] == "cmd"
    )
    args, kw = relaunch
    assert args[1] == "/c" and args[2].endswith(".bat")
    with open(args[2], encoding="ascii", newline="") as f:
        bat = f.read()
    # waits for the installer process, not a fixed number of seconds
    assert 'tasklist /FI "PID eq 4242"' in bat
    # bounded wait — a hung installer must not block the relaunch forever
    assert "goto launch" in bat
    # empty title + quoted path: `start` treats the first quoted token as a
    # window title, so a bare path with spaces would silently launch nothing
    assert f'start "" /D "{tmp_path}" "{exe}"' in bat
    # the bat removes itself once done
    assert 'del "%~f0"' in bat
    assert kw["creationflags"] == NEW_GROUP | NO_WINDOW
    assert kw.get("close_fds") is True


def test_windows_relaunch_waits_for_old_persona_to_die(monkeypatch, tmp_path):
    # Waiting for the INSTALLER alone raced the OLD persona's own teardown: the
    # new persona started while the dying one still held the flet app extraction
    # in %APPDATA%, so flet's delete-and-reextract failed with errno 32 and the
    # user got an "Error starting app" window. The bat must also wait for the
    # exiting persona's pid AND for every process with the exe's image name to
    # vanish, then settle briefly before `start`.
    _force_os(monkeypatch, win=True)
    monkeypatch.setattr(au._platform, "no_window_kwargs", lambda: {})
    monkeypatch.setattr(au.tempfile, "tempdir", str(tmp_path))
    staged = tmp_path / "persona-windows-setup.exe"
    staged.write_bytes(b"MZ")
    exe = tmp_path / "persona.exe"
    exe.write_bytes(b"MZ")
    calls = []

    def fake_popen(args, **kw):
        calls.append((args, kw))

        class P:
            pid = 4242

        return P()

    monkeypatch.setattr(au.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(au, "_installed_windows_exe", lambda: str(exe))
    monkeypatch.setattr(au.os, "getpid", lambda: 7777)

    def fake_exit(code):
        raise SystemExit(code)

    monkeypatch.setattr(au.os, "_exit", fake_exit)
    msgs = []
    with pytest.raises(SystemExit):
        au.apply_and_restart(str(staged), log=msgs.append)
    assert not any("couldn't schedule the relaunch" in m for m in msgs), msgs
    args, _kw = next((a, k) for a, k in calls if a and a[0] == "cmd")
    with open(args[2], encoding="ascii", newline="") as f:
        bat = f.read()
    # waits for the exiting persona's own pid, not just the installer's
    assert 'tasklist /FI "PID eq 7777"' in bat
    # and for lingering same-named processes (flet/Flutter stragglers)
    assert 'tasklist /FI "IMAGENAME eq persona.exe"' in bat
    # a settle pause sits between the wait loop and the launch, giving the OS a
    # beat to release handles after the last holder exits
    assert ":settle" in bat
    assert bat.index(":settle") < bat.index('start ""')
    # the wait stays bounded — no process check may block the relaunch forever
    assert "goto launch" in bat


def test_relaunch_bat_waits_for_installer_image_not_just_broker_pid(tmp_path):
    # #174: the setup.exe needs admin, so the Popen pid is only the
    # medium-integrity UAC broker — it exits the moment the user clicks Yes,
    # while the REAL elevated installer keeps running as a separate process.
    # Waiting on that pid relaunched the OLD persona mid-install and the
    # installer's /CLOSEAPPLICATIONS closed it right back: install succeeded,
    # persona never reopened. The bat must poll the installer's IMAGE NAMES
    # (the downloaded loader AND Inno's <name>.tmp second stage, which is the
    # process that actually runs elevated) until NONE remain, before :settle.
    exe = tmp_path / "persona.exe"
    exe.write_bytes(b"MZ")
    installer = tmp_path / "persona-windows-setup-v2.4.5.exe"
    path = au._write_relaunch_bat(str(exe), str(installer), 4242, 7777)
    try:
        with open(path, encoding="ascii", newline="") as f:
            bat = f.read()
    finally:
        au.os.remove(path)
    wait = bat.split(":settle")[0]
    # the whole installer family, by image name, at any integrity level
    assert '/C:"persona-windows-setup-v2.4.5.exe"' in wait
    assert '/C:"persona-windows-setup-v2.4.5.tmp"' in wait
    # the broker pid is still checked (harmless, and right while it lives)
    assert 'tasklist /FI "PID eq 4242"' in wait
    # the bound must cover the user staring at the UAC prompt plus the
    # install itself — ~5 minutes, not the old ~2
    assert "geq 300" in wait


def test_windows_relaunch_uses_bat_even_without_installer_pid(
    monkeypatch, tmp_path
):
    # #174: the image-name wait works with no pid at all, so a Popen that
    # yields no usable pid must still get the polling bat — the fixed ~14s
    # fallback can't cover UAC dwell time and is reserved for "bat unwritable".
    _force_os(monkeypatch, win=True)
    monkeypatch.setattr(au._platform, "no_window_kwargs", lambda: {})
    monkeypatch.setattr(au.tempfile, "tempdir", str(tmp_path))
    staged = tmp_path / "persona-windows-setup.exe"
    staged.write_bytes(b"MZ")
    exe = tmp_path / "persona.exe"
    exe.write_bytes(b"MZ")
    calls = []

    def fake_popen(args, **kw):
        calls.append((args, kw))

        class P:
            pid = None

        return P()

    monkeypatch.setattr(au.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(au, "_installed_windows_exe", lambda: str(exe))
    monkeypatch.setattr(au.os, "getpid", lambda: 7777)

    def fake_exit(code):
        raise SystemExit(code)

    monkeypatch.setattr(au.os, "_exit", fake_exit)
    msgs = []
    with pytest.raises(SystemExit):
        au.apply_and_restart(str(staged), log=msgs.append)
    assert not any("couldn't schedule the relaunch" in m for m in msgs), msgs
    args, _kw = next((a, k) for a, k in calls if a and a[0] == "cmd")
    assert args[2].endswith(".bat")
    with open(args[2], encoding="ascii", newline="") as f:
        bat = f.read()
    # no bogus "PID eq None" check, but the image-name wait is all there
    assert "None" not in bat
    assert 'tasklist /FI "PID eq 7777"' in bat
    assert '/C:"persona-windows-setup.exe"' in bat
    assert '/C:"persona-windows-setup.tmp"' in bat


def test_relaunch_bat_settle_is_brief(tmp_path):
    # #162: the settle between "everything exited" and the relaunch is one
    # ping beat (~1s), not more — the OS releases the dead processes' file
    # handles near-instantly, and every extra second here is a second the
    # user stares at nothing between the update closing and reopening. The
    # poll cadence stays at ~1s (cmd has no reliable sub-second sleep).
    exe = tmp_path / "persona.exe"
    exe.write_bytes(b"MZ")
    path = au._write_relaunch_bat(
        str(exe), str(tmp_path / "persona-windows-setup.exe"), 4242, 7777
    )
    try:
        with open(path, encoding="ascii", newline="") as f:
            bat = f.read()
    finally:
        au.os.remove(path)
    settle = bat.split(":settle")[1].split(":launch")[0]
    assert "ping -n 2 " in settle
    assert "ping -n 3" not in settle


def test_relaunch_bat_clears_the_flet_extraction_before_start(tmp_path):
    # #195, the un-retryable half: the new persona's bootstrap deletes
    # %APPDATA%\persona\persona\flet\app to re-extract the updated app.zip,
    # and that delete happens before our Python runs — it CANNOT retry. Any
    # handle still open under the dir at that instant (release lag, an AV
    # sweep, a straggler child of the old persona) is a startup crash on a
    # white screen. The bat clears the extraction itself, with bounded
    # retries, before `start`: the new persona then finds nothing to delete.
    # It must cd out of the way first — rd can't remove the directory the
    # shell itself occupies — and the wait-timeout path must skip the purge
    # (never pull the extraction from under an instance that may be alive).
    exe = tmp_path / "persona.exe"
    exe.write_bytes(b"MZ")
    path = au._write_relaunch_bat(
        str(exe), str(tmp_path / "persona-windows-setup.exe"), 4242, 7777
    )
    try:
        with open(path, encoding="ascii", newline="") as f:
            bat = f.read()
    finally:
        au.os.remove(path)
    cache = r"%APPDATA%\persona\persona\flet\app"
    assert bat.index('cd /d "%~dp0"') < bat.index(":wait")
    purge = bat.split(":settle")[1].split(":launch")[0]
    assert f'rd /s /q "{cache}"' in purge
    assert f'if not exist "{cache}" goto launch' in purge
    # bounded — a holder that never dies must not block the relaunch forever
    assert "geq 60" in purge
    # only the clean settle path purges; the wait loop never runs rd
    assert "rd /s /q" not in bat.split(":settle")[0]


def test_relaunch_bat_polls_with_one_snapshot_and_short_circuits(tmp_path):
    # #205: install→window took ~40s and the bat contributed seconds of its
    # own — every poll beat ran five tasklist invocations (hundreds of ms
    # each) back to back, on top of the ~1s ping. A beat must pay at most
    # one tasklist per still-alive check: the image names share ONE
    # unfiltered snapshot, and the first live hit short-circuits straight
    # to the sleep. The #174 bound (geq 300 beats, covers UAC dwell), both
    # pid waits, and the settle→purge→launch ordering (#195) all survive.
    exe = tmp_path / "persona.exe"
    exe.write_bytes(b"MZ")
    path = au._write_relaunch_bat(
        str(exe), str(tmp_path / "persona-windows-setup.exe"), 4242, 7777
    )
    try:
        with open(path, encoding="ascii", newline="") as f:
            bat = f.read()
    finally:
        au.os.remove(path)
    wait = bat.split(":settle")[0]
    # two pid checks + one snapshot — no per-image filtered tasklist runs
    assert wait.count("tasklist") == 3
    assert "IMAGENAME eq" not in wait
    snapshot = [ln for ln in wait.split("\r\n") if "findstr" in ln]
    assert len(snapshot) == 1
    for name in (
        "persona-windows-setup.exe",
        "persona-windows-setup.tmp",
        "persona.exe",
    ):
        assert f'/C:"{name}"' in snapshot[0]
    # every check bails to the sleep on its first hit
    assert wait.count("goto hold") == 3
    assert ":hold" in wait
    assert 'tasklist /FI "PID eq 4242"' in wait
    assert 'tasklist /FI "PID eq 7777"' in wait
    assert "geq 300" in wait
    assert bat.index(":settle") < bat.index(":purge") < bat.index(":launch")
    # the post-start liveness check needs one ~1s beat, not two: `start`
    # returns once CreateProcess succeeded, so the image is already visible
    launch = bat.split(":launch")[1]
    assert "ping -n 2 " in launch
    assert "ping -n 3" not in launch


def test_relaunch_bat_is_silent_and_ascii(tmp_path):
    # Mars SAW a console with ping output during a live update. Every command
    # in the bat must have its output swallowed, and nothing in it may pop a
    # console of its own: `timeout /t` paints a countdown (and refuses to run
    # without console input), `pause`/`echo on` print, and the whole file must
    # be ascii because cmd reads .bat in the OEM codepage.
    exe = tmp_path / "persona.exe"
    exe.write_bytes(b"MZ")
    path = au._write_relaunch_bat(
        str(exe), str(tmp_path / "persona-windows-setup.exe"), 4242, 7777
    )
    try:
        with open(path, encoding="ascii", newline="") as f:
            bat = f.read()
    finally:
        au.os.remove(path)
    lines = [ln for ln in bat.split("\r\n") if ln]
    assert lines[0] == "@echo off"
    assert "timeout" not in bat and "pause" not in bat
    for ln in lines:
        if ln.split()[0].lower() in ("tasklist", "ping", "find", "rd", "cd"):
            assert ">nul" in ln, f"unredirected output: {ln!r}"
    bat.encode("ascii")  # raises if anything non-ascii slipped in


def test_windows_relaunch_falls_back_to_fixed_wait_without_bat(
    monkeypatch, tmp_path
):
    # If the waiter .bat can't be written (temp dir broken, non-encodable path),
    # the relaunch still happens via the inline delayed-start command.
    _force_os(monkeypatch, win=True)
    monkeypatch.setattr(
        au.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, raising=False
    )
    monkeypatch.setattr(
        au.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False
    )
    staged = tmp_path / "persona-windows-setup.exe"
    staged.write_bytes(b"MZ")
    exe = tmp_path / "persona.exe"
    exe.write_bytes(b"MZ")
    calls = []

    def fake_popen(args, **kw):
        calls.append((args, kw))

        class P:
            pid = 4242

        return P()

    def broken_bat(exe, *pids):
        raise OSError("no temp dir")

    monkeypatch.setattr(au.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(au, "_installed_windows_exe", lambda: str(exe))
    monkeypatch.setattr(au, "_write_relaunch_bat", broken_bat)

    def fake_exit(code):
        raise SystemExit(code)

    monkeypatch.setattr(au.os, "_exit", fake_exit)
    msgs = []
    with pytest.raises(SystemExit):
        au.apply_and_restart(str(staged), log=msgs.append)
    assert not any("couldn't schedule the relaunch" in m for m in msgs), msgs
    args, kw = next((a, k) for a, k in calls if str(exe) in a)
    assert args[0] == "cmd"
    assert "ping" in args and "start" in args
    assert kw.get("close_fds") is True
    # the fallback must be exactly as invisible as the bat path: hidden console
    # via CREATE_NO_WINDOW, and NEVER DETACHED_PROCESS — a console-less cmd's
    # console children (ping) allocate a fresh VISIBLE console, which is the
    # terminal-with-a-countdown Mars saw during the 2.3.10 live update.
    assert kw["creationflags"] == 0x00000200 | 0x08000000


def test_windows_relaunch_env_is_scrubbed_of_runtime_vars(monkeypatch, tmp_path):
    # #173: the relaunch chain persona -> cmd -> start persona.exe inherits the
    # DYING process's environment, which the flet runtime filled with
    # per-process values: PYTHONPATH into the OLD version's %APPDATA%
    # extraction, FLET_* server sockets nobody will bind again. The relaunched
    # build re-applies inherited values over its own correct ones (the Windows
    # twin of #135), so it started and died with no window — "the update
    # installed but nothing reopened", while a manual open (clean environment
    # from Explorer) worked perfectly. The relaunch cmd must get a scrubbed
    # environment copy.
    _force_os(monkeypatch, win=True)
    monkeypatch.setattr(au._platform, "no_window_kwargs", lambda: {})
    monkeypatch.setattr(au.tempfile, "tempdir", str(tmp_path))
    staged = tmp_path / "persona-windows-setup.exe"
    staged.write_bytes(b"MZ")
    exe = tmp_path / "persona.exe"
    exe.write_bytes(b"MZ")
    poisoned = {
        "PYTHONPATH": r"C:\Users\x\AppData\Roaming\persona\persona\flet\app",
        "PYTHONHOME": r"C:\Program Files\persona",
        "FLET_SERVER_PORT": "54321",
        "FLET_SERVER_UDS_PATH": "flet_12345.sock",
        "FLET_PYTHON_CALLBACK_SOCKET_ADDR": "stdout_12345.sock",
        "FLET_APP_STORAGE_DATA": r"C:\old\storage",
    }
    for k, v in poisoned.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("PERSONA_KEEP_ME", "1")
    calls = []

    def fake_popen(args, **kw):
        calls.append((args, kw))

        class P:
            pid = 4242

        return P()

    monkeypatch.setattr(au.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(au, "_installed_windows_exe", lambda: str(exe))

    def fake_exit(code):
        raise SystemExit(code)

    monkeypatch.setattr(au.os, "_exit", fake_exit)
    msgs = []
    with pytest.raises(SystemExit):
        au.apply_and_restart(str(staged), log=msgs.append)
    assert not any("couldn't schedule the relaunch" in m for m in msgs), msgs
    _args, kw = next((a, k) for a, k in calls if a and a[0] == "cmd")
    env = kw.get("env")
    assert env is not None, "relaunch must not inherit the dying env as-is"
    for k in poisoned:
        assert k not in env, f"{k} leaked into the relaunched persona"
    assert env.get("PERSONA_KEEP_ME") == "1"  # everything else is kept


def test_windows_relaunch_fallback_env_is_scrubbed_too(monkeypatch, tmp_path):
    # the inline delayed-start fallback (bat unwritable) launches persona the
    # same way, so it needs the same scrubbed environment
    _force_os(monkeypatch, win=True)
    monkeypatch.setattr(au._platform, "no_window_kwargs", lambda: {})
    staged = tmp_path / "persona-windows-setup.exe"
    staged.write_bytes(b"MZ")
    exe = tmp_path / "persona.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setenv("FLET_SERVER_PORT", "54321")
    calls = []

    def fake_popen(args, **kw):
        calls.append((args, kw))

        class P:
            pid = 4242

        return P()

    def broken_bat(exe, *pids):
        raise OSError("no temp dir")

    monkeypatch.setattr(au.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(au, "_installed_windows_exe", lambda: str(exe))
    monkeypatch.setattr(au, "_write_relaunch_bat", broken_bat)

    def fake_exit(code):
        raise SystemExit(code)

    monkeypatch.setattr(au.os, "_exit", fake_exit)
    with pytest.raises(SystemExit):
        au.apply_and_restart(str(staged), log=lambda m: None)
    _args, kw = next((a, k) for a, k in calls if str(exe) in a)
    env = kw.get("env")
    assert env is not None
    assert "FLET_SERVER_PORT" not in env


def test_windows_update_children_get_cwd_outside_the_flet_extraction(
    monkeypatch, tmp_path
):
    # #195 root: the flet host runs with its current directory INSIDE the app
    # extraction — serious_python sets Directory.current to the unpacked
    # %APPDATA%\persona\persona\flet\app. A child spawned without an explicit
    # cwd inherits it, and the relaunch cmd is — by design — still alive when
    # it `start`s the new persona, so its inherited cwd holds a directory
    # handle on the very tree the new instance's bootstrap deletes to
    # re-extract the updated app.zip. RemoveDirectory on another process's cwd
    # is ERROR_SHARING_VIOLATION (winerror 32): "Deletion failed ... errno 32,
    # file held by another process" on a white screen. No pid/image wait can
    # fix that — the holder is the waiter itself — so every process the update
    # spawns (installer AND relaunch chain) must get a cwd outside the
    # extraction.
    _force_os(monkeypatch, win=True)
    monkeypatch.setattr(au._platform, "no_window_kwargs", lambda: {})
    monkeypatch.setattr(au.tempfile, "tempdir", str(tmp_path))
    staged = tmp_path / "persona-windows-setup.exe"
    staged.write_bytes(b"MZ")
    exe = tmp_path / "persona.exe"
    exe.write_bytes(b"MZ")
    calls = []

    def fake_popen(args, **kw):
        calls.append((args, kw))

        class P:
            pid = 4242

        return P()

    monkeypatch.setattr(au.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(au, "_installed_windows_exe", lambda: str(exe))

    def fake_exit(code):
        raise SystemExit(code)

    monkeypatch.setattr(au.os, "_exit", fake_exit)
    msgs = []
    with pytest.raises(SystemExit):
        au.apply_and_restart(str(staged), log=msgs.append)
    assert not any("couldn't schedule the relaunch" in m for m in msgs), msgs
    assert len(calls) == 2  # the installer and the relaunch cmd
    for args, kw in calls:
        assert kw.get("cwd") == au.tempfile.gettempdir(), args


def test_windows_relaunch_fallback_cwd_is_outside_the_extraction_too(
    monkeypatch, tmp_path
):
    # the inline delayed-start fallback (bat unwritable) is alive at `start`
    # time just the same, so it needs the same explicit cwd (#195)
    _force_os(monkeypatch, win=True)
    monkeypatch.setattr(au._platform, "no_window_kwargs", lambda: {})
    staged = tmp_path / "persona-windows-setup.exe"
    staged.write_bytes(b"MZ")
    exe = tmp_path / "persona.exe"
    exe.write_bytes(b"MZ")
    calls = []

    def fake_popen(args, **kw):
        calls.append((args, kw))

        class P:
            pid = 4242

        return P()

    def broken_bat(exe, *pids):
        raise OSError("no temp dir")

    monkeypatch.setattr(au.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(au, "_installed_windows_exe", lambda: str(exe))
    monkeypatch.setattr(au, "_write_relaunch_bat", broken_bat)

    def fake_exit(code):
        raise SystemExit(code)

    monkeypatch.setattr(au.os, "_exit", fake_exit)
    with pytest.raises(SystemExit):
        au.apply_and_restart(str(staged), log=lambda m: None)
    _args, kw = next((a, k) for a, k in calls if str(exe) in a)
    assert kw.get("cwd") == au.tempfile.gettempdir()


def test_relaunch_bat_verifies_the_app_came_up_and_retries(tmp_path):
    # #173 belt-and-suspenders: `start` fails SILENTLY when it loses a race
    # with the installer (persona.exe still locked mid-swap), and the bat then
    # self-deleted having launched nothing. After `start` the bat must confirm
    # a process with the exe's image name exists and retry the start (bounded)
    # until it does.
    exe = tmp_path / "persona.exe"
    exe.write_bytes(b"MZ")
    path = au._write_relaunch_bat(
        str(exe), str(tmp_path / "persona-windows-setup.exe"), 4242, 7777
    )
    try:
        with open(path, encoding="ascii", newline="") as f:
            bat = f.read()
    finally:
        au.os.remove(path)
    launch = bat.split(":launch")[1]
    assert f'start "" /D "{tmp_path}" "{exe}"' in launch
    # after the start: is a persona.exe actually running?
    assert 'tasklist /FI "IMAGENAME eq persona.exe"' in launch
    # if not, loop back and start again — but bounded
    assert "goto launch" in launch
    assert "lss 30" in launch
    # the self-delete still happens last, whether the launch stuck or not
    assert 'del "%~f0"' in launch.split(":done")[1]


_CSC = os.path.join(
    os.environ.get("SystemRoot", r"C:\Windows"),
    "Microsoft.NET", "Framework64", "v4.0.30319", "csc.exe",
)


@pytest.mark.skipif(
    os.name != "nt" or not os.path.isfile(_CSC),
    reason="live relaunch-bat run needs Windows + the .NET csc compiler",
)
def test_relaunch_bat_actually_relaunches_on_windows(tmp_path):
    # Run the REAL bat through a REAL detached cmd exactly as apply_and_restart
    # spawns it, and prove the target exe gets started: the end-to-end check
    # #173 was missing (unit tests validated the bat's text, not that it runs).
    import subprocess
    import time

    marker = tmp_path / "started.marker"
    src = tmp_path / "target.cs"
    src.write_text(
        "class P { static void Main() { "
        f'System.IO.File.WriteAllText(@"{marker}", "up"); '
        "System.Threading.Thread.Sleep(8000); } }"
    )
    exe = tmp_path / "persona-relaunch-check.exe"
    built = subprocess.run(
        [_CSC, "/nologo", "/target:winexe", f"/out:{exe}", str(src)],
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert built.returncode == 0, built.stdout.decode("utf-8", "replace")

    def dead_pid():
        p = subprocess.Popen(
            ["cmd", "/c", "exit"], creationflags=subprocess.CREATE_NO_WINDOW
        )
        p.wait()
        return p.pid

    bat = au._write_relaunch_bat(
        str(exe),
        str(tmp_path / "persona-relaunch-check-setup.exe"),
        dead_pid(),
        dead_pid(),
    )
    # a fake %APPDATA% so the bat's flet-extraction purge (#195) is exercised
    # against a real cmd without touching the machine's real persona cache
    fake_appdata = tmp_path / "appdata"
    cache = fake_appdata / "persona" / "persona" / "flet" / "app"
    cache.mkdir(parents=True)
    (cache / "stale.py").write_text("x")
    env = au._relaunch_env()
    env["APPDATA"] = str(fake_appdata)
    try:
        subprocess.Popen(
            ["cmd", "/c", bat],
            close_fds=True,
            env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW,
        )
        deadline = time.time() + 30
        while time.time() < deadline and not marker.exists():
            time.sleep(0.3)
        assert marker.exists(), "the relaunch bat never started the target exe"
        # the purge ran before the start: the extraction dir is already gone
        assert not cache.exists(), "flet extraction survived the pre-launch purge"
        # the bat deletes itself once the launch is confirmed — best-effort:
        # the `del "%~f0"` can lag on a busy host, and the finally-block below
        # removes it regardless, so a slow self-delete is not a fix failure
        # (the marker proving the exe launched is what #173 verifies).
        deadline = time.time() + 20
        while time.time() < deadline and os.path.exists(bat):
            time.sleep(0.3)
    finally:
        try:
            os.remove(bat)
        except OSError:
            pass
        # the target sleeps ~8s so the bat's liveness check can see it; make
        # sure it's gone before pytest tears the tmp dir down
        subprocess.run(
            ["taskkill", "/F", "/IM", "persona-relaunch-check.exe"],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        time.sleep(0.5)


def test_relaunch_env_drops_runtime_vars_and_keeps_the_rest(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "poison")
    monkeypatch.setenv("FLET_SERVER_PORT", "1")
    monkeypatch.setenv("PERSONA_KEEP_ME", "yes")
    env = au._relaunch_env()
    assert "PYTHONPATH" not in env
    assert "FLET_SERVER_PORT" not in env
    assert env["PERSONA_KEEP_ME"] == "yes"
    # a fresh copy, not os.environ itself
    env["PERSONA_KEEP_ME"] = "mutated"
    assert os.environ["PERSONA_KEEP_ME"] == "yes"


def test_apply_and_restart_never_wipes_engines(monkeypatch, tmp_path):
    # Inno remembers the wipedata/wipeengines task selections from a previous
    # interactive install and re-applies them on silent upgrades. A self-update
    # must never wipe anything, so it has to deselect both tasks explicitly —
    # this is what re-downloaded ~750MB of engines after the 2.3.7→2.3.8 update.
    _force_os(monkeypatch, win=True)
    staged = tmp_path / "persona-windows-setup.exe"
    staged.write_bytes(b"MZ")
    calls = []

    def fake_popen(args, **kw):
        calls.append(args)

        class P:
            pass

        return P()

    monkeypatch.setattr(au.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(au._platform, "no_window_kwargs", lambda: {})
    monkeypatch.setattr(au, "_installed_windows_exe", lambda: "")

    def fake_exit(code):
        raise SystemExit(code)

    monkeypatch.setattr(au.os, "_exit", fake_exit)
    with pytest.raises(SystemExit):
        au.apply_and_restart(str(staged))
    installer_call = next(c for c in calls if str(staged) in c)
    assert "/MERGETASKS=!wipedata,!wipeengines" in installer_call


def test_installer_wipe_tasks_do_not_inherit_previous_selection():
    # Belt to the /MERGETASKS suspenders: the wipe tasks themselves must carry
    # dontinheritcheck, or one interactive install with a box checked poisons
    # every later upgrade (Inno stores selected tasks in the registry and
    # defaults to them next time).
    import os
    import re

    wf = os.path.join(
        os.path.dirname(__file__), "..", ".github", "workflows", "release.yml"
    )
    with open(wf, encoding="utf-8") as f:
        text = f.read()
    tasks = re.findall(r'Name: "(wipedata|wipeengines)".*', text)
    assert len(tasks) == 2
    for line in re.findall(r'Name: "(?:wipedata|wipeengines)".*', text):
        assert "dontinheritcheck" in line, line


def test_linux_relaunch_scrubs_runtime_env(monkeypatch, tmp_path):
    # The flet runtime injects PYTHONPATH/PYTHONHOME (pointing into THIS
    # AppImage's private /tmp/.mount_* dir) and per-process FLET_* server vars
    # into os.environ, and the Flutter shell re-applies any inherited values
    # over its own correct ones. os.execv passes os.environ along, so without
    # scrubbing, every post-update process resolves site-packages from the
    # FIRST generation's mount forever — a chain of in-app updates kept
    # importing from a pre-invisible_playwright bundle and flooded
    # "ModuleNotFoundError: No module named 'invisible_playwright'" (#135).
    _force_os(monkeypatch, linux=True)
    target = tmp_path / "persona.AppImage"
    target.write_bytes(b"old")
    staged = tmp_path / "staged.AppImage"
    staged.write_bytes(b"new")
    monkeypatch.setattr(au, "installed_appimage_path", lambda: str(target))
    monkeypatch.setattr(au, "verify_appimage_runs", lambda p: True)
    # fsync on an O_RDONLY fd is EBADF on Windows, where this test also runs
    monkeypatch.setattr(au.os, "fsync", lambda fd: None)
    poisoned = {
        "PYTHONPATH": "/tmp/.mount_persXXXXXX/usr/site-packages",
        "PYTHONHOME": "/tmp/.mount_persXXXXXX/usr",
        "PYTHONNOUSERSITE": "1",
        "FLET_SERVER_UDS_PATH": "flet_12345.sock",
        "FLET_SERVER_PORT": "54321",
        "FLET_PYTHON_CALLBACK_SOCKET_ADDR": "stdout_12345.sock",
    }
    for k, v in poisoned.items():
        monkeypatch.setenv(k, v)
    seen = {}

    def fake_execv(path, args):
        seen["env"] = dict(au.os.environ)
        seen["path"] = path
        raise SystemExit(0)

    monkeypatch.setattr(au.os, "execv", fake_execv)
    with pytest.raises(SystemExit):
        au.apply_and_restart(str(staged))
    assert seen["path"] == str(target)
    for k in poisoned:
        assert k not in seen["env"], f"{k} leaked into the relaunched process"


def test_linux_swap_and_execv_wait_for_the_probe_to_be_reaped(
    monkeypatch, tmp_path
):
    # #199: no second instance may exist when the update swaps files and
    # execv's — the verify probe's whole process group is killed AND waited on
    # before os.replace runs, so nothing of the probe survives into the
    # relaunch (or still holds a flet cache when the new persona comes up).
    import subprocess

    _force_os(monkeypatch, linux=True)
    target = tmp_path / "persona.AppImage"
    target.write_bytes(b"old")
    staged = tmp_path / "staged.AppImage"
    staged.write_bytes(b"new")
    monkeypatch.setattr(au, "installed_appimage_path", lambda: str(target))
    monkeypatch.setattr(au.os, "fsync", lambda fd: None)
    events = []

    class P:
        pid = 4321
        returncode = 0

        def communicate(self, timeout=None):
            return b"", b""

        def wait(self, timeout=None):
            events.append("wait")
            return 0

        def kill(self):
            events.append("kill")

    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: P())
    monkeypatch.setattr(
        au.os, "killpg", lambda pid, sig: events.append("killpg"), raising=False
    )
    real_replace = au.os.replace

    def replace(src, dst):
        events.append("swap")
        return real_replace(src, dst)

    monkeypatch.setattr(au.os, "replace", replace)

    def execv(path, args):
        events.append("execv")
        raise SystemExit(0)

    monkeypatch.setattr(au.os, "execv", execv)
    with pytest.raises(SystemExit):
        au.apply_and_restart(str(staged))
    assert events.index("killpg") < events.index("swap")
    assert events.index("wait") < events.index("swap")
    assert events.index("swap") < events.index("execv")


def test_apply_and_restart_macos_never_breaks_the_install_on_failure(
    monkeypatch, tmp_path
):
    # macOS applies via _apply_macos (covered in test_app_update_macos.py);
    # apply_and_restart must route there and, when nothing can be swapped,
    # return False without touching anything.
    _force_os(monkeypatch, mac=True)
    staged = tmp_path / "staged.dmg"
    staged.write_bytes(b"x")
    monkeypatch.setattr(au, "installed_macos_app", lambda: "")
    monkeypatch.setattr(
        au, "verify_staged_installer", lambda s, tag="", log=None: True
    )
    monkeypatch.setattr(
        au.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError())
    )
    msgs = []
    assert au.apply_and_restart(str(staged), log=msgs.append) is False
    assert staged.exists()


class _RuntimeProbe:
    """Fake Popen result for the AppImage runtime handling --appimage-extract."""

    def __init__(self, returncode=0, extract_to=None, hang=False, pid=4321):
        self.returncode = returncode
        self.pid = pid
        self._extract_to = extract_to
        self._hang = hang

    def communicate(self, timeout=None):
        import subprocess

        if self._extract_to:
            apprun = os.path.join(self._extract_to, "squashfs-root", "AppRun")
            os.makedirs(os.path.dirname(apprun), exist_ok=True)
            with open(apprun, "w") as f:
                f.write("#!/bin/sh\n")
        if self._hang:
            raise subprocess.TimeoutExpired("probe", timeout)
        return b"", b""

    def wait(self, timeout=None):
        if self._hang:
            self.returncode = -9
        return self.returncode

    def kill(self):
        pass


def test_verify_probe_never_boots_the_app(monkeypatch, tmp_path):
    # #199: ANY probe that starts the app paints a window — AppRun execs the
    # Flutter host, which shows the boot screen BEFORE the Python side (and
    # its PERSONA_SELFTEST gate) ever runs, standing a second persona next to
    # the live one for the whole verify. Every process the probe spawns must
    # be the AppImage RUNTIME handling one of its own --appimage-* commands,
    # which exits before anything inside the payload can start.
    import subprocess

    staged = tmp_path / "new.AppImage"
    staged.write_bytes(b"x")
    cmds = []

    def fake_popen(cmd, **kw):
        cmds.append(list(cmd))
        return _RuntimeProbe(extract_to=kw.get("cwd"))

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(au.os, "killpg", lambda *a: None, raising=False)
    assert au.verify_appimage_runs(str(staged)) is True
    assert cmds, "the probe must actually exercise the runtime"
    for cmd in cmds:
        assert cmd[0] == str(staged)
        assert len(cmd) > 1 and cmd[1].startswith("--appimage-"), cmd


def test_verify_probe_extracts_into_a_throwaway_dir(monkeypatch, tmp_path):
    # The runtime drops squashfs-root into its cwd; that must be a throwaway
    # dir (never the live app's cwd — serious_python chdirs into the flet
    # extraction dir, #195) removed once the probe is done, and the child env
    # must be scrubbed of runtime vars (#135) with the probe in its own
    # session so it is killable as a group.
    import subprocess

    staged = tmp_path / "new.AppImage"
    staged.write_bytes(b"x")
    monkeypatch.setenv("PYTHONPATH", "/tmp/.mount_persXXXXXX/usr/site-packages")
    seen = {}

    def fake_popen(cmd, **kw):
        seen.update(kw)
        seen["cwd_existed"] = os.path.isdir(kw.get("cwd") or "")
        return _RuntimeProbe(extract_to=kw.get("cwd"))

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(au.os, "killpg", lambda *a: None, raising=False)
    assert au.verify_appimage_runs(str(staged)) is True
    assert "PYTHONPATH" not in seen["env"]
    assert seen.get("start_new_session") is True
    cwd = seen.get("cwd")
    assert cwd and seen["cwd_existed"] and cwd != os.getcwd()
    assert not os.path.exists(cwd)


def test_verify_probe_group_is_reaped_before_returning(monkeypatch, tmp_path):
    # #199: the AppImage runtime forks, so killing only the direct child can
    # leave a straggler alive into the swap and execv. The probe must be
    # killed as a whole process GROUP and waited on before verify returns.
    import subprocess

    staged = tmp_path / "new.AppImage"
    staged.write_bytes(b"x")
    events = []

    def fake_popen(cmd, **kw):
        proc = _RuntimeProbe(extract_to=kw.get("cwd"), pid=111)
        proc.wait = lambda timeout=None: events.append("wait") or 0
        events.append("spawn")
        return proc

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        au.os,
        "killpg",
        lambda pid, sig: events.append(f"killpg-{pid}"),
        raising=False,
    )
    assert au.verify_appimage_runs(str(staged)) is True
    assert events.index("killpg-111") > events.index("spawn")
    assert "wait" in events


def test_verify_probe_accepts_a_slow_patternless_extraction(
    monkeypatch, tmp_path
):
    # An older runtime without pattern support extracts the WHOLE payload and
    # can outlive the timeout — but AppRun lands early, and its presence
    # already proves the runtime executes here and the squashfs is readable.
    # The timed-out probe is reaped, not re-run (a second spawn was the old
    # GUI fallback that doubled the #199 window).
    import subprocess

    staged = tmp_path / "new.AppImage"
    staged.write_bytes(b"x")
    spawns = []

    def fake_popen(cmd, **kw):
        spawns.append(list(cmd))
        return _RuntimeProbe(extract_to=kw.get("cwd"), hang=True)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(au.os, "killpg", lambda *a: None, raising=False)
    assert au.verify_appimage_runs(str(staged)) is True
    assert len(spawns) == 1


def test_verify_probe_rejects_a_runtime_that_cant_read_the_payload(
    monkeypatch, tmp_path
):
    # The v2.1.3 brick class: the runtime exits nonzero ("open dir error",
    # truncated/corrupt squashfs) and nothing was extracted — the swap must
    # never happen.
    import subprocess

    staged = tmp_path / "new.AppImage"
    staged.write_bytes(b"x")
    monkeypatch.setattr(
        subprocess, "Popen", lambda cmd, **kw: _RuntimeProbe(returncode=127)
    )
    monkeypatch.setattr(au.os, "killpg", lambda *a: None, raising=False)
    assert au.verify_appimage_runs(str(staged)) is False


def test_verify_probe_rejects_a_hung_runtime_with_nothing_extracted(
    monkeypatch, tmp_path
):
    import subprocess

    staged = tmp_path / "new.AppImage"
    staged.write_bytes(b"x")
    monkeypatch.setattr(
        subprocess, "Popen", lambda cmd, **kw: _RuntimeProbe(hang=True)
    )
    monkeypatch.setattr(au.os, "killpg", lambda *a: None, raising=False)
    assert au.verify_appimage_runs(str(staged)) is False
