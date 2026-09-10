"""PS-349 SUBJECT — a persona session launched through the PRODUCT path, which
this process then holds and (in the degraded arms) degrades.

Run as a child of ``observe.py``. The observer holds ONLY this process's pid and
``/proc`` — the same thing ``BrowserLauncher`` holds. No object, queue or shared
memory crosses between them.

ARMS (``PS349_ARM``) — one control and three degradations that fail DIFFERENTLY
on purpose, because a health signal that catches one and misses another is the
finding this ticket asks for:

  ``healthy``  launch, hold a page, ping every 10s. The control. An IDLE but
               perfectly responsive session.
  ``sigstop``  launch, then SIGSTOP the whole engine tree. Ground truth is
               CERTAIN: every process is alive and none can answer. The
               instrument-validation arm — it exists so a null result in the
               other arms cannot be mistaken for a null instrument.
  ``spin``     launch, then run ``while(true){}`` on the page. The session is
               alive, its main thread is BUSY, and it answers nothing. Degrades
               with HIGH cpu.
  ``jugwedge`` launch, then open tabs over the juggler channel until one blocks
               — PS-171's gesture, re-run here as a degradation GENERATOR, not
               as a re-derivation of PS-171. Degrades with LOW cpu.

Writes ``.txt`` only. ``.gitignore:183`` is ``*.log`` — the trap that cost
PS-171 its arm A. Every path here was ``git check-ignore``'d before the run.
"""
import json
import os
import signal
import sys
import threading
import time

sys.path.insert(0, "/workspace/persona")

ARM = os.environ.get("PS349_ARM", "healthy")
NAME = os.environ.get("PS349_PROFILE", f"ps349-{ARM}")
HOLD = int(os.environ.get("PS349_HOLD", "150"))
NTABS = int(os.environ.get("PS349_NTABS", "8"))
DEADLINE = int(os.environ.get("PS349_DEADLINE", "40"))
ENGINE_DIR = os.path.expanduser("~/.cache/invisible-playwright")
T0 = time.time()


def say(msg):
    print(f"SUBJ {round(time.time() - T0, 1)} {msg}", flush=True)


class Blocked(Exception):
    pass


signal.signal(signal.SIGALRM, lambda s, f: (_ for _ in ()).throw(Blocked()))


def guarded(fn, deadline):
    """Bounded call. A stall becomes a RECORDED OUTCOME, never an absent one."""
    a = time.time()
    signal.alarm(deadline)
    try:
        v = fn()
        signal.alarm(0)
        return True, round(time.time() - a, 2), "", v
    except Blocked:
        return False, round(time.time() - a, 2), f"BLOCKED>{deadline}s", None
    except Exception as exc:
        signal.alarm(0)
        return False, round(time.time() - a, 2), f"{type(exc).__name__}: {exc}"[:160], None


def engine_pids():
    out = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                if ENGINE_DIR in fh.read().decode("utf8", "replace"):
                    out.append(int(pid))
        except Exception:
            continue
    return sorted(out)


say(f"arm={ARM} profile={NAME} pid={os.getpid()}")

from src.models.profile import Profile  # noqa: E402
from src.services.browser.process import spawn_browser, terminate  # noqa: E402
from src.services.browser.invisible_launch import get_ff_eval  # noqa: E402

rec = {"arm": ARM, "profile": NAME, "subject_pid": os.getpid(), "pings": []}
proc = None
try:
    if ARM == "jugwedge":
        # ⚠️ THIS ARM'S GENERATOR IS PS-171's ARM-F HARNESS, NOT PERSONA'S
        # LAUNCHER, and that is stated rather than glossed. The eval hook the
        # launcher publishes exposes `eval` and `goto` only — it does not
        # expose `ctx`, so `ctx.new_page()` (the juggler gesture that
        # reproduces) is not reachable through it, and PS-171 arm E already
        # measured `window.open` to be a NULL INSTRUMENT (three "ok" returns
        # in <0.15s with `ctx.pages` never leaving 1). Reaching the ctx would
        # mean editing the product to make a session easier to observe, which
        # this ticket forbids.
        #
        # It costs nothing for THIS ticket's question. The observer reads
        # `/proc` and nothing else, so what it sees is the engine tree — the
        # same tree, the same binary, the same channel — whoever called
        # `new_page`. This arm is a DEGRADED-SESSION GENERATOR, not a
        # re-derivation of PS-171 and not evidence on the launcher-vs-juggler
        # question.
        from invisible_playwright import InvisiblePlaywright
        from src.services.verify.masking_layer import DEFAULT_LOCALE, context_for
        say("launching via InvisiblePlaywright (PS-171 arm F configuration)")
        with InvisiblePlaywright(headless=True, humanize=False,
                                 locale=DEFAULT_LOCALE) as live:
            ctx, note = context_for(live)
            say(f"LAUNCH {note}")
            first = ctx.new_page()
            first.goto("about:blank", timeout=30000)
            rec["engine_pids_at_ready"] = engine_pids()
            say(f"ENGINE_PIDS {json.dumps(rec['engine_pids_at_ready'])}")
            say("READY")
            blocked_at = None
            for i in range(2, NTABS + 1):
                ok, secs, det, _ = guarded(
                    lambda: ctx.new_page().goto("about:blank",
                                                timeout=DEADLINE * 1000),
                    DEADLINE)
                say(f"tab{i} open ok={ok} {secs}s {det}")
                rec["pings"].append({"tag": "tab", "i": i, "ok": ok,
                                     "s": secs, "detail": det})
                pok, psecs, pdet, _ = guarded(
                    lambda: first.evaluate("1+1"), 8)
                say(f"  ping-tab1 ok={pok} {psecs}s {pdet}")
                rec["pings"].append({"tag": "ping", "i": i, "ok": pok,
                                     "s": psecs, "detail": pdet})
                if not ok:
                    blocked_at = i
                    say("DEGRADED")
                    break
            rec["blocked_at_tab"] = blocked_at
            if blocked_at is None:
                say("NO STALL — arm declined to reproduce")
                rec["outcome"] = "jugwedge-no-stall"
            else:
                time.sleep(HOLD)
                rok, rsecs, rdet, _ = guarded(lambda: first.evaluate("1+1"), 8)
                say(f"RECOVERY after {HOLD}s: ping ok={rok} {rsecs}s {rdet}")
                rec["recovery"] = {"after_s": HOLD, "ok": rok, "s": rsecs,
                                   "detail": rdet}
                rec["outcome"] = "jugwedge-held"
        say("TEARDOWN")
        out = os.environ.get("PS349_OUT")
        if out:
            with open(out, "w") as fh:
                json.dump(rec, fh, indent=2)
        say("RESULT " + json.dumps(rec))
        raise SystemExit(0)

    proc = spawn_browser(Profile(name=NAME, engine="firefox"), in_process=True)
    say(f"spawned handle={type(proc).__name__}")

    hook = None
    for _ in range(90):
        hook = get_ff_eval(NAME)
        if hook:
            break
        time.sleep(1)
    if not hook:
        rec["outcome"] = "NO_EVAL_HOOK"
        say("NO EVAL HOOK")
        raise SystemExit(1)

    ok, secs, det, val = guarded(lambda: hook["eval"]("1+1"), 15)
    say(f"first-eval ok={ok} {secs}s {det} -> {val}")
    rec["first_eval"] = {"ok": ok, "s": secs, "detail": det}
    pids = engine_pids()
    say(f"ENGINE_PIDS {json.dumps(pids)}")
    rec["engine_pids_at_ready"] = pids
    say("READY")

    def ping_loop(tag, n, gap=10, deadline=8):
        for i in range(n):
            time.sleep(gap)
            ok, secs, det, _ = guarded(lambda: hook["eval"]("1+1"), deadline)
            say(f"ping[{tag}] {i} ok={ok} {secs}s {det}")
            rec["pings"].append({"tag": tag, "i": i, "ok": ok, "s": secs,
                                 "detail": det})

    if ARM == "healthy":
        ping_loop("healthy", max(1, HOLD // 10))
        rec["outcome"] = "healthy-held"

    elif ARM == "sigstop":
        say(f"DEGRADE sigstop {len(pids)} pids")
        for p in pids:
            try:
                os.kill(p, signal.SIGSTOP)
            except Exception:
                pass
        rec["stopped_pids"] = pids
        say("DEGRADED")
        ok, secs, det, _ = guarded(lambda: hook["eval"]("1+1"), 20)
        say(f"post-degrade-eval ok={ok} {secs}s {det}")
        rec["post_degrade_eval"] = {"ok": ok, "s": secs, "detail": det}
        time.sleep(HOLD)
        for p in pids:
            try:
                os.kill(p, signal.SIGCONT)
            except Exception:
                pass
        rec["outcome"] = "sigstop-held"

    elif ARM == "spin":
        # A real, product-path degradation: the page's main thread never
        # yields, so the session is alive and answers nothing. Fired from a
        # daemon thread because the call never returns.
        say("DEGRADE spin (while(true){} on the page)")
        threading.Thread(
            target=lambda: hook["eval"]("while(true){}"), daemon=True
        ).start()
        time.sleep(5)
        say("DEGRADED")
        ok, secs, det, _ = guarded(lambda: hook["eval"]("1+1"), 20)
        say(f"post-degrade-eval ok={ok} {secs}s {det}")
        rec["post_degrade_eval"] = {"ok": ok, "s": secs, "detail": det}
        time.sleep(HOLD)
        rec["outcome"] = "spin-held"

    elif ARM == "busy":
        # ⭐ THE FALSE-POSITIVE CONTROL FOR THE CPU ARM, and the arm that
        # decides whether "pinned CPU" is a health signal at all. A healthy
        # session doing REAL WORK burns CPU too. If this arm's CPU reaches the
        # spin arm's band while the session keeps answering, then a
        # pinned-CPU rule fires on a user who is merely browsing — which is
        # the "green light over an unknown" trap running in reverse.
        # Ground truth: HEALTHY.
        say("BUSY: sustained real work on the page, still answering")
        threading.Thread(
            target=lambda: hook["eval"](
                # Real work, not a bare spin: allocate, hash, build DOM. It
                # yields between chunks, so the page stays responsive — which
                # is exactly what a browsing user's session does.
                "(function(){window.__ps349=0;"
                "function chunk(){var s=0;for(var i=0;i<3e6;i++){s+=Math.sqrt(i)|0;}"
                "window.__ps349+=s;setTimeout(chunk,0);} chunk(); return 'started';})()"
            ), daemon=True).start()
        time.sleep(5)
        say("DEGRADED")   # phase mark only — ground truth here is HEALTHY
        ping_loop("busy", max(1, HOLD // 10))
        rec["outcome"] = "busy-held"

    elif ARM in ("busyheavy", "busyheavy2", "busymax"):
        # ⭐ THE ARM THAT DECIDES THE CPU RULE. `busy` used ONE page doing one
        # chunked loop and peaked at 91% — just under the spin arm's 108%. But
        # that load was ARBITRARY: nothing about a user's real browsing caps it
        # at one core. This arm runs the same yielding, responsive work across
        # SEVERAL concurrent chunk loops, which is what a page with several
        # active scripts does. If a HEALTHY session can hold above the spin
        # arm's floor, the pinned-CPU rule has a false positive and the
        # composite does not survive. Ground truth: HEALTHY.
        say("BUSYHEAVY: multi-loop real work, still answering")
        threading.Thread(
            target=lambda: hook["eval"](
                "(function(){window.__ps349=0;"
                "function mk(){function chunk(){var s=0;"
                "for(var i=0;i<3e6;i++){s+=Math.sqrt(i)|0;}"
                "window.__ps349+=s;setTimeout(chunk,0);} chunk();}"
                "for(var k=0;k<LOOPS;k++){mk();} return 'started';})()".replace("LOOPS", os.environ.get("PS349_LOOPS","6"))
            ), daemon=True).start()
        time.sleep(5)
        say("DEGRADED")   # phase mark only — ground truth here is HEALTHY
        ping_loop("busyheavy", max(1, HOLD // 10))
        rec["outcome"] = "busyheavy-held"

    elif ARM == "unreachable":
        # The eval hook is torn out from under the session while the browser
        # stays perfectly alive and responsive. This is NOT a browser fault at
        # all — it is the OBSERVER losing its channel — and it is here because
        # a health check that reads through the automation channel must not
        # report a healthy browser as degraded. Ground truth: the session is
        # FINE.
        from src.services.browser.invisible_launch import unregister_ff_eval
        say("DEGRADE unregister eval hook (browser stays healthy)")
        unregister_ff_eval(NAME)
        say("DEGRADED")
        rec["post_degrade_eval"] = {"ok": False, "s": 0,
                                    "detail": "hook unregistered"}
        time.sleep(HOLD)
        rec["outcome"] = "unreachable-held"

    else:
        rec["outcome"] = f"unknown-arm:{ARM}"

except SystemExit:
    raise
except Exception as exc:
    rec["outcome"] = f"EXC {type(exc).__name__}: {exc}"[:250]
    say("EXC " + rec["outcome"])
finally:
    say("TEARDOWN")
    if proc is not None:
        try:
            terminate(proc, NAME, timeout=5)
        except Exception:
            pass
    out = os.environ.get("PS349_OUT")
    if out:
        with open(out, "w") as fh:
            json.dump(rec, fh, indent=2)
    say("RESULT " + json.dumps(rec))
