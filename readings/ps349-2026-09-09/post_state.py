"""PS-349 — post-run state of the jugwedge harness.

Matches the ENGINE PATH but excludes SELF and any process whose command line
merely CONTAINS the pattern because it is a probe printing it. That self-match
is not a hypothetical: it bit three times during this session (a `pkill -f`
killed the agent's own shell, and two `/proc` walks counted their own heredoc as
an engine process) — the same class `process_group.py`'s docstring warns about
for `pkill -f`, which is why that module keys on process groups and recorded
pids rather than on a command-line substring.
"""
import os
import sys
import time

PAT = ".cache/" + "invisible-playwright"
ME = os.getpid()
PARENT = os.getppid()


def cmdline(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return fh.read().decode("utf8", "replace")
    except Exception:
        return None


def is_engine(pid, cl):
    if pid in (ME, PARENT) or cl is None or PAT not in cl:
        return False
    # A probe prints the pattern; a real engine EXECUTES from that path, so its
    # argv[0] is under it.
    return cl.split("\x00")[0].startswith(os.path.expanduser("~/.cache"))


def cpu_pct(pid, dt=2.0):
    def ticks():
        st = open(f"/proc/{pid}/stat").read()
        f = st[st.rindex(")") + 2:].split()
        return int(f[11]) + int(f[12])
    try:
        a = ticks()
        time.sleep(dt)
        return round((ticks() - a) / os.sysconf("SC_CLK_TCK") / dt * 100, 1)
    except Exception:
        return None


engine = [(int(p), cmdline(int(p))) for p in os.listdir("/proc") if p.isdigit()]
engine = [(p, c) for p, c in engine if is_engine(p, c)]
print(f"surviving ENGINE processes: {len(engine)}")
for p, c in engine:
    print(f"   pid={p} {c.replace(chr(0),' ')[:110]}")

for pid in [int(a) for a in sys.argv[1:]]:
    cl = cmdline(pid)
    if cl is None:
        print(f"\npid {pid}: GONE")
        continue
    st = open(f"/proc/{pid}/stat").read()
    f = st[st.rindex(")") + 2:].split()
    btime = int([l for l in open("/proc/stat")
                 if l.startswith("btime")][0].split()[1])
    age = time.time() - (btime + int(f[19]) / os.sysconf("SC_CLK_TCK"))
    print(f"\npid {pid}: state={f[0]} age={age:.0f}s "
          f"threads={len(os.listdir(f'/proc/{pid}/task'))} "
          f"cpu={cpu_pct(pid)}% cmd={cl.replace(chr(0),' ')[:70]}")
