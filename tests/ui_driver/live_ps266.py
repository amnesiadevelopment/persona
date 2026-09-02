"""Drive PS-266 — the fullscreen Activity Log's bounded reveal — against the REAL app.

WHY THIS FILE EXISTS
--------------------
The standing directive for this project is that everything shipped is exercised
live. A unit test asserting ``max_lines == 5`` is NOT coverage of AC1: it
passes just as happily against an implementation the operator cannot use — the
reveal never painted, the control unclickable, the tail never on screen. PS-179
shipped an un-driven criterion and the owner hit it within minutes; this is the
same surface, so it is driven.

WHAT IT DRIVES
--------------
1. A REAL 460-character refusal — the string ``process.py:233-241`` composes,
   with the ``Error starting process: `` prefix ``launcher.py`` adds — is seeded
   into the log through the app's own ``_log()``.
2. The fullscreen Activity Log is opened by clicking the node the operator
   clicks ("Open full Activity Log").
3. The reveal control is found by its tooltip and CLICKED, and the row's own
   rendered geometry is read before and after.
4. The short line (``Launching shop-de-03``) is checked for the ABSENCE of a
   reveal node (AC2).
5. All of it twice: at 1280x820 and after resizing into the app's minimum
   1024x680, where the character budget is tighter.

THE TRAP THIS SCRIPT AVOIDS, STATED UP FRONT
--------------------------------------------
Flutter's semantics tree carries a Text's FULL string, not its rendered
truncation — so "the tail is in the tree after I clicked" can be true against a
build where the reveal does nothing at all. That check alone would be an
assertion that cannot fail, which is precisely the shape this harness exists to
make impossible. So the load-bearing evidence here is GEOMETRY: the message
node's rendered height grows from one line to several when the reveal is
clicked, and shrinks back when it is clicked again. The semantics text is read
too, and whether the collapsed state already leaked the tail is REPORTED rather
than hidden — if it did, the text check is explicitly recorded as non-evidence.

WHAT IS NOT COVERED, RECORDED RATHER THAN SMOOTHED OVER
-------------------------------------------------------
AC3 (selection/copy) is NOT driven. Text selection in Flutter is a canvas-level
gesture: the glyphs are painted, the selection is not a DOM range, and no
clipboard result is readable from the semantics tree. What IS driven is that
the cell the operator drags across is the SelectableText one — asserted
structurally in tests/test_ps266_fullscreen_log_reveal.py. Recorded as not
covered, with the reason, never as covered by a weaker check.

RUN IT
------
    python3 -m tests.ui_driver.live_ps266

Requires flet, playwright and a chromium at ``driver.SYSTEM_CHROMIUM``. A
SCRIPT, not a pytest module, for the same reason its two siblings are: it boots
a real app and a real browser and reports a table whose output is quoted on the
ticket.
"""

from __future__ import annotations

import os
import sys

from .driver import FletDriver
from .server import serve_app

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: The payload the defect destroys. It is at the END of the refusal, which is
#: why a one-line ellipsis takes exactly the part that resolves the problem.
TAIL = "add a row for that country to _COUNTRY_TZ (launch_policy.py)"

#: A short line that must carry NO reveal (AC2).
SHORT = "Launching shop-de-03"

REVEAL_TIP = "Show the full message"
HIDE_TIP = "Hide the full message"
OPEN_FULLSCREEN_TIP = "Open full Activity Log"

#: Seeds the roster and the REAL refusal. The refusal is composed by calling
#: the product's own ``_timezone_for_profile`` path is not possible without a
#: proxy fixture, so the exact string those lines produce is reproduced here
#: verbatim — including the ``Error starting process: `` prefix that is what
#: actually lands in the log (``launcher.py``). 460 characters.
_SEED = '''
import threading, time

PROFILES = ["shop-de-03", "mail-us-011"]

REFUSAL = (
    "Error starting process: "
    "Profile 'shop-de-03' has proxy 'de-residential-01' assigned and its "
    "exit country is known (DE), "
    "but no timezone is known for that country and its last check recorded "
    "none. Refusing to launch: falling back to UTC would declare a clock "
    "that contradicts the exit's own country \\u2014 the 'spoofed location' tell "
    "this product exists to avoid. Re-checking will NOT help; add a row for "
    "that country to _COUNTRY_TZ (launch_policy.py) to resolve it."
)

_orig_main = App._main

def _patched_main(self, page):
    _orig_main(self, page)
    for n in PROFILES:
        try:
            self.pm.add_profile(n, "", "windows")
        except Exception:
            pass

    def feed():
        time.sleep(6)
        # The SHORT line first, then the long refusal. Both must be in the log
        # at once: AC2 is a comparison between them on the same screen.
        self._log("Launching shop-de-03")
        time.sleep(0.5)
        self._log("Loaded 6 bookmarks, 0 pools for mail-us-011")
        time.sleep(0.5)
        self._log(REFUSAL)

    threading.Thread(target=feed, daemon=True).start()

App._main = _patched_main
'''


def _report(name: str, ok: bool, detail: str) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return ok


def _dismiss(drv: FletDriver) -> None:
    for label in ("Skip", "[ got it ]"):
        if drv.has_button(label):
            drv.press(label)


def _tip_nodes(drv: FletDriver, tip: str) -> list:
    """Every node whose label or text carries ``tip``, small enough to be a
    control rather than the root (whose innerText is the whole page)."""
    out = []
    for n in drv.nodes():
        blob = f"{n.text or ''} {n.label or ''}"
        if tip in blob and n.box[3] < 200:
            out.append(n)
    return out


def _click(drv: FletDriver, node) -> None:
    drv.page.mouse.click(node.box[0] + node.box[2] // 2, node.box[1] + node.box[3] // 2)


def _message_node(drv: FletDriver, needle: str):
    """The rendered node carrying ``needle``, taken as the SMALLEST such box.

    Smallest on purpose: the refusal's text is inside the row, inside the list,
    inside the dialog, inside the page — every one of those ancestors' innerText
    contains it, and taking any of them would measure the ListView's height and
    call it the message's.
    """
    hits = [n for n in drv.nodes() if needle in (n.text or "")]
    if not hits:
        return None
    return min(hits, key=lambda n: n.box[2] * n.box[3])


def _open_fullscreen(drv: FletDriver) -> bool:
    btns = _tip_nodes(drv, OPEN_FULLSCREEN_TIP)
    if not btns:
        return False
    _click(drv, btns[0])
    drv.page.wait_for_timeout(2500)
    return True


def _run_at(drv: FletDriver, label: str, results: list[bool], shot: str) -> None:
    """The whole AC1/AC2 pass at whatever size ``drv`` is currently at."""
    print(f"\n{label} — the fullscreen Activity Log")

    opened = _open_fullscreen(drv)
    results.append(
        _report("the fullscreen Activity Log opens from the dock", opened, f"at {label}")
    )
    if not opened:
        return

    # --- AC2, the negative half, read BEFORE anything is clicked ----------
    reveals = _tip_nodes(drv, REVEAL_TIP)
    n_reveals = len(reveals)
    # The short line and the long line are BOTH on screen. Exactly one reveal
    # means the affordance discriminated; two or three would mean it is drawn
    # on lines that are already whole, which is the noise AC2 forbids.
    short_on_screen = any(SHORT in (n.text or "") for n in drv.nodes())
    results.append(
        _report(
            "a short line and a 460-char line are both on screen",
            short_on_screen,
            f"{SHORT!r} present={short_on_screen}",
        )
    )
    results.append(
        _report(
            "AC2 — exactly one reveal control, on the line that is cut",
            n_reveals == 1,
            f"{n_reveals} node(s) with tooltip {REVEAL_TIP!r} "
            f"(3 log lines on screen, 1 of them over budget)",
        )
    )

    # Is the short line's row carrying one? Answered by geometry rather than
    # by counting: the reveal must not sit on the short row.
    short_node = _message_node(drv, SHORT)
    if short_node and reveals:
        same_row = abs(reveals[0].box[1] - short_node.box[1]) < 12
        results.append(
            _report(
                "AC2 — the reveal is NOT on the short line's row",
                not same_row,
                f"short row y={short_node.box[1]} reveal y={reveals[0].box[1]}",
            )
        )

    # --- AC1: click it, and measure what changed --------------------------
    before = _message_node(drv, "Refusing to launch")
    before_h = before.box[3] if before else None
    # Does the COLLAPSED semantics tree already carry the tail? If it does, a
    # text-presence check after the click is not evidence, and this script says
    # so instead of banking it.
    tail_before = any(TAIL in (n.text or "") for n in drv.nodes())

    if reveals:
        _click(drv, reveals[0])
        drv.page.wait_for_timeout(2000)

    after = _message_node(drv, "Refusing to launch")
    after_h = after.box[3] if after else None
    tail_after = any(TAIL in (n.text or "") for n in drv.nodes())
    hides = _tip_nodes(drv, HIDE_TIP)

    grew = bool(before_h and after_h and after_h > before_h + 8)
    results.append(
        _report(
            "AC1 — clicking the reveal makes the message TALLER on screen",
            grew,
            f"message box height {before_h}px -> {after_h}px "
            f"(one line -> wrapped)",
        )
    )
    results.append(
        _report(
            "AC1 — the reveal flips to its 'hide' state (the row knows it is open)",
            len(hides) == 1,
            f"{len(hides)} node(s) with tooltip {HIDE_TIP!r}",
        )
    )
    results.append(
        _report(
            "AC1 — the sentence's TAIL is present after the reveal",
            tail_after,
            f"{TAIL!r} found={tail_after}"
            + (
                "  [NOTE: also present while COLLAPSED — Flutter's semantics "
                "carries the full string regardless of the rendered "
                "truncation, so this check is NOT evidence on its own; the "
                "height growth above is]"
                if tail_before
                else "  (absent while collapsed — so this IS a real reveal signal)"
            ),
        )
    )

    # The bound itself: revealed, the message must be TALLER than one line and
    # still SHORTER than an unbounded reflow of 460 chars would be. At this
    # budget the whole string is 4-5 lines; an unbounded cell would be the same
    # here, so the honest driven claim is the ceiling: it never exceeds five
    # lines' worth of box.
    if after_h and before_h:
        max_expected = before_h * 5 + 8
        results.append(
            _report(
                "AC1 — the reveal is BOUNDED (never more than 5 lines of box)",
                after_h <= max_expected,
                f"revealed={after_h}px, one line={before_h}px, "
                f"5-line ceiling={max_expected}px",
            )
        )

    drv.screenshot(shot)
    print(f"  screenshot: {shot}")

    # --- and back: the reveal is reversible -------------------------------
    if hides:
        _click(drv, hides[0])
        drv.page.wait_for_timeout(1800)
        back = _message_node(drv, "Refusing to launch")
        back_h = back.box[3] if back else None
        results.append(
            _report(
                "the reveal is reversible — clicking again re-collapses the row",
                bool(back_h and before_h and back_h <= before_h + 4),
                f"height {after_h}px -> {back_h}px (collapsed was {before_h}px)",
            )
        )


def main() -> int:
    results: list[bool] = []

    with serve_app(REPO_ROOT, patch=_SEED) as app:
        print(f"served: {app.url}\nhome:   {app.home}\n")

        # -------------------------------------------------------------
        # The default window the product ships at.
        # -------------------------------------------------------------
        with FletDriver(app.url, width=1280, height=820) as drv:
            _dismiss(drv)
            drv.page.wait_for_timeout(10000)
            _run_at(drv, "1280x820 (the shipped default)", results,
                    "/tmp/ps266-reveal-1280x820.png")

        # -------------------------------------------------------------
        # The app's MINIMUM, reached by RESIZING INTO it — the path a
        # launch-only check misses, and where the budget is tighter.
        # -------------------------------------------------------------
        print("\nAPP MINIMUM — resize INTO 1024x680, do not launch at it")
        with FletDriver(app.url, width=1280, height=820) as drv:
            _dismiss(drv)
            drv.page.wait_for_timeout(10000)
            drv.page.set_viewport_size({"width": 1024, "height": 680})
            drv.page.wait_for_timeout(4000)
            _run_at(drv, "1024x680 (window.min_width)", results,
                    "/tmp/ps266-reveal-1024x680.png")

    print("\nNOT COVERED BY DRIVING — AC3, selection and copy.")
    print("  Text selection in Flutter is a CANVAS-level gesture: the glyphs")
    print("  are painted, the selection is not a DOM range, and no clipboard")
    print("  result is readable from the semantics tree — a drag across the")
    print("  cell here would produce a green check that asserts nothing. What")
    print("  shipped is `selectable=True` on the fullscreen message cell (the")
    print("  capability v2.8.4 had and PS-229 removed), asserted structurally")
    print("  in tests/test_ps266_fullscreen_log_reveal.py. Recorded as not")
    print("  covered, with the reason — never as covered by a weaker check.")

    ok = all(results)
    print(
        f"\n{'ALL DRIVEN CHECKS PASSED' if ok else 'SOME DRIVEN CHECKS FAILED'} "
        f"({sum(results)}/{len(results)})"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
