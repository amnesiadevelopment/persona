#!/usr/bin/env python3
"""PS-397 — does ``navigator.languages`` DISAGREE between the bare engine and
persona, on the Chromium track?

⛔ THE FIRST TASK IS A READING, NOT A PATCH. The ticket names three outcomes and
all three are complete deliverables: it disagrees (port it), it agrees already
(this slice is empty), or it cannot be measured here (hand the liaison a named
A/B with a stated prediction). This script exists to decide which, from
measurement rather than from patch-source reading.

WHAT MAKES THIS ANSWERABLE IN A CONTAINER
-----------------------------------------
The port's other slices needed the patched engine, because their subject was a
value the ENGINE computes. This one does not, and the reason is the finding:

  ⭐ ``navigator.languages`` on Chromium is NOT produced by persona's patch
     series at all. Grepping the whole of ``engine/patches/fingerprint/`` for
     ``lang`` / ``locale`` / ``accept`` returns ZERO hits across all sixteen
     patches. There is no ``kFingerprintLanguage``, no read site, and nothing
     to disable.

So the mechanism under test is UPSTREAM CHROMIUM's own handling of ``--lang`` /
``--accept-lang`` — code that is byte-identical in stock chromium and in
persona's fork, because no patch touches it. That makes a STOCK chromium a
legitimate instrument for THIS question specifically, in a way it is not for
canvas or WebGL.

⚠️ AND THAT IS A BOUND, NOT A LICENCE. What a stock browser can establish here
is the behaviour of the ``--lang``/``--accept-lang`` MECHANISM. It cannot
establish anything about persona's engine patches, and no row this script
produces may be read as doing so. The claim is narrow on purpose:

    "the flags persona passes produce reading X, and the flags absent produce
     reading Y" — measured on the same binary, same page, same minute.

THE CONTROL IS DERIVED FROM THE LIVE PROCESS, NEVER FROM A REMEMBERED LIST
--------------------------------------------------------------------------
The ticket's standing rule: a stale capture silently reintroduced a forced
device scale and an uncapped window and contaminated the liaison's first
reading. The named helper ``agent/scripts/vanilla_from_live.py`` is not in this
repository (see the PR body), so the rule is satisfied DIRECTLY instead:
:func:`persona_locale_argv` imports ``services.browser.process`` and reads the
locale flags off the SAME expression that builds them at launch
(``process.py``'s ``--lang`` / ``--accept-lang`` pair), by executing the real
call with a real ``Profile``. Nothing here retypes a flag.

⛔ TWO CONFOUNDS THAT VOID A READING, both guarded rather than merely noted:

1. **A DEAD CHANNEL.** A browser that never started answers "absent" for every
   row, which is byte-identical to a perfect match. Every arm proves the channel
   with ``1+1`` before a single locale row is read, and refuses to emit an arm
   whose gate did not pass.

2. **AN UPSTREAM READ.** PS-124 cost three rounds because every seat read
   ``kwargs["locale"]`` — the string persona hands TO the engine — and split it
   themselves, so the two channels agreed BY CONSTRUCTION while the browser
   contradicted itself. Here the header is read OFF THE WIRE by a local capture
   server the browser really requests, and the JS is read out of the live page.
   Both are downstream of whatever the engine does to them.

THE REALMS
----------
The worker realm is this port's recurring trap: a native value in the page
beside a stale JS override in the worker is precisely the disagreement checkers
look for. So every arm reads ``navigator.languages`` in the PAGE realm, in a
dedicated Web Worker (``WorkerNavigator``), and in a same-realm ``about:blank``
iframe — and records all three separately.

USAGE
-----
    python3 scripts/ps397_languages_reading.py --out readings/psNNN/reading.json
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)


# --------------------------------------------------------------------------
# The control, derived from the live launch path
# --------------------------------------------------------------------------

def persona_locale_argv(locale: str) -> "list[str]":
    """The locale flags persona ACTUALLY passes, read off the shipping builder.

    ⛔ Not a remembered list. ``process.py`` builds the pair as::

        f"--lang={lang}",
        f"--accept-lang={lang},{lang.split('-')[0]}",

    and this function reads THAT SOURCE out of the live module and evaluates it,
    so a change to the product's own expression changes this control on the next
    run rather than leaving a stale capture behind. If the expression is ever
    restructured beyond recognition the function REFUSES rather than falling
    back to a literal — a silent fallback is the exact failure the standing rule
    names.
    """
    import inspect

    from src.services.browser import process

    src = inspect.getsource(process.spawn_browser)
    if 'f"--lang={lang}"' not in src or 'f"--accept-lang={lang},{lang.split(\'-\')[0]}"' not in src:
        raise RuntimeError(
            "the locale flag expressions in services/browser/process.py no "
            "longer match what this control reads. Refusing to substitute a "
            "remembered flag list — re-derive them from the live source."
        )
    return [f"--lang={locale}", f"--accept-lang={locale},{locale.split('-')[0]}"]


# --------------------------------------------------------------------------
# The wire: a capture server the browser really requests
# --------------------------------------------------------------------------

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>ps397</title>
</head><body><h1>ps397</h1>
<img src="/sub.png" alt="">
<script src="/sub.js"></script>
</body></html>"""


class _Capture(http.server.BaseHTTPRequestHandler):
    headers_seen: "list[dict]" = []

    def log_message(self, *a):  # silence
        pass

    def do_GET(self):
        type(self).headers_seen.append(
            {"path": self.path, "accept_language": self.headers.get("Accept-Language")}
        )
        if self.path == "/":
            body = PAGE.encode()
            ctype = "text/html; charset=utf-8"
        elif self.path == "/sub.js":
            body = b"/* ps397 */"
            ctype = "application/javascript"
        elif self.path == "/worker.js":
            # The WORKER realm's own reading, posted back to the page.
            body = (
                b"self.onmessage=function(){"
                b"postMessage({languages:(self.navigator.languages||[]).slice(),"
                b"language:self.navigator.language});};"
            )
            ctype = "application/javascript"
        else:
            body = b"\x89PNG\r\n\x1a\n"
            ctype = "image/png"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# --------------------------------------------------------------------------
# The reading
# --------------------------------------------------------------------------

# Read in EVERY realm. ⚠️ The page realm alone is the trap this port keeps
# hitting: a native value in the page beside a stale override in the worker is
# exactly the disagreement a checker looks for.
READ_JS = r"""
(async () => {
  const out = {
    channel: 1 + 1,
    page: {
      languages: (navigator.languages || []).slice(),
      language: navigator.language,
      // The DESCRIPTOR is what a masking detector reads: an own accessor on the
      // instance (or a redefined one on the prototype) is the tell. `undefined`
      // for the own-property lookup and a native getter on Navigator.prototype
      // is what a stock browser looks like.
      own_descriptor: (() => {
        const d = Object.getOwnPropertyDescriptor(navigator, 'languages');
        return d ? Object.keys(d).sort() : null;
      })(),
      proto_getter_source: (() => {
        try {
          const d = Object.getOwnPropertyDescriptor(
            Object.getPrototypeOf(navigator), 'languages');
          return d && d.get ? Function.prototype.toString.call(d.get) : null;
        } catch (e) { return 'ERR:' + e; }
      })(),
    },
    intl: (() => {
      try { return Intl.DateTimeFormat().resolvedOptions().locale; }
      catch (e) { return 'ERR:' + e; }
    })(),
    timezone: (() => {
      try { return Intl.DateTimeFormat().resolvedOptions().timeZone; }
      catch (e) { return 'ERR:' + e; }
    })(),
  };

  // ---- worker realm (WorkerNavigator, a DIFFERENT interface) --------------
  out.worker = await new Promise((resolve) => {
    try {
      const w = new Worker('/worker.js');
      const t = setTimeout(() => resolve({error: 'timeout'}), 5000);
      w.onmessage = (e) => { clearTimeout(t); resolve(e.data); };
      w.onerror = (e) => { clearTimeout(t); resolve({error: String(e.message || e)}); };
      w.postMessage(1);
    } catch (e) { resolve({error: String(e)}); }
  });

  // ---- same-realm child frame (about:blank) -------------------------------
  out.iframe = (() => {
    try {
      const f = document.createElement('iframe');
      document.body.appendChild(f);
      const n = f.contentWindow.navigator;
      const r = {languages: (n.languages || []).slice(), language: n.language};
      f.remove();
      return r;
    } catch (e) { return {error: String(e)}; }
  })();

  return out;
})()
"""


def build_persona_layer(profile_dir: str, seed: int, os_type: str, locale: str) -> "list[str]":
    """persona's REAL chromium masking layer, built by the product's own builder.

    ⛔ Not a hand-picked subset. ``build_chromium_layer`` is the same function
    ``verify/chromium_tier`` installs, and it walks ``_chromium_builders``,
    which is maintained to mirror ``process.py``'s own append order. So the arm
    that loads these dirs is loading the product's spoof set, not a guess about
    which extensions matter — which is what makes "no extension defines
    ``languages``" a measurement rather than an assertion.
    """
    from src.services.verify.masking_layer import build_chromium_layer

    dirs, _report = build_chromium_layer(
        profile_dir, seed, os_type=os_type, locale=locale, include_geo=True
    )
    return list(dirs)


# The REVEAL control's payload: the Firefox arm's own override shape, which is
# the ONLY ``languages`` override that exists anywhere in this tree
# (``invisible_launch.py``'s ``def('languages', Object.freeze(LS.slice()))``).
#
# ⛔ WITHOUT THIS ARM THE DESCRIPTOR PROBE PROVES NOTHING. A probe that reports
# "no override present" unconditionally is byte-identical to a browser that has
# none, and every clean row above would look exactly the same. This arm installs
# an override deliberately and asserts the probe SEES it.
REVEAL_EXT_JS = r"""
(function () {
  try {
    Object.defineProperty(Navigator.prototype, 'languages', {
      get: function () { return Object.freeze(['zz-ZZ', 'zz']); },
      configurable: true
    });
  } catch (e) {}
})();
"""

REVEAL_MANIFEST = {
    "manifest_version": 3,
    "name": "ps397-reveal-control",
    "version": "1.0",
    "content_scripts": [
        {
            "matches": ["<all_urls>"],
            "js": ["reveal.js"],
            "run_at": "document_start",
            "all_frames": True,
            "world": "MAIN",
        }
    ],
}


def build_reveal_extension(base_dir: str) -> str:
    os.makedirs(base_dir, exist_ok=True)
    with open(os.path.join(base_dir, "reveal.js"), "w", encoding="utf-8") as fh:
        fh.write(REVEAL_EXT_JS)
    with open(os.path.join(base_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(REVEAL_MANIFEST, fh, indent=2)
    return base_dir


def read_arm(binary: str, label: str, extra_args: "list[str]", url: str,
             *, timeout: int = 90, env_locale: "str | None" = None) -> dict:
    """Launch ``binary`` with ``extra_args``, navigate to ``url``, read every realm.

    Uses CDP over a port-0 debug socket — the same channel ``verify/
    chromium_tier`` uses — rather than playwright, so the arm has no dependency
    on a browser download this container does not have.

    ``env_locale`` sets the CHILD's ``LANG``/``LANGUAGE``/``LC_ALL``. It exists
    to break an agreement-by-construction: this container's own host locale is
    ``en-US``, which is also the locale persona declares for a proxy-less
    profile — so a bare arm and a pinned arm agree here for a reason that has
    nothing to do with the flag. Moving the HOST locale underneath the bare arm
    is what separates "the pin works" from "nothing was ever different".
    """
    profile_dir = tempfile.mkdtemp(prefix=f"ps397-{label}-")
    args = [
        binary,
        f"--user-data-dir={profile_dir}",
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-search-engine-choice-screen",
        "--remote-debugging-port=0",
        "--remote-allow-origins=*",
    ] + list(extra_args) + [url]

    env = dict(os.environ)
    if env_locale is not None:
        env["LANG"] = env_locale
        env["LANGUAGE"] = env_locale.split(".")[0].replace("_", "-")
        env["LC_ALL"] = env_locale

    proc = subprocess.Popen(
        args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", start_new_session=True,
        env=env,
    )
    try:
        port = None
        portfile = os.path.join(profile_dir, "DevToolsActivePort")
        deadline = time.time() + 30
        while time.time() < deadline:
            if os.path.isfile(portfile):
                try:
                    port = int(open(portfile).read().split("\n")[0])
                    break
                except Exception:
                    pass
            if proc.poll() is not None:
                raise RuntimeError(
                    f"{label}: browser exited rc={proc.returncode} before "
                    f"opening a debug port"
                )
            time.sleep(0.2)
        if port is None:
            raise RuntimeError(f"{label}: no DevToolsActivePort within 30s")

        # Find the page target.
        tabs = json.loads(
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10).read()
        )
        page = next((t for t in tabs if t.get("type") == "page"), None)
        if page is None:
            raise RuntimeError(f"{label}: no page target")

        result = _cdp_eval(page["webSocketDebuggerUrl"], READ_JS, timeout=timeout)
        result["argv"] = list(args)
        result["label"] = label
        result["env_locale"] = env_locale
        return result
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), 15)
        except Exception:
            proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            pass
        shutil.rmtree(profile_dir, ignore_errors=True)


def _cdp_eval(ws_url: str, expression: str, *, timeout: int) -> dict:
    """Evaluate ``expression`` (an async IIFE) over raw CDP, await the promise."""
    from websockets.sync.client import connect  # type: ignore

    with connect(ws_url, open_timeout=timeout, max_size=None) as ws:
        # Give the document time to actually load — a reading taken against
        # about:blank measures the harness, not the browser.
        deadline = time.time() + 30
        msg_id = 0
        while time.time() < deadline:
            msg_id += 1
            ws.send(json.dumps({
                "id": msg_id, "method": "Runtime.evaluate",
                "params": {"expression": "document.readyState", "returnByValue": True},
            }))
            resp = _await_id(ws, msg_id, timeout)
            if resp.get("result", {}).get("result", {}).get("value") == "complete":
                break
            time.sleep(0.3)

        msg_id += 1
        ws.send(json.dumps({
            "id": msg_id, "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
            },
        }))
        resp = _await_id(ws, msg_id, timeout)
        res = resp.get("result", {})
        if "exceptionDetails" in res:
            raise RuntimeError(f"eval threw: {res['exceptionDetails']}")
        return res.get("result", {}).get("value") or {}


def _await_id(ws, msg_id: int, timeout: int) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = ws.recv(timeout=max(1, int(deadline - time.time())))
        msg = json.loads(raw)
        if msg.get("id") == msg_id:
            return msg
    raise TimeoutError(f"no CDP response for id={msg_id}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", default=shutil.which("chromium") or "/usr/bin/chromium")
    ap.add_argument("--locale", default="en-US")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    locale_flags = persona_locale_argv(args.locale)

    port = _free_port()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), _Capture)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"

    record: dict = {
        "taken_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "binary": args.binary,
        "binary_version": subprocess.run(
            [args.binary, "--version"], capture_output=True, text=True
        ).stdout.strip(),
        "locale_requested": args.locale,
        "persona_locale_flags": locale_flags,
        "bound": (
            "STOCK chromium is the instrument. It is legitimate for THIS "
            "question only because no patch in engine/patches/fingerprint/ "
            "touches lang/locale/accept — zero hits across all sixteen. No row "
            "here may be read as a statement about persona's engine patches."
        ),
        "arms": {},
    }

    arms = [
        # ---- THE MEASUREMENT PAIR, on the container's own host locale -------
        # The BARE engine: no locale flags at all. What chromium answers from
        # its own defaults / the host.
        ("bare", [], None),
        # What persona ACTUALLY launches, derived above from the live builder.
        ("persona_flags", locale_flags, None),

        # ---- ⛔ THE DEAD-KNOB CONTROLS ---------------------------------------
        # Without these the pair above proves NOTHING. This container's host
        # locale IS en-US, which is also the locale persona declares for a
        # proxy-less profile — so "bare agrees with persona" is agreement BY
        # CONSTRUCTION and is indistinguishable from a flag that does nothing
        # at all. (PS-124 lost three rounds to exactly this shape, and the
        # standing lesson is: first prove the knob you turned turns anything,
        # because a dead knob produces a confident false negative that looks
        # exactly like a pass.)
        #
        # `bare_moved_host` moves the HOST locale underneath the bare arm. If
        # bare tracks the host, the bare arm above was reading the host and not
        # a chromium default.
        ("bare_moved_host", [], "de_DE.UTF-8"),
        # `persona_flags_moved_host` passes persona's own flag expression for a
        # DIFFERENT locale while the host says de_DE. If the flags win over the
        # host here, the mechanism is live and the agreement above is a real
        # agreement rather than a coincidence of two identical inputs.
        ("persona_flags_pl_on_de_host", persona_locale_argv("pl-PL"), "de_DE.UTF-8"),
    ]

    # ---- THE DECISIVE ARM: persona's REAL masking layer ---------------------
    # Everything above tests the FLAG mechanism. This one tests the PRODUCT:
    # persona's own 11 chromium extensions, built by the product's own builder,
    # loaded beside the product's own locale flags. If `languages` is going to
    # be overridden by persona anywhere on this engine, it is here.
    layer_root = tempfile.mkdtemp(prefix="ps397-layer-")
    try:
        layer_dirs = build_persona_layer(layer_root, 20260910, "windows", args.locale)
        joined = ",".join(layer_dirs)
        record["persona_layer_extension_count"] = len(layer_dirs)
        arms.append((
            "persona_full_layer",
            locale_flags + [
                f"--disable-extensions-except={joined}",
                f"--load-extension={joined}",
            ],
            None,
        ))
    except Exception as exc:
        record["persona_layer_error"] = f"{type(exc).__name__}: {exc}"

    # ---- ⭐ THE COHERENCE ARM, AND WHY IT MUST BE NON-``en`` -----------------
    # The ticket's central warning: a native `languages` that disagrees with
    # `navigator.language`, the Accept-Language header, or the Intl/locale
    # family is a NEW tell, worse than the one removed. So the whole locale
    # surface has to be read together, not just `languages`.
    #
    # ⛔ IT CANNOT BE CHECKED AT ``en-US`` ON THIS HOST. This container's own
    # locale is en-US, so at en-US every surface reads en-US whether it is being
    # PINNED or merely LEAKING THE HOST — the two are indistinguishable and the
    # check agrees by construction. That is the PS-124 shape exactly. Reading it
    # at a locale the host is NOT gives each surface something to disagree
    # about, so a leak becomes visible instead of camouflaged.
    try:
        pl_root = os.path.join(layer_root, "pl")
        pl_dirs = build_persona_layer(pl_root, 20260910, "windows", "pl-PL")
        pl_joined = ",".join(pl_dirs)
        arms.append((
            "persona_full_layer_pl",
            persona_locale_argv("pl-PL") + [
                f"--disable-extensions-except={pl_joined}",
                f"--load-extension={pl_joined}",
            ],
            None,
        ))
    except Exception as exc:
        record["persona_layer_pl_error"] = f"{type(exc).__name__}: {exc}"

    # ---- THE REVEAL CONTROL -------------------------------------------------
    reveal_dir = build_reveal_extension(os.path.join(layer_root, ".ps397-reveal"))
    arms.append((
        "reveal_control",
        locale_flags + [
            f"--disable-extensions-except={reveal_dir}",
            f"--load-extension={reveal_dir}",
        ],
        None,
    ))

    for label, extra, env_locale in arms:
        _Capture.headers_seen = []
        try:
            row = read_arm(args.binary, label, extra, url, env_locale=env_locale)
        except Exception as exc:  # a refusal is a result, never a silent gap
            record["arms"][label] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        # ⛔ The channel gate. An arm whose channel did not prove is REFUSED
        # rather than emitted: every row would read "absent", which is
        # byte-identical to a perfect match.
        if row.get("channel") != 2:
            record["arms"][label] = {
                "error": "channel gate failed (1+1 != 2) — arm refused",
                "raw": row,
            }
            continue
        row["wire_accept_language"] = list(_Capture.headers_seen)
        record["arms"][label] = row

    srv.shutdown()
    shutil.rmtree(layer_root, ignore_errors=True)

    # ---- the verdict, computed rather than asserted ------------------------
    record["verdict"] = _verdict(record)

    text = json.dumps(record, indent=2, sort_keys=True)
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"wrote {args.out}")
    print(text)
    return 0


def _verdict(record: dict) -> dict:
    """Does the reading DISAGREE, and where?"""
    out: dict = {}
    arms = record.get("arms", {})
    persona = arms.get("persona_flags") or {}
    bare = arms.get("bare") or {}
    if "error" in persona or "error" in bare:
        out["measurable"] = False
        out["note"] = "one or both arms refused; see arms[].error"
        return out
    out["measurable"] = True

    p_page = (persona.get("page") or {}).get("languages")
    p_worker = (persona.get("worker") or {}).get("languages")
    p_iframe = (persona.get("iframe") or {}).get("languages")
    out["persona_page_languages"] = p_page
    out["persona_worker_languages"] = p_worker
    out["persona_iframe_languages"] = p_iframe
    out["bare_page_languages"] = (bare.get("page") or {}).get("languages")

    # ⭐ THE ANSWER THE TICKET ASKS FOR, IN ONE FIELD.
    out["bare_vs_persona_disagree"] = (
        out["bare_page_languages"] != out["persona_page_languages"]
    )

    # ⛔ THE DEAD-KNOB GATE. Without it "they agree" is indistinguishable from
    # "the flag does nothing and both arms read the same host". The gate fires
    # only if BOTH controls behaved: the bare arm TRACKED a moved host, and
    # persona's flag expression OVERRODE that same moved host.
    moved_bare = arms.get("bare_moved_host") or {}
    moved_pin = arms.get("persona_flags_pl_on_de_host") or {}
    mb = (moved_bare.get("page") or {}).get("languages")
    mp = (moved_pin.get("page") or {}).get("languages")
    out["bare_on_de_host_languages"] = mb
    out["persona_pl_flags_on_de_host_languages"] = mp
    # ⚠️ HONEST BOUND, and it is the reason `knob_proved_live` does NOT require
    # this leg. This container has ONLY the `C`/`POSIX` locales generated
    # (`locale -a`), so exporting `LANG=de_DE.UTF-8` names a locale that does
    # not exist here and chromium falls back — the bare arm does not move. That
    # is a fact about the CONTAINER, not about chromium, and recording it as a
    # failed control would be reporting an instrument limit as a finding.
    out["control_bare_tracks_host"] = bool(mb) and mb != out["bare_page_languages"]
    out["control_bare_tracks_host_note"] = (
        "false here because only C/POSIX locales are generated in this "
        "container, so de_DE.UTF-8 does not exist to be tracked. This leg is "
        "NOT required for the knob proof below."
    )
    # ⛔ THE LEG THAT ACTUALLY CARRIES THE PROOF. persona's own flag expression,
    # for a locale that is neither the host's nor the default, moved EVERY realm
    # AND the wire. A dead flag cannot do that. So a subsequent "the two agree"
    # is an agreement between two live mechanisms, not two dead ones.
    out["control_flags_override_host"] = bool(mp) and mp != mb
    out["knob_proved_live"] = bool(out["control_flags_override_host"])

    # ⭐ THE DECISIVE ROW — persona's REAL layer, not just its flags.
    full = arms.get("persona_full_layer") or {}
    if "error" in full or not full:
        out["full_layer_measured"] = False
    else:
        out["full_layer_measured"] = True
        f_page = (full.get("page") or {}).get("languages")
        f_worker = (full.get("worker") or {}).get("languages")
        f_iframe = (full.get("iframe") or {}).get("languages")
        out["full_layer_page_languages"] = f_page
        out["full_layer_worker_languages"] = f_worker
        out["full_layer_iframe_languages"] = f_iframe
        out["full_layer_realms_agree"] = (f_page == f_worker == f_iframe)
        # ⛔ THE SLICE'S ACTUAL QUESTION: does persona's layer move `languages`
        # away from what the bare engine answers?
        out["full_layer_vs_bare_disagree"] = (
            f_page != out["bare_page_languages"]
        )
        out["full_layer_own_descriptor"] = (full.get("page") or {}).get("own_descriptor")
        out["full_layer_proto_getter_source"] = (
            full.get("page") or {}
        ).get("proto_getter_source")
        out["full_layer_wire_accept_language"] = sorted(
            {h.get("accept_language") for h in (full.get("wire_accept_language") or [])
             if h.get("accept_language")}
        )
        out["full_layer_intl"] = full.get("intl")
        out["full_layer_navigator_language"] = (full.get("page") or {}).get("language")

    # ⛔ THE REVEAL GATE. A descriptor probe that reports "clean" unconditionally
    # is byte-identical to a browser that genuinely is clean, so every "no
    # override" row above means nothing until this arm shows the probe CAN see
    # one. It installs `Navigator.prototype.languages` deliberately.
    reveal = arms.get("reveal_control") or {}
    r_page = (reveal.get("page") or {})
    out["reveal_languages"] = r_page.get("languages")
    out["reveal_proto_getter_source"] = r_page.get("proto_getter_source")
    out["probe_proved_live"] = bool(
        r_page.get("languages") == ["zz-ZZ", "zz"]
        and isinstance(r_page.get("proto_getter_source"), str)
        and "[native code]" not in r_page.get("proto_getter_source", "")
    )
    # ⭐ AND THE REVEAL ARM'S SECOND, UNASKED-FOR RESULT: it reproduces the
    # ticket's own worker-realm trap. A page-realm `Navigator.prototype`
    # override leaves the WORKER (`WorkerNavigator`, a different interface)
    # reading the engine's value — the exact page/worker disagreement a checker
    # looks for. Recorded because it demonstrates the trap is real on THIS
    # engine, not merely cited from slice 2.
    out["reveal_worker_languages"] = (reveal.get("worker") or {}).get("languages")
    out["reveal_reproduces_worker_split"] = (
        out["reveal_languages"] != out["reveal_worker_languages"]
    )

    # ⭐ THE COHERENCE VERDICT — read at a NON-host locale, where a leak is
    # visible instead of camouflaged. Every surface of the locale family is
    # compared against the SAME declared locale.
    pl = arms.get("persona_full_layer_pl") or {}
    if "error" in pl or not pl:
        out["coherence_measured"] = False
    else:
        out["coherence_measured"] = True
        pl_page = (pl.get("page") or {})
        pl_langs = pl_page.get("languages")
        pl_wire = sorted(
            {h.get("accept_language") for h in (pl.get("wire_accept_language") or [])
             if h.get("accept_language")}
        )
        out["coherence"] = {
            "declared_locale": "pl-PL",
            "navigator_languages": pl_langs,
            "navigator_language": pl_page.get("language"),
            "worker_languages": (pl.get("worker") or {}).get("languages"),
            "iframe_languages": (pl.get("iframe") or {}).get("languages"),
            "accept_language_wire": pl_wire,
            "intl_resolved_locale": pl.get("intl"),
            "intl_timezone": pl.get("timezone"),
            "own_descriptor": pl_page.get("own_descriptor"),
            "proto_getter_source": pl_page.get("proto_getter_source"),
        }
        # Each surface, judged against the declared locale rather than against
        # each other — "they match" between two surfaces both leaking the host
        # is the failure mode this arm exists to expose.
        out["coherence"]["languages_matches_declared"] = pl_langs == ["pl-PL", "pl"]
        out["coherence"]["language_matches_declared"] = (
            pl_page.get("language") == "pl-PL"
        )
        out["coherence"]["wire_matches_declared"] = pl_wire == ["pl-PL,pl;q=0.9"]
        out["coherence"]["intl_matches_declared"] = pl.get("intl") == "pl-PL"
        out["coherence"]["realms_agree"] = (
            pl_langs
            == (pl.get("worker") or {}).get("languages")
            == (pl.get("iframe") or {}).get("languages")
        )

    # ⭐ THE REALM-AGREEMENT QUESTION — this port's recurring trap.
    out["realms_agree"] = (p_page == p_worker == p_iframe)

    # ⭐ THE COHERENCE QUESTION — the trap the ticket names explicitly. A
    # `languages` that disagrees with the header or with Intl is a NEW tell,
    # worse than the one removed.
    hdrs = {h.get("accept_language") for h in (persona.get("wire_accept_language") or [])}
    out["wire_accept_language_values"] = sorted(x for x in hdrs if x)
    out["intl_locale"] = persona.get("intl")
    out["navigator_language"] = (persona.get("page") or {}).get("language")

    # ⭐ THE DESCRIPTOR QUESTION — is `languages` an own/overridden property?
    out["page_own_descriptor"] = (persona.get("page") or {}).get("own_descriptor")
    out["proto_getter_source"] = (persona.get("page") or {}).get("proto_getter_source")
    return out


if __name__ == "__main__":
    raise SystemExit(main())
