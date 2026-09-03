"""Drive PS-273 — the bulk-create refusal report — against the REAL app.

WHY THIS FILE EXISTS
--------------------
The standing directive for this project is that everything shipped is exercised
live. The two acceptance criteria that matter here are not statements about
code, they are statements about what an OPERATOR can see:

* AC2 — "the dialog remains open and the operator can read, per name, which
  names were refused and why, WITHOUT consulting the Activity Log."
* AC3 — "the Activity Log retains a durable per-name record after the dialog is
  dismissed."

A unit test asserting ``on_create(...) is not None`` is NOT coverage of AC2. It
passes just as happily against a build where the message is returned and then
dropped on the floor — the error row never made visible, the dialog popped
anyway, the text painted off-screen below the fold. That is precisely the shape
of the defect PS-273 exists to fix (a reason that is *computed* and then not
*shown*), so a check that cannot tell the two apart is worthless here.
``live_ps229.py``'s header documents what shipping an un-driven criterion cost.

So this script boots the real app served over the web, drives a real pointer
through CDP, and reads the consequences out of the rendered accessibility tree.

WHAT IT DRIVES
--------------
1. A REAL paste, into the real multiline field, containing all three reasoned
   causes at once plus a name that must succeed:
   ``alpha`` (already exists, seeded through the app's own ProfileManager),
   ``beta`` (must be created), ``bad/name`` (invalid character).
2. The real ``[ create ]`` button is CLICKED.
3. The dialog is then checked to be STILL ON SCREEN — by the continued presence
   of its own controls, and by the paste field still holding the paste.
4. The refusal text is read off the screen and matched for BOTH refused names
   and BOTH reason sentences.
5. The partial-success reassurance ("already saved") is matched, because the
   dialog staying open on a partial success is a new way to create
   duplicates-by-anxiety if the operator is not told the 45 that worked are
   done.
6. A SECOND round in the same dialog: a paste that REPEATS 40 names. The
   painted height of the error region is measured, and the controls that sit
   BELOW it in the layout are required to still be on screen — this is the
   defect PR #209's first round shipped (the refusal list was capped and the
   repeats list was not, so the repeats line pushed the os_type and tags
   controls out of the viewport).
7. A THIRD round: a CLEAN paste whose only oddity is a repeated name. The
   dialog must CLOSE (a repeat is not a refusal) and the repeat must still be
   accounted for in the Activity Log — the case the inline report structurally
   cannot cover.
8. The whole-batch ``IncoherentProfile`` refusal is NOT reachable from the
   dropdown (it is narrowed), so that arm is recorded as NOT COVERED below
   rather than faked.
9. The dialog is dismissed and the Activity Log is read for the durable
   per-name record (AC3), for refusals AND for repeats.

THE TRAP THIS SCRIPT AVOIDS, STATED UP FRONT
--------------------------------------------
Every ancestor node's ``innerText`` contains every descendant's, so "the reason
is somewhere on the page" is nearly unfalsifiable — it would match the same
string sitting in an invisible widget. Two things make the evidence real here:
the refusal text is required to be inside a node whose box is INSIDE the
dialog's own box and has non-zero height, and the check that the dialog is
still open is made from a control (``[ create ]``) that only exists while the
dialog is mounted. A build that popped the dialog fails check 3 no matter what
strings are in the tree.

WHAT IS NOT COVERED, RECORDED RATHER THAN SMOOTHED OVER
-------------------------------------------------------
The ``IncoherentProfile`` arm is NOT driven end-to-end through the dialog. The
bulk dialog's os_type control is a narrowed ``build_os_dropdown``, so a
non-canonical spelling (``win``) cannot be selected through the UI at all —
that narrowing is PS-187's design and this ticket does not touch it. The arm is
reachable only by calling the lane below the dropdown, which is exactly the
"handler called directly" that does not count as driving. It is therefore
covered at the service and action layers
(``tests/test_bulk.py::test_bulk_create_reason_for_an_incoherent_profile`` and
``tests/test_ps273_bulk_create_reasons.py::test_an_incoherent_batch_returns_the_coherence_reason``)
and recorded here as NOT COVERED BY DRIVING, with the reason — never as covered
by a weaker check.

RUN IT
------
    python3 -m tests.ui_driver.live_ps273

Requires flet, playwright and a chromium at ``driver.SYSTEM_CHROMIUM``. A
SCRIPT, not a pytest module, for the same reason its siblings are: it boots a
real app and a real browser and reports a table whose output is quoted on the
ticket.
"""

from __future__ import annotations

import os
import sys

from .driver import FletDriver
from .server import serve_app

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: Seeds ONE profile through the app's own ProfileManager, so the
#: already-exists refusal is a real collision with a real stored record rather
#: than a simulated one.
_SEED = '''
_orig_main = App._main

def _patched_main(self, page):
    _orig_main(self, page)
    try:
        self.pm.add_profile("alpha", "", "windows")
    except Exception:
        pass

App._main = _patched_main
'''

#: The paste. All three outcomes in one batch: a collision, a success, and an
#: invalid character. Comma-separated because ``parse_names`` accepts either
#: and typing a literal newline into a Flutter multiline field through CDP is
#: an Enter keypress whose handling is not what this script is measuring.
PASTE = "alpha, beta, bad/name"

#: ROUND 2 — the repeats paste, the defect PR #209's first round shipped. The
#: refusal list was capped and the repeats list was not, so a paste that
#: repeats many names rendered one enormous comma-joined line that pushed the
#: "Operating system" and "Tags" controls — which sit BELOW error_text in
#: dialogs/bulk.py's layout — out of the dialog's scroll viewport. Uncapped,
#: 40 repeats is a 452-char line; this drives that exact shape and measures the
#: PAINTED HEIGHT of the error region, which is the thing that does the pushing.
REPEAT_NAMES = [f"r{i:02d}" for i in range(40)]
REPEAT_PASTE = ", ".join(REPEAT_NAMES + REPEAT_NAMES + ["bad/name"])

#: The height the error region must stay under. The dialog body is ~700px of
#: viewport at 1280x900; the uncapped build measured h=153px at 40 repeats and
#: h=310px at 150, against a capped-refusal contribution of two lines. A
#: capped build renders the head, one refusal, one repeats line and one
#: overflow line — comfortably under this. Chosen as a CEILING that the
#: uncapped 40-repeat build already fails, so the check is falsifiable.
MAX_ERROR_HEIGHT = 130

#: The two reason sentences that must reach the operator's eyes, verbatim.
REASON_EXISTS = "a profile with that name exists"
REASON_INVALID = "Name contains invalid characters: /"
REASSURANCE = "already saved"


def _report(name: str, ok: bool, detail: str) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return ok


def _note(name: str, detail: str) -> None:
    print(f"  [NOTE] {name}: {detail}")


def _dismiss(drv: FletDriver) -> None:
    for label in ("Skip", "[ got it ]"):
        if drv.has_button(label):
            drv.press(label)


def _dialog_box(drv: FletDriver):
    """The bulk dialog's own box, taken by geometry.

    The dialog content is a 460px-wide Container. Its node is found as the
    tallest node narrow enough to be the dialog and wide enough not to be a
    single control — and it is only used to BOUND the text search, so a loose
    match here can only ever make the evidence stricter, never weaker.
    """
    best = None
    for n in drv.nodes():
        _x, _y, w, h = n.box
        if 300 < w < 700 and h > 200 and (best is None or h > best.box[3]):
            best = n
    return best


def _text_in_dialog(drv: FletDriver) -> str:
    """Everything rendered INSIDE the dialog's box, with a real height.

    Deliberately not "anywhere on the page": every ancestor's innerText
    contains every descendant's, so a page-wide match would also be satisfied
    by a string sitting in a zero-height or off-screen node. Requiring the
    carrying node to be inside the dialog and to have painted height is what
    makes this evidence about what the OPERATOR sees.
    """
    box = _dialog_box(drv)
    if box is None:
        return ""
    dx, dy, dw, dh = box.box
    out = []
    for n in drv.nodes():
        x, y, w, h = n.box
        if h <= 0 or w <= 0:
            continue
        if x >= dx - 4 and y >= dy - 4 and x + w <= dx + dw + 4 and y + h <= dy + dh + 4:
            words = (n.text or "") or (n.label or "")
            if words.strip():
                out.append(words)
    return "\n".join(out)


def _error_node(drv: FletDriver):
    """The rendered error region inside the dialog, by its own content.

    Found by matching the one phrase only the refusal report emits, then taking
    the SMALLEST node carrying it — every ancestor's text contains its
    descendants', so the smallest carrier is the text node itself rather than
    the dialog around it. Its box is what the height check measures, because
    height is what pushes the controls below it off screen.
    """
    best = None
    for n in drv.nodes():
        words = (n.text or "") or (n.label or "")
        if "not created" not in words and "repeated name" not in words:
            continue
        _x, _y, w, h = n.box
        if h <= 0 or w <= 0:
            continue
        if best is None or h < best.box[3]:
            best = n
    return best


def _retype(drv: FletDriver, index: int, value: str) -> str:
    """Replace a FILLED field's contents, addressing it by index.

    ``type_into`` addresses a field by its hint text, and a Flutter field drops
    its hint once it holds a value — so the paste field cannot be found by
    label for the second and third rounds. Indexing is the harness's own
    documented answer to that, and the clear is a real select-all + Delete
    through the keyboard rather than a state poke.
    """
    fields = drv.page.locator("input, textarea")
    field = fields.nth(index)
    field.click()
    drv.page.wait_for_timeout(300)
    drv.page.keyboard.press("Control+A")
    drv.page.keyboard.press("Delete")
    drv.page.wait_for_timeout(300)
    drv.page.keyboard.type(value)
    drv.page.wait_for_timeout(2000)
    return field.input_value()


def _open_bulk(drv: FletDriver) -> bool:
    """The operator's own route in: [ + NEW PROFILE ] then [ bulk ]."""
    for label in ("[ + new ]", "+ NEW PROFILE", "NEW PROFILE"):
        if drv.has_button(label):
            drv.press(label, settle_ms=3000)
            break
    else:
        return False
    if not drv.has_button("[ bulk ]"):
        return False
    drv.press("[ bulk ]", settle_ms=3000)
    return drv.has_button("[ create ]")


def _log_lines(drv: FletDriver) -> str:
    """Everything the Activity Log region is showing, as one blob."""
    vw = drv.page.evaluate("() => window.innerWidth")
    out = []
    for n in drv.nodes():
        _x, y, w, h = n.box
        if y > 55 and w > vw * 0.5 and h < 400:
            words = (n.text or "") or (n.label or "")
            if words.strip():
                out.append(words)
    return "\n".join(out)


def main() -> int:
    results: list[bool] = []

    with serve_app(REPO_ROOT, patch=_SEED) as app:
        print(f"served: {app.url}\nhome:   {app.home}\n")

        with FletDriver(app.url, width=1280, height=900) as drv:
            _dismiss(drv)
            drv.page.wait_for_timeout(9000)

            print("PS-273 — a bulk paste containing a refusal, driven live\n")

            opened = _open_bulk(drv)
            results.append(
                _report(
                    "the Bulk Create dialog opens by the operator's own route",
                    opened,
                    "[ + NEW PROFILE ] -> [ bulk ] -> [ create ] present",
                )
            )
            if not opened:
                print(drv.describe())
                return 1

            # --- the paste, typed into the REAL field ---------------------
            typed = drv.type_into(
                "one per line or comma-separated", PASTE, settle_ms=1500
            )
            results.append(
                _report(
                    "the paste reaches the real multiline field",
                    PASTE.replace(" ", "") in typed.replace(" ", ""),
                    f"field holds {typed!r}",
                )
            )

            before = _text_in_dialog(drv)
            results.append(
                _report(
                    "no refusal is shown BEFORE [ create ] is pressed",
                    REASON_INVALID not in before and REASON_EXISTS not in before,
                    "the error row starts hidden — so what is read after the "
                    "click is a CONSEQUENCE of the click, not scenery",
                )
            )

            # --- press it -------------------------------------------------
            drv.press("[ create ]", settle_ms=3500)

            # AC2, the load-bearing half: the dialog is STILL THERE.
            still_open = drv.has_button("[ create ]") and drv.has_button("[ cancel ]")
            results.append(
                _report(
                    "AC2 — the dialog STAYS OPEN after a partly-refused batch",
                    still_open,
                    "[ create ] and [ cancel ] are still mounted; before "
                    "PS-273 on_create returned None and dialogs/bulk.py "
                    "popped the dialog here",
                )
            )

            shown = _text_in_dialog(drv)
            drv.screenshot("/tmp/ps273-refusal-inline.png")
            print("  screenshot: /tmp/ps273-refusal-inline.png")

            results.append(
                _report(
                    "AC2 — the refused NAMES are on screen, in the dialog",
                    "bad/name" in shown and "alpha" in shown,
                    f"'bad/name' found={'bad/name' in shown}, "
                    f"'alpha' found={'alpha' in shown}",
                )
            )
            results.append(
                _report(
                    "AC2 — the invalid-name REASON is on screen, naming the "
                    "offending character",
                    REASON_INVALID in shown,
                    f"{REASON_INVALID!r} found={REASON_INVALID in shown}",
                )
            )
            results.append(
                _report(
                    "AC2 — the already-exists REASON is on screen, and is "
                    "distinguishable from the other cause",
                    REASON_EXISTS in shown,
                    f"{REASON_EXISTS!r} found={REASON_EXISTS in shown}",
                )
            )
            results.append(
                _report(
                    "AC2 — the operator is told the successful creations are "
                    "already saved",
                    REASSURANCE in shown,
                    "the dialog now stays open on a partial success; without "
                    "this the operator re-submits the whole paste",
                )
            )

            # The paste is still in the field, which is what "correct it in
            # place" actually requires.
            fields = drv.fields()
            kept = any(PASTE.split(",")[0].strip() in (f.value or "") for f in fields)
            results.append(
                _report(
                    "AC2 — the paste is still in the field, so it can be "
                    "corrected in place",
                    kept,
                    f"fields: {[f.value for f in fields]}",
                )
            )

            # --- AC3 (round 1): dismiss, then read the durable record ------
            #
            # Read HERE, before the later rounds, deliberately: the Activity
            # Log is a bounded scrolling region, and round 2 writes 40 repeat
            # lines. Reading round 1's refusal record after that would be
            # reading a region those lines have scrolled it out of — which
            # would fail for a reason that has nothing to do with durability.
            drv.press("[ cancel ]", settle_ms=3000)
            results.append(
                _report(
                    "the dialog can still be dismissed",
                    not drv.has_button("[ create ]"),
                    "[ cancel ] closes it — the report does not trap the "
                    "operator in the dialog",
                )
            )

            log_blob = _log_lines(drv)
            drv.screenshot("/tmp/ps273-activity-log.png")
            print("  screenshot: /tmp/ps273-activity-log.png")

            results.append(
                _report(
                    "AC3 — the Activity Log carries a per-name refusal line "
                    "AFTER the dialog is gone",
                    "not created" in log_blob.lower(),
                    f"per-name line found={'not created' in log_blob.lower()}",
                )
            )
            results.append(
                _report(
                    "AC3 — the durable record names the refused profiles",
                    "bad/name" in log_blob or "alpha" in log_blob,
                    "the log row hoists a known profile name into its own "
                    "column (parse_event), so 'alpha' may appear there rather "
                    "than in the message",
                )
            )
            results.append(
                _report(
                    "the batch actually happened — 'beta' was created",
                    "beta" in log_blob,
                    "the refusal report must not have cost the successes",
                )
            )

            # --- ROUND 2: the repeats paste --------------------------------
            #
            # This is the defect PR #209's first round shipped: the refusal
            # list was capped and the repeats list was not, so a paste that
            # repeats many names rendered one enormous line and pushed the
            # controls BELOW error_text out of the dialog's viewport. Driven
            # rather than reasoned, because "the message is shorter" is a
            # statement about a string and the failure is about PAINTED
            # HEIGHT.
            print("\n  --- round 2: a paste that repeats 40 names ---")
            reopened = _open_bulk(drv)
            results.append(
                _report(
                    "the dialog re-opens for the repeats round",
                    reopened,
                    "[ + NEW PROFILE ] -> [ bulk ] again",
                )
            )
            if not reopened:
                print(drv.describe())
                return 1
            typed2 = _retype(drv, 0, REPEAT_PASTE)
            results.append(
                _report(
                    "the repeats paste reaches the real field",
                    "r39" in typed2 and "bad/name" in typed2,
                    f"field holds {len(typed2)} chars",
                )
            )
            drv.press("[ create ]", settle_ms=4000)

            results.append(
                _report(
                    "the dialog is still open after the repeats batch",
                    drv.has_button("[ create ]") and drv.has_button("[ cancel ]"),
                    "bad/name is refused, so the report holds it open",
                )
            )

            rep_shown = _text_in_dialog(drv)
            drv.screenshot("/tmp/ps273-repeats-capped.png")
            print("  screenshot: /tmp/ps273-repeats-capped.png")

            err = _error_node(drv)
            err_h = err.box[3] if err is not None else -1
            results.append(
                _report(
                    "the error region stays BOUNDED with 40 repeats in the "
                    "paste",
                    err is not None and 0 < err_h <= MAX_ERROR_HEIGHT,
                    f"error region painted height = {err_h}px "
                    f"(ceiling {MAX_ERROR_HEIGHT}px; the uncapped build "
                    f"measured 153px at this paste and 310px at 150 repeats)",
                )
            )
            results.append(
                _report(
                    "the TRUE repeat count is still reported, though the "
                    "names are not all listed",
                    "40 repeated names" in rep_shown,
                    "capping the list must not misreport the size of the "
                    "problem",
                )
            )
            results.append(
                _report(
                    "the overflow is POINTED AT, not dropped",
                    "more repeated names" in rep_shown
                    and "Activity Log" in rep_shown,
                    "the cap is only safe because the durable record exists "
                    "to defer to",
                )
            )
            # THE THING THE HEIGHT ACTUALLY COSTS: the controls below
            # error_text in the layout must still be reachable. This is what
            # "you can fix it in place" means and what the uncapped build took
            # away.
            results.append(
                _report(
                    "the controls BELOW the error region are still on screen",
                    "Operating system" in rep_shown and "Tags" in rep_shown,
                    "these sit after error_text in dialogs/bulk.py's column; "
                    "the uncapped repeats line pushed them out of the scroll "
                    "viewport",
                )
            )

            drv.press("[ cancel ]", settle_ms=3000)
            log_blob2 = _log_lines(drv)
            results.append(
                _report(
                    "AC3 — the Activity Log carries the durable REPEAT record "
                    "too, which is what makes capping the inline list safe",
                    "listed more than once" in log_blob2,
                    "every repeat is logged per name before the cap is "
                    "applied — the same contract the refusal cap relies on",
                )
            )

            # --- ROUND 3: a CLEAN paste whose only oddity is a repeat ------
            #
            # The case the inline report cannot cover BY DESIGN: a repeat is
            # not a refusal, so the dialog correctly closes and there is no
            # message. Three rows in, two profiles out — before this round the
            # third row was accounted for NOWHERE. The durable record is the
            # only surface that can carry it, which is why it is written
            # unconditionally rather than inside the "something was refused"
            # branch.
            print("\n  --- round 3: a clean paste with a repeat ---")
            if not _open_bulk(drv):
                results.append(
                    _report("the dialog re-opens for round 3", False, "could not re-open")
                )
            else:
                drv.type_into(
                    "one per line or comma-separated",
                    "gamma, gamma, delta",
                    settle_ms=1500,
                )
                drv.press("[ create ]", settle_ms=4000)
                closed = not drv.has_button("[ create ]")
                results.append(
                    _report(
                        "a repeat alone does NOT hold the dialog open",
                        closed,
                        "nothing was refused; a repeat must not be dressed up "
                        "as a failure",
                    )
                )
                if not closed:
                    drv.press("[ cancel ]", settle_ms=2000)
                clean_log = _log_lines(drv)
                drv.screenshot("/tmp/ps273-clean-repeat-log.png")
                print("  screenshot: /tmp/ps273-clean-repeat-log.png")
                results.append(
                    _report(
                        "...and the repeat is STILL accounted for, in the log",
                        "listed more than once" in clean_log
                        or "created once" in clean_log,
                        "3 rows pasted, 2 profiles created — the third row is "
                        "recorded rather than silently absorbed. This is the "
                        "case the inline report structurally cannot cover.",
                    )
                )
                results.append(
                    _report(
                        "the clean batch really did create both names",
                        "delta" in clean_log and "gamma" in clean_log,
                        "the repeat cost neither creation",
                    )
                )

    print("\nNOT COVERED BY DRIVING — the IncoherentProfile arm.")
    print("  The bulk dialog's os_type control is a NARROWED")
    print("  build_os_dropdown, so a non-canonical spelling ('win') cannot be")
    print("  selected through the UI at all — that narrowing is PS-187's")
    print("  design and PS-273 does not touch it. Reaching the arm would mean")
    print("  calling the lane below the dropdown, which is the 'handler called")
    print("  directly' this directive rules out as coverage. It is covered at")
    print("  the service and action layers instead:")
    print("    tests/test_bulk.py::test_bulk_create_reason_for_an_incoherent_profile")
    print("    tests/test_ps273_bulk_create_reasons.py"
          "::test_an_incoherent_batch_returns_the_coherence_reason")
    print("  Recorded as not covered, with the reason — never as covered by a")
    print("  weaker check.")

    ok = all(results)
    print(
        f"\n{'ALL DRIVEN CHECKS PASSED' if ok else 'SOME DRIVEN CHECKS FAILED'} "
        f"({sum(results)}/{len(results)})"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
