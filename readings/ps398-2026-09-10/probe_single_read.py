"""Same window probe, but under CPU CONTENTION and a short switch interval —
the conditions a loaded CI runner supplies and an idle container does not."""
import os, sys, threading, time
sys.path.insert(0, "/workspace/persona")
from src.services.browser.invisible_launch import get_ff_eval, register_ff_eval, unregister_ff_eval

NAME = "ps398-loaded"
TRIALS = 400
sys.setswitchinterval(0.000005)

stop = threading.Event()
def burn():
    x = 0
    while not stop.is_set():
        x += 1
for _ in range(4):
    threading.Thread(target=burn, daemon=True).start()

def trial():
    unregister_ff_eval(NAME)
    r, w = os.pipe(); out = os.fdopen(w, "w", buffering=1); reader = os.fdopen(r)
    res = {"v": None}
    def session():
        out.write("BROWSER_STARTED\n"); out.flush()
        def _live_page(): return None
        def _ff_eval(e): return None
        def _ff_goto(u): return None
        register_ff_eval(NAME, {"eval": _ff_eval, "goto": _ff_goto})
    def recorder():
        while True:
            line = reader.readline()
            if not line: return
            if line.strip() == "BROWSER_STARTED": break
        hook = get_ff_eval(NAME)
        res["v"] = not (hook and callable(hook.get("eval")))
    t = threading.Thread(target=recorder); t.start()
    session(); t.join(5); out.close(); reader.close()
    return res["v"]

misses = sum(1 for _ in range(TRIALS) if trial() is True)
stop.set()
print(f"loaded: trials={TRIALS} single-read MISSES={misses} rate={misses/TRIALS:.1%}")
unregister_ff_eval(NAME)
