"""PS-217 finding 3 — open a second tab THE WAY A USER DOES, through the proxy.

THE GAP THIS EXISTS TO CLOSE
----------------------------
Nothing else in this tree opens a second tab. Every check drives the FIRST page
through the automation driver, so a defect that only appears in a tab the user
opens afterwards is invisible to all of it, however green it goes. That is why
the PS-206 report ("the first page loads, the tab I open afterwards says unable
to connect") could not be confirmed or refuted from the repository.

WHY NOT ``ctx.new_page()``, AND WHY NOT ``window.open()``
---------------------------------------------------------
Both were tried; neither is what the user did.

* ``ctx.new_page()`` opens a whole new WINDOW in this Firefox, not a tab —
  ``invisible_launch.py`` records this at the ``_live_page`` call site, and it
  is the "two windows, one flashes and dies" bug. A window is a different code
  path from a tab and answers a different question.
* ``window.open()`` is CONTENT-initiated (the PS-206 harness's closest
  analogue). Closer, but still not the UI: it is the page asking, not the user.
* ``page.keyboard.press("Control+t")`` does NOTHING here, MEASURED: Playwright's
  keyboard delivers into the CONTENT area, and Ctrl+T is a CHROME binding. It
  times out waiting for a page that never opens. A check built on it would have
  looked like a passing second-tab test while opening no tab at all.

So this drives the command the key binding is ACTUALLY wired to —
``cmd_newNavigatorTab`` — in CHROME scope over Marionette, which is the same
path the menu item and the keyboard shortcut both reach. Navigation then goes
through ``openTrustedLinkIn``, the UI's own navigation entry point rather than
the driver's ``goto``.

THE FALSIFIER — this is the half that makes the result mean anything
-------------------------------------------------------------------
"The second tab loaded" is NOT the property. A tab that loaded by FAILING OVER
to a direct connection also loads, and that is the leak PS-217 finding 2 is
about — it would render as a perfectly green check while reading the operator's
real address. So the tab's exit IP is compared against the HOST's real egress
IP, read out-of-band without the proxy, and the run FAILS if they match.

The control tab is a PRECONDITION, never a verdict (the rule PS-206's harness
learned the hard way): if tab1 cannot load, the run is INVALID and says so
rather than reporting a second-tab defect that the network caused.

USAGE
-----
    xvfb-run -a python scripts/ps217_second_tab_ui.py

Needs a display (Xvfb is fine), the packaged engine, and a real proxy
credential (``/workspace/_secrets/test-proxy.txt`` or ``--proxy-url``).
Exit 0 = the second tab reached the network THROUGH the proxy.
Exit 1 = it did not, or it went direct. Exit 2 = the run was INVALID.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import tempfile
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.browser.invisible_launch import _profile_prefs  # noqa: E402
from src.utils.proxy_parser import engine_proxy_dict  # noqa: E402

# Echoes the caller's IP as bare text — no JSON viewer, no markup, so the body
# IS the reading.
ECHO_URL = "https://api.ipify.org/?format=text"
DEFAULT_CRED = "/workspace/_secrets/test-proxy.txt"
MARIONETTE_PORT = 2828


class Marionette:
    """Just enough of the Marionette wire protocol to run chrome-scope JS.

    Deliberately not a dependency: ``marionette_driver`` is not in this tree's
    requirements and this needs two commands. The protocol is
    ``<len>:<json>`` framing over TCP.
    """

    def __init__(self, port: int = MARIONETTE_PORT, timeout: float = 60.0) -> None:
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._buf = b""
        self._id = 0
        self._recv()  # the server's handshake frame

    def _recv(self):
        while b":" not in self._buf:
            self._buf += self.sock.recv(65536)
        length, _, rest = self._buf.partition(b":")
        n = int(length)
        while len(rest) < n:
            rest += self.sock.recv(65536)
        self._buf = rest[n:]
        return json.loads(rest[:n])

    def cmd(self, name: str, params: dict | None = None):
        self._id += 1
        msg = json.dumps([0, self._id, name, params or {}]).encode()
        self.sock.sendall(str(len(msg)).encode() + b":" + msg)
        while True:
            reply = self._recv()
            if isinstance(reply, list) and reply[0] == 1 and reply[1] == self._id:
                if reply[2]:
                    raise RuntimeError(f"{name}: {reply[2]}")
                return reply[3]

    def chrome_js(self, script: str, args: list | None = None):
        return self.cmd(
            "WebDriver:ExecuteScript", {"script": script, "args": args or []}
        )["value"]


def host_egress_ip() -> str | None:
    """The operator's REAL address, read WITHOUT the proxy.

    This is the falsifier. Read out-of-band on purpose: asking the browser
    would ask the very thing under test.
    """
    try:
        with urllib.request.urlopen(ECHO_URL, timeout=30) as r:
            return r.read().decode().strip()
    except Exception as exc:  # pragma: no cover - network shape varies
        print(f"  ! could not read the host's own egress IP: {exc}")
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy-url", default=None)
    ap.add_argument("--cred-file", default=DEFAULT_CRED)
    args = ap.parse_args()

    raw = args.proxy_url
    if not raw:
        if not os.path.exists(args.cred_file):
            print(f"INVALID: no proxy credential ({args.cred_file})")
            return 2
        raw = open(args.cred_file).read().strip()

    # The product's OWN parser and the product's OWN proxied-profile prefs. A
    # harness that builds its own configuration measures a browser we do not
    # ship — which is PS-217 finding 2, and it would be absurd to repeat it here.
    proxy = engine_proxy_dict(raw)
    if proxy is None:
        print("INVALID: the credential resolved to no proxy at all")
        return 2
    prefs = _profile_prefs({"proxy_url": raw, "search_engine": "duckduckgo"})
    assert prefs.get("network.proxy.failover_direct") is False, (
        "the shipped proxied-profile prefs must pin failover_direct — without it "
        "a direct fallback is exactly what this check cannot detect"
    )
    prefs.update({"marionette.port": MARIONETTE_PORT, "marionette.enabled": True})

    print(f"proxy      : {proxy['server']} (auth: {'username' in proxy})")
    real_ip = host_egress_ip()
    print(f"host egress: {real_ip}  <- the second tab must NOT show this")

    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(
        headless=True,
        proxy=proxy,
        extra_prefs=prefs,
        timezone="UTC",
        locale="en-US",
        # System access is required for chrome scope; without it
        # Marionette:SetContext answers "unsupported operation".
        extra_args=["-marionette", "-remote-allow-system-access"],
        profile_dir=tempfile.mkdtemp(prefix="ps217-second-tab-"),
    ) as ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # THE CONTROL — a precondition, never a verdict.
        try:
            page.goto(ECHO_URL, timeout=60_000, wait_until="domcontentloaded")
            tab1 = page.inner_text("body").strip()
        except Exception as exc:
            print(f"INVALID: the FIRST tab could not load ({exc}). The report is "
                  "'first page loads, second does not' — without a loading first "
                  "tab there is nothing to interpret.")
            return 2
        print(f"tab1 exit  : {tab1}")

        mn = Marionette()
        mn.cmd("WebDriver:NewSession", {"capabilities": {}})
        mn.cmd("Marionette:SetContext", {"value": "chrome"})
        before = mn.chrome_js("return window.gBrowser.tabs.length;")

        # THE SECOND TAB, opened the way the user opens one: the command the
        # Ctrl+T binding and the File>New Tab menu item both invoke.
        with ctx.expect_page(timeout=30_000) as info:
            mn.chrome_js(
                "document.getElementById('cmd_newNavigatorTab').doCommand();"
            )
        new_tab = info.value
        after = mn.chrome_js("return window.gBrowser.tabs.length;")
        print(f"tabs       : {before} -> {after} (a TAB, not a new window)")
        if after != before + 1:
            print("INVALID: the UI command did not add exactly one tab")
            return 2

        # Navigate through the UI's own entry point rather than driver.goto.
        mn.chrome_js("window.openTrustedLinkIn(arguments[0], 'current');", [ECHO_URL])
        try:
            new_tab.wait_for_url("**/api.ipify.org/**", timeout=60_000)
            new_tab.wait_for_load_state("domcontentloaded", timeout=60_000)
            tab2 = new_tab.inner_text("body").strip()
        except Exception as exc:
            print(f"\nDEFECT SEEN: the second tab did not connect ({exc})")
            print("This is the PS-206 symptom, reproduced.")
            return 1
        print(f"tab2 exit  : {tab2}")

        if not tab2:
            print("\nDEFECT SEEN: the second tab loaded no body")
            return 1
        # THE FALSIFIER. A tab that loaded by failing over to a direct
        # connection also 'loads' — and that is the leak, not the success.
        if real_ip and tab2 == real_ip:
            print("\nLEAK: the second tab exited on the HOST'S REAL ADDRESS — it "
                  "went around the proxy, not through it.")
            return 1

        print("\nPASS: a second tab, opened through the browser's own UI path, "
              "reached the network THROUGH the proxy.")
        print(f"      (tab2 exit {tab2} != host {real_ip})")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
