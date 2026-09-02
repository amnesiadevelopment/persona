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
make impossible. So the load-bearing evidence here is GEOMETRY: the row's
rendered height grows from one line to several when the reveal is clicked, and
shrinks back when it is clicked again. The tail IS read too, and this run
reports it as present while COLLAPSED — so that check is explicitly recorded as
non-evidence rather than banked.

FOUR THINGS THIS SCRIPT MEASURED THAT NO STRUCTURAL TEST COULD
--------------------------------------------------------------
Each of these made an earlier draft report a WORKING view as broken, and each
is now recorded at its site in this file rather than in a commit message:

1. A ``selectable`` Text is a canvas-level SelectableText and paints a
   semantics node with an EMPTY string. Restoring selection (AC3) therefore
   removed the message column from the accessibility tree entirely — the row
   read as "shop-de-03 / 18:07:20" with the refusal missing. Fixed in
   ``dialogs/log.py`` with ``semantics_label``; found only by driving.
2. A ``Container`` with ``on_click`` is ABSORBED into the row's merged
   semantics node once the message carries a label: the whole 1248px row
   becomes one button and the chevron has no box of its own, so nothing can
   address it. The reveal is an ``IconButton`` for that reason.
3. A tooltip's string propagates into every ANCESTOR's ``innerText``, so
   counting the tip counted the depth of the widget tree — 2 "controls" at
   1280px and 4 at 1024px where exactly one was drawn.
4. ``parse_event`` hoists the profile OUT of the prose, so the seeded
   "Launching shop-de-03" renders as "Launching". Matching the seeded string
   reported the short line as absent from a view plainly showing it.

WHAT IS NOT COVERED, RECORDED RATHER THAN SMOOTHED OVER
-------------------------------------------------------
AC3 (selection/copy) is NOT driven. Text selection in Flutter is a canvas-level
gesture: the glyphs are painted, the selection is not a DOM range, and no
clipboard result is readable from the semantics tree. What ships is
``selectable=True`` on the fullscreen message cell, asserted structurally in
tests/test_ps266_fullscreen_log_reveal.py. Recorded as not covered, with the
reason, never as covered by a weaker check.

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
#:
#: "Launching", not "Launching shop-de-03" — and the difference is the whole
#: point of the profile column. ``parse_event`` HOISTS the profile name out of
#: the prose into its own column, so what the message cell actually renders is
#: the remainder. Matching on the seeded string reported "the short line is not
#: on screen" against a view that plainly shows it.
SHORT = "Launching"

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


def _click(drv: FletDriver, node) -> None:
    drv.page.mouse.click(node.box[0] + node.box[2] // 2, node.box[1] + node.box[3] // 2)


def _rows(drv: FletDriver) -> list:
    """The log's ROW nodes, as ``(node, text)``.

    Two measured facts shape this, and reading either wrong reports a working
    view as broken:

    * A ``selectable`` Text is a canvas-level SelectableText that paints an
      EMPTY semantics string — restoring selection removed the message column
      from the tree entirely, which is why ``dialogs/log.py`` names it with
      ``semantics_label``. So a row's words arrive as ``.text`` on a plain row
      and as ``.label`` on a row Flutter has MERGED (which it does once the row
      contains a focusable button). Both are read.
    * Every ancestor's ``innerText`` contains every descendant's, so the
      full-height ListView and the full-page nodes match any needle at all.
      Rows are therefore taken by GEOMETRY — nearly the full viewport width,
      and short enough not to be the list itself. The width test is RELATIVE to
      the viewport, not an absolute pixel count: the log body is 1248px at
      1280x820 and 992px at the 1024x680 minimum, so a fixed ">1000" threshold
      silently matched nothing at exactly the window this script exists to
      check twice.
    """
    vw = drv.page.evaluate("() => window.innerWidth")
    out = []
    for n in drv.nodes():
        _x, y, w, h = n.box
        if y > 55 and w > vw * 0.7 and h < 300:
            words = (n.text or "") or (n.label or "")
            if words.strip():
                out.append((n, words))
    return out


def _row_with(drv: FletDriver, needle: str):
    for n, words in _rows(drv):
        if needle in words:
            return n, words
    return None, ""


def _reveal_buttons(drv: FletDriver) -> list:
    """Every reveal CONTROL on screen, taken by geometry, not by tooltip text.

    A tooltip string cannot be counted here: Flutter merges the row's semantics
    once it contains a focusable child, so the tip lands on the ROW's label
    (beside the profile and the whole sentence) and every ancestor of that row
    repeats it — counting the string reported 2 affordances at 1280px and 4 at
    1024px where exactly one is drawn. The BUTTON itself is unambiguous: a
    tappable 20x20 node inside the log body, which nothing else in this view
    is. The tip is still asserted, but on the row that owns it.
    """
    return [
        n
        for n in drv.nodes()
        if n.tappable and n.box[1] > 55 and n.box[2] <= 26 and n.box[3] <= 26
    ]


def _open_fullscreen(drv: FletDriver) -> bool:
    """Click the dock's "open fullscreen" node — addressed leniently.

    Deliberately NOT :func:`_tip_nodes`. That one is strict because it COUNTS
    (a loose match there inflated 1 reveal into 4); this one only needs to hit
    a control it already knows is unique, and the dock's button paints its
    tooltip on a non-tappable wrapper rather than on the tappable node itself —
    the same shape ``live_ps229.py:_node_with`` matches.
    """
    for n in drv.nodes():
        blob = f"{n.text or ''} {n.label or ''}"
        if OPEN_FULLSCREEN_TIP in blob and n.box[3] < 200:
            _click(drv, n)
            drv.page.wait_for_timeout(2500)
            return True
    return False


def _char_budget(drv: FletDriver) -> int:
    """The one-line character budget at the driver's CURRENT viewport width.

    The same arithmetic ``dialogs/log.py`` budgets with, recomputed here from
    the live viewport rather than imported — so the driven check and the
    implementation cannot agree merely by sharing one wrong number. Floored at
    the app minimum for the same reason the implementation floors it.
    """
    vw = drv.page.evaluate("() => window.innerWidth")
    return max(1, int((max(vw, 1024) - 297) / (11.5 * 0.6)))


def _message_of(words: str) -> str:
    """The MESSAGE out of a row's semantics string.

    A row reads as ``[tooltip] profile message timestamp``, newline-joined.
    The profile and the 8-char timestamp are their own columns (``parse_event``
    hoists the profile OUT of the prose, which is why the seeded "Launching
    shop-de-03" renders as "Launching"), and the tooltip only appears once the
    row carries a control. The message is what is left after those are dropped.
    """
    import re

    parts = [p for p in (words or "").split("\n") if p.strip()]
    parts = [p for p in parts if p.strip() not in (REVEAL_TIP, HIDE_TIP)]
    parts = [p for p in parts if not re.fullmatch(r"\d{2}:\d{2}:\d{2}", p.strip())]
    # What remains is [profile, message], or just [message] when the profile
    # did not resolve. The message is the longest remaining piece.
    return max(parts, key=len) if parts else ""


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
    rows = _rows(drv)
    buttons = _reveal_buttons(drv)
    short_row, _ = _row_with(drv, SHORT)
    long_row, long_words = _row_with(drv, "Refusing to launch")

    results.append(
        _report(
            "a short line and a 460-char line are both on screen",
            short_row is not None and long_row is not None,
            f"{len(rows)} row(s); short={short_row is not None} "
            f"refusal={long_row is not None}",
        )
    )
    # THE RULE, not a hardcoded count. An earlier draft asserted "exactly one
    # reveal on screen" and failed at 1024x680 for an HONEST reason: that
    # session's log had accumulated more lines, and at the tighter budget a
    # second one is genuinely over it — so the affordance was right and the
    # assertion was wrong. Counting affordances measures the fixture. The
    # property AC2 actually states is a BICONDITIONAL, so that is what is
    # checked: a row carries the control if and only if its message overruns
    # the cell at THIS window width.
    budget = _char_budget(drv)
    over = [w for _n, w in rows if len(_message_of(w)) > budget]
    tipped = [w for _n, w in rows if REVEAL_TIP in w or HIDE_TIP in w]
    results.append(
        _report(
            "AC2 — a row carries the reveal IF AND ONLY IF it is over budget",
            len(over) == len(tipped)
            and all(REVEAL_TIP in w or HIDE_TIP in w for w in over),
            f"budget={budget} chars at this width; {len(over)} of {len(rows)} "
            f"rows over it, {len(tipped)} carrying the control, "
            f"{len(buttons)} tappable control node(s) painted",
        )
    )
    results.append(
        _report(
            "AC2 — the affordance is drawn for some rows and not all of them",
            0 < len(tipped) < len(rows),
            f"{len(tipped)} of {len(rows)} rows — on EVERY row it would be the "
            f"noise AC2 forbids; on none, there is no fix",
        )
    )
    # The affordance is on the CUT row, not the whole one — checked by which
    # row's semantics carries the tip, and by vertical coincidence.
    tip_on_long = REVEAL_TIP in long_words
    _short_row, short_words = _row_with(drv, SHORT)
    tip_on_short = REVEAL_TIP in short_words
    results.append(
        _report(
            "AC2 — the reveal is on the CUT row and not on the short one",
            tip_on_long and not tip_on_short,
            f"refusal row ({len(_message_of(long_words))} chars) carries "
            f"{REVEAL_TIP!r}={tip_on_long}; short row "
            f"({len(_message_of(short_words))} chars) carries it={tip_on_short}",
        )
    )
    if buttons and long_row is not None:
        aligned = any(abs(b.box[1] - long_row.box[1]) < 20 for b in buttons)
        results.append(
            _report(
                "AC2 — a control sits on the refusal's own row",
                aligned,
                f"button y={[b.box[1] for b in buttons]} "
                f"refusal row y={long_row.box[1]}",
            )
        )

    # --- AC1: click it, and measure what changed --------------------------
    before_h = long_row.box[3] if long_row is not None else None
    # Does the COLLAPSED tree already carry the tail? Flutter's semantics
    # carries a Text's full string regardless of the rendered truncation, so if
    # it does, a text-presence check after the click is NOT evidence — and this
    # script says so instead of banking it.
    tail_before = TAIL in long_words

    if buttons:
        _click(drv, buttons[0])
        drv.page.wait_for_timeout(2200)

    after_row, after_words = _row_with(drv, "Refusing to launch")
    after_h = after_row.box[3] if after_row is not None else None
    tail_after = TAIL in after_words
    hide_shown = HIDE_TIP in after_words

    grew = bool(before_h and after_h and after_h > before_h + 8)
    results.append(
        _report(
            "AC1 — clicking the reveal makes the message TALLER on screen",
            grew,
            f"row box height {before_h}px -> {after_h}px (one line -> wrapped)",
        )
    )
    results.append(
        _report(
            "AC1 — the control flips to its 'hide' state (the row knows it is open)",
            hide_shown,
            f"revealed row carries {HIDE_TIP!r}={hide_shown}",
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

    # The bound itself. A revealed row must be taller than one line and never
    # taller than five lines' worth of box — an unbounded reveal is the
    # original defect deferred by one click, and at a narrower window a pasted
    # stack trace is exactly what would prove it.
    if after_h and before_h:
        ceiling = before_h * 5 + 10
        results.append(
            _report(
                "AC1 — the reveal is BOUNDED (never more than 5 lines of box)",
                after_h <= ceiling,
                f"revealed={after_h}px, one line={before_h}px, "
                f"5-line ceiling={ceiling}px",
            )
        )

    drv.screenshot(shot)
    print(f"  screenshot: {shot}")

    # --- and back: the reveal is reversible -------------------------------
    buttons2 = _reveal_buttons(drv)
    if buttons2:
        _click(drv, buttons2[0])
        drv.page.wait_for_timeout(1800)
        back_row, _ = _row_with(drv, "Refusing to launch")
        back_h = back_row.box[3] if back_row is not None else None
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
