"""A LOCAL page that reads the vectors, so the layer can be shown to REACH one.

Why a local page
----------------
The claim this subsystem has to be able to make is *"a change to persona's
masking moves the reading"*. Until now it could not make that claim at all,
because the harness installed no masking (see :mod:`masking_layer`). Installing
it is only half: **an assertion that the layer was installed is not evidence
that it reached the page.** PS-78 measured exactly that gap in the product —
``add_init_script`` registered a spoof that was present on a fresh launch and
ABSENT on every restored tab — so "we called the builder" and "the page sees it"
are genuinely different claims, and only the second one is worth anything.

The honest way to settle the second claim is a DIFFERENTIAL: read a vector,
change something in the extension layer that the page observes, read it again,
and show the record moved.

Why LOCAL rather than through the operator's exit
-------------------------------------------------
The live differential through the exit is the natural confirmation and it is
deliberately not this module's venue. ``/workspace/_secrets/`` is per-container
and routinely absent — measured absent in this container — only a human can
restore it, and PS-69 already hit this wall: it was re-scoped mid-flight to
prove engine, machine and seed each move a reading WITHOUT needing the exit, and
PS-10 records an explicit instruction not to re-introduce the dependency.

So the page is served from loopback by :func:`serve_probe_page`. The substance
of the differential is unchanged; only the venue moves. A run of this module
needs no credential, no proxy, no exit and no network.

What the page reads
-------------------
The vectors the catalogue's own patterns read, chosen because each is driven by
a DIFFERENT part of the layer, so a differential can point at which part moved:

* ``webgl_pixel_hash`` — a hash over ``gl.readPixels`` bytes. This is the exact
  vector PS-97's collision was found on and the one whose fix could not be seen
  to land, so it is the sharpest single reading here.
* ``audio_digest`` — a sum over an ``AnalyserNode`` float readback. The one
  vector the probe inventory grades INDEPENDENT (``probes.py:365``): every other
  seed-derived vector is drawn from a finite pool, so a collision proves nothing.
* ``navigator_language`` / ``intl_locale`` — read through
  ``Intl.DateTimeFormat(...).resolvedOptions().locale`` for a REQUESTED locale,
  which is a direct read of the override's presence. Deliberately not a bare
  ``navigator.language``: this host has only C/POSIX locales, so a bare read
  returns ``en-US`` whether the spoof is working OR leaking, and would report a
  confident false "no drift" — the near-miss PS-78 records.

Every reading is a STRING or a number the page computed, never a boolean about
whether a patch is installed. A probe that asked ``typeof
window.__personaWebglPatched`` would pass on a page the spoof never reached, and
would be the "asserts on what was written, not on what happens" failure class
knowledge article PS-11 is about — arriving inside the very instrument built to
detect it.
"""

from __future__ import annotations

import http.server
import json
import threading
from dataclasses import dataclass
from typing import Any

# The vectors this page reads, in the record's own vocabulary.
WEBGL_PIXEL_HASH = "webgl_pixel_hash"
AUDIO_DIGEST = "audio_digest"
NAVIGATOR_LANGUAGE = "navigator_language"
INTL_LOCALE = "intl_locale"

PROBE_VECTORS = (
    WEBGL_PIXEL_HASH,
    AUDIO_DIGEST,
    NAVIGATOR_LANGUAGE,
    INTL_LOCALE,
)

# The locale the Intl probe REQUESTS. Not the locale being spoofed: asking for a
# locale and reading back what resolvedOptions() says is what distinguishes a
# live override from a host that happens to agree with it.
INTL_REQUEST_LOCALE = "de-DE"

# The JS the page runs. Kept as one string so the same source can be evaluated
# directly in a page (see `read_vectors`) as well as served, and the two can
# never drift into reading different things.
#
# Each vector is computed from REAL work the page does — pixels actually
# rendered, floats actually read back — so a spoof that did not reach this realm
# produces the unperturbed value rather than an error. That is the whole design:
# the failure mode of a missing spoof must be a DIFFERENT NUMBER, not an
# exception, because an exception would be recorded as "unobtainable" and would
# look like an environment problem instead of a masking one.
PROBE_JS = r"""
(() => {
  const out = {};

  // --- webgl_pixel_hash: a hash over ACTUAL readPixels bytes ---------------
  // Not the renderer STRING. The strings were already spoofed per seed while
  // the pixels collided — the sharper failure, because a profile claiming a
  // distinct GPU while rendering the shared one is self-contradictory in a way
  // a plain missing spoof is not.
  try {
    const c = document.createElement('canvas');
    c.width = 64; c.height = 64;
    const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
    if (!gl) {
      out.webgl_pixel_hash = 'unavailable:no-webgl-context';
    } else {
      gl.clearColor(0.2, 0.45, 0.7, 1.0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.enable(gl.SCISSOR_TEST);
      for (let i = 0; i < 8; i++) {
        gl.scissor(i * 8, 0, 8, 64);
        gl.clearColor(i / 8, 0.5, 1 - i / 8, 1.0);
        gl.clear(gl.COLOR_BUFFER_BIT);
      }
      gl.disable(gl.SCISSOR_TEST);
      const px = new Uint8Array(64 * 64 * 4);
      gl.readPixels(0, 0, 64, 64, gl.RGBA, gl.UNSIGNED_BYTE, px);
      // FNV-1a over every byte. Every byte, deliberately: a hash over a sample
      // could sit entirely inside the region a perturbation happens to miss,
      // and would report "no change" for a spoof that is working.
      let h = 0x811c9dc5;
      for (let i = 0; i < px.length; i++) {
        h ^= px[i];
        h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
      }
      out.webgl_pixel_hash = ('00000000' + h.toString(16)).slice(-8);
    }
  } catch (e) {
    out.webgl_pixel_hash = 'error:' + (e && e.name);
  }

  // --- audio_digest: the canonical graph, RENDERED, then summed -----------
  // Deliberately the SAME method probes.py:337 uses - OfflineAudioContext, a
  // triangle oscillator into a DynamicsCompressor, `startRendering()`, then
  // `getChannelData(0)` summed over a fixed window and rounded to 6dp.
  //
  // THE RENDER IS THE LOAD-BEARING PART, and its absence was a measured defect
  // in the first version of this file. That version built the graph, attached
  // an AnalyserNode and called `getFloatFrequencyData` WITHOUT ever calling
  // `startRendering()` - so the analyser had seen no audio, every bin read
  // -Infinity, the isFinite filter dropped all of them, and the vector read a
  // constant `0.000000` on BOTH sides of the differential. A dead vector that
  // reports a plausible number is worse than a missing one: it reads as "the
  // spoof did not move this" when the truth is "nothing was ever measured".
  //
  // It matters more than the other three because `audio.digest` is the ONE
  // vector the probe inventory grades INDEPENDENT (probes.py:365). Every other
  // seed-derived vector is drawn from a finite pool, so two profiles colliding
  // proves nothing; this one is continuous, and PS-73 measured four profiles
  // with four DISTINCT seeds reading 35.749972 identically to 6dp - which is
  // not coincidence but the perturbation never being applied.
  //
  // Returns a PROMISE, which is why the whole probe resolves through one.
  const audio = (() => {
    try {
      const Ctx = window.OfflineAudioContext || window.webkitOfflineAudioContext;
      if (!Ctx) return Promise.resolve('unavailable:no-offline-audio-context');
      const ctx = new Ctx(1, 44100, 44100);
      const osc = ctx.createOscillator();
      osc.type = 'triangle';
      osc.frequency.value = 10000;
      const comp = ctx.createDynamicsCompressor();
      comp.threshold.value = -50;
      comp.knee.value = 40;
      comp.ratio.value = 12;
      comp.attack.value = 0;
      comp.release.value = 0.25;
      osc.connect(comp);
      comp.connect(ctx.destination);
      osc.start(0);
      return ctx.startRendering().then((buf) => {
        const d = buf.getChannelData(0);
        let sum = 0;
        for (let i = 4500; i < 5000; i++) sum += Math.abs(d[i]);
        return (Math.round(sum * 1e6) / 1e6).toFixed(6);
      }).catch((e) => 'error:' + (e && e.name));
    } catch (e) {
      return Promise.resolve('error:' + (e && e.name));
    }
  })();

  // --- the locale pair -----------------------------------------------------
  try {
    out.navigator_language = String(navigator.language);
  } catch (e) {
    out.navigator_language = 'error:' + (e && e.name);
  }
  try {
    // Requesting a locale and reading back what resolvedOptions() reports is a
    // DIRECT read of the override's presence. A bare navigator.language read
    // returns en-US on this host whether the spoof works or leaks.
    out.intl_locale = String(
      new Intl.DateTimeFormat('%%INTL_REQUEST_LOCALE%%').resolvedOptions().locale
    );
  } catch (e) {
    out.intl_locale = 'error:' + (e && e.name);
  }

  // The audio vector is the only asynchronous one (it must be RENDERED before
  // it can be read), so the whole probe resolves through it. Everything above
  // is already computed.
  return Promise.resolve(audio).then((digest) => {
    out.audio_digest = digest;
    return out;
  });
})()
"""


def probe_js(*, intl_request_locale: str = INTL_REQUEST_LOCALE) -> str:
    """The probe source, with its requested locale substituted in."""
    return PROBE_JS.replace("%%INTL_REQUEST_LOCALE%%", intl_request_locale)


PROBE_PAGE_TEMPLATE = """<!doctype html>
<meta charset="utf-8">
<title>persona local probe</title>
<body><pre id="out">reading...</pre>
<script>
  // Rendered into the page as TEXT, because the browser tier reads pages
  // through inner_text: `page.evaluate` is blocked by CSP on real checker
  // pages (measured, recorded in browser_tier's docstring), so the local page
  // is read the SAME way a real checker is. One reading path, so the
  // demonstration cannot succeed through a route the real run does not have.
  try {
    // The probe resolves a PROMISE (the audio vector must be rendered offline
    // before it can be read), so the text is written in a .then rather than
    // synchronously. The reader settles before reading, and a page still
    // showing "reading..." parses as an unread reading rather than as a page
    // that said nothing.
    Promise.resolve(%%PROBE_JS%%).then((vectors) => {
      document.getElementById('out').textContent =
        JSON.stringify(vectors, null, 2);
    }).catch((e) => {
      document.getElementById('out').textContent =
        JSON.stringify({error: String(e)}, null, 2);
    });
  } catch (e) {
    document.getElementById('out').textContent =
      JSON.stringify({error: String(e)}, null, 2);
  }
</script>
</body>
"""


def probe_page_html(*, intl_request_locale: str = INTL_REQUEST_LOCALE) -> str:
    """The whole local page, as HTML."""
    return PROBE_PAGE_TEMPLATE.replace(
        "%%PROBE_JS%%", probe_js(intl_request_locale=intl_request_locale)
    )


@dataclass(frozen=True)
class ProbeReading:
    """One page-load's worth of vectors.

    ``vectors`` maps vector name -> the string the PAGE computed. Strings rather
    than parsed numbers on purpose: the record compares them for equality, and a
    float round-trip is exactly how a six-decimal audio delta gets quietly
    normalised away.
    """

    vectors: "dict[str, str]"
    note: str = ""

    def as_record(self) -> dict:
        return {
            "vectors": {k: self.vectors[k] for k in sorted(self.vectors)},
            "note": self.note,
        }


def parse_probe_text(text: str) -> ProbeReading:
    """Parse the local page's rendered text into a reading.

    The page renders JSON as its visible text, so this is the same
    read-as-text path the browser tier uses on real checkers.
    """
    stripped = (text or "").strip()
    if not stripped:
        return ProbeReading(vectors={}, note="the probe page rendered no text")
    # The page may carry surrounding chrome; take the outermost JSON object.
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        return ProbeReading(
            vectors={},
            note="the probe page's text carried no JSON object",
        )
    try:
        data = json.loads(stripped[start:end + 1])
    except ValueError as exc:
        return ProbeReading(
            vectors={}, note=f"the probe page's JSON did not parse: {exc}"
        )
    if not isinstance(data, dict):
        return ProbeReading(
            vectors={}, note="the probe page's JSON was not an object"
        )
    return ProbeReading(
        vectors={str(k): str(v) for k, v in data.items()}
    )


class _ProbeHandler(http.server.BaseHTTPRequestHandler):
    """Serves the probe page and nothing else."""

    html = ""

    def do_GET(self):  # noqa: N802 - stdlib naming
        body = self.html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # No CSP header: the page must be able to run its own inline script.
        # A real checker page's CSP is what blocks `page.evaluate`, and the
        # tier reads through inner_text for that reason — this page is read the
        # same way, so it needs no evaluate either.
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args, **kwargs):
        # Silent: the harness's stderr is a report, not a web server log.
        pass


class ProbeServer:
    """A loopback HTTP server for the probe page, as a context manager.

    Bound to ``127.0.0.1`` on an EPHEMERAL port. Loopback because the whole
    point is that this needs no exit, and ephemeral because a fixed port would
    collide with a co-resident run and produce a reading of somebody else's
    page.
    """

    def __init__(self, *, intl_request_locale: str = INTL_REQUEST_LOCALE) -> None:
        self._html = probe_page_html(intl_request_locale=intl_request_locale)
        self._server: Any = None
        self._thread: "threading.Thread | None" = None

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("the probe server is not running")
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/"

    def __enter__(self) -> "ProbeServer":
        handler = type("_BoundProbeHandler", (_ProbeHandler,), {"html": self._html})
        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.1},
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
            finally:
                self._server.server_close()
                self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


def serve_probe_page(*, intl_request_locale: str = INTL_REQUEST_LOCALE) -> ProbeServer:
    """Start the loopback probe server. Use as a context manager."""
    return ProbeServer(intl_request_locale=intl_request_locale).__enter__()


def differential(before: ProbeReading, after: ProbeReading) -> dict:
    """What moved between two probe readings.

    This is the whole point of the module, so it is worth being precise about
    what each outcome MEANS:

    ``moved``
        Vectors whose value differs. A change in the extension layer reached the
        page — the harness can observe persona's masking.
    ``unchanged``
        Vectors that read identically. Expected for vectors the change does not
        drive; a vector that SHOULD have moved and did not is the PS-97 shape,
        where a fix looked like it failed because the fixed code never ran.
    ``appeared`` / ``vanished``
        Vectors present on one side only. Reported apart from ``moved`` because
        "this reading did not exist" and "this reading changed" are different
        findings, and collapsing them would let a probe that stopped working
        read as a spoof that started working.

    ``any_moved`` is the headline the demonstration turns on.
    """
    before_v = before.vectors
    after_v = after.vectors
    shared = sorted(set(before_v) & set(after_v))
    moved = {
        k: {"before": before_v[k], "after": after_v[k]}
        for k in shared
        if before_v[k] != after_v[k]
    }
    unchanged = sorted(k for k in shared if before_v[k] == after_v[k])
    return {
        "moved": moved,
        "unchanged": unchanged,
        "appeared": sorted(set(after_v) - set(before_v)),
        "vanished": sorted(set(before_v) - set(after_v)),
        "any_moved": bool(moved),
    }


__all__ = [
    "AUDIO_DIGEST",
    "INTL_LOCALE",
    "INTL_REQUEST_LOCALE",
    "NAVIGATOR_LANGUAGE",
    "PROBE_JS",
    "PROBE_VECTORS",
    "WEBGL_PIXEL_HASH",
    "ProbeReading",
    "ProbeServer",
    "differential",
    "parse_probe_text",
    "probe_js",
    "probe_page_html",
    "serve_probe_page",
]
