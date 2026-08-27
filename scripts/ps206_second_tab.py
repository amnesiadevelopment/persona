"""PS-206 — does a SECOND tab fail to connect through the profile's proxy?

THE GAP THIS EXISTS TO CLOSE
----------------------------
Nothing else in this tree ever opens a second tab. Every check drives the FIRST
page through the automation driver, so a defect that appears only in a tab the
user opens afterwards is invisible to all of it, however green it goes.

The owner's report (3.0.1 / Windows, Firefox engine): the first page of a
session loads, and a tab he opens afterwards reports "unable to connect", which
he describes as looking like the network dropped.

WHAT THIS DRIVES
----------------
The PACKAGED engine (invisible_playwright + the pinned Firefox build), through a
REAL authenticated SOCKS5 proxy, using the product's OWN ``_proxy_dict`` and the
product's OWN proxied-profile prefs, in four legs:

  baseline    tabs via ctx.new_page(), healthy proxy
  content     tabs via window.open() from the live page - the closer analogue of
              a user's Ctrl-T, since in THIS Firefox new_page() opens a new
              WINDOW (invisible_launch.py ~3481)
  failover    break the proxy hop, prove the break BIT, heal it, verify the heal
              out-of-band, then open tabs - does the browser refuse on its own
              memory of a failure the network has already recovered from?
  concurrency hold N SOCKS connections open at once - can a live tab's sockets
              starve a new tab's?

TWO RULES THIS HARNESS ENFORCES ON ITSELF
-----------------------------------------
Both were learned by this harness producing a WRONG answer first.

1. **PRE-FLIGHT EVERY PROBE HOST, IN EVERY LEG.** A host that is simply dead
   through the proxy reads at the assertion site as "the tab could not
   connect". That produced a false REPRODUCED banner (api.my-ip.io: 0/5 through
   the proxy with curl, Firefox not involved). EVERY leg pre-flights - a dead
   host in baseline/content/concurrency yields an equally confident, equally
   false "DEFECT SEEN", and two of the five hosts below were already dead
   through one real proxy. Too few usable hosts ABORTS the leg as INVALID.

2. **THE FAULT MUST BE PROVEN TO BITE, AND THE HEAL PROVEN TO TAKE.** The first
   fault-injection run had all tabs riding ONE pooled keep-alive connection, so
   the injected break never reached a tab and the run went green while
   measuring nothing. Each tab now loads a DISTINCT host, and a green "tab
   during outage" ABORTS the run as INVALID rather than reporting "did not
   reproduce". The recovery is checked the same way and in the same direction:
   the upstream is a rotating backconnect gateway whose real exit can die on
   its own schedule, so if the proxy is not verifiably reachable again BEFORE
   any tab is touched, the run aborts as INVALID - otherwise [4] fails because
   the NETWORK is down and reports it as the product defect.

Both directions of PS-14: distrust an instrument that reports a failure, and
distrust one that reports a success.

EXIT CODE
---------
0 only when every leg run came back "no defect seen". Any DEFECT SEEN or any
INVALID (instrument) exits 1, so the outcome is unambiguous when this is run on
a machine none of us can watch and the result arrives as a pasted terminal tail.

USAGE
-----
    python3 scripts/ps206_second_tab.py all
    python3 scripts/ps206_second_tab.py baseline --tabs 5
    python3 scripts/ps206_second_tab.py failover [--mode polite|abrupt] [--pin-failover]
    python3 scripts/ps206_second_tab.py content
    python3 scripts/ps206_second_tab.py concurrency [--conns 48]

Needs a proxy url in PERSONA_TEST_PROXY, or --proxy-file <path> (one
``socks5://user:pass@host:port`` line). Requires Xvfb for the headless engine.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import socket
import socketserver
import struct
import sys
import tempfile
import threading
import time

try:
    import socks  # PySocks
except Exception:  # pragma: no cover - dependency is declared in requirements
    socks = None


# ---------------------------------------------------------------- proxy plumbing

def load_proxy_url(args) -> str:
    if args.proxy_file:
        return pathlib.Path(args.proxy_file).read_text(encoding="utf-8").strip()
    env = os.environ.get("PERSONA_TEST_PROXY", "").strip()
    if env:
        return env
    sys.exit(
        "no proxy configured: set PERSONA_TEST_PROXY or pass --proxy-file. "
        "This harness measures a PROXIED profile; a direct profile cannot "
        "exhibit the reported symptom."
    )


def proxy_dict(proxy_url: str):
    """Verbatim mirror of services/browser/invisible_launch.py::_proxy_dict.

    Copied rather than imported ON PURPOSE: this harness must measure what the
    SHIPPED launch builds, and an import would silently follow a refactor of
    that function while this docstring kept claiming otherwise.

    (Its socks5-only regex is a known defect owned by PS-217 - a non-socks5
    scheme falls to {"server": url} and drops credentials. Mirrored faithfully,
    warts and all, because mirroring the fixed version would measure a product
    that is not the one the user is running.)
    """
    if not proxy_url:
        return None
    m = re.match(r"socks5://(?:([^:]+):([^@]+)@)?(.+)", proxy_url)
    if not m:
        return {"server": proxy_url}
    user, pw, hostport = m.group(1), m.group(2), m.group(3)
    d = {"server": f"socks5://{hostport}"}
    if user:
        d["username"] = user
        d["password"] = pw
    return d


def upstream_parts(url: str):
    m = re.match(r"socks5://(?:([^:]+):([^@]+)@)?([^:@]+):(\d+)", url)
    if not m:
        sys.exit(f"cannot parse proxy url shape: {url[:24]}...")
    return m.group(3), int(m.group(4)), m.group(1), m.group(2)


# The proxied-profile prefs the SHIPPED Firefox launch applies
# (invisible_launch.py ~2350). Deliberately does NOT include
# network.proxy.failover_direct: the verify harness pins it
# (verify/browser_tier.py:194) and the shipped launch does not. That asymmetry
# is PS-217's to resolve; --pin-failover measures whether it matters here.
SHIPPED_PROXIED_PREFS = {
    "media.peerconnection.ice.relay_only": True,
    "media.peerconnection.ice.no_host": True,
    "media.peerconnection.ice.default_address_only": True,
    "media.peerconnection.ice.proxy_only_if_behind_proxy": True,
    "media.peerconnection.use_document_iceservers": False,
    "network.proxy.socks_remote_dns": True,
    "network.trr.mode": 5,
}

# One distinct host per tab: a tab that reuses another tab's pooled connection
# is not exercising a new proxy hop, which is the whole point of the question.
PROBE_HOSTS = [
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
    "https://icanhazip.com",
    "https://ipinfo.io/ip",
    "https://checkip.amazonaws.com",
]


# ------------------------------------------------------- controllable SOCKS shim

SHIM = {"fail": False, "conns": 0, "refused": 0, "forwarded": 0, "mode": "polite"}


def _recv_exactly(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed")
        buf += chunk
    return buf


def _pump(a, b):
    def one(src, dst):
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except Exception:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass

    t1 = threading.Thread(target=one, args=(a, b), daemon=True)
    t2 = threading.Thread(target=one, args=(b, a), daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()


class _ShimHandler(socketserver.BaseRequestHandler):
    """Speaks no-auth SOCKS5 to Firefox, adds credentials upstream.

    On demand it refuses, the way a rotating backconnect gateway's exit dies:
      polite - a well-formed SOCKS5 0x01 "general failure" reply, then close
      abrupt - close mid-handshake with no reply at all
    """

    upstream = None

    def handle(self):
        c = self.request
        SHIM["conns"] += 1
        up = None
        try:
            hdr = _recv_exactly(c, 2)
            _recv_exactly(c, hdr[1])
            c.sendall(b"\x05\x00")

            req = _recv_exactly(c, 4)
            atyp = req[3]
            if atyp == 1:
                addr = socket.inet_ntoa(_recv_exactly(c, 4))
            elif atyp == 3:
                addr = _recv_exactly(c, _recv_exactly(c, 1)[0]).decode()
            else:
                addr = socket.inet_ntop(socket.AF_INET6, _recv_exactly(c, 16))
            port = struct.unpack("!H", _recv_exactly(c, 2))[0]

            if SHIM["fail"]:
                SHIM["refused"] += 1
                try:
                    if SHIM["mode"] == "polite":
                        c.sendall(b"\x05\x01\x00\x01"
                                  + socket.inet_aton("0.0.0.0")
                                  + struct.pack("!H", 0))
                    c.close()
                except Exception:
                    pass
                return

            uh, up_port, uu, upw = _ShimHandler.upstream
            up = socks.socksocket()
            up.set_proxy(socks.SOCKS5, uh, up_port, rdns=True,
                         username=uu, password=upw)
            up.settimeout(30)
            up.connect((addr, port))
            up.settimeout(None)
            c.sendall(b"\x05\x00\x00\x01" + socket.inet_aton("0.0.0.0")
                      + struct.pack("!H", 0))
            SHIM["forwarded"] += 1
            _pump(c, up)
        except Exception:
            pass
        finally:
            for s in (c, up):
                if s is not None:
                    try:
                        s.close()
                    except Exception:
                        pass


class _Shim(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_shim(upstream):
    _ShimHandler.upstream = upstream
    srv = _Shim(("127.0.0.1", 0), _ShimHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


# ------------------------------------------------------------------- primitives

def shim_proxy(port):
    """The local shim's coordinates, in the same shape as ``upstream_parts``.

    It speaks no-auth SOCKS5 to its client and adds credentials upstream itself,
    hence the ``None`` user/pass.
    """
    return ("127.0.0.1", port, None, None)


def socks_reachable(proxy, host, timeout=25) -> bool:
    """Can ``host``:443 be reached through this SOCKS5 proxy?

    ``proxy`` is an ``upstream_parts``-shaped ``(host, port, user, pw)`` tuple,
    so the SAME check runs against the local shim and against the real upstream.
    It used to hardcode ``127.0.0.1`` because it was written for the shim, which
    is why only the shim-driven leg could pre-flight anything.
    """
    phost, pport, puser, ppw = proxy
    s = socks.socksocket()
    s.set_proxy(socks.SOCKS5, phost, pport, rdns=True,
                username=puser, password=ppw)
    s.settimeout(timeout)
    try:
        s.connect((host, 443))
        s.close()
        return True
    except Exception:
        return False


def preflight(proxy, urls):
    """Prove every probe host is reachable through the proxy BEFORE measuring.

    An unreachable probe host is an INSTRUMENT fault and reads at the assertion
    site exactly like the product defect being hunted. This already produced one
    false 'reproduced'.

    EVERY leg calls this, not just the fault-injection one: a dead host in
    baseline/content/concurrency yields an equally confident, equally false
    'DEFECT SEEN'. Two of the five hosts below were already dead through one
    real proxy, so this is a live risk on whatever proxy this next runs against.
    """
    print("pre-flight: probe hosts through the proxy")
    good = []
    for u in urls:
        host = u.split("/")[2]
        if socks_reachable(proxy, host):
            print(f"    {host:24} reachable")
            good.append(u)
        else:
            print(f"    {host:24} UNREACHABLE -> excluded (instrument, not product)")
    return good


def usable_hosts(proxy, minimum, leg):
    """Pre-flight, and refuse to measure at all on too degraded an instrument.

    Returns ``None`` (-> INVALID, never a product verdict) rather than a short
    list, because a leg that quietly measures fewer hosts than it was designed
    for is exactly the kind of unbounded result this ticket exists to stop.
    """
    good = preflight(proxy, PROBE_HOSTS)
    if len(good) < minimum:
        print(f"  ABORT ({leg}): {len(good)} usable probe host(s), need "
              f"{minimum}. Refusing to measure on a degraded instrument - "
              f"a dead host reads here exactly like the defect being hunted.")
        return None
    return good


def open_engine(proxy, prefs, profile_prefix):
    from invisible_playwright import InvisiblePlaywright

    return InvisiblePlaywright(
        headless=True,
        proxy=proxy,
        extra_prefs=prefs,
        profile_dir=tempfile.mkdtemp(prefix=profile_prefix),
    )


def load(page, label, url, timeout=45000):
    host = url.split("/")[2]
    t0 = time.time()
    try:
        resp = page.goto(url, timeout=timeout, wait_until="domcontentloaded")
        dt = (time.time() - t0) * 1000
        body = page.evaluate("document.body ? document.body.innerText.trim() : ''")
        print(f"  {label} [{host:22}] OK   status="
              f"{resp.status if resp else '?'} exit={body[:22]!r} {dt:.0f}ms",
              flush=True)
        return True, body
    except Exception as exc:
        dt = (time.time() - t0) * 1000
        msg = str(exc).splitlines()[0]
        print(f"  {label} [{host:22}] FAIL {dt:.0f}ms {msg[:100]}", flush=True)
        return False, msg


# ------------------------------------------------------------------------- legs

def leg_baseline(proxy_url, prefs, tabs):
    print("\n=== LEG: baseline (ctx.new_page(), healthy proxy) ===")
    usable = usable_hosts(upstream_parts(proxy_url), 2, "baseline")
    if usable is None:
        return None
    # ONE DISTINCT HOST PER TAB. Cycling hosts modulo would let two tabs ride
    # one pooled keep-alive connection, so the second would not exercise a new
    # proxy hop at all - which is the entire question. Pre-flight can exclude
    # hosts, so the tab count follows the survivors rather than the request.
    if tabs > len(usable):
        print(f"  NOTE: {tabs} tabs requested, {len(usable)} probe host(s) "
              f"survived pre-flight -> driving {len(usable)} tabs, one per "
              f"host (reusing a host would measure a pooled connection).")
        tabs = len(usable)
    results = []
    with open_engine(proxy_dict(proxy_url), prefs, "ps206-base-") as ctx:
        first = ctx.pages[0] if ctx.pages else ctx.new_page()
        results.append(load(first, "tab1 first ", usable[0])[0])
        for i in range(2, tabs + 1):
            results.append(load(ctx.new_page(), f"tab{i} opened", usable[i - 1])[0])
    print(f"  -> {sum(results)}/{len(results)} tabs loaded")
    return all(results)


def leg_content(proxy_url, prefs):
    """Tabs opened by CONTENT (window.open), the closer analogue of Ctrl-T."""
    print("\n=== LEG: content-opened tabs (window.open) ===")
    usable = usable_hosts(upstream_parts(proxy_url), 2, "content")
    if usable is None:
        return None
    p = dict(prefs)
    p["dom.disable_open_during_load"] = False
    results, exits = [], []
    with open_engine(proxy_dict(proxy_url), p, "ps206-content-") as ctx:
        first = ctx.pages[0] if ctx.pages else ctx.new_page()
        ok, body = load(first, "tab1 first ", usable[0])
        results.append(ok)
        exits.append(body)
        for i, url in enumerate(usable[1:], start=2):
            t0 = time.time()
            try:
                with ctx.expect_page(timeout=45000) as info:
                    first.evaluate("u => window.open(u, '_blank')", url)
                pg = info.value
                pg.wait_for_load_state("domcontentloaded", timeout=45000)
                body = pg.evaluate(
                    "document.body ? document.body.innerText.trim() : ''")
                print(f"  tab{i} win.open [{url.split('/')[2]:22}] OK   "
                      f"exit={body[:22]!r} {(time.time() - t0) * 1000:.0f}ms",
                      flush=True)
                results.append(True)
                exits.append(body)
            except Exception as exc:
                print(f"  tab{i} win.open [{url.split('/')[2]:22}] FAIL "
                      f"{(time.time() - t0) * 1000:.0f}ms "
                      f"{str(exc).splitlines()[0][:90]}", flush=True)
                results.append(False)
    distinct = len({e for e in exits if e})
    print(f"  -> {sum(results)}/{len(results)} tabs loaded; "
          f"{distinct} distinct exit IPs "
          f"({'fresh proxy connection per tab' if distinct > 1 else 'shared exit'})")
    return all(results)


def leg_failover(proxy_url, prefs, mode):
    """Break the proxy, PROVE it bit, heal it, PROVE it healed, then open tabs."""
    print(f"\n=== LEG: transient proxy failure (mode={mode}) ===")
    SHIM["mode"] = mode
    srv, port = start_shim(upstream_parts(proxy_url))
    try:
        shim = shim_proxy(port)
        usable = usable_hosts(shim, 4, "failover")
        if usable is None:
            return None

        with open_engine({"server": f"socks5://127.0.0.1:{port}"},
                         prefs, "ps206-failover-") as ctx:
            print("\n [1] first page, proxy healthy")
            first = ctx.pages[0] if ctx.pages else ctx.new_page()
            tab1_ok = load(first, "tab1 first ", usable[0])[0]

            print("\n [2] proxy hop BREAKS (a rotating exit dies)")
            SHIM["fail"] = True
            during_ok = load(ctx.new_page(), "tab2 outage", usable[1])[0]
            if during_ok:
                print("\n  !! INVALID RUN: the injected fault never reached the "
                      "tab (connection reuse).\n     Nothing is measured; not "
                      "reporting a result. !!")
                SHIM["fail"] = False
                return None
            print("     fault confirmed to bite.")

            print("\n [3] proxy HEALED, verified out-of-band before touching "
                  "Firefox")
            SHIM["fail"] = False
            healed = socks_reachable(shim, usable[2].split("/")[2])
            print(f"     shim reachable again: {healed}")
            if not healed:
                # The upstream is a ROTATING BACKCONNECT gateway: its real exit
                # can die on its own schedule, independently of the flag just
                # cleared. Continuing here would let [4] fail because the
                # NETWORK is down and report it as *** REPRODUCED *** - the
                # exact false-positive class this harness exists to refuse.
                print("\n  !! INVALID RUN: the proxy did not actually recover; "
                      "anything the tabs\n     report now is the network, not "
                      "the browser. !!")
                return None

            print("\n [4] tabs opened AFTER the heal   <-- THE MEASUREMENT")
            after = [load(ctx.new_page(), f"tab{i} healed", u)[0]
                     for i, u in enumerate(usable[2:5], start=3)]

            print("\n [5] the original tab, reloaded")
            reload_ok = load(first, "tab1 reload", usable[0])[0]
    finally:
        SHIM["fail"] = False
        try:
            srv.shutdown()
        finally:
            srv.server_close()

    print(f"\n  shim: conns={SHIM['conns']} refused={SHIM['refused']} "
          f"forwarded={SHIM['forwarded']}")
    print(f"  tab1 before outage : {'OK' if tab1_ok else 'FAIL'}")
    print(f"  tab during outage  : FAIL (fault confirmed to bite)")
    print(f"  proxy heal verified: OK (out-of-band, before any tab)")
    print(f"  tabs after heal    : {sum(after)}/{len(after)} OK")
    print(f"  original tab reload: {'OK' if reload_ok else 'FAIL'}")
    if tab1_ok and not all(after):
        print("\n  *** REPRODUCED *** a tab opened AFTER the proxy recovered "
              "still cannot connect:\n  the browser is refusing on its own "
              "memory of the failure, not on the network.")
        return False
    print("\n  Not reproduced: the browser recovered as soon as the proxy did.")
    return True


def leg_concurrency(proxy_url, n):
    """Can a live tab's held-open sockets starve a NEW tab's connection?"""
    print(f"\n=== LEG: concurrency ({n} held-open SOCKS connections) ===")
    host, port, user, pw = upstream_parts(proxy_url)
    usable = usable_hosts((host, port, user, pw), 1, "concurrency")
    if usable is None:
        return None
    # Pre-flighted, so 'established 0/N' now means the gateway refused the
    # CONCURRENCY - not that this one host happens to be dead through the proxy.
    target = usable[0].split("/")[2]
    print(f"  holding connections to {target}")
    results = {}

    def hold(i, seconds=10):
        s = socks.socksocket()
        s.set_proxy(socks.SOCKS5, host, port, rdns=True, username=user, password=pw)
        s.settimeout(30)
        try:
            s.connect((target, 443))
            results[i] = "ok"
            time.sleep(seconds)
            s.close()
        except Exception as exc:
            results[i] = f"FAIL:{type(exc).__name__}"

    threads = [threading.Thread(target=hold, args=(i,), daemon=True)
               for i in range(n)]
    for t in threads:
        t.start()
    time.sleep(6)
    ok = sum(1 for v in results.values() if v == "ok")
    print(f"  -> established {ok}/{n} concurrent connections")
    for t in threads:
        t.join(timeout=20)
    return ok >= n - 1


# ------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("leg", nargs="?", default="all",
                    choices=["all", "baseline", "content", "failover",
                             "concurrency"])
    ap.add_argument("--proxy-file")
    ap.add_argument("--tabs", type=int, default=5)
    ap.add_argument("--conns", type=int, default=48)
    ap.add_argument("--mode", default="polite", choices=["polite", "abrupt"])
    ap.add_argument("--pin-failover", action="store_true",
                    help="also set network.proxy.failover_direct=False, which "
                         "the verify harness pins and the shipped launch does not")
    args = ap.parse_args()

    if socks is None:
        sys.exit("PySocks is required (declared in requirements.txt)")

    proxy_url = load_proxy_url(args)
    prefs = dict(SHIPPED_PROXIED_PREFS)
    if args.pin_failover:
        prefs["network.proxy.failover_direct"] = False

    host, port, user, _ = upstream_parts(proxy_url)
    print(f"proxy {host}:{port} auth={'yes' if user else 'no'}   "
          f"failover_direct pinned={args.pin_failover}")

    verdicts = {}
    if args.leg in ("all", "baseline"):
        verdicts["baseline"] = leg_baseline(proxy_url, prefs, args.tabs)
    if args.leg in ("all", "content"):
        verdicts["content"] = leg_content(proxy_url, prefs)
    if args.leg in ("all", "failover"):
        verdicts["failover"] = leg_failover(proxy_url, prefs, args.mode)
    if args.leg in ("all", "concurrency"):
        verdicts["concurrency"] = leg_concurrency(proxy_url, args.conns)

    print("\n================ SUMMARY ================")
    for k, v in verdicts.items():
        state = {True: "no defect seen", False: "DEFECT SEEN",
                 None: "INVALID (instrument)"}[v]
        print(f"  {k:12} {state}")
    clean = all(v is True for v in verdicts.values())
    if clean:
        print("\n  The reported symptom did NOT reproduce in these legs.")
        print("  NOTE: this fleet is LINUX. needs_fork_launch() is Linux-only, so")
        print("  Windows takes the thread launch path and records pid 0 - the")
        print("  owner's platform is NOT measured by this run.")
    # Non-zero on anything that is not a clean 'did not reproduce'. The primary
    # use of this harness is someone running it on a machine none of us can
    # watch and sending back the result, so DEFECT SEEN and INVALID must be
    # unambiguous even when they arrive as a pasted terminal tail.
    sys.exit(0 if clean else 1)


if __name__ == "__main__":
    main()
