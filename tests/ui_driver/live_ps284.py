"""Drive PS-284 — the trash page's urgency order and its last day — against the REAL app.

WHY THIS FILE EXISTS
--------------------
The standing directive for this project is that everything shipped is exercised
live. Both halves of this ticket are claims about what an operator SEES on a
page: *which row is at the top*, and *what the row says about its clock*. A
structural test over the built control tree passes just as happily against a
page that never paints, a list the framework re-orders, or a caller that
quietly reverts to ``trash_service.list()``. PS-179 shipped an un-driven
criterion and the owner hit it within minutes, so this is driven.

It is also the gap the ticket names explicitly: no widget was rendered during
research (``flet`` and ``websockets`` were absent from the research container),
so the claim that ``build_trash_page`` renders its list IN ORDER was READ from
the source and never observed. This file observes it.

WHAT IT DRIVES
--------------
One REAL served app against an isolated home holding FIVE aged bookmarks, from
25 days of runway down to half a day PAST the retention window — seeded by
backdating ``deleted_at`` in the home's ``trash.json``, which is the field the
product itself writes and reads. The operator's own gesture is used to get
there: the ``trash`` nav item in the rail is CLICKED, exactly as a person
arriving from the badge would.

1. **ORDER (AC1)** — the rendered rows are read top-to-bottom by their y
   coordinate and must come out nearest-destruction-first, and every entry the
   rail's badge counted must sit ABOVE every entry it did not. The partition is
   computed from the product's own ``expiring_within`` window rather than
   hand-written, so it cannot pass by agreeing with one seeded arrangement.

2. **LEGIBILITY (AC2)** — the three states must render three DISTINCT strings
   on screen: an entry with hours left, an entry already past the window, and
   an entry with days left. The already-past row must say what actually happens
   to it (destroyed on the next app start) and must NOT borrow the "expires in"
   phrasing that belongs to an entry with time remaining.

Then the two FALSIFICATION passes (AC5), which are the only thing that makes
the two above worth anything. They are SEPARATE because the ticket ships two
independent changes and one sabotage would take both down together — a single
combined revert would give a red that says nothing about WHICH check reads the
page:

3. **ORDER REVERTED** — the same home, served against a build whose
   ``TrashService.by_urgency`` returns the old recency order. Only the read is
   reverted; the rendering is untouched. Check 1 must go RED and check 2 must
   stay GREEN.

4. **RENDERING REVERTED** — the same home, served against a build whose
   ``expiry_phrase`` is restored to its pre-PS-284 body, the verbatim
   ``max(0, int((expires_at - now) // 86400))`` expression under an
   ``expires in {n}d``. Only the phrasing is reverted; the ordering is
   untouched. Check 2 must go RED and check 1 must stay GREEN.

THE TRAPS THIS SCRIPT AVOIDS, STATED UP FRONT
---------------------------------------------
1. **Every ancestor's ``innerText`` contains every descendant's** (measured in
   PS-266, where counting a tooltip counted the depth of the widget tree). The
   card list node therefore carries every row's text concatenated, and the page
   node carries the rail's too — so "the string is in the tree" is true of a
   page in ANY order. Rows are located by EXACT text equality against a seeded
   name, and their order is taken from the rendered y COORDINATE — geometry, on
   the page, as painted.
2. **A phrase check alone cannot see an ordering.** ``expires in 5h`` being
   present says nothing about where it is. So order and legibility are read as
   two separate properties from the same painted screen, and each has its own
   sabotage that leaves the other alone.
3. **The seeded set is not degenerate.** The urgent/non-urgent partition is
   asserted to be a PROPER split (some of each) before the ordering claim is
   made — a fixture where everything is urgent would make check 1 pass against
   any order at all.

WHAT IS NOT COVERED, RECORDED RATHER THAN SMOOTHED OVER
-------------------------------------------------------
* **COLOUR and TYPOGRAPHY are not driven.** Flutter paints them to canvas; no
  colour reaches the accessibility tree. A screenshot is captured for each pass
  so a human can look, but no automated claim is made about them.
* **The 30-day boundary is not reached by waiting.** Obviously. It is reached
  by backdating ``deleted_at``, the same field the product writes and reads.
* **``purge_expired`` is NOT exercised by this harness, and the past-window row
  is only visible BECAUSE of that.** ``serve_app`` boots ``App(Container())``
  directly and never runs ``src/main.py``, where the start-up purge lives — so
  the entry this page describes as "destroyed when persona next opens" would in
  a real launch have been destroyed by that launch. That makes the harness the
  right place to observe the STRING and the wrong place to check the claim
  behind it; the purge itself is asserted at the service level in
  ``tests/test_trash_service.py``. Recorded rather than smoothed over.
* **The REST lane's recency order (AC3) has no visual surface** and is asserted
  through the real route handler in ``tests/test_ps284_trash_urgency.py``.

RUN IT
------
    python3 -m tests.ui_driver.live_ps284

Requires flet, playwright and a chromium at ``driver.SYSTEM_CHROMIUM``. A
SCRIPT, not a pytest module, for the same reason its siblings are: it boots a
real app and a real browser and reports a table whose output is quoted on the
ticket.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import uuid

from .driver import FletDriver
from .server import serve_app

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: Kept in step with the product rather than restated, so the harness and the
#: implementation cannot agree merely by sharing one wrong number.
sys.path.insert(0, REPO_ROOT)
from src.services.trash.store import (  # noqa: E402
    EXPIRY_WARNING_DAYS,
    RETENTION_DAYS,
)
from src.ui.components.trash_page import PAST_WINDOW_PHRASE  # noqa: E402

DAY = 86400.0

#: The rail is a hard 200px; the page body starts to the right of it. Used to
#: keep the rail's own nodes out of the page reading.
RAIL_WIDTH = 200

#: The seeded trash: ``(name, age in days)``. Five entries spanning both sides
#: of the 7-day warning window AND both sides of the 30-day floor, so the
#: partition check below has something to partition and the legibility check
#: has all three states on ONE screen.
_SEED = [
    ("roomy-jar", 5.0),                     # 25d left  — days
    ("middling-jar", 20.0),                 # 10d left  — days
    ("week-jar", 24.0),                     # 6d left   — inside the badge window
    ("hours-jar", RETENTION_DAYS - 0.25),   # ~6h left  — hours, used to say "0d"
    ("past-jar", RETENTION_DAYS + 0.5),     # 12h PAST  — used to say "0d" too
]

#: Nearest destruction first — what the page must show, derived from the seed
#: rather than typed out, so the expectation cannot drift from the fixture.
_EXPECTED_ORDER = [n for n, _ in sorted(_SEED, key=lambda p: -p[1])]

#: Which of the seeded entries the rail's badge counts: destruction inside the
#: next ``EXPIRY_WARNING_DAYS``. Computed with the product's OWN constants.
_URGENT = {n for n, age in _SEED if RETENTION_DAYS - age <= EXPIRY_WARNING_DAYS}

#: SABOTAGE 1 — the urgency READ reverted to the recency order the page used
#: before this ticket. Nothing about the rendering is touched, so a green
#: ordering check here would mean check 1 never read the painted page at all.
_SABOTAGE_ORDER = '''
from src.services.trash import service as _svc

def _recency(self, kind=None):
    return self.trash.list(kind)

_svc.TrashService.by_urgency = _recency
'''

#: SABOTAGE 2 — the RENDERING reverted to its pre-PS-284 body, verbatim: the
#: floor division under the max(0, ...) clamp that collapsed "23h left",
#: "2h left" and "already past" into one identical string. Nothing about the
#: ordering is touched.
_SABOTAGE_RENDER = '''
from src.ui.components import trash_page as _tp

def _old_phrase(entry, now):
    days_left = max(0, int((entry.expires_at() - now) // 86400))
    return f"expires in {days_left}d"

_tp.expiry_phrase = _old_phrase
'''


def _seed_home(entries: list[tuple[str, float]]) -> str:
    """An isolated PERSONA_HOME whose trash.json holds ``(name, age_days)``.

    Written as the PRODUCT writes it — the same six keys ``TrashEntry.to_dict``
    emits — and read back by the real ``TrashStore._load`` when ``Container()``
    builds in the child. Nothing here is a test-only code path.
    """
    home = tempfile.mkdtemp(prefix="persona-ps284-")
    os.makedirs(home, exist_ok=True)
    now = time.time()
    payload = {}
    for name, age_days in entries:
        eid = uuid.uuid4().hex
        payload[eid] = {
            "id": eid,
            "kind": "bookmark",
            "name": name,
            "deleted_at": now - age_days * DAY,
            "payload": {"name": name, "url": "https://example.invalid"},
            "material_path": "",
        }
    path = os.path.join(home, "trash.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.chmod(path, 0o600)
    return home


def _report(name: str, ok: bool, detail: str) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return ok


def _dismiss(drv: FletDriver) -> None:
    for label in ("Skip", "[ got it ]"):
        if drv.has_button(label):
            drv.press(label)


def _open_trash(drv: FletDriver) -> bool:
    """Click the rail's ``trash`` nav item — the operator's own route in.

    Geometry first: a tooltip string propagates into every ancestor's
    innerText, so the nav item is located INSIDE the 200px rail and then by the
    SMALLEST such node that says "trash" (its ancestors — the nav column, the
    rail container — say it too).
    """
    best = None
    for n in drv.nodes():
        if n.box[0] >= RAIL_WIDTH or n.box[0] + n.box[2] > RAIL_WIDTH:
            continue
        if "trash" not in f"{n.text or ''} {n.label or ''}".lower():
            continue
        if best is None or n.box[3] < best.box[3]:
            best = n
    if best is None:
        return False
    drv.page.mouse.click(best.box[0] + best.box[2] // 2, best.box[1] + best.box[3] // 2)
    drv.page.wait_for_timeout(3000)
    return True


def _read_rows(drv: FletDriver, names: list[str]) -> list[tuple[str, str]]:
    """The page's rows as ``(name, meta line)``, TOP TO BOTTOM as painted.

    Two measured facts shape this:

    * Every ancestor's innerText contains every descendant's, so the card list
      node carries all five rows' text concatenated and matching a substring
      would "find" every row at one y. A row's NAME node is therefore taken by
      EXACT text equality against the seeded name, which no ancestor satisfies.
    * The order is read off the rendered y coordinate, not off any list the
      code handed the framework. That is the whole point: the claim is about
      what is painted where.
    """
    nodes = [n for n in drv.nodes() if n.box[0] >= RAIL_WIDTH]
    metas = [
        n for n in nodes
        if " · deleted " in (n.text or "") and (n.text or "").count(" · ") == 2
    ]
    rows: list[tuple[int, str, str]] = []
    for name in names:
        hits = [n for n in nodes if (n.text or "").strip() == name]
        if not hits:
            continue
        y = hits[0].box[1]
        # The row's meta line is the one painted just beneath its name.
        below = sorted(
            (m for m in metas if 0 <= m.box[1] - y < 60), key=lambda m: m.box[1]
        )
        rows.append((y, name, below[0].text.strip() if below else "<no meta line>"))
    return [(name, meta) for _y, name, meta in sorted(rows)]


def _observe(home: str, label: str, shot: str, patch: str = "") -> list[tuple[str, str]]:
    """Boot one app against ``home``, open the trash page, and read it."""
    with serve_app(REPO_ROOT, home=home, patch=patch) as app:
        print(f"\n{label}\n  served: {app.url}\n  home:   {app.home}")
        with FletDriver(app.url, width=1280, height=820) as drv:
            _dismiss(drv)
            drv.page.wait_for_timeout(9000)
            if not _open_trash(drv):
                print("  !! could not find the trash nav item in the rail")
                drv.screenshot(shot)
                return []
            rows = _read_rows(drv, [n for n, _ in _SEED])
            drv.screenshot(shot)
            print(f"  screenshot: {shot}")
            for i, (name, meta) in enumerate(rows, 1):
                mark = "  <== URGENT (badge counts it)" if name in _URGENT else ""
                print(f"   {i}. {name:<14} {meta!r}{mark}")
            return rows


# --- the two properties, each read from a painted page --------------------


def _order_ok(rows: list[tuple[str, str]]) -> tuple[bool, str]:
    """AC1: nearest destruction first, and every badge-counted row above the rest."""
    if len(rows) != len(_SEED):
        return False, f"read {len(rows)} row(s), expected {len(_SEED)}"
    order = [name for name, _ in rows]
    if order != _EXPECTED_ORDER:
        return False, f"rendered {order}, expected {_EXPECTED_ORDER}"
    positions = [i for i, name in enumerate(order) if name in _URGENT]
    if positions != list(range(len(_URGENT))):
        return False, (
            f"the badge counts {sorted(_URGENT)} but they render at positions "
            f"{[p + 1 for p in positions]} of {len(order)}"
        )
    return True, (
        f"rendered {order}; the {len(_URGENT)} entries the badge counts occupy "
        f"positions {[p + 1 for p in positions]}"
    )


def _legibility_ok(rows: list[tuple[str, str]]) -> tuple[bool, str]:
    """AC2: hours-left, already-past and days-left are three DISTINCT truths."""
    meta = dict(rows)
    hours, past, days = (
        meta.get("hours-jar", ""),
        meta.get("past-jar", ""),
        meta.get("roomy-jar", ""),
    )
    clocks = [m.split(" · ")[-1] for m in (hours, past, days) if m]
    if len(clocks) != 3:
        return False, f"only read {len(clocks)} of the 3 rows: {clocks}"
    if len(set(clocks)) != 3:
        return False, f"the three states print {len(set(clocks))} distinct string(s): {clocks}"
    if "h" not in clocks[0] or "0d" in clocks[0]:
        return False, f"the hours-left row reads {clocks[0]!r}"
    if PAST_WINDOW_PHRASE not in clocks[1] or "expires in" in clocks[1]:
        return False, f"the already-past row reads {clocks[1]!r}"
    if not clocks[2].endswith("d"):
        return False, f"the days-left row reads {clocks[2]!r}"
    return True, f"three distinct strings: {clocks}"


def main() -> int:
    results: list[bool] = []

    print("=" * 74)
    print("PS-284 — the trash page's urgency order and its last day, driven live")
    print(f"RETENTION_DAYS={RETENTION_DAYS}  EXPIRY_WARNING_DAYS={EXPIRY_WARNING_DAYS}")
    print(f"badge counts: {sorted(_URGENT)} of {len(_SEED)} seeded entries")
    print("=" * 74)

    home = _seed_home(_SEED)

    rows = _observe(
        home,
        "1. THE REAL PAGE — five aged entries, reached by clicking the trash nav",
        "/tmp/ps284-trash-page.png",
    )
    ok, detail = _order_ok(rows)
    results.append(
        _report("AC1 — nearest destruction FIRST, badge-counted rows on top", ok, detail)
    )
    ok, detail = _legibility_ok(rows)
    results.append(
        _report("AC2 — hours / already-past / days are three DISTINCT truths", ok, detail)
    )

    # -----------------------------------------------------------------
    # AC5 — TWO falsifications, one per shipped change. Separate on
    # purpose: a single combined revert takes both checks down together
    # and so proves neither of them is reading its OWN property.
    # -----------------------------------------------------------------
    print("\n" + "-" * 74)
    print("3. FALSIFICATION A (AC5) — the SAME home, served against a build whose")
    print("   TrashService.by_urgency returns the old RECENCY order. The AC1")
    print("   check MUST go red; the AC2 check MUST stay green.")
    print("-" * 74)
    broken = _observe(
        home,
        "3. SABOTAGED READ — the page ordered as it was before this ticket",
        "/tmp/ps284-falsification-order.png",
        patch=_SABOTAGE_ORDER,
    )
    order_ok, order_detail = _order_ok(broken)
    leg_ok, leg_detail = _legibility_ok(broken)
    results.append(
        _report(
            "AC5 — reverting the ORDER makes the AC1 check go RED",
            not order_ok,
            f"{order_detail} — if AC1 stayed green here it is not reading the "
            f"painted page",
        )
    )
    results.append(
        _report(
            "AC5 — and leaves the AC2 check GREEN (the sabotage is isolated)",
            leg_ok,
            leg_detail,
        )
    )

    print("\n" + "-" * 74)
    print("4. FALSIFICATION B (AC5) — the SAME home, served against a build whose")
    print("   expiry_phrase is the verbatim pre-PS-284 floor-division expression.")
    print("   The AC2 check MUST go red; the AC1 check MUST stay green.")
    print("-" * 74)
    broken = _observe(
        home,
        "4. SABOTAGED RENDER — 'expires in 0d' for every one of the last-day states",
        "/tmp/ps284-falsification-render.png",
        patch=_SABOTAGE_RENDER,
    )
    order_ok, order_detail = _order_ok(broken)
    leg_ok, leg_detail = _legibility_ok(broken)
    results.append(
        _report(
            "AC5 — reverting the RENDERING makes the AC2 check go RED",
            not leg_ok,
            f"{leg_detail} — this is the defect verbatim: several distinct "
            f"deadlines printing one identical string",
        )
    )
    results.append(
        _report(
            "AC5 — and leaves the AC1 check GREEN (the sabotage is isolated)",
            order_ok,
            order_detail,
        )
    )

    print("\n" + "=" * 74)
    print("NOT COVERED BY DRIVING, recorded rather than smoothed over:")
    print("  * COLOUR/TYPOGRAPHY. Flutter paints them to canvas and no colour")
    print("    reaches the accessibility tree. Screenshots are captured for a")
    print("    human to look at; no automated claim is made.")
    print("  * purge_expired is NOT exercised here — serve_app boots")
    print("    App(Container()) and never runs src/main.py, which is WHY the")
    print("    past-window row is on screen at all. The string is observed")
    print("    here; the purge behind it is asserted at the service level.")
    print("  * AC3 (GET /trash still recency-ordered) has no visual surface;")
    print("    it is asserted through the real route handler in")
    print("    tests/test_ps284_trash_urgency.py.")
    print("  * The 30-day boundary is reached by backdating deleted_at, not by")
    print("    waiting. Same field the product writes and reads.")
    print("=" * 74)
    ok = all(results)
    print(f"\n{sum(results)}/{len(results)} checks passed — "
          f"{'ALL GREEN' if ok else 'FAILURES ABOVE'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
