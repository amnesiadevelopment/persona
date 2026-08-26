"""Drive the Activity Log console dock against the REAL running app (PS-179).

WHY THIS FILE IS COMMITTED
--------------------------
The ticket's verification standard is that everything shipped is exercised
live. The scroll criteria in particular CANNOT be established headlessly: a
test that calls ``_on_scroll`` directly cannot distinguish the fixed behaviour
from the broken one, because the broken one has a scroll region too. The
defect this direction exists to fix — and the self-pausing-follow bug found on
the way — were both only visible with real events arriving into a real
viewport.

The first round of this work ran that check from an UNCOMMITTED scratch script,
so the evidence could not be re-run by anyone else and the suite's own docstring
pointed at a file that was not in the repo. This is that script, committed.

RUN IT
------
    python3 -m tests.ui_driver.live_log_dock

Requires flet, playwright and a chromium at ``driver.SYSTEM_CHROMIUM``. It is a
SCRIPT, not a pytest module, deliberately: it boots a real app and a real
browser, feeds a live event stream for ~30s and reports a table. It is run when
the dock changes, and its output is what gets quoted on the ticket.

WHAT IT COVERS, AND WHAT IT DOES NOT
------------------------------------
Driven here: AC1 (full-width band under rail and page), AC2 (collapse to a
34px strip that still reports), AC6 (follow -> pause on a real wheel gesture ->
one click back), AC7 (a position established before an event still points at
the same entry after it), AC8 (the rail at 1024x680 — reached by RESIZING into
it, which is the path the launch-only check missed).

NOT driven: AC3, the resize grip. It is a GestureDetector with no visible label
and paints no semantics node, so it is not addressable by this driver — six
coordinate offsets across the band each with a real press/move/release moved
``LogDock.height`` by nothing. The clamp and the collapse/expand round trip are
covered headless in tests/test_log_dock.py instead. Recorded as not covered,
with the reason, rather than as covered by a weaker check.
"""

from __future__ import annotations

import os
import sys
import time

from .driver import FletDriver
from .server import serve_app

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: Seeds a roster and a live event feed into the served app, so the console has
#: many profiles to group by and events genuinely ARRIVING while it is watched.
#: Runs in the child after `src` is importable and before the app is built —
#: the same hook the negative-control test uses. It patches nothing about the
#: dock; it only calls the app's own _log(), which is what every real UI action
#: calls.
_SEED_A_LIVE_FEED = '''
import threading, time

PROFILES = ["shop-de-03", "mail-us-011", "shop-us-01", "bank-uk-07"]

_orig_main = App._main

def _patched_main(self, page):
    # _main is SYNC (src/ui/app.py). Wrapping it in async made flet await a
    # None and the whole seed never ran.
    _orig_main(self, page)
    for n in PROFILES:
        try:
            self.pm.add_profile(n, "", "windows")
        except Exception:
            pass

    def feed():
        # The four shapes the profile column must parse, plus one event with
        # no resolvable profile so the neutral placeholder is exercised.
        shapes = [
            "Launching {p}",
            "{p}: LAUNCH_FAILED: engine firefox-142 missing",
            "Loaded 6 bookmarks, 0 pools for {p}",
            "Session ended: {p}",
            "Engine update available",
        ]
        i = 0
        time.sleep(6)
        while True:
            p = PROFILES[i % len(PROFILES)]
            self._log(shapes[i % len(shapes)].format(p=p))
            i += 1
            time.sleep(0.35)

    threading.Thread(target=feed, daemon=True).start()

App._main = _patched_main
'''

# The console's own geometry, read from the shipped module so this script
# cannot drift from the values the app actually uses.
_GEOMETRY_JS = """() => {
  // LEAVES ONLY. The root flt-semantics carries the whole page's innerText,
  // so any match against it is a match against everything on screen.
  const els = [...document.querySelectorAll('flt-semantics')]
    .filter(e => !e.querySelector('flt-semantics'));
  const texts = els.map(e => (e.innerText || '').trim());
  return {
    following: texts.some(t => t.includes('following')),
    paused: texts.some(t => t.includes('paused')),
    jump: texts.some(t => /\\d+ new/.test(t) || t.includes('jump to newest')),
    events_label: (texts.find(t => /· \\d+ events/.test(t)) || ''),
    plus_counter: (texts.find(t => /^\\+\\d+$/.test(t)) || ''),
  };
}"""


def _state(drv: FletDriver) -> dict:
    return drv.page.evaluate(_GEOMETRY_JS)


def _dismiss_onboarding(drv: FletDriver) -> None:
    """Get to the main screen.

    TWO gates, not one: the onboarding wizard AND the "what's new" dialog that
    3.0.0 shows on first run. Dismissing only the first leaves a modal covering
    the whole app, and every probe below then reports the console as absent —
    which reads exactly like a broken dock rather than an undismissed dialog.
    """
    for label in ("Skip", "[ got it ]"):
        if drv.has_button(label):
            drv.press(label)


def _band_box(drv: FletDriver) -> tuple[int, int, int, int] | None:
    """The console band's bounding box, found by the header text it carries."""
    # Leaf nodes only, for the same reason _GEOMETRY_JS filters: the root node
    # contains "ACTIVITY" in its innerText and its box is the WHOLE VIEWPORT,
    # which silently turns the AC1 width check into a tautology that passes
    # even when the console is not on screen at all.
    boxes = [
        n.box for n in drv.nodes() if n.leaf and n.text and "ACTIVITY" in n.text.strip()
    ]
    return boxes[0] if boxes else None


def _follow_box(drv: FletDriver) -> tuple[int, int, int, int] | None:
    """The follow-state indicator, which sits at the console's RIGHT edge."""
    # NOT gated on `leaf`: the driver computes leaf as "no child elements", and
    # this indicator wraps its text in one, so requiring leaf found nothing and
    # read as a missing console. An EXACT text match is already specific enough
    # to exclude the root (whose text is the entire page).
    for n in drv.nodes():
        t = (n.text or "").strip()
        if t in ("following", "paused — reading"):
            return n.box
    return None


def _report(name: str, ok: bool, detail: str) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return ok


def main() -> int:
    results: list[bool] = []
    with serve_app(REPO_ROOT, patch=_SEED_A_LIVE_FEED) as app:
        print(f"served: {app.url}\nhome:   {app.home}\n")

        # ---- AC1 / AC6 / AC7 / AC2 at the default size -------------------
        with FletDriver(app.url, width=1440, height=950) as drv:
            _dismiss_onboarding(drv)
            drv.page.wait_for_timeout(9000)  # let the feed start arriving
            vw = drv.page.evaluate("() => window.innerWidth")

            print("AC1 — full-width dock under rail and page")
            # The band paints no node of its own, so its EXTENT is measured
            # from the console content that does: the header label sits at the
            # far left (x <= rail width) and the follow indicator at the far
            # right, and BOTH sit below the page content. A single box would
            # not do — the only node whose width equals the viewport is the
            # root, and matching that made this check pass vacuously.
            head, foll = _band_box(drv), _follow_box(drv)
            spans = bool(head and foll) and head[0] <= 12 and (
                foll[0] + foll[2]
            ) >= vw - 120
            under_rail = bool(head) and head[0] < 200  # left of the 200px rail edge
            results.append(
                _report(
                    "console content spans the window under the rail",
                    spans and under_rail,
                    f"header={head} follow={foll} viewport_width={vw}",
                )
            )

            print("\nAC6 — follow, then a real wheel gesture up")
            before = _state(drv)
            results.append(
                _report("header reads following", before["following"], str(before))
            )

            # A real wheel gesture over the console band, not a synthetic call.
            drv.page.mouse.move(vw // 2, head[1] + 60)
            for _ in range(6):
                drv.page.mouse.wheel(0, -240)
                drv.page.wait_for_timeout(120)
            drv.page.wait_for_timeout(1500)
            paused = _state(drv)
            results.append(
                _report(
                    "scrolling up pauses the follow",
                    paused["paused"] and not paused["following"],
                    str(paused),
                )
            )
            results.append(
                _report("a way back is offered", paused["jump"], str(paused))
            )

            print("\nAC7 — the position survives events arriving")
            # Ten flushes at ~0.35s apart is >3s of live arrivals.
            t0 = time.time()
            drv.page.wait_for_timeout(6000)
            still = _state(drv)
            results.append(
                _report(
                    "still paused after ~%.0fs of arrivals" % (time.time() - t0),
                    still["paused"],
                    str(still),
                )
            )

            print("\nAC2 — collapse still reports")
            # The collapse target is the header's left cluster.
            drv.page.mouse.click(head[0] + 40, head[1] + 12)
            drv.page.wait_for_timeout(5000)
            collapsed = _state(drv)
            newbox = _band_box(drv)
            results.append(
                _report(
                    "collapsed strip still counts what arrived",
                    bool(collapsed["plus_counter"]),
                    f"counter={collapsed['plus_counter']!r} box={newbox}",
                )
            )

        # ---- AC8: RESIZE into the minimum, do not launch at it ------------
        print("\nAC8 — resize INTO 1024x680 (the path a launch-only check misses)")
        with FletDriver(app.url, width=1280, height=820) as drv:
            _dismiss_onboarding(drv)
            drv.page.wait_for_timeout(6000)
            tall = _band_box(drv)

            drv.page.set_viewport_size({"width": 1024, "height": 680})
            drv.page.wait_for_timeout(4000)
            short = _band_box(drv)
            texts = [n.text for n in drv.nodes() if n.text]
            has_trash = any("trash" in (t or "").lower() for t in texts)

            results.append(
                _report(
                    "the dock yielded height on the resize",
                    bool(tall and short) and short[3] <= tall[3],
                    f"before={tall} after={short}",
                )
            )
            results.append(
                _report("the rail still shows trash", has_trash, f"found={has_trash}")
            )
            drv.screenshot("/tmp/ps179-resized-1024x680.png")
            print("  screenshot: /tmp/ps179-resized-1024x680.png")

    print("\nAC3 — NOT COVERED: the grip paints no semantics node and is not")
    print("      addressable by this driver. Clamp + round trip covered headless.")
    ok = all(results)
    print(f"\n{'ALL DRIVEN CHECKS PASSED' if ok else 'SOME DRIVEN CHECKS FAILED'} "
          f"({sum(results)}/{len(results)})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
