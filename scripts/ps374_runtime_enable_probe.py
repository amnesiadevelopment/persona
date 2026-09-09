"""PS-374 — read patch 001's two `Runtime` sites off a LIVE CDP channel.

WHY THIS EXISTS
───────────────
`engine/patches/fingerprint/001-disable-runtime.enable.patch` hard-codes
`V8RuntimeAgentImpl::enabled()` to `false` and routes two call sites through it.
Nothing in `tests/` or `.github/` mentioned `Runtime.enable` before this script
(`git grep -rln "Runtime.enable" -- tests/ .github/` → zero hits), so a rebase
that dropped or mangled the patch would be SILENT: the engine still builds, no
test reddens, and every AI-controlled chromium profile starts announcing
execution contexts again.

This is the instrument that makes that visible. It launches a chromium binary
with a real `--remote-debugging-port`, speaks CDP over a raw stdlib WebSocket
(no playwright — it is not a dependency of this project), calls `Runtime.enable`
and reads what comes back.

⛔ IT MEASURES; IT DOES NOT REPAIR. Nothing here edits a patch, and no fix for
the leak site named below is implemented by this file. PS-374 is a verification
ticket.

THE TWO SITES, AND WHY BOTH ARE READ
─────────────────────────────────────
Patch 001 converts exactly two `m_enabled` reads in `v8-runtime-agent-impl.cc`:

  SITE B  `messageAdded`                 → `if (enabled()) reportMessage(...)`
  (and the globalBindings guard in the context-created path)

It does NOT convert `reportExecutionContextCreated`, which still reads the raw
field. So the two sites answer two DIFFERENT questions and must not be conflated:

  SITE A — `Runtime.executionContextCreated` announced?
      This is the LEAK the ticket asks about. It is NOT evidence about whether
      our patch is present, because the patch never covered this site.

  SITE B — is a console message logged AFTER `Runtime.enable` reported?
      ⭐ THIS is the patch oracle. A binary carrying patch 001 must SUPPRESS it;
      an unpatched binary reports it. It is the only one of the two that can
      tell "the patch is in this binary" from "the patch was lost in a rebase",
      which is the question the guard exists to answer.

⚠️ SITE B IS COMPARED BY PAYLOAD, NEVER BY COUNT. `Runtime.enable` REPLAYS
stored console history on a different code path from `messageAdded`, so a
message logged BEFORE enable arrives on a patched binary too. A first pass at
this compared `consoleAPICalled` cardinality (1 vs 2) — real numbers, and
uninterpretable. Two distinctly-marked messages, one logged before enable and
one after, is what turns it into an oracle: only the AFTER marker exercises the
patched site.

  GENERAL RULE FOR THIS DOMAIN: whenever a CDP domain replays history on enable,
  distinguish by PAYLOAD, never by cardinality.

SIX PRECONDITIONS, AND THE PROBE REFUSES RATHER THAN REPORTS CLEAN
───────────────────────────────────────────────────────────────────
An absence assertion passes hardest when the channel was never live: "the realm
is clean" and "the realm was never reached" are the same green, and on this
vector the second is the more likely failure. So every reading is gated behind
six separately-asserted preconditions, and any failure yields INCONCLUSIVE —
never "no leak":

  P1  the debugging endpoint answered
  P2  a page target exists
  P3  the WebSocket handshake returned 101
  P4  `Page.navigate` was acked
  P4b `1 + 1` evaluated to `2` — a REAL execution context exists
  P5  ⭐ an UNSOLICITED event actually arrived — the event channel is LIVE
  P6  `Runtime.enable` was acked with no error object

P5 is the load-bearing one and the easy one to omit.

BOUNDS OF WHAT THIS INSTRUMENT CAN SAY
───────────────────────────────────────
* Linux only, `--headless=new`. Headful and the Windows/macOS assets are
  unmeasured by this script.
* It reads the CDP channel directly. ⛔ No checker verdict (creepjs, iphey) is
  consulted, and none would be evidence about this patch — those read many bot
  signals at once.
* It says nothing about the fingerprint axis (canvas/WebGL noise, PS-373). This
  is automation-detection only.

USAGE
─────
    python3 -m scripts.ps374_runtime_enable_probe \\
        --binary /path/to/chrome --label personium \\
        -o readings/ps374-.../artifacts/personium.json

    python3 -m scripts.ps374_runtime_enable_probe --self-test
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import os
import pathlib
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

TICKET = "PS-374"

#: The console markers. Distinct strings, so the reading is by PAYLOAD.
MARKER_BEFORE = "ps374-console-BEFORE-enable"
MARKER_AFTER = "ps374-console-AFTER-enable"

#: Every precondition, in the order the probe asserts them. Named here so the
#: verdict function and the probe cannot drift about what the six are.
PRECONDITIONS = (
    "p1_endpoint_answered",
    "p2_page_target",
    "p3_ws_handshake_101",
    "p4_page_navigate_acked",
    "p4b_execution_context_real",
    "p5_unsolicited_event_arrived",
    "p6_runtime_enable_acked",
)

# Exit codes. INCONCLUSIVE is deliberately distinct from both verdicts: "I could
# not measure this" and "this is fine" are different answers.
EXIT_PATCH_PRESENT = 0
EXIT_PATCH_LOST = 1
EXIT_INCONCLUSIVE = 2


# ───────────────────────────────────────────────────────────────────────────
# A minimal RFC 6455 client. websockets/websocket-client are not dependencies
# of this project and a probe must not add one to be runnable.
# ───────────────────────────────────────────────────────────────────────────
class MiniWS:
    """Just enough WebSocket to speak CDP: text frames, client-masked, no
    fragmentation, no permessage-deflate (DevTools does not offer it)."""

    def __init__(self, url: str, timeout: float = 20.0) -> None:
        m = re.match(r"ws://([^/:]+):(\d+)(/.*)$", url)
        if not m:
            raise ValueError(f"not a ws:// url: {url}")
        host, port, path = m.group(1), int(m.group(2)), m.group(3)
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self.sock.sendall(req.encode())
        self._buf = b""
        head = self._read_until(b"\r\n\r\n")
        self.status_line = head.split(b"\r\n", 1)[0].decode("latin-1")
        self.handshake_101 = " 101 " in f" {self.status_line} "
        if self.handshake_101:
            expect = base64.b64encode(
                hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
            ).decode()
            self.accept_ok = expect.encode() in head
        else:
            self.accept_ok = False

    def _read_until(self, sentinel: bytes) -> bytes:
        while sentinel not in self._buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("socket closed during handshake")
            self._buf += chunk
        head, self._buf = self._buf.split(sentinel, 1)
        return head + sentinel

    def _recv_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self._buf)))
            if not chunk:
                raise ConnectionError("socket closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send(self, text: str) -> None:
        payload = text.encode()
        header = bytearray([0x81])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < (1 << 16):
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def recv(self, timeout: float | None = None) -> str | None:
        """One text frame, or None on timeout / close / a non-text frame."""
        if timeout is not None:
            self.sock.settimeout(timeout)
        try:
            b0, b1 = self._recv_exact(2)
        except (socket.timeout, TimeoutError, ConnectionError):
            return None
        opcode = b0 & 0x0F
        n = b1 & 0x7F
        try:
            if n == 126:
                n = struct.unpack(">H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._recv_exact(8))[0]
            payload = self._recv_exact(n) if n else b""
        except (socket.timeout, TimeoutError, ConnectionError):
            return None
        if opcode == 0x9:  # ping -> pong, then keep reading
            return self.recv(timeout)
        if opcode == 0x8:  # close
            return None
        if opcode != 0x1:
            return self.recv(timeout)
        return payload.decode("utf-8", "replace")

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.sock.close()


# ───────────────────────────────────────────────────────────────────────────
# The verdict. Pure, so it can be tested without a browser.
# ───────────────────────────────────────────────────────────────────────────
def verdict(reading: dict) -> "tuple[int, str, dict]":
    """Decide what a reading says about PATCH 001 — not about the leak.

    ⭐ The verdict keys on SITE B (console after enable), because that is the
    site patch 001 actually covers. Site A (`executionContextCreated`) is
    recorded and reported, and is deliberately NOT part of the pass/fail: the
    patch never covered it, so a leak there is a scope fact about the patch, not
    evidence that the patch went missing. Keying the guard on site A would make
    it permanently red against a correctly-patched binary, and a gate that
    cannot pass teaches people to ignore it.
    """
    pre = reading.get("preconditions") or {}
    missing = [p for p in PRECONDITIONS if not pre.get(p)]
    if missing:
        return (
            EXIT_INCONCLUSIVE,
            f"INCONCLUSIVE — preconditions not met: {', '.join(missing)}",
            {"unmet_preconditions": missing},
        )

    site_b_after = bool(reading.get("console_after_enable_reported"))
    site_b_before = bool(reading.get("console_before_enable_reported"))
    site_a = int(reading.get("execution_context_created_count") or 0)

    detail = {
        "unmet_preconditions": [],
        "site_a_execution_context_created": site_a,
        "site_b_console_after_enable_reported": site_b_after,
        "site_b_console_before_enable_reported": site_b_before,
        "leak_present": site_a > 0,
    }

    if site_b_after:
        return (
            EXIT_PATCH_LOST,
            "PATCH 001 LOST OR NOT ACTING — a console message logged AFTER "
            f"Runtime.enable was reported ({MARKER_AFTER!r} arrived). "
            "V8RuntimeAgentImpl::messageAdded is reaching reportMessage, which "
            "means enabled() is not hard-coded false in this binary.",
            detail,
        )

    return (
        EXIT_PATCH_PRESENT,
        "PATCH 001 PRESENT AND ACTING — console reporting is suppressed after "
        "Runtime.enable"
        + (
            f"; NOTE site A still announces executionContextCreated x{site_a} "
            "(reportExecutionContextCreated was never in patch 001's scope)"
            if site_a
            else "; and no executionContextCreated was announced"
        ),
        detail,
    )


# ───────────────────────────────────────────────────────────────────────────
# The live arm.
# ───────────────────────────────────────────────────────────────────────────
def _http_json(url: str, timeout: float = 10.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 (loopback)
        return json.loads(r.read().decode())


def _wait_for_endpoint(port_file: pathlib.Path, deadline: float) -> int | None:
    while time.time() < deadline:
        if port_file.exists():
            try:
                first = port_file.read_text(encoding="utf-8").splitlines()[0].strip()
                if first.isdigit() and int(first) > 0:
                    return int(first)
            except (OSError, IndexError):
                pass
        time.sleep(0.2)
    return None


def measure(binary: str, label: str, *, timeout: float = 60.0) -> dict:
    """Launch ``binary``, drive the two sites, return a reading dict."""
    reading: dict = {
        "ticket": TICKET,
        "label": label,
        "binary": binary,
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "preconditions": {p: False for p in PRECONDITIONS},
        "events_seen": [],
        "execution_context_created_count": 0,
        "console_before_enable_reported": False,
        "console_after_enable_reported": False,
        "errors": [],
    }

    real = pathlib.Path(binary).resolve()
    if not real.exists():
        reading["errors"].append(f"binary does not exist: {binary}")
        return reading
    try:
        v = subprocess.run(  # noqa: S603
            [str(real), "--version"], capture_output=True, text=True, timeout=30
        )
        reading["version_string"] = (v.stdout or v.stderr).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        reading["errors"].append(f"--version failed: {exc}")

    user_dir = tempfile.mkdtemp(prefix="ps374-profile-")
    proc = None
    ws = None
    deadline = time.time() + timeout
    try:
        args = [
            str(real),
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={user_dir}",
            # The two switches process.py appends for an ai_control profile.
            "--remote-debugging-port=0",
            "--remote-allow-origins=*",
            "about:blank",
        ]
        proc = subprocess.Popen(  # noqa: S603
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
        )
        port = _wait_for_endpoint(pathlib.Path(user_dir) / "DevToolsActivePort", deadline)
        if port is None:
            reading["errors"].append("DevToolsActivePort never appeared")
            return reading
        reading["cdp_port"] = port

        # ── P1 ─────────────────────────────────────────────────────────────
        version = None
        while time.time() < deadline:
            try:
                version = _http_json(f"http://127.0.0.1:{port}/json/version")
                break
            except (urllib.error.URLError, OSError, json.JSONDecodeError):
                time.sleep(0.2)
        if version is None:
            reading["errors"].append("P1: /json/version never answered")
            return reading
        reading["preconditions"]["p1_endpoint_answered"] = True
        reading["browser_string"] = version.get("Browser")
        reading["v8_version"] = version.get("V8-Version")

        # ── P2 ─────────────────────────────────────────────────────────────
        target = None
        while time.time() < deadline:
            try:
                targets = _http_json(f"http://127.0.0.1:{port}/json/list")
            except (urllib.error.URLError, OSError, json.JSONDecodeError):
                targets = []
            pages = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
            if pages:
                target = pages[0]
                break
            time.sleep(0.2)
        if target is None:
            reading["errors"].append("P2: no page target with a webSocketDebuggerUrl")
            return reading
        reading["preconditions"]["p2_page_target"] = True

        # ── P3 ─────────────────────────────────────────────────────────────
        ws = MiniWS(target["webSocketDebuggerUrl"], timeout=15.0)
        reading["ws_status_line"] = ws.status_line
        if not (ws.handshake_101 and ws.accept_ok):
            reading["errors"].append(f"P3: handshake not 101/accept-ok ({ws.status_line})")
            return reading
        reading["preconditions"]["p3_ws_handshake_101"] = True

        seq = [0]
        events: list[dict] = []

        def call(method: str, params: dict | None = None, wait: float = 15.0):
            seq[0] += 1
            mid = seq[0]
            ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
            end = time.time() + wait
            while time.time() < end:
                raw = ws.recv(timeout=max(0.2, end - time.time()))
                if raw is None:
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("id") == mid:
                    return msg
                if "method" in msg:
                    events.append(msg)
            return None

        def drain(seconds: float) -> None:
            end = time.time() + seconds
            while time.time() < end:
                raw = ws.recv(timeout=max(0.05, end - time.time()))
                if raw is None:
                    continue
                with contextlib.suppress(json.JSONDecodeError):
                    msg = json.loads(raw)
                    if "method" in msg:
                        events.append(msg)

        # ── P4: a real page, so a real execution context exists ────────────
        page_enable = call("Page.enable")
        nav = call(
            "Page.navigate",
            {"url": "data:text/html,<title>ps374</title><h1>ps374</h1>"},
        )
        if not (nav and "result" in nav and nav["result"].get("frameId")):
            reading["errors"].append(f"P4: Page.navigate not acked ({nav})")
            return reading
        reading["preconditions"]["p4_page_navigate_acked"] = True
        drain(1.5)

        # ── P4b: prove the context is REAL, not merely announced ───────────
        ev = call("Runtime.evaluate", {"expression": "1+1", "returnByValue": True})
        if not (ev and ev.get("result", {}).get("result", {}).get("value") == 2):
            reading["errors"].append(f"P4b: 1+1 did not evaluate to 2 ({ev})")
            return reading
        reading["preconditions"]["p4b_execution_context_real"] = True

        # ── P5: an UNSOLICITED event must have arrived ─────────────────────
        # Without this, "no leak" and "the channel was never live" are the same
        # green — and on this vector the second is the more likely failure.
        if page_enable is None:
            reading["errors"].append("P5 setup: Page.enable was not acked")
            return reading
        if not events:
            drain(2.0)
        reading["preconditions"]["p5_unsolicited_event_arrived"] = bool(events)
        if not events:
            reading["errors"].append("P5: no unsolicited event ever arrived — channel not live")
            return reading

        # ── SITE B setup: a console message logged BEFORE enable ───────────
        # It arrives on a patched binary too (Runtime.enable REPLAYS history on
        # a different path), which is exactly why the AFTER marker is the oracle
        # and the comparison is by payload rather than by count.
        call(
            "Runtime.evaluate",
            {"expression": f"console.log({MARKER_BEFORE!r})", "returnByValue": True},
        )
        drain(0.5)

        # ── P6: Runtime.enable, acked with no error ────────────────────────
        before_index = len(events)
        enabled = call("Runtime.enable")
        reading["runtime_enable_reply"] = enabled
        if not (enabled and "result" in enabled and "error" not in enabled):
            reading["errors"].append(f"P6: Runtime.enable not cleanly acked ({enabled})")
            return reading
        reading["preconditions"]["p6_runtime_enable_acked"] = True
        drain(2.0)

        # ── SITE B reading: a console message logged AFTER enable ──────────
        call(
            "Runtime.evaluate",
            {"expression": f"console.log({MARKER_AFTER!r})", "returnByValue": True},
        )
        drain(2.0)

        post = events[before_index:]
        reading["events_seen"] = sorted({e.get("method", "") for e in post})
        reading["all_events_seen"] = sorted({e.get("method", "") for e in events})
        reading["execution_context_created_count"] = sum(
            1 for e in post if e.get("method") == "Runtime.executionContextCreated"
        )
        reading["execution_context_created_payloads"] = [
            e.get("params") for e in post if e.get("method") == "Runtime.executionContextCreated"
        ]

        console_text = []
        for e in events:
            if e.get("method") != "Runtime.consoleAPICalled":
                continue
            for arg in (e.get("params") or {}).get("args") or []:
                if isinstance(arg.get("value"), str):
                    console_text.append(arg["value"])
        reading["console_payloads"] = console_text
        reading["console_before_enable_reported"] = MARKER_BEFORE in console_text
        reading["console_after_enable_reported"] = MARKER_AFTER in console_text
        return reading
    except Exception as exc:  # noqa: BLE001 — a probe records its own failure
        reading["errors"].append(f"{type(exc).__name__}: {exc}")
        return reading
    finally:
        if ws is not None:
            ws.close()
        if proc is not None:
            # ⚠️ REAP THE GROUP, NOT THE PID. A browser is not a leaf process:
            # chromium forks a zygote, a gpu-process, a crashpad handler and a
            # renderer per tab, and `proc.terminate()` signals only the pid we
            # hold — every descendant survives, is reparented to init, and is
            # from that moment unreachable from any handle we ever had.
            #
            # MEASURED HERE, not inherited as advice: an earlier draft of this
            # probe called `terminate()`/`kill()` and left a growing pile of
            # `chrome_crashpad_handler` processes behind — 380 after a handful
            # of runs. That is the failure PS-341 records as a false positive
            # that looked exactly like a product defect: an exhausted PID budget
            # degrades the NEXT launch into a contentless failure, and the leak
            # stops looking like a leak and starts looking like a property of
            # the code under test. On this probe it would surface as an
            # INCONCLUSIVE reading — precisely the outcome nobody investigates.
            #
            # `reap_process_group` is the repo's own reaper and carries the
            # self-kill guard (a group kill aimed at a non-leader resolves to
            # OUR group). The launch above passes `start_new_session=True`, so
            # there genuinely is a group to signal. Verified: every descendant
            # is gone within ~2s of a run finishing (11 live chrome processes
            # during a run, 0 after).
            #
            # ⚠️ WHAT THIS DOES NOT FIX, stated rather than glossed: a
            # descendant killed after its parent has exited is reparented to
            # pid 1, and if pid 1 does not reap (a bare container init, as in
            # this project's own test container) it stays a ZOMBIE — holding a
            # pid slot while consuming nothing else. Only pid 1 can reap those,
            # so no code here can. It is the difference between a leak that
            # grows without bound in CPU and memory and one that costs a handful
            # of pid-table entries per run; this closes the first.
            with contextlib.suppress(Exception):
                from src.services.browser.process_group import reap_process_group

                reap_process_group(proc, timeout=10.0)
            # Belt and braces, and specifically a WAIT: even a correctly killed
            # child stays a zombie until someone reaps its exit status.
            with contextlib.suppress(Exception):
                proc.kill()
            with contextlib.suppress(Exception):
                proc.wait(timeout=10)
        shutil.rmtree(user_dir, ignore_errors=True)


# ───────────────────────────────────────────────────────────────────────────
# Self-test: every verdict must be reachable, or this is not a guard.
# ───────────────────────────────────────────────────────────────────────────
def _all_preconditions_met() -> dict:
    return dict.fromkeys(PRECONDITIONS, True)


def _self_test() -> int:
    cases = [
        (
            "patched binary, leak still present at site A",
            {
                "preconditions": _all_preconditions_met(),
                "execution_context_created_count": 1,
                "console_before_enable_reported": True,
                "console_after_enable_reported": False,
            },
            EXIT_PATCH_PRESENT,
        ),
        (
            "patch lost — console reported after enable",
            {
                "preconditions": _all_preconditions_met(),
                "execution_context_created_count": 1,
                "console_before_enable_reported": True,
                "console_after_enable_reported": True,
            },
            EXIT_PATCH_LOST,
        ),
        (
            "channel never live — P5 unmet",
            {
                "preconditions": {**_all_preconditions_met(), "p5_unsolicited_event_arrived": False},
                "execution_context_created_count": 0,
                "console_after_enable_reported": False,
            },
            EXIT_INCONCLUSIVE,
        ),
    ]
    bad = 0
    for name, reading, want in cases:
        got, headline, _ = verdict(reading)
        ok = got == want
        bad += 0 if ok else 1
        print(f"  [{'ok' if ok else 'FAIL'}] {name}: want exit {want}, got {got} — {headline}")
    return 1 if bad else 0


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--binary", help="chromium binary to measure")
    ap.add_argument("--label", default="unlabelled")
    ap.add_argument("-o", "--out", help="write the reading JSON here")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()
    if not args.binary:
        ap.error("--binary is required unless --self-test is given")

    reading = measure(args.binary, args.label)
    code, headline, detail = verdict(reading)
    reading["verdict"] = {"exit": code, "headline": headline, "detail": detail}

    if args.out:
        p = pathlib.Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(reading, indent=2, sort_keys=True), encoding="utf-8")
        print(f"recorded -> {p}")

    print(json.dumps({k: v for k, v in reading.items() if k != "runtime_enable_reply"}, indent=2)[:4000])
    print()
    print(f"== {TICKET} [{args.label}] {headline}")
    return code


if __name__ == "__main__":
    sys.exit(main())
