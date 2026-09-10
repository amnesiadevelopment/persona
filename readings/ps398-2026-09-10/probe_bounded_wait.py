import os, sys, threading, time
sys.path.insert(0, "/workspace/persona")
from src.services.browser.invisible_launch import get_ff_eval, register_ff_eval, unregister_ff_eval
from src.services.verify import baseline as bl

NAME = "ps398-loaded-fixed"; TRIALS = 400
sys.setswitchinterval(0.000005)
stop = threading.Event()
def burn():
    x = 0
    while not stop.is_set(): x += 1
for _ in range(4): threading.Thread(target=burn, daemon=True).start()

class _Alive:
    def poll(self): return None

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
        res["v"] = bl._await_ff_eval_hook(_Alive(), NAME) is None
    t = threading.Thread(target=recorder); t.start()
    session(); t.join(15); out.close(); reader.close()
    return res["v"]

started = time.monotonic()
misses = sum(1 for _ in range(TRIALS) if trial() is True)
el = time.monotonic() - started
stop.set()
print(f"loaded+fix: trials={TRIALS} MISSES={misses} rate={misses/TRIALS:.1%} elapsed={el:.2f}s ({el/TRIALS*1000:.2f} ms/trial)")
unregister_ff_eval(NAME)
