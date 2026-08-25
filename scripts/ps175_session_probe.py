"""PS-175 probe — does a Firefox profile restore its tabs across a restart?

Drives the REAL launch path (spawn_browser → invisible_launch._child), opens a
tab, stops the profile the way production stops it, relaunches over the SAME
data dir, and reports what came back. Nothing here asserts on a pref: the
verdict is read from the live page after the restart and from the session file
on disk.

Run under xvfb-run with PERSONA_HOME exported BEFORE python starts.
"""
import json
import os
import queue
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------- mozlz4 ----
# Firefox stores the session as mozlz4: b"mozLz40\0" + uint32 LE raw size +
# an LZ4 *block*. No lz4 module on this box, so decode the block by hand.


def lz4_block_decompress(src: bytes, raw_size: int) -> bytes:
    out = bytearray()
    pos = 0
    end = len(src)
    while pos < end:
        token = src[pos]
        pos += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[pos]
                pos += 1
                lit += b
                if b != 255:
                    break
        out += src[pos:pos + lit]
        pos += lit
        if pos >= end:
            break
        offset = src[pos] | (src[pos + 1] << 8)
        pos += 2
        mlen = token & 0x0F
        if mlen == 15:
            while True:
                b = src[pos]
                pos += 1
                mlen += b
                if b != 255:
                    break
        mlen += 4
        start = len(out) - offset
        for i in range(mlen):
            out.append(out[start + i])
    return bytes(out[:raw_size])


def read_mozlz4(path: str):
    with open(path, "rb") as fh:
        blob = fh.read()
    if not blob.startswith(b"mozLz40\0"):
        return None
    raw_size = struct.unpack("<I", blob[8:12])[0]
    return json.loads(lz4_block_decompress(blob[12:], raw_size).decode("utf-8"))


def session_tab_urls(profile_dir: str):
    """Every tab URL Firefox currently holds in this profile's session store."""
    found = {}
    for rel in ("sessionstore.jsonlz4",
                os.path.join("sessionstore-backups", "recovery.jsonlz4"),
                os.path.join("sessionstore-backups", "previous.jsonlz4")):
        p = os.path.join(profile_dir, rel)
        if not os.path.exists(p):
            found[rel] = None
            continue
        try:
            data = read_mozlz4(p)
            urls = []
            for win in (data or {}).get("windows", []):
                for tab in win.get("tabs", []):
                    entries = tab.get("entries", [])
                    idx = max(0, tab.get("index", len(entries)) - 1)
                    if entries:
                        urls.append(entries[min(idx, len(entries) - 1)].get("url"))
            found[rel] = urls
        except Exception as e:  # noqa: BLE001
            found[rel] = f"<undecodable: {e}>"
    return found


# ------------------------------------------------------------- launching ----

def pump(proc, sink):
    for line in iter(proc.stdout.readline, ""):
        if not line:
            break
        sink.put(line.rstrip("\n"))


def launch(profile, wait=90.0, label=""):
    """Launch and wait for BROWSER_STARTED on a pump thread (never an inline
    readline: a session that starts then goes silent would block forever)."""
    from src.services.browser.process import spawn_browser

    proc = spawn_browser(profile, in_process=True)
    sink = queue.Queue()
    threading.Thread(target=pump, args=(proc, sink), daemon=True).start()
    lines = []
    deadline = time.monotonic() + wait
    started = False
    while time.monotonic() < deadline:
        try:
            line = sink.get(timeout=max(0.1, deadline - time.monotonic()))
        except queue.Empty:
            break
        lines.append(line)
        print(f"    [{label}] {line}", flush=True)
        if "BROWSER_STARTED" in line:
            started = True
            break
        if "LAUNCH_FAILED" in line or "BROWSER_CLOSED" in line:
            break
    return proc, sink, lines, started


def stop_like_production(proc, name, timeout=2):
    """Exactly what launcher.stop_profile does: process.terminate(proc, name,
    timeout) — SIGTERM/stop_event, then SIGKILL after `timeout` seconds."""
    from src.services.browser.invisible_launch import unregister_ff_eval
    from src.services.browser.process import terminate

    t0 = time.monotonic()
    terminate(proc, name, timeout)
    unregister_ff_eval(name)
    return time.monotonic() - t0


def drain(sink, seconds=6.0, label=""):
    deadline = time.monotonic() + seconds
    out = []
    while time.monotonic() < deadline:
        try:
            line = sink.get(timeout=max(0.05, deadline - time.monotonic()))
        except queue.Empty:
            break
        out.append(line)
        print(f"    [{label}] {line}", flush=True)
    return out


def main():
    stop_timeout = float(os.environ.get("PS175_STOP_TIMEOUT", "2"))
    settle = float(os.environ.get("PS175_SETTLE", "8"))
    with_bookmarks = os.environ.get("PS175_BOOKMARKS") == "1"
    name = os.environ.get("PS175_PROFILE", "ps175-restore")

    from src.core.config import DATA_DIR
    from src.services.browser.invisible_launch import get_ff_eval
    from src.services.profile.manager import ProfileManager

    # A local page, so the measurement never depends on the network.
    page_dir = os.path.join(os.environ["PERSONA_HOME"], "pages")
    os.makedirs(page_dir, exist_ok=True)
    tab_url = "file://" + os.path.join(page_dir, "tab1.html")
    with open(os.path.join(page_dir, "tab1.html"), "w") as fh:
        fh.write("<html><head><title>PS175-TAB-ONE</title></head>"
                 "<body><h1>ps175 tab one</h1></body></html>")

    pm = ProfileManager()
    if name in pm.profiles:
        pm.delete_profile(name, permanent=True)
    kwargs = dict(proxy=None, os_type="windows", engine="firefox")
    if with_bookmarks:
        kwargs["bookmarks"] = ["https://example.com"]
    assert pm.add_profile(name, **kwargs), "add_profile failed"
    profile = pm.profiles[name]

    data_dir = os.path.join(DATA_DIR, name)
    inner = os.path.join(data_dir, ".invisible-profile")

    print(f"\n=== PS-175 probe: stop_timeout={stop_timeout}s "
          f"bookmarks={with_bookmarks} settle={settle}s ===")
    print(f"data_dir = {data_dir}")

    # ---- launch 1 -----------------------------------------------------
    print("\n--- LAUNCH 1 (fresh) ---")
    proc, sink, _, started = launch(profile, label="L1")
    if not started:
        print("RESULT: launch 1 never reported BROWSER_STARTED — unobtained reading")
        return 3
    hook = get_ff_eval(name)
    print(f"  eval hook: {'present' if hook else 'MISSING'}")
    if not hook:
        stop_like_production(proc, name, stop_timeout)
        print("RESULT: no eval hook — unobtained reading")
        return 3

    hook["goto"](tab_url)
    time.sleep(2.0)
    print(f"  tab now at: {hook['eval']('location.href')}")
    print(f"  tab title : {hook['eval']('document.title')}")

    # Let the periodic sessionstore write (interval=1500ms) run.
    print(f"  settling {settle}s so sessionstore's periodic write lands...")
    time.sleep(settle)
    print(f"  session file BEFORE close: "
          f"{json.dumps(session_tab_urls(inner), indent=2)}")

    # ---- stop, the way production stops --------------------------------
    print(f"\n--- STOP (production path, timeout={stop_timeout}s) ---")
    took = stop_like_production(proc, name, stop_timeout)
    print(f"  terminate() returned after {took:.2f}s")
    drain(sink, 6.0, label="L1")
    time.sleep(2.0)
    after = session_tab_urls(inner)
    print(f"  session file AFTER close: {json.dumps(after, indent=2)}")

    # ---- launch 2, same data dir = the restart -------------------------
    print("\n--- LAUNCH 2 (restart over the same data dir) ---")
    profile = ProfileManager().profiles[name]
    proc2, sink2, _, started2 = launch(profile, label="L2")
    if not started2:
        print("RESULT: launch 2 never reported BROWSER_STARTED — unobtained reading")
        return 3
    hook2 = get_ff_eval(name)
    time.sleep(4.0)
    restored_href = hook2["eval"]("location.href") if hook2 else None
    restored_title = hook2["eval"]("document.title") if hook2 else None
    print(f"  live page after restart: href={restored_href!r} "
          f"title={restored_title!r}")
    print(f"  session file after restart: "
          f"{json.dumps(session_tab_urls(inner), indent=2)}")

    stop_like_production(proc2, name, stop_timeout)
    drain(sink2, 5.0, label="L2")

    print("\n=== VERDICT ===")
    tabs_back = restored_href == tab_url or restored_title == "PS175-TAB-ONE"
    print(f"  tab opened before restart : {tab_url}")
    print(f"  live page after restart   : {restored_href}")
    print(f"  TABS RESTORED: {tabs_back}")
    return 0 if tabs_back else 1


if __name__ == "__main__":
    sys.exit(main())
