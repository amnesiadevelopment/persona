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
    assert (
        'tasklist /FI "IMAGENAME eq persona-windows-setup-v2.4.5.exe" /FO CSV'
        in wait
    )
    assert (
        'tasklist /FI "IMAGENAME eq persona-windows-setup-v2.4.5.tmp" /FO CSV'
        in wait
    )
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
    assert 'IMAGENAME eq persona-windows-setup.exe' in bat
    assert 'IMAGENAME eq persona-windows-setup.tmp' in bat


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
        if ln.split()[0].lower() in ("tasklist", "ping", "find"):
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
    try:
        subprocess.Popen(
            ["cmd", "/c", bat],
            close_fds=True,
            env=au._relaunch_env(),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW,
        )
        deadline = time.time() + 30
        while time.time() < deadline and not marker.exists():
            time.sleep(0.3)
        assert marker.exists(), "the relaunch bat never started the target exe"
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


def test_verify_probe_passes_selftest_flag(monkeypatch, tmp_path):
    # The fast self-test probe must run the build with PERSONA_SELFTEST=1 so it
    # exits at the gate before ft.run — no window ever appears.
    import subprocess

    staged = tmp_path / "new.AppImage"
    staged.write_bytes(b"x")
    seen = {}

    class _Out:
        stdout = b"SELFTEST_OK\n"
        returncode = 0

    def fake_run(cmd, **kw):
        seen["env"] = kw.get("env", {})
        return _Out()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert au.verify_appimage_runs(str(staged)) is True
    assert seen["env"].get("PERSONA_SELFTEST") == "1"


def test_verify_fallback_stays_windowless(monkeypatch, tmp_path):
    # A cold FUSE mount can make the first probe time out before the hook
    # prints. The fallback re-run must KEEP PERSONA_SELFTEST=1, so a build with
    # the hook (every current release) still exits before ft.run and never
    # flashes a bare pre-splash window next to the live app (#177: the "two
    # personas + black window" the user saw). The old code dropped the flag and
    # launched a full GUI here.
    import subprocess

    staged = tmp_path / "new.AppImage"
    staged.write_bytes(b"x")
    seen = {}

    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 30))

    class _Proc:
        pid = 4321

        def poll(self):
            return 0  # exited cleanly at the self-test gate

    def fake_popen(cmd, **kw):
        seen["env"] = kw.get("env", {})
        return _Proc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    assert au.verify_appimage_runs(str(staged)) is True
    assert seen["env"].get("PERSONA_SELFTEST") == "1"
