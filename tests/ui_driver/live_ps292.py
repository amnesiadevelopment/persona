"""Drive PS-292 — clearing the profile filter in the fullscreen Activity Log —
against the REAL app.

THE REPORT, 2026-09-03: "есть баг когда лог на весь екран при выборе сверху
фильтр по определнному профилю то обратно не возварщает на общий список при
выключении фильтрации."

WHY THIS FILE EXISTS
--------------------
Because the structural suite beside it (``tests/test_ps292_log_profile_filter_clear.py``)
cannot see the thing that broke on the second attempt at this fix, and said so.
Every assertion there reads an in-process control property — a ``.tooltip``, a
``.content.value``, a painted ``color`` — and a control's properties are
IDENTICAL whether it is painted at x=970 with a 40px box or at x=1024 with a
box zero pixels wide. The first PR for this ticket shipped a longer label on the
profile row's clear control, correctly, and pushed the dialog's only exit off
the right edge of a minimum-size window. Nothing in-process could have caught
that. So the pixel tier is where this one is finally checked.

WHAT IT DRIVES
--------------
**Pass A — the operator's question, at two window sizes** (1280x820 and the
app's own minimum 1024x680, ``page.window.min_width``):

  1. the fullscreen Activity Log opens from the dock;
  2. exactly ONE header control reads "all" — the defect was TWO, eight pixels
     apart, and the operator pressing the wrong one is the whole bug report;
  3. each clear control NAMES ITS OWN AXIS in its tooltip, so the two are
     distinguishable to a screen reader as well as to an eye;
  4. clicking a profile NARROWS the list, and the "N of M" readout follows;
  5. pressing the SEVERITY "all" leaves the profile filter alone — the
     miss-press from the bug report, asserted as a non-event;
  6. pressing "all profiles" returns the FULL list, which is the sentence the
     owner wrote;
  7. the close button is on screen with a real box, and CLICKING IT LEAVES.

**Pass B — the exit, across roster sizes at the app's minimum window.** The
close button's rendered box is read at 4, 5, 6 and 8 profiles at 1024x680, and
at each size the dialog is actually DISMISSED by clicking it. This is the pass
the previous attempt would have failed at five.

**Pass C — falsification.** Pass B is re-run against a build whose
:func:`~src.ui.dialogs.log.log_header` is reverted to the single unbroken Row it
was before this fix — the close button back inside the tools group, no
``expand``, no ``scroll``. Everything else is untouched, INCLUDING the longer
label. Pass B's five-profile check MUST go red there. If it stays green, pass B
is not reading the rendered header and this whole file is decoration.

**Pass D — the tools' horizontal SCROLL, as a gesture.** At eight profiles on
the 1024px window the run overflows; the last profile is scrolled into view and
PRESSED, and the list must narrow to it. An earlier revision of this file
recorded the gesture as not driven and asserted only its consequence — which
was honest and was also the weaker claim, because a filter that can be seen and
not pressed is PS-292's own complaint one control along.

**Pass E — falsification of the HORIZONTAL check.** Pass A now asserts the tool
run stays right-anchored beside the exit; pass E rebuilds the header without
``alignment=END`` — everything else identical — and that assertion must go red.

WHY THERE IS A HORIZONTAL CHECK AT ALL
---------------------------------------
Because the second revision of this fix shipped a 450px LEFT shift of the whole
toolbar and all twenty checks in this file stayed green. Every one of them bands
header controls by vertical ``y`` and then reads labels, row counts and
dismissal — a band cannot see a shift ALONG the band, so a reviewer had to
notice it by eye. ``expand=True`` reserves the exit's space and then packs its
own contents under the default ``MainAxisAlignment.START``, collapsing a run
that the outer ``SPACE_BETWEEN`` had held right-anchored since the ``ab83eb7``
redesign. The gap between the last tool and the close button is now measured
(:func:`_tools_right_edge`), and pass E proves that measurement can go red.

WHAT WAS MEASURED HERE THAT NO STRUCTURAL TEST COULD
-----------------------------------------------------
The numbers below are this script's output, not an estimate, at 1024x680:

    profiles   pre-fix header        this header
    4          (970, 12, 40, 40)     (970, 12, 40, 40)
    5          (1024, 12, 0, 40)     (970, 12, 40, 40)
    6          absent from the tree  (970, 12, 40, 40)
    8          absent from the tree  (970, 12, 40, 40)

and the dialog is ``modal=True`` with no Escape handler and no scrim dismissal,
both driven and confirmed inert — so at five profiles the pre-fix build had no
exit at all.

THE FIVE-PROFILE PRE-FIX READING IS NOT STABLE BETWEEN A ZERO-WIDTH BOX AND AN
ABSENT NODE, and it is recorded that way rather than tidied. The table above is
from a standalone probe against the pre-fix build, where the button read
``(1024, 12, 0, 40)`` — a node that exists, pinned to the viewport edge, zero
pixels wide. PASS C, which reverts the same layout inside a served app, reads
it as absent from the semantics tree altogether. Both are the same failure to
an operator and both fail the same check, because the check is "a box with real
width that actually dismisses the dialog" and not "a node exists". Which of the
two Flutter produces evidently depends on how far past the edge the run
overflows, so neither reading should be quoted as THE pre-fix behaviour.

THE TRAPS THIS SCRIPT AVOIDS, STATED UP FRONT
----------------------------------------------
1. **A tooltip's string propagates into every ANCESTOR's ``innerText``**
   (measured in PS-266, where counting a tip counted the depth of the widget
   tree). So "the page contains 'all profiles'" proves nothing. Header controls
   are therefore taken by GEOMETRY — tappable, in the header band — and their
   VISIBLE label is read as the last line of the node's text, the tooltip
   being the lines before it.
2. **Counting rows by text needle.** Every ancestor of a row matches any needle
   its rows contain, so rows are taken by geometry (nearly the full body width)
   exactly as ``live_ps266.py`` does, and the width test is RELATIVE to the
   viewport because the body is 1248px at 1280 and 992px at 1024.
3. **Asserting the state dict instead of the screen.** Nothing here reads
   ``state``; the evidence is the number of ROWS painted and the "N of M"
   readout, which are the two things the operator actually looks at.
4. **"The close button node exists".** A node with a ZERO-WIDTH box exists and
   is unclickable, which is precisely the regression. Both the box AND an
   actual dismissal are checked.

WHAT IS NOT COVERED, recorded rather than smoothed over
--------------------------------------------------------
* **COLOUR is not driven.** The cleared state is marked with the theme accent
  and BOLD weight, and Flutter paints both to canvas — no colour and no font
  weight reaches the accessibility tree. Half of the shipped fix (calling
  ``paint_profiles()`` at build so the profile row states its own state on the
  first frame) is therefore asserted STRUCTURALLY, in
  ``tests/test_ps292_log_profile_filter_clear.py::
  test_on_open_the_profile_row_is_painted_and_says_it_is_unfiltered``, and is
  visible in the screenshots this script captures for a human to look at. It is
  recorded as not driven rather than covered by a weaker check.

RUN IT
------
    python3 -m tests.ui_driver.live_ps292

Requires flet, playwright and a chromium at ``driver.SYSTEM_CHROMIUM``. A
SCRIPT, not a pytest module, for the same reason its six siblings are: it boots
a real app and a real browser and reports a table whose output is quoted on the
ticket.
"""

from __future__ import annotations

import os
import re
import sys

from .driver import FletDriver
from .server import serve_app

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: The two profiles the functional pass runs with. TWO, not one: the claim
#: under test is that clearing goes back to ALL of them, which a roster where
#: every filtered view is already the full view could not show.
_PAIR = ("shop-de-03", "mail-us-011")

OPEN_FULLSCREEN_TIP = "Open full Activity Log"
CLOSE_TIP = "Back to the dock"
ALL_SEVERITIES_TIP = "Show every severity"
ALL_PROFILES_TIP = "Show every profile — clears the profile filter"
ALL_PROFILES_LABEL = "all profiles"

#: The header band. The header sits at y=12..49 inside a dialog pinned to the
#: page, and the list body starts at y=62 — so 55 separates them with room on
#: both sides, and it is the same divider ``live_ps266.py`` uses on this view.
_HEADER_MAX_Y = 55

#: How much space may sit between the LAST TOOL and the exit before the run has
#: stopped being right-anchored. The outer Row is ``SPACE_BETWEEN`` with a 6px
#: inner spacing and the close button carries its own padding, so a
#: right-anchored run leaves a small gap; a LEFT-PACKED one leaves ~400px at
#: 1280x820 with two profiles (measured: search field right edge ~595+220 vs a
#: close button at 1226). 120 sits far from both readings — this is a check
#: against a collapse, not a pixel-perfect layout pin, and a pin would go red
#: on any legitimate spacing change.
_MAX_TOOLS_TO_EXIT_GAP = 120

#: Seeds a roster and enough events that filtering visibly narrows. Every
#: profile gets two lines, so a one-profile view is always a strict subset.
_SEED = '''
import threading, time

PROFILES = {profiles!r}

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
        for n in PROFILES:
            self._log("Launching " + n)
            time.sleep(0.15)
        for n in PROFILES:
            self._log("Loaded 6 bookmarks, 0 pools for " + n)
            time.sleep(0.15)

    threading.Thread(target=feed, daemon=True).start()


App._main = _patched_main
'''

#: PASS C's sabotage: :func:`log_header` reverted to the single unbroken Row it
#: was before this fix — the close button back INSIDE the tools group, no
#: ``expand``, no ``scroll``. Nothing else changes; in particular the longer
#: "all profiles" label stays, because the point is to isolate the LAYOUT.
_SABOTAGE = '''
import flet as ft
from src.ui.dialogs import log as _log


def _one_row_header(brand, tools, close_button):
    return ft.Row(
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            brand,
            ft.Row(
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[*tools, close_button],
            ),
        ],
    )


_log.log_header = _one_row_header
'''


#: PASS E's sabotage: :func:`log_header` WITHOUT ``alignment=END`` on the tools
#: Row — i.e. exactly the second revision of this fix, which left-packed the
#: whole tool run because an ``expand=True`` child fills its space and then
#: packs under the default ``MainAxisAlignment.START``. Everything else is
#: identical, INCLUDING ``expand`` and ``scroll``, so what is isolated is the
#: alignment and nothing else.
_ALIGNMENT_SABOTAGE = '''
import flet as ft
from src.ui.dialogs import log as _log


def _start_packed_header(brand, tools, close_button):
    return ft.Row(
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            brand,
            ft.Row(
                expand=True,
                scroll=ft.ScrollMode.AUTO,
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=tools,
            ),
            close_button,
        ],
    )


_log.log_header = _start_packed_header
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


def _blob(node) -> str:
    return f"{node.text or ''}\n{node.label or ''}"


def _visible_label(node) -> str:
    """The words PAINTED on a header control, with its tooltip stripped off.

    A tooltipped control's semantics text arrives as ``"<tooltip>\\n<label>"``
    — measured on this very header: the severity clear control reads
    ``'Show every severity\\nall'`` and the profile one reads
    ``'Show every profile — clears the profile filter\\nall profiles'``. The
    LAST non-empty line is the label. This distinction is the whole of check 2:
    matching anywhere in the blob would find "all" inside "all profiles" and
    inside both tooltips, and would report the defect present against a build
    that fixed it.
    """
    parts = [p.strip() for p in (node.text or "").split("\n") if p.strip()]
    return parts[-1] if parts else ""


def _tools_right_edge(drv: FletDriver) -> int:
    """The right edge of the RIGHTMOST tool in the header, close button aside.

    THIS IS THE HORIZONTAL READING THE REST OF THIS FILE COULD NOT MAKE, and it
    exists because of a specific miss. Every other check here bands controls by
    VERTICAL ``y`` (:data:`_HEADER_MAX_Y`) and then asserts on labels, row
    counts and dismissal — so when the second revision of this fix moved the
    entire tool run 450px to the LEFT, all twenty checks stayed green and a
    reviewer had to notice it by eye. A band cannot see a shift along the band.

    What is measured is the GAP between the last tool and the exit, not an
    absolute x: an absolute coordinate is a fact about one roster at one width,
    while "the run ends near the button" is the property the ``SPACE_BETWEEN``
    header has had since the ``ab83eb7`` redesign. Left-packed, that gap opens
    to ~400px of dead space; right-anchored it is the Row's own spacing.

    Header-band nodes are bounded by width so the dialog and page ancestors —
    which span the viewport and would read as a rightmost edge of 1280 whatever
    the tools do — cannot answer for a control.
    """
    close_boxes = {n.box for n in _close_nodes(drv)}
    edges = [
        n.box[0] + n.box[2]
        for n in drv.nodes()
        if n.box[1] < _HEADER_MAX_Y and 0 < n.box[2] <= 400 and n.box not in close_boxes
    ]
    return max(edges) if edges else 0


def _header_controls(drv: FletDriver) -> list:
    """Every tappable control in the header band, by GEOMETRY.

    Not by tooltip text: a tip propagates into every ancestor's innerText
    (PS-266), so a text search returns the page. Tappable + inside the header
    band is unambiguous — the only other tappable things on this screen are the
    log rows' reveal chevrons, which are all below it.
    """
    return [n for n in drv.nodes() if n.tappable and n.box[1] < _HEADER_MAX_Y]


def _control_reading(drv: FletDriver, label: str):
    """The header control whose VISIBLE label is exactly ``label``, or None."""
    hits = [n for n in _header_controls(drv) if _visible_label(n) == label]
    return hits[0] if len(hits) == 1 else None


def _close_nodes(drv: FletDriver) -> list:
    """Every node carrying the close button's tooltip that is BUTTON-SIZED.

    The tip is on the button's own wrapper here (measured: a non-tappable node
    at the same box as the tappable one), and it also lands on the page and
    dialog ancestors — which are 1280x820 and would read as a perfectly healthy
    "the exit is on screen". The size bound is what makes this a reading of the
    BUTTON. Returned as a list, not a bool, because the regression is a node
    that EXISTS with a zero-width box.
    """
    out = []
    for n in drv.nodes():
        if CLOSE_TIP in _blob(n) and n.box[3] <= 60 and n.box[2] <= 60:
            out.append(n)
    return out


def _rows(drv: FletDriver) -> list[str]:
    """The log's ROW nodes' words, taken by geometry.

    The same reading ``live_ps266.py`` documents: every ancestor's innerText
    contains every descendant's, so a needle matches the whole list and the
    whole page. A row is nearly the full body width and short — and the width
    test is RELATIVE to the viewport, because the body is 1248px at 1280 and
    992px at the 1024 minimum, where a fixed threshold silently matches nothing.
    """
    vw = drv.page.evaluate("() => window.innerWidth")
    out = []
    for n in drv.nodes():
        _x, y, w, h = n.box
        if y > _HEADER_MAX_Y and w > vw * 0.7 and h < 300:
            words = (n.text or "") or (n.label or "")
            if words.strip():
                out.append(words)
    return out


def _readout(drv: FletDriver) -> str:
    """The "N of M" label — the OTHER thing the operator reads to decide
    whether the filter came off."""
    for n in drv.nodes():
        text = (n.text or "").strip()
        if re.fullmatch(r"\d+ of \d+", text):
            return text
    return ""


def _in_dialog(drv: FletDriver) -> bool:
    return any(n.role == "alertdialog" for n in drv.nodes())


def _open_fullscreen(drv: FletDriver) -> bool:
    """Click the dock's "open fullscreen" node — addressed leniently.

    The dock's button paints its tooltip on a NON-tappable wrapper rather than
    on the tappable node itself (the shape ``live_ps229.py:_node_with``
    matches), so this cannot go through :func:`_header_controls`. It only needs
    to hit a control it already knows is unique, so a loose match is safe here
    in a way it is not where something is COUNTED.
    """
    for n in drv.nodes():
        if OPEN_FULLSCREEN_TIP in _blob(n) and n.box[3] < 200:
            _click(drv, n)
            drv.page.wait_for_timeout(2500)
            return True
    return False


# ----------------------------------------------------------------------
# PASS A — the operator's question
# ----------------------------------------------------------------------


def _functional_pass(drv: FletDriver, label: str, results: list[bool], shot: str) -> None:
    print(f"\n{label} — clearing the profile filter")

    opened = _open_fullscreen(drv)
    results.append(_report("the fullscreen Activity Log opens from the dock", opened, label))
    if not opened:
        return

    # --- 2. exactly ONE control reads "all" ------------------------------
    reading_all = [n for n in _header_controls(drv) if _visible_label(n) == "all"]
    results.append(
        _report(
            "exactly ONE header control reads 'all'",
            len(reading_all) == 1,
            f"{len(reading_all)} found at "
            f"{[n.box for n in reading_all]} — TWO is the PS-292 defect itself, "
            f"and they rendered 8px apart",
        )
    )

    # --- 3. each clear control names its own axis ------------------------
    sev_all = _control_reading(drv, "all")
    prof_all = _control_reading(drv, ALL_PROFILES_LABEL)
    tips_ok = (
        sev_all is not None
        and prof_all is not None
        and ALL_SEVERITIES_TIP in _blob(sev_all)
        and ALL_PROFILES_TIP in _blob(prof_all)
    )
    results.append(
        _report(
            "each clear control NAMES ITS OWN AXIS",
            tips_ok,
            f"severity 'all' -> {_blob(sev_all).split(chr(10))[0]!r}; "
            f"profile clear -> {_blob(prof_all).split(chr(10))[0]!r}"
            if sev_all is not None and prof_all is not None
            else f"severity={sev_all is not None} profile={prof_all is not None}",
        )
    )
    if sev_all is None or prof_all is None:
        return

    # --- 3b. the tool run is still RIGHT-ANCHORED ------------------------
    # The horizontal reading. See :func:`_tools_right_edge` for why it is a
    # GAP and not an absolute x, and for the 450px shift that every other
    # check in this file was structurally unable to see.
    closes_now = [n for n in _close_nodes(drv) if n.box[2] > 0]
    gap = (min(c.box[0] for c in closes_now) - _tools_right_edge(drv)) if closes_now else -1
    results.append(
        _report(
            "the tool run stays RIGHT-ANCHORED beside the exit",
            0 <= gap <= _MAX_TOOLS_TO_EXIT_GAP,
            f"gap between the last tool and the close button = {gap}px "
            f"(<= {_MAX_TOOLS_TO_EXIT_GAP} required; left-packing this run "
            f"opens ~400px of dead space here, which is what the second "
            f"revision of this fix shipped unnoticed)",
        )
    )

    # --- 4/5. clicking a profile NARROWS -------------------------------
    full_rows = _rows(drv)
    full_readout = _readout(drv)
    target = _control_reading(drv, _PAIR[0])
    if target is None:
        results.append(_report("the profile control is addressable", False, _PAIR[0]))
        return
    _click(drv, target)
    drv.page.wait_for_timeout(1500)
    narrowed = _rows(drv)
    narrowed_readout = _readout(drv)
    results.append(
        _report(
            "clicking a profile NARROWS the list",
            0 < len(narrowed) < len(full_rows),
            f"{len(full_rows)} rows -> {len(narrowed)} rows "
            f"(readout {full_readout!r} -> {narrowed_readout!r})",
        )
    )

    # --- 6. the severity "all" is a NON-EVENT for the profile filter -----
    _click(drv, _control_reading(drv, "all"))
    drv.page.wait_for_timeout(1500)
    after_sev = _rows(drv)
    results.append(
        _report(
            "pressing the SEVERITY 'all' leaves the profile filter alone",
            len(after_sev) == len(narrowed),
            f"{len(narrowed)} rows -> {len(after_sev)} rows — this is the "
            f"miss-press from the bug report, and it correctly does nothing",
        )
    )

    drv.screenshot(shot)
    print(f"  screenshot: {shot}")

    # --- 7. pressing "all profiles" returns the FULL list ----------------
    _click(drv, _control_reading(drv, ALL_PROFILES_LABEL))
    drv.page.wait_for_timeout(1500)
    restored = _rows(drv)
    restored_readout = _readout(drv)
    results.append(
        _report(
            "pressing 'all profiles' returns the FULL list",
            len(restored) == len(full_rows) and restored_readout == full_readout,
            f"{len(after_sev)} rows -> {len(restored)} rows "
            f"(readout {restored_readout!r}, was {full_readout!r} unfiltered) — "
            f"this is the owner's sentence, driven",
        )
    )

    # --- 8. and the exit works -------------------------------------------
    closes = _close_nodes(drv)
    boxed = [n for n in closes if n.box[2] > 0 and n.box[3] > 0]
    if boxed:
        _click(drv, boxed[0])
        drv.page.wait_for_timeout(1500)
    results.append(
        _report(
            "the close button has a real box AND dismisses the log",
            bool(boxed) and not _in_dialog(drv),
            f"boxes={[n.box for n in closes]}, still in dialog={_in_dialog(drv)}",
        )
    )


# ----------------------------------------------------------------------
# PASS B / C — the exit, across roster sizes
# ----------------------------------------------------------------------


def _exit_pass(counts: list[int], patch: str, label: str) -> dict[int, dict]:
    """Read the close button's box at 1024x680 for each roster size, and try
    to LEAVE. One served app per size, because the roster is seeded at boot."""
    print(f"\n{label}")
    out: dict[int, dict] = {}
    for n in counts:
        names = [f"profile-{i:02d}" for i in range(n)]
        with serve_app(REPO_ROOT, patch=_SEED.format(profiles=names) + patch) as app:
            with FletDriver(app.url, width=1280, height=820) as drv:
                _dismiss(drv)
                drv.page.wait_for_timeout(10000)
                # The view is OPENED at the minimum, not resized into with it
                # already open: nothing in dialogs/log.py binds page.on_resize,
                # so the dialog keeps the width it opened with. Same reasoning
                # ``live_ps266.py`` records for the same view.
                drv.page.set_viewport_size({"width": 1024, "height": 680})
                drv.page.wait_for_timeout(3000)
                opened = _open_fullscreen(drv)
                closes = _close_nodes(drv)
                boxed = [c for c in closes if c.box[2] > 0 and c.box[3] > 0]
                left = False
                if boxed:
                    _click(drv, boxed[0])
                    drv.page.wait_for_timeout(1500)
                    left = not _in_dialog(drv)
                out[n] = {
                    "opened": opened,
                    "boxes": [c.box for c in closes],
                    "usable": bool(boxed),
                    "left": left,
                }
                print(
                    f"    {n} profiles @1024x680  opened={opened}  "
                    f"close={out[n]['boxes'] or 'ABSENT FROM THE TREE'}  "
                    f"dismissed={left}"
                )
    return out


def _escape_hatches(drv: FletDriver) -> dict[str, bool]:
    """Whether ANY other way out of this dialog exists. All three are inert —
    ``modal=True``, no Escape handler, no scrim dismissal, no actions row — and
    that is what makes the close button's box a safety property rather than a
    cosmetic one."""
    out = {}
    drv.page.keyboard.press("Escape")
    drv.page.wait_for_timeout(1200)
    out["escape"] = not _in_dialog(drv)
    if not out["escape"]:
        w = drv.page.evaluate("() => window.innerWidth")
        h = drv.page.evaluate("() => window.innerHeight")
        drv.page.mouse.click(w - 3, h // 2)
        drv.page.wait_for_timeout(1200)
        out["right_edge"] = not _in_dialog(drv)
        drv.page.mouse.click(w // 2, h - 3)
        drv.page.wait_for_timeout(1200)
        out["scrim"] = not _in_dialog(drv)
    return out


def _scroll_pass(results: list[bool]) -> None:
    """PASS D — the tools' horizontal SCROLL, driven as a GESTURE, with
    ``alignment=END`` in place.

    Two reasons this exists. The first: alignment and scroll INTERACT — an
    aligned run and a scrolling one are laid out by the same Row, and only the
    static layout was measured when ``END`` was added, so "the scroll still
    reaches the last filter" was an assumption. The second: the earlier
    revision of this file recorded the scroll gesture as NOT driven and
    asserted only its consequence (the exit keeps its box). That was honest and
    it was also the weaker claim — a clipped filter that cannot be reached is
    PS-292's own complaint one control along.

    So: at eight profiles on a 1024px window the run overflows; the last
    profile is scrolled into view with a horizontal wheel over the header and
    then PRESSED, and the evidence is that the list NARROWS to it.

    A DRIVER-TIER GOTCHA, recorded because it will bite the next author: after
    a horizontal scroll the semantics tree reports a STALE ``y`` for the moved
    nodes (measured: ``(665, -46, ...)`` for a control painted in the header
    band). The x is current and the y is not, so the click is issued at the
    node's x and at the header band's own centre rather than at the reported
    box — and the screenshot beside it is what a human checks that against.
    """
    print("\nPASS D — the tools SCROLL, and the last filter can be pressed")
    names = [f"profile-{i:02d}" for i in range(8)]
    with serve_app(REPO_ROOT, patch=_SEED.format(profiles=names)) as app:
        with FletDriver(app.url, width=1280, height=820) as drv:
            _dismiss(drv)
            drv.page.wait_for_timeout(10000)
            drv.page.set_viewport_size({"width": 1024, "height": 680})
            drv.page.wait_for_timeout(3000)
            if not _open_fullscreen(drv):
                results.append(_report("PASS D: the log opens at 8 profiles", False, ""))
                return

            last = names[-1]
            before = _control_reading(drv, last)
            vw = drv.page.evaluate("() => window.innerWidth")
            clipped = before is not None and (before.box[0] + before.box[2]) > vw - 40
            print(
                f"    before the scroll: {last} at "
                f"{before.box if before else 'ABSENT'} (viewport {vw}px) — "
                f"clipped={clipped}"
            )

            rows_before = len(_rows(drv))
            drv.page.mouse.move(vw // 2, 30)
            for _ in range(6):
                drv.page.mouse.wheel(300, 0)
                drv.page.wait_for_timeout(300)
            drv.page.wait_for_timeout(1200)
            drv.screenshot("/tmp/ps292-scrolled-1024x680.png")

            moved = _control_reading(drv, last)
            reached = moved is not None and (moved.box[0] + moved.box[2]) <= vw
            results.append(
                _report(
                    "the horizontal scroll REACHES the last filter with END set",
                    reached,
                    f"{last} now at {moved.box if moved else 'ABSENT'} "
                    f"(viewport {vw}px; the y is stale after a scroll — see the "
                    f"docstring and /tmp/ps292-scrolled-1024x680.png)",
                )
            )
            if not reached:
                return

            # Click at the node's CURRENT x and the header band's centre: the
            # reported y is stale after the scroll, the x is not.
            drv.page.mouse.click(moved.box[0] + moved.box[2] // 2, 30)
            drv.page.wait_for_timeout(1800)
            rows_after = len(_rows(drv))
            results.append(
                _report(
                    "and pressing that scrolled-in filter NARROWS the list",
                    0 < rows_after < rows_before,
                    f"{rows_before} rows -> {rows_after} rows "
                    f"(readout {_readout(drv)!r}) — a filter that can be seen "
                    f"but not pressed would be PS-292 one control along",
                )
            )


def _alignment_pass(results: list[bool]) -> None:
    """PASS E — FALSIFICATION of the horizontal check added in this round.

    Pass A now asserts that the tool run stays right-anchored beside the exit.
    That check is worth exactly as much as its ability to go RED against the
    thing it was written for — so here the header is rebuilt WITHOUT
    ``alignment=END`` and with everything else identical, which is precisely
    the build a reviewer rejected, and the same gap is measured.

    If this stays under the threshold, the check in Pass A is decoration and
    the next left-packing will ship exactly as the last one did.
    """
    print("\nPASS E — FALSIFICATION: log_header without alignment=END")
    with serve_app(
        REPO_ROOT,
        patch=_SEED.format(profiles=list(_PAIR)) + _ALIGNMENT_SABOTAGE,
    ) as app:
        with FletDriver(app.url, width=1280, height=820) as drv:
            _dismiss(drv)
            drv.page.wait_for_timeout(10000)
            if not _open_fullscreen(drv):
                results.append(_report("PASS E: the log opens", False, ""))
                return
            closes = [n for n in _close_nodes(drv) if n.box[2] > 0]
            edge = _tools_right_edge(drv)
            gap = (min(c.box[0] for c in closes) - edge) if closes else -1
            sev = _control_reading(drv, "all")
            print(
                f"    START-packed: severity 'all' at "
                f"{sev.box if sev else 'ABSENT'}, last tool right edge {edge}, "
                f"gap to exit {gap}px"
            )
            results.append(
                _report(
                    "removing alignment=END makes the right-anchor check go RED",
                    not (0 <= gap <= _MAX_TOOLS_TO_EXIT_GAP),
                    f"gap {gap}px vs the {_MAX_TOOLS_TO_EXIT_GAP}px bound — if "
                    f"this is green, Pass A's horizontal check cannot see a "
                    f"left-packed toolbar and does not earn its place",
                )
            )


def main() -> int:
    results: list[bool] = []

    print("=" * 74)
    print("PS-292 — clearing the profile filter, driven against the real app")
    print("=" * 74)

    # ---------------- PASS A ----------------
    with serve_app(REPO_ROOT, patch=_SEED.format(profiles=list(_PAIR))) as app:
        print(f"served: {app.url}\nhome:   {app.home}")

        with FletDriver(app.url, width=1280, height=820) as drv:
            _dismiss(drv)
            drv.page.wait_for_timeout(10000)
            _functional_pass(
                drv, "1280x820 (the shipped default)", results,
                "/tmp/ps292-clear-1280x820.png",
            )

        with FletDriver(app.url, width=1280, height=820) as drv:
            _dismiss(drv)
            drv.page.wait_for_timeout(10000)
            drv.page.set_viewport_size({"width": 1024, "height": 680})
            drv.page.wait_for_timeout(3000)
            _functional_pass(
                drv, "1024x680 (window.min_width)", results,
                "/tmp/ps292-clear-1024x680.png",
            )

            # The dialog's other exits, checked once. Everything below rests on
            # these three being inert.
            _open_fullscreen(drv)
            hatches = _escape_hatches(drv)
            results.append(
                _report(
                    "the close button is the log's ONLY exit (so its box is a "
                    "safety property)",
                    not any(hatches.values()),
                    f"escape/right-edge/scrim all dismissed the dialog? {hatches}",
                )
            )

    # ---------------- PASS B ----------------
    fixed = _exit_pass(
        [4, 5, 6, 8],
        patch="",
        label="PASS B — the EXIT at the app's minimum window, across roster sizes",
    )
    for n, r in fixed.items():
        results.append(
            _report(
                f"the exit survives {n} profiles at 1024x680",
                r["opened"] and r["usable"] and r["left"],
                f"close box {r['boxes']}, dismissed={r['left']}",
            )
        )

    # ---------------- PASS C ----------------
    broken = _exit_pass(
        [5],
        patch=_SABOTAGE,
        label=(
            "PASS C — FALSIFICATION: log_header reverted to the single "
            "unbroken Row (close button back inside the tools group)"
        ),
    )
    r5 = broken[5]
    results.append(
        _report(
            "reverting log_header makes the 5-profile exit check go RED",
            not (r5["usable"] and r5["left"]),
            f"sabotaged close box {r5['boxes'] or 'ABSENT'}, dismissed="
            f"{r5['left']} — if the exit still works here, PASS B's green is "
            f"not reading the rendered header and this file proves nothing",
        )
    )

    # ---------------- PASS D ----------------
    _scroll_pass(results)

    # ---------------- PASS E ----------------
    _alignment_pass(results)

    print("\n" + "=" * 74)
    print("NOT COVERED BY DRIVING, recorded rather than smoothed over:")
    print("  * COLOUR AND FONT WEIGHT. The cleared state is marked with the")
    print("    theme accent and BOLD, both painted to canvas — neither reaches")
    print("    the accessibility tree. The paint_profiles() half of this fix is")
    print("    asserted structurally in tests/test_ps292_log_profile_filter_")
    print("    clear.py and is visible in the screenshots above for a human.")
    print("=" * 74)

    ok = all(results)
    print(
        f"\n{'ALL DRIVEN CHECKS PASSED' if ok else 'SOME DRIVEN CHECKS FAILED'} "
        f"({sum(results)}/{len(results)})"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
