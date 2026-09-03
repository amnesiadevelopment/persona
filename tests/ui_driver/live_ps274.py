"""Drive PS-274 — the operator-declared exit timezone — against the REAL app.

WHY THIS FILE EXISTS
--------------------
The standing directive for this project is that everything shipped is exercised
live. The unit suite in ``tests/test_ps274_declared_exit_timezone.py`` asserts
the store, the launch value and the render tree — all of which pass just as
happily against a build whose field never PAINTS, whose save button never
reaches the store, and whose network page draws the warning off screen. The
ticket's own "Honest bounds" #2 says so explicitly: no widget was rendered when
the ticket was written, and AC11 exists because that gap is the implementer's
to close.

WHAT IT DRIVES
--------------
A proxy is seeded into the served app's own store in the exact shipped
deadlock — ``country_code='RO' timezone='' last_check_ok=True`` — through the
product's own ``ProxyStore.mark_checked``. Then, by pressing the controls a
user reads:

1. The network page shows the proxy as UNLAUNCHABLE, not as healthy. This is
   the AC8 half, and it is checked BEFORE anything is typed, because the whole
   complaint is that this row was pixel-identical to a working one.
2. ``[ edit ]`` opens the proxy dialog and the exit-timezone field is THERE
   (AC1's "a way to declare it" — before this ticket, ``grep -rn timezone
   src/ui/`` returned two hits, both prose in a changelog).
3. A zone is typed and ``[ save ]`` pressed.
4. The dialog is RE-OPENED and the field holds the declared zone — the
   persistence half of AC1, read off the screen rather than off the object.
5. The network page's unlaunchable indication is GONE.
6. And the file on disk carries the declaration, so a restart would see it.

AND THE WRITE-SIDE SEAM, which the first round of this ticket shipped broken
--------------------------------------------------------------------------
The country gate that retires a declaration when the exit moves is a READ-side
guard, and the dialog prefills its field from the RAW stored zone. Re-submitting
that prefilled value unconditionally re-stamped the gate's KEY to the CURRENT
country, so a bare ``[ save ]`` — changing nothing — handed a CZ exit a Romanian
clock. Every unit test was green throughout: each drove ONE layer (the gate as a
pure predicate, the dialog once on a proxy that never moved, the store lifecycle
with no dialog), and the defect lived in the ORDER rather than in any of them.

It is driven here because the gesture that triggers it is exactly the one the
new network note sends the operator to perform. A proxy is seeded ALREADY IN
THE MOVED STATE (declared for RO, exit now CZ — the move itself is not an
operator gesture and cannot be driven), then:

7. Its row still says unlaunchable, and the dialog PREFILLS the retired zone —
   so the input the defect re-submitted is observed, not assumed.
8. ``[ save ]`` is pressed with NOTHING touched: the dialog closes (an untouched
   field must not block an unrelated save), the country the zone was declared
   FOR is still RO on disk, and the row still says unlaunchable.
9. The retired zone is REPLACED with the one the new exit needs, which IS
   accepted — not re-arming must not become a second deadlock.
10. And on a NEVER-CHECKED proxy, declaring a zone is refused at the dialog
    with a sentence, because a declaration is made FOR a country and there is
    none on file. It used to be accepted, close the dialog, and store a zone
    bound to an empty country that nothing ever re-bound.

THE TRAP THIS SCRIPT AVOIDS, STATED UP FRONT
--------------------------------------------
Every ancestor node's ``innerText`` in Flutter's semantics tree contains every
descendant's, so the ROOT node matches any needle at all. A bare ``needle in
page_text`` check for the unlaunchable sentence would therefore be an assertion
that CANNOT FAIL — the exact shape this harness exists to make impossible.

The obvious remedy — take rows BY GEOMETRY, the rule ``live_ps266.py:_rows``
arrived at for the log — DOES NOT TRANSFER HERE, and the first run of this
script proved it by reporting a working build as broken. The network page paints
ONE MERGED 1015x148 container over BOTH proxy rows, and that container is wide
and short and passes the geometry filter. So every needle matched every row: the
DE row read as carrying the RO row's warning (a false FAIL on AC8), and — the
expensive half — ``[ edit ]`` resolved inside that shared box and clicked the
WRONG PROXY's button, after which the script typed a zone into ``de-exit`` and
correctly reported ``ro-exit``'s disk as empty. Three FAILs, no defect.

Rows are therefore anchored on an EXACT-TEXT LEAF (the 43x21 node whose whole
string is the proxy's name) and the row's band is derived from it, so a needle
can only ever address one proxy. And the check runs in BOTH directions in one
run: present on the RO proxy, absent on a DE proxy seeded beside it, and absent
again on the RO proxy once the zone is declared. A marker that is always on is
not a signal.

A SECOND MEASURED TRAP: a field's ``aria-label`` in Flutter web is its HINT,
and the hint is DROPPED once the field holds a value (``driver.py``'s
``_field_index`` records this). So the timezone field can be addressed by its
hint while empty — which is how it is typed into — and must be addressed BY
POSITION on the re-open, where it is full.

A THIRD, found by this run and not previously recorded anywhere in this
harness: Flutter web does not mirror a field's text into the backing
``<input>`` until that input is FOCUSED. An unfocused field reads ``value=''``
whatever it displays — the HOST field holding ``1.2.3.4`` reads ``''`` too,
which is what proves this is a fact about the harness and not about the
product. ``drv.fields()`` is therefore a reliable census of WHICH fields exist
and an unreliable one of what they hold; ``type_into`` never hit it because it
clicks before it types. The re-open check clicks the field and then reads it.

A FOURTH, found while adding the write-side seam checks below: ``type_into``
clicks and then types, so on a field that ALREADY HOLDS A VALUE it APPENDS. It
produced ``'Europe/BucharestEurope/Prague'``, which the product correctly
refused as not a zone name — a green-looking product defect that was entirely
a harness artifact. Every sibling script types into empty fields, which is why
nothing upstream had met it; a prefilled field is the norm in this script and
not the exception. ``_replace_tz`` selects-all first.

RUN IT
------
    python3 -m tests.ui_driver.live_ps274

Requires flet, playwright and a chromium at ``driver.SYSTEM_CHROMIUM``. A
SCRIPT, not a pytest module, for the same reason its three siblings are: it
boots a real app and a real browser and reports a table whose output is quoted
on the ticket.
"""

from __future__ import annotations

import json
import os
import sys

from .driver import FletDriver
from .server import serve_app

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: The exit country from the ticket's transcript — a real, ordinary country
#: with no ``_COUNTRY_TZ`` row.
RO_NAME = "ro-exit"
DE_NAME = "de-exit"
#: Seeded ALREADY IN THE MOVED STATE — a zone declared while the exit was in
#: Romania, and a later check that found the exit in Czechia. The move itself
#: cannot be driven (there is no operator gesture that changes a provider's
#: exit country) but the GESTURE THAT BROKE ON IT can be: pressing [ save ] on
#: this proxy's dialog without touching anything.
MOVED_NAME = "moved-exit"
#: Never checked. Declaring a zone on it must be REFUSED with a sentence, not
#: accepted into a record that can never activate.
FRESH_NAME = "unchecked-exit"
ZONE = "Europe/Bucharest"

#: The sentence the row must carry. Imported from the product rather than
#: retyped, so a wording change cannot make this script silently stop checking
#: anything.
from src.ui.components.network_page import UNLAUNCHABLE_NOTE  # noqa: E402

#: The field's HINT — how an EMPTY field is addressed (see the module
#: docstring: the hint is dropped once the field holds a value).
TZ_HINT = "e.g. Europe/Bucharest  — only needed if launching is refused"

#: Seeds two proxies into the app's OWN store, through the product's own
#: writers, before the UI is constructed. The RO one is the shipped deadlock;
#: the DE one is the control that proves the indication is not simply always on.
_SEED = f'''
from src.services.proxy.store import ProxyStore

_seed_store = ProxyStore()
_seed_store.add({RO_NAME!r}, "socks5://u:pw@1.2.3.4:1080")
# Exactly what a provider that reports a country and no usable zone produces.
_seed_store.mark_checked({RO_NAME!r}, "RO", "Romania", "5.6.7.8", "", None, None)
_seed_store.add({DE_NAME!r}, "socks5://u:pw@5.6.7.8:1080")
_seed_store.mark_checked({DE_NAME!r}, "DE", "Germany", "9.9.9.9", "", None, None)
# A backconnect proxy declared for RO whose exit has since MOVED to CZ. The
# country gate has retired the declaration and the launch refuses; the record
# still carries the zone, which is what the dialog prefills its field from.
_seed_store.add({MOVED_NAME!r}, "socks5://u:pw@2.2.2.2:1080")
_seed_store.mark_checked({MOVED_NAME!r}, "RO", "Romania", "2.2.2.2", "", None, None)
_seed_store.set_manual_timezone({MOVED_NAME!r}, {ZONE!r})
_seed_store.mark_checked({MOVED_NAME!r}, "CZ", "Czechia", "3.3.3.3", "", None, None)
# Added but never checked — the ordinary "fill in the whole form first" case.
_seed_store.add({FRESH_NAME!r}, "socks5://u:pw@4.4.4.4:1080")
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


def _name_node(drv: FletDriver, proxy_name: str):
    """The node that paints a proxy's NAME, and nothing wider.

    ANCHORED ON AN EXACT-TEXT LEAF, deliberately — this is the trap that made
    the first run of this script report a working build as broken, so it is
    recorded here rather than in a commit message.

    Selecting rows by geometry ("wide and short, below the header") is the rule
    ``live_ps266.py`` uses for the log, and it does NOT transfer: the network
    page paints ONE merged 1015x148 container over BOTH proxy rows, which is
    wide and short and passes that filter. Every needle then matched every row,
    so the DE control row read as carrying the RO row's warning (a false FAIL),
    and worse, ``[ edit ]`` was resolved inside that shared box and clicked the
    WRONG PROXY's button — the script then typed a zone into de-exit and
    reported ro-exit's disk as empty, which it correctly was.

    A name leaf is 43x21 with the name as its whole string, so an exact match
    on ``.text`` cannot address two proxies at once.
    """
    for n in drv.nodes():
        if (n.text or "").strip() == proxy_name:
            return n
    return None


def _row_band(node) -> tuple[int, int]:
    """The vertical band ONE proxy row occupies, from its name node.

    Measured against the shipped layout at 1280x820: the name sits at y=99, its
    meta line at y=122, and its four buttons at y=102 — so a band of
    (name.y - 20, name.y + 45) contains exactly one row and the next row's name
    (y=178) is well outside it.
    """
    _x, y, _w, _h = node.box
    return y - 20, y + 45


def _row_text(drv: FletDriver, proxy_name: str) -> str | None:
    """The META LINE of one proxy's row — the sentence the operator reads.

    Returns None when the row cannot be found at all, so "the row is missing"
    and "the row is silent" are never conflated: the second is a real verdict
    and the first is a broken check.
    """
    name_node = _name_node(drv, proxy_name)
    if name_node is None:
        return None
    top, bottom = _row_band(name_node)
    nx = name_node.box[0]
    for n in drv.nodes():
        x, y, w, _h = n.box
        text = (n.text or "").strip()
        # Same left edge as the name, inside its band, and not the name itself.
        if top <= y <= bottom and abs(x - nx) < 6 and text and text != proxy_name:
            return text
    return ""


def _row_says_unlaunchable(drv: FletDriver, proxy_name: str) -> bool | None:
    line = _row_text(drv, proxy_name)
    if line is None:
        return None
    return UNLAUNCHABLE_NOTE in line


def _open_edit_for(drv: FletDriver, proxy_name: str) -> bool:
    """Press the ``[ edit ]`` control belonging to a NAMED row.

    ``drv.press("[ edit ]")`` cannot be used: there are two proxies on screen
    and therefore two identical buttons, and pressing "the last one" is a coin
    flip dressed as a test. The button is taken from the named row's own
    vertical band — see ``_name_node`` for what went wrong when the band was
    resolved from a container instead.
    """
    name_node = _name_node(drv, proxy_name)
    if name_node is None:
        return False
    top, bottom = _row_band(name_node)
    hits = [
        n
        for n in drv.nodes()
        if n.tappable
        and (n.text or "").strip() == "[ edit ]"
        and top <= n.box[1] <= bottom
    ]
    if len(hits) != 1:
        return False
    _click(drv, hits[0])
    drv.page.wait_for_timeout(2500)
    return True


def _dialog_open(drv: FletDriver) -> bool:
    return drv.has_button("[ save ]") or drv.has_button("[ cancel ]")


def _tz_index(drv: FletDriver) -> int | None:
    """The POSITION of the exit-timezone field among the dialog's inputs.

    Addressed by position rather than by hint because Flutter exposes a field's
    HINT as its ``aria-label`` and DROPS it once the field holds a value
    (``driver.py``'s ``_field_index`` records this) — and a FILLED field is
    exactly the state the seam checks operate in. The position is resolved
    relative to the rotate-url field, whose own hint is stable and which the
    dialog paints immediately above it, rather than from a magic index.
    """
    fields = drv.fields()
    for i, f in enumerate(fields):
        if f.label == TZ_HINT:
            return i
    rotate = [
        i for i, f in enumerate(fields)
        if f.label == "provider endpoint that forces a new exit IP"
    ]
    if not rotate or rotate[0] + 1 >= len(fields):
        return None
    return rotate[0] + 1


def _tz_field_value(drv: FletDriver) -> str | None:
    """What the exit-timezone field currently HOLDS, read off the live DOM.

    TWO measured facts, and getting either wrong reports a working dialog as
    empty — the second one made the first run of this script FAIL on a build
    that persists correctly.

    1. ADDRESS IT BY POSITION, NOT BY HINT — see ``_tz_index``.

    2. FOCUS IT FIRST. Flutter web does not mirror a field's text into the
       backing ``<input>`` until that input is FOCUSED — an unfocused field
       reads ``value=''`` whatever it displays, and the whole census reads
       empty (the host field holding "1.2.3.4" reads '' too, which is what
       proves this is a harness fact and not a product one). ``type_into``
       never hit this because it clicks before it types. So: click, then read.
    """
    index = _tz_index(drv)
    if index is None:
        return None
    loc = drv.page.locator("input, textarea").nth(index)
    loc.click()
    drv.page.wait_for_timeout(600)
    return loc.input_value()


def _replace_tz(drv: FletDriver, value: str) -> str | None:
    """REPLACE the exit-timezone field's contents and return what it holds.

    ``drv.type_into`` clicks and then types, which APPENDS: on a field already
    holding a declaration it produced 'Europe/BucharestEurope/Prague', which
    the product correctly refused as not a zone name. That is a HARNESS fact,
    not a product one, and it is recorded here because it can only show up on
    a PREFILLED field — every check in the harness's siblings types into an
    empty one, so nothing upstream had met it.

    Select-all then type, so the keystrokes land as a replacement.
    """
    index = _tz_index(drv)
    if index is None:
        return None
    loc = drv.page.locator("input, textarea").nth(index)
    loc.click()
    drv.page.wait_for_timeout(300)
    loc.press("Control+a")
    drv.page.keyboard.type(value)
    drv.page.wait_for_timeout(1200)
    return loc.input_value()


def _stored(app, proxy_name: str) -> dict:
    """The proxy AS IT IS ON DISK in the served app's own home.

    The point of the field is that it survives a restart, and the served app is
    not restarted here — so the file is the honest stand-in: this is the exact
    bytes a fresh ``ProxyStore`` would read.
    """
    path = os.path.join(app.home, "proxies.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh).get(proxy_name, {})
    except OSError:
        return {}


def main() -> int:
    results: list[bool] = []

    with serve_app(REPO_ROOT, patch=_SEED) as app:
        print(f"served: {app.url}\nhome:   {app.home}\n")

        with FletDriver(app.url, width=1280, height=820) as drv:
            _dismiss(drv)
            drv.page.wait_for_timeout(8000)

            # ---- the network page ------------------------------------
            if drv.has_button("network"):
                drv.press("network")
            drv.page.wait_for_timeout(2500)

            ro_seen = _name_node(drv, RO_NAME)
            de_seen = _name_node(drv, DE_NAME)
            results.append(
                _report(
                    "both seeded proxies are on the network page",
                    ro_seen is not None and de_seen is not None,
                    f"{RO_NAME}={ro_seen is not None} {DE_NAME}={de_seen is not None}",
                )
            )
            if ro_seen is None:
                print("\n  cannot continue: the RO row is not on screen")
                print(drv.describe()[:3000])
                return 1

            # ---- AC8, BOTH directions in one read ---------------------
            ro_warned = _row_says_unlaunchable(drv, RO_NAME)
            de_warned = _row_says_unlaunchable(drv, DE_NAME)
            results.append(
                _report(
                    "AC8 — the checked-but-unlaunchable RO proxy SAYS SO on its row",
                    ro_warned is True,
                    f"{RO_NAME} meta line: {_row_text(drv, RO_NAME)!r}",
                )
            )
            results.append(
                _report(
                    "AC8 — the launchable DE proxy does NOT (the marker is a signal, "
                    "not decoration)",
                    de_warned is False,
                    f"{DE_NAME} meta line: {_row_text(drv, DE_NAME)!r}",
                )
            )
            drv.screenshot("/tmp/ps274-network-unlaunchable.png")
            print("  screenshot: /tmp/ps274-network-unlaunchable.png")

            # ---- AC1, the door ---------------------------------------
            opened = _open_edit_for(drv, RO_NAME)
            results.append(
                _report("the proxy dialog opens from the row's [ edit ]",
                        opened and _dialog_open(drv), f"dialog={_dialog_open(drv)}")
            )
            if not (opened and _dialog_open(drv)):
                print(drv.describe()[:3000])
                return 1

            present = any(f.label == TZ_HINT for f in drv.fields())
            results.append(
                _report(
                    "AC1 — the exit-timezone field is IN the dialog, empty",
                    present,
                    f"fields on screen: {[f.label for f in drv.fields()]}",
                )
            )
            drv.screenshot("/tmp/ps274-dialog-field.png")
            print("  screenshot: /tmp/ps274-dialog-field.png")

            typed = drv.type_into(TZ_HINT, ZONE) if present else ""
            results.append(
                _report("AC1 — the zone can be TYPED into it", typed == ZONE,
                        f"field holds {typed!r}")
            )

            drv.press("[ save ]")
            drv.page.wait_for_timeout(2500)
            results.append(
                _report("AC1 — [ save ] closes the dialog (the value was accepted)",
                        not _dialog_open(drv), f"dialog still open={_dialog_open(drv)}")
            )

            # ---- AC1, persistence: on disk AND back on screen ---------
            on_disk = _stored(app, RO_NAME)
            results.append(
                _report(
                    "AC1 — the declaration is on DISK, with the country it was "
                    "declared for (what a restart reads)",
                    on_disk.get("manual_timezone") == ZONE
                    and on_disk.get("manual_timezone_country") == "RO",
                    f"proxies.json: manual_timezone="
                    f"{on_disk.get('manual_timezone')!r} country="
                    f"{on_disk.get('manual_timezone_country')!r}",
                )
            )

            reopened = _open_edit_for(drv, RO_NAME)
            held = _tz_field_value(drv) if reopened else None
            results.append(
                _report(
                    "AC1 — RE-OPENING the dialog shows the declared zone",
                    held == ZONE,
                    f"the field holds {held!r} on re-open",
                )
            )
            drv.screenshot("/tmp/ps274-dialog-persisted.png")
            print("  screenshot: /tmp/ps274-dialog-persisted.png")
            if _dialog_open(drv):
                drv.press("[ cancel ]")
                drv.page.wait_for_timeout(1500)

            # ---- AC8 again: the marker CLEARS -------------------------
            after = _row_says_unlaunchable(drv, RO_NAME)
            results.append(
                _report(
                    "AC8 — the unlaunchable indication CLEARS once a zone is "
                    "declared (a marker that never clears trains the operator to "
                    "ignore it)",
                    after is False,
                    f"{RO_NAME} meta line now: {_row_text(drv, RO_NAME)!r}",
                )
            )
            drv.screenshot("/tmp/ps274-network-cleared.png")
            print("  screenshot: /tmp/ps274-network-cleared.png")

            # ---- the value is validated at the door -------------------
            if _open_edit_for(drv, DE_NAME):
                bad = drv.type_into(TZ_HINT, "Not/AZone")
                drv.press("[ save ]")
                drv.page.wait_for_timeout(2000)
                still_open = _dialog_open(drv)
                results.append(
                    _report(
                        "AC7 — a plausible non-zone is REFUSED at the dialog and the "
                        "dialog stays open (the operator is told, not silently "
                        "ignored)",
                        still_open,
                        f"typed {bad!r}, dialog still open={still_open}",
                    )
                )
                drv.screenshot("/tmp/ps274-dialog-rejected.png")
                print("  screenshot: /tmp/ps274-dialog-rejected.png")
                results.append(
                    _report(
                        "AC7 — and nothing was written for it",
                        _stored(app, DE_NAME).get("manual_timezone", "") == "",
                        f"proxies.json manual_timezone="
                        f"{_stored(app, DE_NAME).get('manual_timezone')!r}",
                    )
                )
                if _dialog_open(drv):
                    drv.press("[ cancel ]")
                    drv.page.wait_for_timeout(1500)

            # ---- THE WRITE-SIDE SEAM ---------------------------------
            # The country gate is a READ-side guard, and the first round of
            # this ticket shipped a dialog that re-derived the gate's KEY from
            # a prefilled form field. A bare [ save ] — changing nothing —
            # re-stamped the declaration onto the CURRENT country, so a proxy
            # whose exit had moved RO->CZ launched with a Romanian clock. Every
            # unit test was green: each drove ONE layer, and the defect was in
            # the ORDER. It is driven here because the gesture that triggers it
            # is exactly the one the new network note sends the operator to
            # perform ("set the exit timezone in [ edit ]").
            before = _stored(app, MOVED_NAME)
            results.append(
                _report(
                    "the moved-exit proxy is seeded retired: zone on file, "
                    "declared for RO, exit now CZ",
                    before.get("manual_timezone") == ZONE
                    and before.get("manual_timezone_country") == "RO"
                    and before.get("country_code") == "CZ",
                    f"proxies.json: {before.get('manual_timezone')!r} declared_for="
                    f"{before.get('manual_timezone_country')!r} country="
                    f"{before.get('country_code')!r}",
                )
            )
            results.append(
                _report(
                    "AC8 — a retired declaration does NOT hide the row's "
                    "unlaunchable indication (the gate is what the render reads)",
                    _row_says_unlaunchable(drv, MOVED_NAME) is True,
                    f"{MOVED_NAME} meta line: {_row_text(drv, MOVED_NAME)!r}",
                )
            )

            if _open_edit_for(drv, MOVED_NAME):
                prefilled = _tz_field_value(drv)
                results.append(
                    _report(
                        "the dialog PREFILLS the retired zone (this is the "
                        "input the defect re-submitted — driven, not assumed)",
                        prefilled == ZONE,
                        f"the field holds {prefilled!r}",
                    )
                )
                drv.screenshot("/tmp/ps274-moved-prefilled.png")
                print("  screenshot: /tmp/ps274-moved-prefilled.png")
                # Press [ save ] having touched NOTHING.
                drv.press("[ save ]")
                drv.page.wait_for_timeout(2500)
                results.append(
                    _report(
                        "a bare [ save ] still CLOSES the dialog (an untouched "
                        "field must not be able to block an unrelated save)",
                        not _dialog_open(drv),
                        f"dialog still open={_dialog_open(drv)}",
                    )
                )
                after = _stored(app, MOVED_NAME)
                results.append(
                    _report(
                        "THE DEFECT: a bare [ save ] must not re-arm the "
                        "declaration onto the NEW country",
                        after.get("manual_timezone_country") == "RO",
                        f"declared_for={after.get('manual_timezone_country')!r} "
                        f"(was RO; 'CZ' here is the CZ-exit-with-a-Romanian-clock "
                        f"state) zone={after.get('manual_timezone')!r}",
                    )
                )
                results.append(
                    _report(
                        "and the row STILL says unlaunchable afterwards — the "
                        "refusal was not laundered by opening the dialog",
                        _row_says_unlaunchable(drv, MOVED_NAME) is True,
                        f"{MOVED_NAME} meta line: {_row_text(drv, MOVED_NAME)!r}",
                    )
                )
                drv.screenshot("/tmp/ps274-moved-after-save.png")
                print("  screenshot: /tmp/ps274-moved-after-save.png")

            # ---- and the operator can still ANSWER for the moved exit -
            if _open_edit_for(drv, MOVED_NAME):
                typed = _replace_tz(drv, "Europe/Prague")
                results.append(
                    _report(
                        "the retired zone can be REPLACED with the one the new "
                        "exit needs",
                        typed == "Europe/Prague",
                        f"the field holds {typed!r}",
                    )
                )
                drv.press("[ save ]")
                drv.page.wait_for_timeout(2500)
                moved = _stored(app, MOVED_NAME)
                results.append(
                    _report(
                        "the RECOVERY path: declaring the CZ zone for the moved "
                        "exit is accepted (not re-arming must not become a second "
                        "deadlock)",
                        moved.get("manual_timezone") == "Europe/Prague"
                        and moved.get("manual_timezone_country") == "CZ",
                        f"proxies.json: {moved.get('manual_timezone')!r} "
                        f"declared_for={moved.get('manual_timezone_country')!r}",
                    )
                )
                results.append(
                    _report(
                        "AC8 — and the row's indication clears for it",
                        _row_says_unlaunchable(drv, MOVED_NAME) is False,
                        f"{MOVED_NAME} meta line now: {_row_text(drv, MOVED_NAME)!r}",
                    )
                )
                drv.screenshot("/tmp/ps274-moved-recovered.png")
                print("  screenshot: /tmp/ps274-moved-recovered.png")
                if _dialog_open(drv):
                    drv.press("[ cancel ]")
                    drv.page.wait_for_timeout(1500)

            # ---- a declaration needs a CHECKED exit country ------------
            # Adding a proxy and filling in the whole form before pressing
            # [ check ] is an ordinary sequence. It used to be accepted, closed
            # the dialog, and stored a zone bound to an empty country that
            # nothing ever re-bound — a silent, permanent no-op.
            if _open_edit_for(drv, FRESH_NAME):
                drv.type_into(TZ_HINT, ZONE)
                drv.press("[ save ]")
                drv.page.wait_for_timeout(2500)
                still_open = _dialog_open(drv)
                results.append(
                    _report(
                        "declaring a zone on a NEVER-CHECKED proxy is refused at "
                        "the dialog, which stays open",
                        still_open,
                        f"dialog still open={still_open}",
                    )
                )
                told = [
                    (n.text or "") for n in drv.nodes()
                    if "check" in (n.text or "").lower()
                    and "timezone" in (n.text or "").lower()
                ]
                results.append(
                    _report(
                        "and the operator is TOLD to check it first (a sentence, "
                        "not a silent success)",
                        bool(told),
                        f"on screen: {told[:1]}",
                    )
                )
                drv.screenshot("/tmp/ps274-unchecked-refused.png")
                print("  screenshot: /tmp/ps274-unchecked-refused.png")
                results.append(
                    _report(
                        "and nothing was written for it",
                        _stored(app, FRESH_NAME).get("manual_timezone", "") == "",
                        f"proxies.json manual_timezone="
                        f"{_stored(app, FRESH_NAME).get('manual_timezone')!r}",
                    )
                )
                if _dialog_open(drv):
                    drv.press("[ cancel ]")
                    drv.page.wait_for_timeout(1500)

    print("\nNOT COVERED BY DRIVING — recorded rather than smoothed over.")
    print("  1. AC2, the LAUNCH itself. Launching a profile spawns a real")
    print("     chromium/firefox engine, which this container has no binary or")
    print("     display for, and the value under test is an argv entry rather")
    print("     than anything on screen — a driven check here could only ever")
    print("     assert that a click did not crash. It is asserted instead on")
    print("     the REAL argv, through the product's own spawn_browser with")
    print("     Popen faked at the process boundary, in")
    print("     tests/test_ps274_declared_exit_timezone.py::")
    print("     test_a_declared_zone_reaches_the_real_chromium_argv.")
    print("  2. AC4's country-MOVE rows, AS A TRANSITION. A move is a new check")
    print("     result from a provider whose exit changed country; there is no")
    print("     operator gesture that produces one, so the move itself cannot be")
    print("     driven. Asserted as the five-row lifecycle table against the real")
    print("     store and the real _proxy_timezone. What IS driven is everything")
    print("     the operator does AFTER a move: a proxy is seeded in the moved")
    print("     state and the dialog is driven on it, which is where the first")
    print("     round's defect lived.")
    print("  3. AN APPLICATION RESTART. The served app is not restarted here;")
    print("     what is driven is that the declaration is in proxies.json, which")
    print("     is the exact bytes a fresh ProxyStore reads. The restart itself")
    print("     is asserted in the unit suite through a second ProxyStore over")
    print("     the same file.")

    ok = all(results)
    print(
        f"\n{'ALL DRIVEN CHECKS PASSED' if ok else 'SOME DRIVEN CHECKS FAILED'} "
        f"({sum(results)}/{len(results)})"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
