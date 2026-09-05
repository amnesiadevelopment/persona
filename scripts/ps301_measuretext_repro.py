"""PS-301: the `measureText` finding, reduced to its smallest reproduction.

The full PS-301 run (``scripts/ps301_engine_launch_read``) reads eight cells
across two engines and six realms. This is the ONE cell that failed, cut down
to the shortest thing that still shows it, so the finding can be re-checked in
under a minute and re-checked again after somebody fixes it.

WHAT IT SHOWS
-------------
persona's self-built Chromium returns ``TextMetrics`` values that are
**~2.2e-06 times the true value** — a width of ``-0.00037`` where the same
string in the same-version unpatched Chromium measures ``163.88``. Every metric
on the object is destroyed the same way: width, the four ``actualBoundingBox*``
values, the baselines.

That is not noise. It is a page-visible defect and a fingerprinting SIGNAL in
its own right: no real browser reports a sub-micron text width, so a detector
does not need to compare anything to know it is looking at an instrumented
engine. It also breaks any page that lays text out by measuring it.

THE CAUSE, and why it is a two-patch interaction rather than one bad line
--------------------------------------------------------------------------
``TextMetrics::Shuffle(double)`` is a **MULTIPLIER** — upstream's caller passed
``Document::GetNoiseFactorX()``, which upstream initialises to
``1 + (RandDouble() - 0.5) * 0.000003``, i.e. a number *centred on 1*. Scaling
by ~1.0 perturbs a metric; scaling by ~0 annihilates it.

Two of our patches then moved in opposite directions:

* ``014-client-rects`` redefined ``noise_factor_x_`` from that
  centred-on-1 MULTIPLIER to ``norm_x * 0.002`` — an **OFFSET** centred on 0 —
  and correspondingly changed its own call sites from ``Scale()`` to
  ``Offset()``. Self-consistent.
* ``015-canvas-measure-text`` stopped reading ``GetNoiseFactorX()`` and
  computed its own ``noise_x = norm_x * 0.00001`` — also an offset-shaped value
  centred on 0 — but still passes it to ``Shuffle()``, which still multiplies.

So 015 hands a value in ``[-5e-06, +5e-06]`` to a function that multiplies by
it. Each patch is defensible read alone; the pair is not.

THE ARITHMETIC IS THE PROOF, and it is what makes this a certainty rather than
an inference: the patched/control ratio is **identical across every string**
(``-2.2406942064968623e-06`` for "a", for "ab", for "hello world", for a
21-character string). A constant ratio is what a multiply produces and what an
add cannot. The value also sits inside 015's own stated ``norm_x * 0.00001``
range, which is what ties it to that specific line.

SCOPE — READ THIS BEFORE CONCLUDING IT IS OLD NEWS
----------------------------------------------------
Measured on **144.0.7559.132**, the only patched binary reachable from CI. But
the defect is NOT confined to it: ``015-canvas-measure-text.patch`` is
**byte-identical at HEAD** after the PS-299 rebase onto 152.0.7977.75 (it was
one of the six patches the rebase left untouched), so the same two lines are in
the 152 engines. Confirming that by measurement on a 152 binary is left to
whoever has one — this file states what it measured and on what.

⭐ persona's masking layer HIDES it — on an origin the layer can reach. With the
extension layer ON, ``measureText`` reads the true metrics again: the
``measuretext`` extension replaces the value before a page sees it. So the
defect is invisible to any test that runs the product end-to-end, which is why
it needed a **layer-OFF** read of a **raw engine** to surface at all. It matters
anyway — the engine is shipped as the thing that masks natively, and a native
layer that has to be rescued by the JS layer is not doing its job.

⚠️ THE PAGE IS SERVED OVER HTTP FOR THAT REASON, and it is not a detail. An
earlier revision of this script probed a ``data:`` URL, where the layer-ON and
layer-OFF readings came back IDENTICAL — which looks like "the layer does not
help" and is not. Chromium does not inject extension content scripts into
``data:`` URLs at all, so that run measured the engine twice and the layer never
once. A loopback ``http://`` origin is the smallest venue where BOTH states are
really presented, so ``--layer on`` measures something.

Run from the repo root::

    python3 -m scripts.ps301_measuretext_repro \
        --patched-dir /tmp/ps301/engine \
        --control-dir /tmp/ps301/engine-control
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import time

# Strings of increasing length. The POINT of using several is the ratio: a
# multiplicative fault gives the SAME patched/control ratio for all of them,
# and an additive one cannot.
SAMPLES = ("a", "ab", "hello world", "persona PS-301 \u2014 canvas")

_PAGE = """<!doctype html><meta charset="utf-8"><pre id="o">PENDING</pre><script>
var c = document.createElement('canvas');
var x = c.getContext('2d');
x.font = '14px Arial';
var out = [];
%%SAMPLES%%.forEach(function (t) {
  var m = x.measureText(t);
  out.push({
    text: t,
    width: m.width,
    ascent: m.actualBoundingBoxAscent,
    descent: m.actualBoundingBoxDescent,
    left: m.actualBoundingBoxLeft,
    right: m.actualBoundingBoxRight
  });
});
document.getElementById('o').textContent = JSON.stringify(out);
</script>"""


def _serve(html: bytes):
    """A loopback origin for the probe page.

    Deliberately http:// rather than a data: URL — see the module docstring:
    chromium injects no content script into data:, so a data: probe silently
    measures the engine twice and never presents the layer at all.
    """
    import http.server
    import threading

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib naming
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, *a, **kw):
            pass

    class _Server:
        def __enter__(self):
            self._srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            self._t = threading.Thread(
                target=self._srv.serve_forever,
                kwargs={"poll_interval": 0.1},
                daemon=True,
            )
            self._t.start()
            host, port = self._srv.server_address[:2]
            self.url = f"http://{host}:{port}/"
            return self

        def __exit__(self, *exc):
            try:
                self._srv.shutdown()
            finally:
                self._srv.server_close()
            self._t.join(timeout=5)

    return _Server()


def read(engine_dir: str, url: str, *, seed: int, install_layer: bool) -> list:
    """Read TextMetrics out of one engine, through a real launched page."""
    from src.core import config as _config
    from src.services.verify import chromium_tier

    prev = os.environ.get("PERSONA_ENGINE_DIR")
    os.environ["PERSONA_ENGINE_DIR"] = engine_dir
    importlib.reload(_config)
    try:
        session = chromium_tier.ChromiumSession(
            "",
            seed=seed,
            declared_machine="windows",
            allow_unsandboxed=True,
            allow_no_proxy=True,
            install_layer=install_layer,
        )
        with session as live:
            page = live.new_page()
            page.goto(url, timeout=90000, wait_until="load")
            time.sleep(1.5)
            return json.loads(page.inner_text("#o"))
    finally:
        if prev is None:
            os.environ.pop("PERSONA_ENGINE_DIR", None)
        else:
            os.environ["PERSONA_ENGINE_DIR"] = prev
        importlib.reload(_config)


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--patched-dir", required=True)
    ap.add_argument("--control-dir", required=True)
    ap.add_argument("--seed", type=int, default=4242)
    ap.add_argument(
        "--layer", default="off", choices=("on", "off"),
        help="persona's masking layer. Defaults OFF — the layer HIDES this.",
    )
    args = ap.parse_args(argv)
    on = args.layer == "on"

    html = _PAGE.replace("%%SAMPLES%%", json.dumps(list(SAMPLES))).encode("utf-8")
    with _serve(html) as server:
        patched = read(args.patched_dir, server.url, seed=args.seed, install_layer=on)
        control = read(args.control_dir, server.url, seed=args.seed, install_layer=on)

    print(f"PS-301 — measureText, patched vs control (layer {args.layer})")
    print("=" * 72)
    ratios = []
    for p, c in zip(patched, control):
        ratio = (p["width"] / c["width"]) if c["width"] else float("nan")
        ratios.append(ratio)
        print(f"  {p['text']!r}")
        print(f"      control width : {c['width']}")
        print(f"      patched width : {p['width']}")
        print(f"      ratio         : {ratio}")
    print()
    distinct = {f"{r:.17g}" for r in ratios}
    if len(distinct) != 1:
        print(f"  ratios are NOT constant ({len(distinct)} distinct) — re-read the cause.")
        return 0

    ratio = float(distinct.pop())
    print(f"  RATIO IS CONSTANT across {len(ratios)} strings: {ratio!r}")
    print(
        "  A constant ratio is what a MULTIPLY produces and what an ADD cannot,\n"
        "  which is the evidence that this is TextMetrics::Shuffle() multiplying."
    )
    print()
    # The two states are read against DIFFERENT expectations, because they are
    # different claims. Collapsing them into one "constant ratio" line is what
    # made an earlier revision of this script look like it had proved something
    # on a data: URL where it had measured nothing.
    if abs(ratio) < 1e-3:
        print(
            "  VERDICT: the engine's OWN measureText is DESTROYED — the reported\n"
            "  metrics are ~0, not perturbed. 015 hands Shuffle() a value in\n"
            "  [-5e-06, +5e-06] (norm_x * 0.00001, centred on 0) where upstream\n"
            "  passed a MULTIPLIER centred on 1. No real browser reports a\n"
            "  sub-micron text width, so this is itself a detection signal."
        )
    elif 0.9 < ratio < 1.1:
        print(
            "  VERDICT: metrics are PLAUSIBLE here — a small perturbation around\n"
            "  1.0, which is the shape a working spoof has.\n"
            "  With --layer on that means persona's `measuretext` extension is\n"
            "  REPLACING the engine's broken value before the page sees it: the\n"
            "  JS layer is rescuing the native layer, and the underlying engine\n"
            "  defect is still there (re-run with --layer off to see it)."
        )
    else:
        print(f"  VERDICT: unexpected ratio {ratio!r} — neither destroyed nor ~1.")
    return 0


if __name__ == "__main__":  # pragma: no cover - script entry point
    raise SystemExit(main())
