"""PS-171 arm C — THE STOCK-FIREFOX CONTROL (and its channel-matched twin).

WHY THIS FILE EXISTS
--------------------
Rounds 1-5 of this record ran four arms (A2/B/D/E/F) that all launched through
persona's ``InvisiblePlaywright`` launcher. ``PS171_LAYER=0`` turned off the
per-tab masking layer, but it did NOT remove persona's PATCHED ENGINE or its
launcher. So a stall originating in the engine patches or in the launcher would
have reproduced in all four arms and been attributed away as "not persona's".
The ticket names the stock-Firefox control in scope twice ("the single cheapest
thing to run first"); it had not been run. This file runs it.

THE TWO ARMS, AND WHY BOTH ARE NEEDED
-------------------------------------
Stock Firefox has no juggler channel, so a stock arm cannot use the gesture the
other arms use. Driving stock over Marionette and comparing it against a
juggler arm would confound TWO variables at once (build AND channel), and a
"stock did not stall" from such a pair could not be attributed to either.

So this harness runs BOTH sides over the SAME channel (Marionette), with the
same instrument and the same gesture, and the only difference between them is
WHICH BINARY IS EXECUTED:

    C1  stock      upstream Firefox 151.0, downloaded from ftp.mozilla.org
    C2  patched    persona's engine build, executed DIRECTLY as a bare binary
                   -- no persona launcher, no masking layer, no juggler

Both report ``Mozilla Firefox 151.0``, so C1-vs-C2 is not a version comparison:
it isolates persona's ENGINE PATCHES with the channel, the gesture, the
instrument and the profile flags all held constant.

WHAT THIS ARM CAN AND CANNOT ATTRIBUTE -- read before using the result
----------------------------------------------------------------------
It is a control on the BUILD, not on the CHANNEL. Neither C1 nor C2 uses
juggler, so if the stall is absent from both, that is consistent with TWO
different explanations and this arm cannot separate them:

    (a) persona's engine patches do not cause the stall, and the stall belongs
        to the juggler channel or to the launcher; or
    (b) the stall needs the juggler channel to be EXPRESSED at all, and this
        arm simply cannot reach it.

Do not read a quiet arm C as "stock Firefox is fine, therefore persona's build
is at fault" -- it says nothing of the kind. What it CAN do is falsify: if the
stall reproduces on C1 (plain upstream Firefox, nothing of persona's involved),
the defect is NOT persona's at all, and that would change the record's
conclusion outright. That asymmetry is the point of running it.

The instrument is the corrected whole-process-tree ``/proc`` sampler used by
arms A2/E/F (instantaneous ``utime+stime`` deltas), NOT the ``ps pcpu``
lifetime-average sampler used by arms B/D. Process counts here are therefore
comparable with A2/E/F and NOT with B/D.
"""
import json
import os
import signal
import socket
import subprocess
import sys
import time

BIN = os.environ["PS171_BIN"]
TAG = os.environ.get("PS171_TAG", "C")
NTABS = int(os.environ.get("PS171_NTABS", "5"))
DEADLINE = int(os.environ.get("PS171_DEADLINE", "40"))
RECOVERY = int(os.environ.get("PS171_RECOVERY", "40"))
PORT = int(os.environ.get("PS171_PORT", "2828"))
PROF = os.environ.get("PS171_PROFILE", "/tmp/ps171c/prof")
OUT = os.environ["PS171_OUT"]

# The /proc matcher keys on the directory the binary lives in, so each arm counts
# ITS OWN process tree and never the other arm's leftovers.
MATCH = os.path.dirname(os.path.realpath(BIN))
CLK = os.sysconf("SC_CLK_TCK")
_t0 = time.time()
_prev = {}
rec = {"tag": TAG, "bin": BIN, "match": MATCH, "ntabs": NTABS,
       "deadline_s": DEADLINE, "channel": "marionette", "tabs": [],
       "samples": []}


class Blocked(Exception):
    pass


signal.signal(signal.SIGALRM, lambda s, f: (_ for _ in ()).throw(Blocked()))


def say(m):
    print(m, flush=True)


def sample():
    """Whole-tree /proc sample: instantaneous CPU, RSS, per-thread states."""
    procs, cpu_total, thr = [], 0.0, {}
    for pid in sorted(p for p in os.listdir("/proc") if p.isdigit()):
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as fh:
                if MATCH not in fh.read().decode("utf8", "replace"):
                    continue
            with open("/proc/%s/stat" % pid) as fh:
                raw = fh.read()
        except Exception:
            continue
        t = raw[raw.rindex(")") + 2:].split()
        ticks = int(t[11]) + int(t[12])
        cpu = None
        p = _prev.get(pid)
        _prev[pid] = (ticks, time.time())
        if p:
            dt = time.time() - p[1]
            if dt > 0:
                cpu = round((ticks - p[0]) / CLK / dt * 100, 1)
                cpu_total += cpu
        try:
            for tid in os.listdir("/proc/%s/task" % pid):
                with open("/proc/%s/task/%s/stat" % (pid, tid)) as f2:
                    r2 = f2.read()
                s2 = r2[r2.rindex(")") + 2:].split()[0]
                thr[s2] = thr.get(s2, 0) + 1
        except Exception:
            pass
        procs.append({"pid": int(pid), "st": t[0],
                      "rss_mb": round(int(t[21]) * 4096 / 1048576, 1)})
    return {"t": round(time.time() - _t0, 1), "n": len(procs),
            "cpu": round(cpu_total, 1),
            "rss_mb": round(sum(p["rss_mb"] for p in procs), 1), "thr": thr}


def snap(phase):
    s = sample()
    s["phase"] = phase
    rec["samples"].append(s)
    say("  WD " + json.dumps(s))
    return s


# ---- marionette wire protocol (length-prefixed JSON) ----------------------
def recv(s):
    n = b""
    while not n.endswith(b":"):
        c = s.recv(1)
        if not c:
            raise RuntimeError("marionette socket closed")
        n += c
    need = int(n[:-1].decode())
    buf = b""
    while len(buf) < need:
        chunk = s.recv(need - len(buf))
        if not chunk:
            raise RuntimeError("marionette socket closed mid-frame")
        buf += chunk
    return json.loads(buf.decode())


_mid = [0]


def cmd(s, name, params):
    _mid[0] += 1
    b = json.dumps([0, _mid[0], name, params]).encode()
    s.sendall(str(len(b)).encode() + b":" + b)
    return recv(s)


def guarded(fn, deadline):
    """Run fn under a hard SIGALRM deadline so a stall is OBSERVED, not waited on."""
    a = time.time()
    signal.alarm(deadline)
    try:
        r = fn()
        signal.alarm(0)
        return True, round(time.time() - a, 2), "", r
    except Blocked:
        return False, round(time.time() - a, 2), "BLOCKED>%ds" % deadline, None
    except Exception as exc:
        signal.alarm(0)
        return (False, round(time.time() - a, 2),
                "%s: %s" % (type(exc).__name__, exc)[:150], None)


os.makedirs(PROF, exist_ok=True)
# The port MUST be set as a pref. ``MOZ_MARIONETTE_PORT`` is NOT read by Firefox
# -- an earlier attempt set it, Firefox listened on the default 2828 anyway, and
# the harness dialled the requested port and reported "never listened" while a
# perfectly healthy browser sat on the other one. The two arms run on different
# ports so a leftover process from one can never be dialled by the other.
with open(os.path.join(PROF, "user.js"), "w") as fh:
    fh.write('user_pref("browser.shell.checkDefaultBrowser", false);\n'
             'user_pref("datareporting.policy.dataSubmissionEnabled", false);\n'
             'user_pref("browser.sessionstore.resume_from_crash", false);\n'
             'user_pref("toolkit.telemetry.enabled", false);\n'
             'user_pref("marionette.port", %d);\n' % PORT)

env = {**os.environ, "MOZ_DISABLE_CONTENT_SANDBOX": "1", "MOZ_HEADLESS": "1",
       "MOZ_CRASHREPORTER_DISABLE": "1"}
errf = open(os.environ.get("PS171_STDERR", "/tmp/ps171c/ff.stderr"), "w")
a = time.time()
proc = subprocess.Popen([BIN, "--headless", "--marionette", "-profile", PROF],
                        stdout=errf, stderr=errf, env=env)
sock = None
for _ in range(120):
    try:
        sock = socket.create_connection(("127.0.0.1", PORT), 5)
        sock.settimeout(DEADLINE + 30)
        break
    except Exception:
        time.sleep(0.5)
if sock is None:
    rec["outcome"] = "NEVER_LISTENED"
    with open(OUT, "w") as fh:
        json.dump(rec, fh, indent=2)
    say("FAILED: marionette never listened")
    sys.exit(1)

rec["handshake"] = recv(sock)
rec["t_launch_s"] = round(time.time() - a, 2)
say("LAUNCH %.2fs | %s" % (rec["t_launch_s"], rec["handshake"]))
cmd(sock, "WebDriver:NewSession", {})
snap("session")

handles = []
try:
    for i in range(NTABS):
        n = i + 1
        e = {"tab": n}
        say("--- tab %d at t=%.1fs" % (n, time.time() - _t0))

        if n == 1:
            r = cmd(sock, "WebDriver:GetWindowHandles", {})
            handles = r[3] if isinstance(r[3], list) else r[3].get("value", [])
            e["t_new_tab_s"] = 0.0
            e["new_tab_ok"] = True
        else:
            ok, dt, err, r = guarded(
                lambda: cmd(sock, "WebDriver:NewWindow",
                            {"type": "tab", "focus": True}), DEADLINE)
            e["new_tab_ok"], e["t_new_tab_s"] = ok, dt
            if err:
                e["new_tab_err"] = err
            if ok:
                handles.append(r[3]["handle"])
        say("    new_tab %s in %ss" % (e["new_tab_ok"], e["t_new_tab_s"]))
        if not e["new_tab_ok"]:
            e["procs"] = snap("stall_tab%d" % n)
            rec["tabs"].append(e)
            rec["outcome"] = "STALLED_AT_TAB_%d" % n
            say("STALL at tab %d" % n)
            break

        ok, dt, err, _ = guarded(
            lambda: cmd(sock, "WebDriver:Navigate", {"url": "about:blank"}),
            DEADLINE)
        e["goto_ok"], e["t_goto_s"] = ok, dt
        if err:
            e["goto_err"] = err

        # Does the FIRST tab still answer while the newest one is open? Same
        # discriminator the juggler arms use ("the browser stopped answering"
        # vs "this one tab stalled").
        def ping():
            cmd(sock, "WebDriver:SwitchToWindow", {"handle": handles[0]})
            return cmd(sock, "WebDriver:ExecuteScript",
                       {"script": "return 1+1", "args": []})
        ok, dt, err, _ = guarded(ping, DEADLINE)
        e["ping_tab0_ok"], e["t_ping_tab0_s"] = ok, dt
        if err:
            e["ping_err"] = err
        if ok and handles:
            cmd(sock, "WebDriver:SwitchToWindow", {"handle": handles[-1]})
        e["procs"] = snap("tab%d" % n)
        rec["tabs"].append(e)
        say("TAB " + json.dumps(e))
    else:
        rec["outcome"] = "ALL_%d_TABS_OPENED" % NTABS
except Exception as exc:
    rec["outcome"] = "EXCEPTION %s: %s" % (type(exc).__name__, exc)
    say("EXCEPTION " + rec["outcome"])

# Recovery watch -- does it come back, or is it permanent? Same axis as arm F.
say("--- recovery watch %ds" % RECOVERY)
for _ in range(RECOVERY // 4):
    time.sleep(4)
    snap("recovery")

rec["t_total_s"] = round(time.time() - _t0, 1)
with open(OUT, "w") as fh:
    json.dump(rec, fh, indent=2)
say("RESULT " + json.dumps({k: v for k, v in rec.items() if k != "samples"}))
try:
    proc.terminate()
    proc.wait(10)
except Exception:
    proc.kill()
