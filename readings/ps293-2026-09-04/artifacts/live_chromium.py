"""Live end-to-end trace of ONE hourly Chromium tick, into a throwaway ENGINE_DIR."""
import os, sys, time, json
os.environ["PERSONA_HOME"] = "/tmp/ps293/home"
sys.path.insert(0, "/workspace/persona")

from src.services.engine import updater as up, policy as pol

log = []
def L(m):
    log.append(f"{time.strftime('%H:%M:%S')} {m}")
    print(log[-1], flush=True)

L(f"ENGINE_DIR={up.ENGINE_DIR}")
L(f"is_installed (cold) = {up.is_installed()}")

# --- step 1: the fetch the hourly tick performs
t0=time.time()
tag, url, digest, verdict, message = up.fetch_latest_checked()
L(f"fetch_latest_checked -> tag={tag} verdict={verdict} msg={message!r} ({time.time()-t0:.1f}s)")
L(f"policy.check({tag}) -> {pol.check(tag)}  is_installable={pol.is_installable(tag)}")

# --- step 2: FIRST install (cold) so we then have something to UPGRADE
def prog(done,total):
    if total and done % (40*1024*1024) < 65536:
        L(f"  ... {done/1e6:.0f}/{total/1e6:.0f} MB")

t0=time.time()
ok = up.download_engine(url, digest=digest, progress=prog, defer_if_in_use=False, log=L, tag=tag)
L(f"cold download_engine -> {ok} ({time.time()-t0:.0f}s)")
if ok:
    up.record_installed_build(tag, digest)
    up.write_version(tag)
L(f"is_installed after cold install = {up.is_installed()}  current_version={up.current_version()!r}")

# --- step 3: simulate the UPGRADE tick. Pretend installed is older.
up.write_version("100.0.0.0")
L(f"forced current_version -> {up.current_version()!r}; is_newer({tag}) = {up.is_newer(tag, up.current_version())}")

# 3a. tree IN USE -> must defer, must NOT replace
up.set_in_use_provider(lambda: True)
try:
    t0=time.time()
    r = up.download_engine(url, digest=digest, progress=None, defer_if_in_use=True, log=L, tag=tag)
    L(f"UNEXPECTED: returned {r} instead of deferring")
except up.InstallDeferred as e:
    L(f"in-use unattended -> InstallDeferred({e}) after {time.time()-t0:.1f}s  (no re-download: asset reused)")
L(f"after deferral: version.txt still {up.current_version()!r}, is_installed={up.is_installed()}")

# 3b. no oracle wired -> fails CLOSED
up.set_in_use_provider(None)
try:
    up.download_engine(url, digest=digest, defer_if_in_use=True, log=L, tag=tag)
    L("UNEXPECTED: unwired oracle did not defer")
except up.InstallDeferred as e:
    L(f"unwired oracle -> InstallDeferred({e})")

# 3c. oracle raises -> fails CLOSED
up.set_in_use_provider(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
try:
    up.download_engine(url, digest=digest, defer_if_in_use=True, log=L, tag=tag)
    L("UNEXPECTED: raising oracle did not defer")
except up.InstallDeferred as e:
    L(f"raising oracle -> InstallDeferred({e})")

# 3d. tree IDLE -> the install actually lands
up.set_in_use_provider(lambda: False)
t0=time.time()
ok = up.download_engine(url, digest=digest, progress=None, defer_if_in_use=True, log=L, tag=tag)
L(f"idle unattended download_engine -> {ok} ({time.time()-t0:.0f}s, asset already on disk)")
if ok:
    up.record_installed_build(tag, digest)
    up.write_version(tag)
L(f"FINAL current_version={up.current_version()!r} is_installed={up.is_installed()}")
L(f"builds.json = {open(up.BUILDS_FILE).read() if os.path.exists(up.BUILDS_FILE) else 'absent'}")
L(f"ENGINE_DIR listing: {sorted(os.listdir(up.ENGINE_DIR))[:12]}")

open("/tmp/ps293/live_chromium.log","w").write("\n".join(log))
