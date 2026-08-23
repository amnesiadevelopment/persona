"""THE Windows swap-and-relaunch .bat generator.

persona had two hand-maintained emitters for what is one script: the full
installer's relauncher (app_update/updater.py) and the code-only fast path's
app.zip swapper (app_update/fast_update.py). Both spelled out the same bounded
wait loop, the same flet-extraction purge, the same launch-and-confirm retry and
the same self-delete — so every fix had to be made twice, and #195 famously was
not: it landed in the installer's copy, "the fast path skipped" it, and had to be
re-fixed there separately. #174, #195, #205 and #229 were all fixed in one
emitter at a time. One generator, one place to fix.

The two callers differ in exactly three ways, which are this module's
parameters:

  * WHAT to wait for. The installer path waits on the whole Inno process family
    by image name (the elevated second stage is a process we never got a handle
    to, #174); the fast path waits only on this persona.
  * WHAT to do once everything has exited — the "stage". The installer path has
    nothing to do (the installer already replaced the files) and drops straight
    into the purge; the fast path copies the new app.zip + hash into place.
  * The temp-file prefix.

Everything else — the poll bounds, the purge retry cap, the ~3s beat before the
launch confirm, the bounded re-launch — is shared and must stay that way.

Why cmd does what it does here, preserved from both originals:
  * Sleeps go through `ping`, because `timeout` refuses to run without console
    input and paints a countdown.
  * The file is written as ASCII: cmd reads .bat in the OEM codepage, which only
    agrees with Python's encodings in the ASCII range. A non-ASCII path raises
    here so the caller can fall back to an inline relaunch that passes the path
    through the (Unicode) process command line.
  * The bat cd's to its own directory up front — `rd` cannot remove the
    directory the shell itself occupies, and the spawner's cwd is nothing to
    rely on.
"""

import os
import tempfile

# path_provider resolves the app-data root as %APPDATA%\<company>\<product>
# (both "persona" in pyproject.toml), and the flet bootstrap unpacks app.zip to
# flet\app beneath it — the dir it deletes on a hash change.
FLET_APP_DIR = r"%APPDATA%\persona\persona\flet\app"

# ~900 near-instant beats: enough wall clock for a slow silent install. The beat
# is a `ping -n 1` (~10ms) rather than a full second because the tasklist
# snapshot above it (~0.3s) is throttle enough — and every second of polling is
# a second the user spends looking at nothing (#205).
_WAIT_BEATS = 900
# Bounded so a handle that never releases can't block the relaunch forever.
_PURGE_TRIES = 60
# A genuine failure must not spawn a fistful of persona processes (#229).
_LAUNCH_TRIES = 5


def pid_check(pid) -> str:
    """Wait-loop clause: hold while a specific pid is still alive. '' for a
    falsy pid, so a caller with no pid to watch simply contributes nothing."""
    if not pid:
        return ""
    return (
        f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul\r\n'
        "if not errorlevel 1 goto hold\r\n"
    )


def image_snapshot_check(names) -> str:
    """Wait-loop clause: hold while ANY of `names` is running, costing ONE
    tasklist call for the whole set.

    tasklist runs hundreds of ms per invocation and is the dominant cost of a
    poll beat, so the image names are matched against a single unfiltered
    snapshot rather than one filtered run each (#205).

    /FO CSV because the default table format truncates long image names to fit a
    25-char column and a truncated name never matches; /L because the names
    carry regex-special dots. A substring hit against another CSV field is
    harmless — it only keeps a beat busy, and the wait stays bounded.
    """
    unique = [n for n in dict.fromkeys(names) if n]
    if not unique:
        return ""
    patterns = " ".join(f'/C:"{name}"' for name in unique)
    return (
        f"tasklist /FO CSV /NH 2>nul | findstr /I /L {patterns} >nul\r\n"
        "if not errorlevel 1 goto hold\r\n"
    )


def image_check(image: str) -> str:
    """Wait-loop clause: hold while a single image name is running."""
    if not image:
        return ""
    return (
        f'tasklist /FI "IMAGENAME eq {image}" /FO CSV /NH 2>nul'
        f' | find /I "{image}" >nul\r\n'
        "if not errorlevel 1 goto hold\r\n"
    )


def _purge_block(label: str, done_label: str, counter: str) -> str:
    """The bounded flet-extraction purge, as an addressable block.

    Emitted once per LAUNCH ROUTE rather than once per script. The rule the
    whole module is built on is that a launch must never hand a stale
    extraction to the new persona's bootstrap — whose delete runs before any of
    our Python and therefore cannot retry (#195). A second route to a `start`
    is a second place that rule has to hold, so the purge is a generator and
    both routes render from it: they cannot drift, and a fix lands in one place
    exactly as this module's whole existence argues for.

    `counter` is per-block on purpose. Sharing `purges` across routes would let
    a first route that spent the budget leave the second with none.
    """
    return (
        f":{label}\r\n"
        f'if not exist "{FLET_APP_DIR}" goto {done_label}\r\n'
        f'rd /s /q "{FLET_APP_DIR}" >nul 2>&1\r\n'
        f'if not exist "{FLET_APP_DIR}" goto {done_label}\r\n'
        f"set /a {counter}+=1\r\n"
        f"if %{counter}% geq {_PURGE_TRIES} goto {done_label}\r\n"
        # Recheck near-instantly — the exists probe is the throttle, not the ping.
        "ping -n 1 127.0.0.1 >nul\r\n"
        f"goto {label}\r\n"
    )


def build_bat(exe: str, wait_checks: str, stage_label: str,
              stage_body: str = "", recover_body: str = "",
              confirm_body: str = "") -> str:
    """The whole script: wait for every holder to exit, run the caller's stage,
    purge the stale flet extraction, launch, confirm, self-delete.

    The ORDER is the point, and every step of it was a bug once:

    1. WAIT until nothing holds the files. The swap can't happen while a process
       still has them open (errno-32, #195), and the launch must not race the
       old persona's teardown — the new instance would start while the dying one
       still held the flet extraction, flet's delete-and-reextract would fail
       with errno 32, and the user got "Error starting app". Bounded, so a hung
       process can't block the relaunch forever.
    2. STAGE — the caller's file operations, now that nothing holds anything.
    3. PURGE the flet extraction. The new persona's bootstrap deletes it to
       unpack the updated app.zip BEFORE any of our Python runs, so THAT delete
       cannot retry: any handle still open at that instant (release lag, an AV
       sweep, a straggler child) crashes the new instance on a white screen
       (#195). We delete it here instead, with bounded retries, so the new
       persona finds nothing to delete and unpacks fresh. The wait-timeout path
       jumps straight to :launch and skips this — never pull the extraction out
       from under an instance that may still be alive.
    4. LAUNCH, then CONFIRM after a real beat. `start` returns as soon as
       CreateProcess succeeded, but the new persona re-extracts app.zip behind
       its boot screen for several seconds before persona.exe registers in
       tasklist. A near-instant recheck sees "not running yet" and launches a
       SECOND instance that races the extraction; one wins, the other dies —
       "reopened then closed again quickly" (#229). So sleep ~3s BEFORE the
       confirm, count re-launches on their OWN counter (the wait loop's `tries`
       is already near its cap by then), and retry only a handful of times.
    5. SELF-DELETE, whether the launch stuck or not.

    `recover_body` and `confirm_body` are the caller's OPT-IN arms on that last
    step, and they default to "" exactly as `stage_body` does — a caller that
    passes neither gets a byte-identical script to before they existed, which is
    what keeps the full installer's relauncher (updater.py) untouched. This
    module stays ignorant of WHAT is being swapped:

      * `confirm_body` runs once the confirm SUCCEEDED — the caller's chance to
        drop whatever it retained in its stage, so nothing accumulates.
      * `recover_body` runs once the confirm has failed its whole re-launch
        budget — the caller's chance to put back what its stage moved aside.
        A PURGE and one more `start` follow it, and then the script is done.

        The purge is not optional decoration on that arm. `goto recover` is a
        SECOND route to a `start`, and step 3 above is a property of every
        route to a launch, not of one section: :launch gets away with carrying
        no purge of its own only because every route into it passes through
        :purge first. So the recovery arm renders its own from the same
        `_purge_block` generator, on its own counter — the normal route may
        have spent `purges` down to nothing before the boots budget ran out.

        The recovered launch is deliberately NOT re-confirmed and NOT
        re-recovered: a second recovery has nothing left to restore and would
        only re-enter the loop we just exhausted (and, since `boots` is already
        at its cap by then, a bare `goto purge` here would fall back into
        :recover a second time and fire a stray extra `start`).
    """
    image = os.path.basename(exe)
    stage = f":{stage_label}\r\n" + (stage_body or "")
    # Route the confirm's success edge through the caller's arm only when there
    # IS one — otherwise the jump target stays :done, unchanged.
    confirmed_target = "confirmed" if confirm_body else "done"
    recover = (
        ":recover\r\n"
        # Its OWN purge budget: the normal route may have spent `purges` down to
        # nothing, and the recovery launch must not inherit an exhausted counter.
        "set rpurges=0\r\n"
        + recover_body
        # Falls through into the recovery purge exactly as the stage falls into
        # the normal one. The restore has just put the OLD zip + OLD hash back
        # while the extraction on disk is the NEW (bad) one, so the bootstrap
        # WILL delete and re-extract — this route depends on that delete more
        # than any other, and it is precisely the delete that cannot retry
        # (#195). Skipping the purge here would hand the last-resort launch the
        # one hazard the whole script exists to remove. It is not "safe because
        # the confirm failed": the confirm is a tasklist probe ~3s after
        # `start`, so a persona slow to register (#229) fails it while alive and
        # mid-extraction, and five such attempts leave more holders open here,
        # not fewer.
        + _purge_block("rpurge", "rlaunch", "rpurges")
        + ":rlaunch\r\n"
        + f'start "" /D "{os.path.dirname(exe)}" "{exe}"\r\n'
        + "goto done\r\n"
    ) if recover_body else ""
    confirmed = (":confirmed\r\n" + confirm_body) if confirm_body else ""
    # Where the spent re-launch budget lands. With NO arms this is "" and the
    # confirm falls straight through to :done exactly as it always has — that
    # empty string is what makes the full installer's script byte-identical.
    if recover:
        exhausted_jump = "goto recover\r\n"
    elif confirmed:
        # Nothing to restore, but :confirmed sits between here and :done and
        # must be jumped OVER, not fallen into.
        exhausted_jump = "goto done\r\n"
    else:
        exhausted_jump = ""
    return (
        "@echo off\r\n"
        'cd /d "%~dp0" >nul 2>&1\r\n'
        "set tries=0\r\n"
        "set boots=0\r\n"
        "set purges=0\r\n"
        ":wait\r\n"
        + wait_checks +
        f"goto {stage_label}\r\n"
        ":hold\r\n"
        "set /a tries+=1\r\n"
        f"if %tries% geq {_WAIT_BEATS} goto launch\r\n"
        "ping -n 1 127.0.0.1 >nul\r\n"
        "goto wait\r\n"
        + stage
        # No sleep before the purge: the wait loop already proved every holder is
        # gone, so handle release is done, and the purge below retries rd on the
        # rare straggler. A settle ping was a full second of dead time between
        # the update closing persona and reopening it (#205).
        + _purge_block("purge", "launch", "purges")
        + ":launch\r\n"
        # empty title + quoted path: `start` treats the first quoted token as a
        # window title, so a bare path with spaces would launch nothing
        f'start "" /D "{os.path.dirname(exe)}" "{exe}"\r\n'
        "ping -n 4 127.0.0.1 >nul\r\n"
        f'tasklist /FI "IMAGENAME eq {image}" /FO CSV /NH 2>nul'
        f' | find /I "{image}" >nul\r\n'
        f"if not errorlevel 1 goto {confirmed_target}\r\n"
        "set /a boots+=1\r\n"
        f"if %boots% lss {_LAUNCH_TRIES} goto launch\r\n"
        # The budget is spent. Where that falls to depends on which arms exist,
        # and the jump is explicit rather than a fall-through because falling
        # into :confirmed here would run the caller's SUCCESS cleanup on a boot
        # that never succeeded — dropping exactly what recovery needs.
        + exhausted_jump
        + recover
        + confirmed +
        ":done\r\n"
        '(goto) 2>nul & del "%~f0"\r\n'
    )


def write_bat(content: str, prefix: str) -> str:
    """Write a generated script to a temp .bat and return its path.

    ASCII on purpose — see the module docstring. A failed write removes the
    half-made file and re-raises, so the caller falls back rather than spawning
    a truncated script.
    """
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".bat")
    try:
        with os.fdopen(fd, "w", encoding="ascii", newline="") as f:
            f.write(content)
    except Exception:
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    return path
