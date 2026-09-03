"""Drive PS-272 — the trash's near-expiry signal on the nav rail — against the REAL app.

WHY THIS FILE EXISTS
--------------------
The standing directive for this project is that everything shipped is exercised
live. PS-179 shipped an un-driven criterion and the owner hit it within
minutes. The claim this ticket makes is *visual and negative in half its
cases*: "the rail says something when an entry is about to be destroyed, and
says NOTHING when it is not". A unit test over the built control tree passes
just as happily against a badge that never paints, is clipped out of the 200px
rail, or is drawn on the wrong nav item. So it is driven.

WHAT IT DRIVES
--------------
Three REAL served apps, each with its own isolated home, because the property
under test is a comparison BETWEEN trash states and one process cannot hold
two:

1. **NEAR EXPIRY** — a trashed bookmark backdated so it is destroyed in 3 days,
   inside the 7-day warning window. The rail must carry the signal.
2. **FAR FROM EXPIRY** — a trashed bookmark deleted *now*, 30 days of runway.
   The trash is NOT empty; it simply holds nothing near its deadline. The rail
   must carry NOTHING. This is the case that separates "a signal" from "a
   trash counter", and it is the half that `_status_needs_reveal`'s rule is
   actually about.
3. **EMPTY** — no trash at all. The rail must carry nothing.

Then the FALSIFICATION pass (AC4), which is the only thing that makes the
three above worth anything:

4. **SABOTAGED** — the same near-expiry home, served against a build whose
   ``_nav_button`` is reverted to its pre-PS-272 body (icon + label, no
   badge). The check from pass 1 is re-run and MUST GO RED. If it stays green,
   pass 1's green says nothing about the rendered nav and this whole file is
   decoration.

THE TRAPS THIS SCRIPT AVOIDS, STATED UP FRONT
---------------------------------------------
1. **A tooltip's string propagates into every ANCESTOR's ``innerText``**
   (measured in PS-266, where counting a tip counted the depth of the widget
   tree). So the whole page matches any needle at all, and the signal is
   therefore located by GEOMETRY first — a small node inside the 200px rail —
   and only then by what it says.
2. **Flutter MERGES a row's semantics once it contains a focusable child.**
   Each nav item is a ``Container(on_click=...)``, so the badge is absorbed
   into the nav button's own node rather than painting one of its own. The
   evidence is therefore read off the TRASH NAV NODE — which is exactly the
   right place, because "the signal is on the trash item and on no other" is
   the property AC1 states.
3. **"The count is in the tree" is a weak check on its own**: a digit can come
   from anywhere. So the assertion is a comparison across the three homes with
   the SAME code — the difference between them can only be the trash state.

WHAT IS NOT COVERED, RECORDED RATHER THAN SMOOTHED OVER
-------------------------------------------------------
* **COLOUR is not driven.** The badge is filled with ``COLORS["warning"]``, and
  Flutter paints colour to canvas — no colour reaches the accessibility tree
  and no DOM node carries it. A screenshot is captured for each pass so a human
  can look, but this script makes no automated colour claim. Asserted
  structurally in ``tests/test_ps272_trash_expiry_signal.py`` instead.
* **The countdown's ARITHMETIC is not driven here.** Which entries fall inside
  the window is a store-level property with an injectable clock, and it is
  asserted there — including the read-only-on-disk checks (AC2), which have no
  visual surface at all. What is driven here is the RAIL, which is what had
  none.
* **The 30-day boundary is not driven by waiting.** Obviously. The near-expiry
  state is reached by backdating ``deleted_at`` in the isolated home's
  ``trash.json``, which is the same field the product writes and reads.

RUN IT
------
    python3 -m tests.ui_driver.live_ps272

Requires flet, playwright and a chromium at ``driver.SYSTEM_CHROMIUM``. A
SCRIPT, not a pytest module, for the same reason its three siblings are: it
boots real apps and a real browser and reports a table whose output is quoted
on the ticket.
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
from src.ui.components.sidebar import EXPIRY_BADGE_TIP  # noqa: E402

DAY = 86400.0

#: The rail is a hard 200px. Anything the signal paints lives inside it.
RAIL_WIDTH = 200

#: How the SABOTAGE reverts the signal: ``_nav_button`` restored to its
#: pre-PS-272 body — an icon and a label and nothing else. Everything else
#: about the build is untouched, INCLUDING the store query and the count the
#: app computes and passes in. That is deliberate: it isolates the RENDER, so a
#: green in this pass would mean pass 1's evidence never came from the nav at
#: all.
_SABOTAGE = '''
import flet as ft
from src.ui.components import sidebar as _sb

def _no_badge_nav_button(key, icon, label, active, on_navigate, badge=0):
    color = _sb.COLORS["accent"] if active else _sb.COLORS["text_sub"]
    return ft.Container(
        border_radius=3,
        bgcolor=_sb.COLORS["card_hover"] if active else "transparent",
        border=ft.Border.all(1, _sb.COLORS["accent"] if active else "transparent"),
        padding=ft.Padding.symmetric(horizontal=14, vertical=10),
        on_click=lambda _, k=key: on_navigate(k),
        ink=True,
        content=ft.Row(
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Icon(icon, size=18, color=color),
                ft.Text(label, size=14, color=color, font_family=_sb.MONO),
            ],
        ),
    )

_sb._nav_button = _no_badge_nav_button
'''


def _seed_home(entries: list[tuple[str, float]]) -> str:
    """An isolated PERSONA_HOME whose trash.json holds ``(name, age_days)``.

    Written as the PRODUCT writes it — the same six keys ``TrashEntry.to_dict``
    emits — and read back by the real ``TrashStore._load`` when ``Container()``
    builds in the child. Nothing here is a test-only code path; backdating
    ``deleted_at`` is the only way to reach a 27-day-old entry inside a run.
    """
    home = tempfile.mkdtemp(prefix="persona-ps272-")
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
    with open(os.path.join(home, "trash.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.chmod(os.path.join(home, "trash.json"), 0o600)
    return home


def _report(name: str, ok: bool, detail: str) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return ok


def _dismiss(drv: FletDriver) -> None:
    for label in ("Skip", "[ got it ]"):
        if drv.has_button(label):
            drv.press(label)


def _rail_nodes(drv: FletDriver) -> list:
    """Every semantics node painted INSIDE the 200px rail.

    Geometry first, deliberately. A tooltip string propagates into every
    ancestor's innerText — measured in PS-266, where matching the tip matched
    the whole page — so a text search alone cannot tell "the rail carries the
    signal" from "the document contains the word".
    """
    return [n for n in drv.nodes() if n.box[0] < RAIL_WIDTH and n.box[2] <= RAIL_WIDTH]


def _trash_nav(drv: FletDriver):
    """The rail's ``trash`` nav node.

    Each nav item is a ``Container(on_click=...)``, so Flutter emits ONE
    tappable node per item and MERGES the badge into it. That merge is why the
    signal is read here rather than as a node of its own — and it is also the
    right place to read it, since "on the trash item and on no other" is the
    property under test.
    """
    best = None
    for n in _rail_nodes(drv):
        blob = f"{n.text or ''} {n.label or ''}"
        if "trash" not in blob.lower():
            continue
        # The SMALLEST rail node that says "trash" is the nav row itself; its
        # ancestors (the nav Column, the rail Container) say it too.
        if best is None or n.box[3] < best.box[3]:
            best = n
    return best


def _nav_for(drv: FletDriver, label: str):
    best = None
    for n in _rail_nodes(drv):
        blob = f"{n.text or ''} {n.label or ''}".lower()
        if label not in blob:
            continue
        if best is None or n.box[3] < best.box[3]:
            best = n
    return best


def _signal_on(node) -> tuple[bool, str]:
    """Does this nav node carry the expiry signal, and what does it say?"""
    if node is None:
        return False, "<node absent>"
    blob = f"{node.text or ''} {node.label or ''}"
    return EXPIRY_BADGE_TIP in blob, blob.replace("\n", " | ").strip()


def _observe(home: str, label: str, shot: str, patch: str = "") -> dict:
    """Boot one app against ``home`` and read the rail."""
    with serve_app(REPO_ROOT, home=home, patch=patch) as app:
        print(f"\n{label}\n  served: {app.url}\n  home:   {app.home}")
        with FletDriver(app.url, width=1280, height=820) as drv:
            _dismiss(drv)
            drv.page.wait_for_timeout(9000)
            trash = _trash_nav(drv)
            present, blob = _signal_on(trash)
            others = {
                key: _signal_on(_nav_for(drv, key))[0]
                for key in ("profiles", "network", "bookmarks", "tags",
                            "certificates", "connect")
            }
            drv.screenshot(shot)
            print(f"  screenshot: {shot}")
            return {
                "present": present,
                "blob": blob,
                "others": others,
                "rail_nodes": len(_rail_nodes(drv)),
                "box": trash.box if trash is not None else None,
            }


def main() -> int:
    results: list[bool] = []

    print("=" * 74)
    print("PS-272 — the trash's near-expiry signal, driven against the real app")
    print(f"RETENTION_DAYS={RETENTION_DAYS}  EXPIRY_WARNING_DAYS={EXPIRY_WARNING_DAYS}")
    print("=" * 74)

    # 27 days old => destroyed in 3 => inside the 7-day window.
    near_home = _seed_home([("old-jar", RETENTION_DAYS - 3)])
    # Deleted just now => 30 days of runway. NOT EMPTY, and still silent.
    far_home = _seed_home([("fresh-jar", 0)])
    empty_home = _seed_home([])

    near = _observe(
        near_home,
        "1. NEAR EXPIRY — one entry, destroyed in 3 days",
        "/tmp/ps272-near-expiry.png",
    )
    results.append(
        _report(
            "AC1 — the trash nav item carries a visible signal",
            near["present"],
            f"trash nav reads {near['blob']!r} (box={near['box']}, "
            f"{near['rail_nodes']} node(s) in the rail)",
        )
    )
    results.append(
        _report(
            "AC1 — the signal carries the COUNT (1 entry inside the window)",
            "1" in (near["blob"] or ""),
            f"trash nav reads {near['blob']!r}",
        )
    )
    results.append(
        _report(
            "AC1 — no OTHER nav item carries it",
            not any(near["others"].values()),
            f"{near['others']}",
        )
    )

    far = _observe(
        far_home,
        "2. FAR FROM EXPIRY — trash NOT empty, 30 days of runway",
        "/tmp/ps272-far-from-expiry.png",
    )
    results.append(
        _report(
            "AC1 — a non-empty trash with nothing near its deadline is SILENT",
            not far["present"],
            f"trash nav reads {far['blob']!r} — a signal here would be the "
            f"permanent chrome _status_needs_reveal refuses",
        )
    )

    empty = _observe(
        empty_home,
        "3. EMPTY TRASH",
        "/tmp/ps272-empty-trash.png",
    )
    results.append(
        _report(
            "AC1 — an empty trash is SILENT",
            not empty["present"],
            f"trash nav reads {empty['blob']!r}",
        )
    )

    # -----------------------------------------------------------------
    # AC4 — THE FALSIFICATION. Same home as pass 1, same near-expiry
    # entry, same store query, same count computed and passed in — with
    # the RENDER reverted. Pass 1's check is re-run and must go RED.
    # -----------------------------------------------------------------
    print("\n" + "-" * 74)
    print("4. FALSIFICATION (AC4) — the SAME near-expiry home, served against a")
    print("   build whose _nav_button is reverted to its pre-PS-272 body.")
    print("   The AC1 check above is re-run and MUST FAIL here.")
    print("-" * 74)
    broken = _observe(
        near_home,
        "4. SABOTAGED BUILD — the badge removed from the rendered nav",
        "/tmp/ps272-falsification.png",
        patch=_SABOTAGE,
    )
    results.append(
        _report(
            "AC4 — removing the signal makes the AC1 check go RED",
            not broken["present"],
            f"sabotaged trash nav reads {broken['blob']!r} — if the signal is "
            f"still reported here, AC1's green above is worthless because it "
            f"is not reading the rendered nav at all",
        )
    )

    print("\n" + "=" * 74)
    print("NOT COVERED BY DRIVING, recorded rather than smoothed over:")
    print("  * COLOUR. The badge is COLORS['warning']; Flutter paints colour to")
    print("    canvas and no colour reaches the accessibility tree. Screenshots")
    print("    are captured for a human to look at; no automated claim is made.")
    print("    Asserted structurally in tests/test_ps272_trash_expiry_signal.py.")
    print("  * AC2 (the query is read-only, on bytes on disk) has NO visual")
    print("    surface. Asserted at the store level on an injectable clock.")
    print("  * The 30-day boundary is reached by backdating deleted_at, not by")
    print("    waiting. Same field the product writes and reads.")
    print("=" * 74)
    ok = all(results)
    print(f"\n{sum(results)}/{len(results)} checks passed — "
          f"{'ALL GREEN' if ok else 'FAILURES ABOVE'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
