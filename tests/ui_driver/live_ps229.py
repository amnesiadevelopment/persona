"""Drive the PS-229 Activity Log / engines-rail rework against the REAL app.

WHY THIS FILE EXISTS
--------------------
PS-179's own acceptance record marked AC3 — resize by drag — as "NOT COVERED BY
DRIVING" and shipped it. That unverified criterion is the one the owner hit
within minutes of installing 3.0.1: "я не могу ползунком менять высоту активити
лога". The standing directive for this project is that every surface shipped is
exercised live, and the ticket is explicit that a handler called directly, a
substring found in generated source, or an assertion that a mechanism exists is
NOT coverage here — each of those passes against an implementation that does
not work, which is exactly how AC3 shipped broken.

So this script drives a REAL POINTER against a REAL SERVED APP and reads the
consequences out of the accessibility tree and the rendered geometry.

WHAT CHANGED THAT MAKES AC3 DRIVABLE AT ALL
-------------------------------------------
The grip was a bare ``GestureDetector``, which paints NO semantics node — that
is *why* nothing could address it to test it, and why the old
``live_log_dock.py`` recorded it as not covered rather than pretending. It now
carries a tooltip, which makes Flutter emit a real node, and a 14px hit region
instead of 9px. Measured here: the node is present at a box of height 14 and a
press/move/release on it moves the console.

RUN IT
------
    python3 -m tests.ui_driver.live_ps229

Requires flet, playwright and a chromium at ``driver.SYSTEM_CHROMIUM``. It is a
SCRIPT rather than a pytest module, deliberately and for the same reason
``live_log_dock.py`` is: it boots a real app and a real browser, feeds a live
event stream and reports a table. Its output is what gets quoted on the ticket.

WHAT IS NOT COVERED, RECORDED RATHER THAN SMOOTHED OVER
-------------------------------------------------------
The ROW-ALIGNMENT fix (one type size across the row) is asserted structurally in
tests/test_log_dock.py and is NOT driven here, because the defect it fixes does
not reproduce on this platform: it is a font-metric error, invisible with
"monospace" resolving to DejaVu Sans Mono on Linux and visible with Consolas on
Windows. Driving it on this box would produce a green check that says nothing
about the machine the bug was reported from. Recorded as not covered, with the
reason, rather than as covered by a weaker check.
"""

from __future__ import annotations

import os
import sys
import time

from .driver import FletDriver
from .server import serve_app

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: Seeds a roster and a live event feed, so the console has profiles to group by
#: and events genuinely ARRIVING while it is watched. It patches nothing about
#: the dock — it only calls the app's own _log(), which is what every real UI
#: action calls.
#:
#: The profile names are the harness's, not a requirement: they exist so the
#: profile column and the fullscreen per-profile filter have something real to
#: parse. The BUILD strings the rail is measured against are deliberately the
#: long real ones, so the fit is measured under genuine load.
_SEED = '''
import threading, time

PROFILES = ["shop-de-03", "mail-us-011", "shop-us-01", "bank-uk-07"]

_orig_main = App._main

def _patched_main(self, page):
    _orig_main(self, page)
    for n in PROFILES:
        try:
            self.pm.add_profile(n, "", "windows")
        except Exception:
            pass

    def feed():
        shapes = [
            "Launching {p}",
            "{p}: LAUNCH_FAILED: engine firefox-142 missing",
            "Loaded 6 bookmarks, 0 pools for {p}",
            "Session ended: {p}",
            "Engine update available",
        ]
        i = 0
        time.sleep(5)
        while True:
            p = PROFILES[i % len(PROFILES)]
            self._log(shapes[i % len(shapes)].format(p=p))
            i += 1
            time.sleep(0.35)

    threading.Thread(target=feed, daemon=True).start()

App._main = _patched_main
'''

GRIP_TIP = "Drag to resize the Activity Log"


def _report(name: str, ok: bool, detail: str) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return ok


def _dismiss(drv: FletDriver) -> None:
    """Two gates: the onboarding wizard AND the "what's new" dialog."""
    for label in ("Skip", "[ got it ]"):
        if drv.has_button(label):
            drv.press(label)


def _node_with(drv: FletDriver, needle: str):
    """The first node whose text or label carries ``needle``.

    Leaf-ish matching on purpose: the ROOT node's innerText is the whole page,
    so a naive `in` test matches everything on screen and turns a check into a
    tautology that passes when the control is absent.
    """
    for n in drv.nodes():
        blob = f"{n.text or ''} {n.label or ''}"
        if needle in blob and n.box[3] < 200:
            return n
    return None


def _grip(drv: FletDriver):
    return _node_with(drv, GRIP_TIP)


#: How long the console needs before a gesture's effect is READABLE.
#:
#: MEASURED, and it is the difference between this script reporting the truth
#: and reporting a defect that is not there. Sampling the semantics tree
#: immediately after ``mouse.up()`` returns the PRE-GESTURE geometry — the
#: first draft of this file read at +0ms and reported "the grip did nothing"
#: against a build where the grip works. Sampled: +0ms still says "6 rows" /
#: top=780, +400ms says "14 rows" / top=604, and it settles at "15 rows" /
#: top=582 by +1300ms. So the read is deliberately late, and a check that
#: needs a stable value re-reads until it stops moving.
_SETTLE_MS = 2500


def _rows_readout(drv: FletDriver) -> str:
    """The console's own size readout — "6 rows" / "1 row".

    Read from the RENDERED tree rather than computed, because the point is what
    the operator can see. ``fullmatch`` so the root node (whose text is the
    whole page) cannot answer for it.
    """
    import re

    for n in drv.nodes():
        t = (n.text or "").strip()
        if re.fullmatch(r"\d+ rows?", t):
            return t
    return ""


def _settled(drv: FletDriver, read, tries: int = 6, gap_ms: int = 500):
    """Read ``read`` until two consecutive samples agree.

    Flutter repaints asynchronously, so a single sample after a gesture is a
    race — see :data:`_SETTLE_MS`. Polling for stability rather than sleeping a
    fixed time keeps the check honest on a slow box without inflating the run.
    """
    # Wait out the known repaint window FIRST. Polling for "two consecutive
    # samples agree" on its own is not enough: during a smooth animation two
    # adjacent samples can legitimately match while the value is still moving,
    # which latched a mid-flight "8 rows" against a console the screenshot
    # showed at "1 row".
    drv.page.wait_for_timeout(_SETTLE_MS)
    previous = read()
    for _ in range(tries):
        drv.page.wait_for_timeout(gap_ms)
        current = read()
        if current == previous:
            return current
        previous = current
    return previous


def _band_top(drv: FletDriver) -> int | None:
    """The console band's TOP EDGE, taken from the grip that sits on it.

    The band paints no node of its own, so its top is the grip's — which is
    exactly the edge the drag moves, and therefore the honest thing to measure.
    """
    g = _grip(drv)
    return g.box[1] if g else None


def _drag_grip(drv: FletDriver, dy: int, steps: int = 12) -> None:
    """A REAL pointer gesture on the grip: press, move in steps, release.

    Steps matter and are not decoration. A single mouse.move is one enormous
    frame; the defect being verified lives in the PER-FRAME delta path
    (``e.local_delta.y``), and the accumulator that makes small frames add up
    is the thing that was broken. So this moves the way a hand does.
    """
    g = _grip(drv)
    assert g is not None, "the grip is not addressable — AC3 cannot be driven"
    x = g.box[0] + g.box[2] // 2
    y = g.box[1] + g.box[3] // 2

    drv.page.mouse.move(x, y)
    drv.page.mouse.down()
    for i in range(1, steps + 1):
        drv.page.mouse.move(x, y + int(dy * i / steps))
        drv.page.wait_for_timeout(45)
    drv.page.mouse.up()
    # GENEROUS ON PURPOSE. The next gesture re-reads the grip's box, and a
    # stale box puts the press somewhere that is no longer the grip — so the
    # drag silently does nothing and reads as a product defect. Measured: a
    # 900ms settle produced exactly that false negative at 1024x680.
    drv.page.wait_for_timeout(_SETTLE_MS)


def main() -> int:
    results: list[bool] = []

    with serve_app(REPO_ROOT, patch=_SEED) as app:
        print(f"served: {app.url}\nhome:   {app.home}\n")

        # ---------------------------------------------------------------
        # The default, the grip, and the one-line state — at a normal size.
        # ---------------------------------------------------------------
        with FletDriver(app.url, width=1440, height=980) as drv:
            _dismiss(drv)
            drv.page.wait_for_timeout(9000)

            print("DEFAULT — the console opens at SIX ROWS on a fresh process")
            # OBSERVED, not computed: this is the readout the console paints
            # for itself, on a process that has just started.
            opened = _rows_readout(drv)
            results.append(
                _report(
                    "a freshly started process opens at 6 rows",
                    opened == "6 rows",
                    f"readout={opened!r}",
                )
            )

            print("\nAC3 — the grip is ADDRESSABLE (the PS-179 blocker)")
            g = _grip(drv)
            results.append(
                _report(
                    "the grip paints a semantics node a pointer can find",
                    g is not None,
                    f"node={g.box if g else None} tooltip={GRIP_TIP!r}",
                )
            )
            results.append(
                _report(
                    "its hit region is the widened one, not the 9px original",
                    bool(g) and g.box[3] >= 14,
                    f"height={g.box[3] if g else None}px",
                )
            )

            print("\nAC3 — the grip RESIZES under a real pointer gesture")
            before_top = _band_top(drv)
            before_rows = _rows_readout(drv)
            # Drag UP: the console grows. Read only once the repaint has
            # SETTLED — see _SETTLE_MS; an immediate read returns the
            # pre-gesture geometry and reports a working grip as broken.
            _drag_grip(drv, dy=-220)
            after_rows = _settled(drv, lambda: _rows_readout(drv))
            after_top = _band_top(drv)

            grew = (
                before_top is not None
                and after_top is not None
                and after_top < before_top - 20
            )
            results.append(
                _report(
                    "dragging up moved the band's top edge and grew the console",
                    grew and after_rows != before_rows,
                    f"top {before_top}->{after_top}  rows {before_rows!r}->{after_rows!r}",
                )
            )

            # Drag DOWN: it shrinks again. The old bug was ASYMMETRIC (up dead,
            # down working), so verifying only one direction would have passed
            # against the broken build.
            mid_top, mid_rows = after_top, after_rows
            _drag_grip(drv, dy=+150)
            back_rows = _settled(drv, lambda: _rows_readout(drv))
            back_top = _band_top(drv)
            results.append(
                _report(
                    "dragging down shrinks it again (the old bug was one-way)",
                    back_top is not None
                    and mid_top is not None
                    and back_top > mid_top + 10,
                    f"top {mid_top}->{back_top}  rows {mid_rows!r}->{back_rows!r}",
                )
            )

            drv.screenshot("/tmp/ps229-drag-resized.png")
            print("  screenshot: /tmp/ps229-drag-resized.png")

            print("\nONE-LINE STATE — a real one-row log that KEEPS REPORTING")
            # Reached through the control that names it, which is the gesture
            # the owner asked for ("либо скрыть до 1 строчки").
            if drv.has_button("Shrink to a single line"):
                drv.press("Shrink to a single line")
            # SETTLED, not a fixed sleep: measured, this lands on "1 row" by
            # ~2.5s and a 1.5s read catches it mid-flight still reporting the
            # previous size — a pass/fail decided by the box's speed.
            one = _settled(drv, lambda: _rows_readout(drv))
            results.append(
                _report("the console reaches a single row", one == "1 row", f"readout={one!r}")
            )

            # THE POINT OF THE STATE: it is a LOG, not a dead strip. Watch the
            # newest line change while it is one row tall.
            def newest() -> str:
                rows = [
                    n.text
                    for n in drv.nodes()
                    if n.text and "\n" in n.text and n.box[3] <= 24 and n.box[2] > 400
                ]
                return rows[-1] if rows else ""

            first = newest()
            drv.page.wait_for_timeout(4000)
            second = newest()
            results.append(
                _report(
                    "events still arrive while it is one row tall",
                    bool(first) and bool(second) and first != second,
                    f"{first[:40]!r} -> {second[:40]!r}",
                )
            )
            drv.screenshot("/tmp/ps229-one-line.png")
            print("  screenshot: /tmp/ps229-one-line.png")

            print("\nFULLSCREEN — borderless, with filters and search")
            btn = _node_with(drv, "Open full Activity Log")
            if btn:
                drv.page.mouse.click(btn.box[0] + btn.box[2] // 2, btn.box[1] + btn.box[3] // 2)
            drv.page.wait_for_timeout(2500)

            texts = [(n.text or "").strip() for n in drv.nodes()]
            blob = " ".join(texts)
            vw = drv.page.evaluate("() => window.innerWidth")
            vh = drv.page.evaluate("() => window.innerHeight")

            # The severity filters, the per-profile filters and the search are
            # what the size BUYS — a full screen of log is a different reading
            # task from a six-line tail.
            #
            # Each of the three is read from the surface that ACTUALLY carries
            # it, which is not the same surface for all three:
            #   * the filter chips are semantics text nodes;
            #   * the SEARCH FIELD is a real DOM input, so it appears in
            #     drv.fields() and NOT in the semantics text — looking for it
            #     in the text blob reported "no search" against a view that
            #     has one;
            #   * the match readout is matched ANCHORED. A bare `\d+ of \d+`
            #     also matches the profile list's own "Page 1 of 1" behind the
            #     view, i.e. it passed without the readout existing at all.
            import re

            has_sev = all(w in blob for w in ("failures", "ok", "info"))
            has_search = any(
                "search the log" in (f.label or "") for f in drv.fields()
            )
            has_profile = "shop-de-03" in blob
            match_readout = any(
                re.fullmatch(r"\d+ of \d+", t) for t in texts
            )
            results.append(
                _report(
                    "severity filters, profile filters, search and a match readout",
                    has_sev and has_search and has_profile and match_readout,
                    f"sev={has_sev} search={has_search} profiles={has_profile} readout={match_readout}",
                )
            )

            # BORDERLESS: the old view was a fixed 1000x600 AlertDialog floating
            # in a scrim. The surface must now be the WINDOW.
            spans = [
                n.box
                for n in drv.nodes()
                if n.box[2] >= vw - 40 and n.box[3] >= vh - 120
            ]
            results.append(
                _report(
                    "the view fills the window rather than floating in a frame",
                    bool(spans),
                    f"viewport={vw}x{vh} full-bleed boxes={spans[:2]}",
                )
            )
            drv.screenshot("/tmp/ps229-fullscreen.png")
            print("  screenshot: /tmp/ps229-fullscreen.png")

        # ---------------------------------------------------------------
        # The rail's budget, reached by RESIZING INTO the app minimum.
        # ---------------------------------------------------------------
        print("\nAPP MINIMUM — resize INTO 1024x680, do not launch at it")
        with FletDriver(app.url, width=1280, height=820) as drv:
            _dismiss(drv)
            drv.page.wait_for_timeout(7000)

            drv.page.set_viewport_size({"width": 1024, "height": 680})
            drv.page.wait_for_timeout(4000)

            texts = [(n.text or "").lower() for n in drv.nodes() if n.text]
            blob = " ".join(texts)
            has_trash = "trash" in blob
            has_version = "persona v" in blob

            results.append(
                _report(
                    "the rail's bottom cluster survives the resize (trash + version)",
                    has_trash and has_version,
                    f"trash={has_trash} version_panel={has_version}",
                )
            )

            print("\n  THE DRAG BUDGET — the owner's call: the rail wins")
            # THE KNOWN-OPEN ITEM THIS TICKET CLOSES. Before the fix, drag_by()
            # clamped to MAX_HEIGHT only and ignored affordable_height(), so at
            # this window the grip would take the dock to 494px and leave the
            # fixed cluster starved by 374px — after which any window event
            # silently healed it. Drag as hard as an operator can and confirm
            # the cluster is STILL there.
            for _ in range(4):
                _drag_grip(drv, dy=-260)

            capped = _settled(drv, lambda: _rows_readout(drv))
            texts_after = [(n.text or "").lower() for n in drv.nodes() if n.text]
            blob_after = " ".join(texts_after)
            still_trash = "trash" in blob_after
            still_version = "persona v" in blob_after

            results.append(
                _report(
                    "an all-out drag cannot starve the rail at the app minimum",
                    still_trash and still_version,
                    f"rows={capped!r} trash={still_trash} version_panel={still_version}",
                )
            )
            # The documented consequence of the owner's decision, OBSERVED.
            #
            # ASSERTED AS THE EXACT MEASURED VALUE, not a tolerant range. The
            # budget is arithmetic — affordable_height(680) is 120px, which is
            # height_for_rows(3) — so a range would accept a cap that had
            # silently drifted, and drift is precisely what this check exists
            # to catch: the ticket is explicit that the ~3-row cap is "not a
            # number to quietly raise".
            results.append(
                _report(
                    "the grip stops at the window's budget (3 rows), by design",
                    capped == "3 rows",
                    f"readout={capped!r} (the accepted cap at 1024x680)",
                )
            )
            drv.screenshot("/tmp/ps229-min-1024x680.png")
            print("  screenshot: /tmp/ps229-min-1024x680.png")

    print("\nNOT COVERED — the Windows row-alignment observation.")
    print("  The three-type-size baseline error is a FONT-METRIC defect: it is")
    print("  invisible with DejaVu Sans Mono (Linux) and visible with Consolas")
    print("  (Windows). This box cannot reproduce it, so driving it here would")
    print("  produce a green check that says nothing about the machine it was")
    print("  reported from. The fix is structural (one type size across the")
    print("  row) and is asserted in tests/test_log_dock.py. Recorded as not")
    print("  covered, with the reason — never as covered by a weaker check.")

    ok = all(results)
    print(
        f"\n{'ALL DRIVEN CHECKS PASSED' if ok else 'SOME DRIVEN CHECKS FAILED'} "
        f"({sum(results)}/{len(results)})"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
