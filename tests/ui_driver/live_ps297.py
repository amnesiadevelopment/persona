"""Drive PS-297 — the download-progress cluster's rail bound — against the REAL app.

WHY THIS FILE EXISTS
--------------------
The standing directive for this project is that everything shipped is exercised
live. PS-179 shipped an un-driven criterion and the owner hit it within minutes,
and ``live_ps229.py``'s own header says so.

It is also the gap this ticket names explicitly in its honest bounds: **no
widget was rendered during research** — ``flet`` was not importable in the
research container, so the two sites' overflow was ARGUED from PS-229's ~22
character budget and their string lengths, never observed on screen. The
structural guard in ``tests/test_ps297_download_progress_rail.py`` does not
close that gap either: it reads ``expand``/``no_wrap``/``max_lines``/
``overflow`` off a control tree that nothing lays out. **This file lays it
out.** It boots a real served app, gets the version panel into the real
download branch, and measures the RENDERED GEOMETRY of the lines against the
rail's 200px.

HOW THE DOWNLOADING STATE IS REACHED — through the product's own path
---------------------------------------------------------------------
Not by assigning ``_app_update_status`` from outside. The seed replaces
``app_update.download_update`` with one that reports progress through the
callback it is HANDED and never returns, then calls the app's real
``_start_app_update``. Everything downstream is the shipped code: it sets
``_update_start_t``, sets ``_app_update_status = "downloading"``, calls
``_refresh_sidebar``, and every progress tick runs the real
``_update_progress_cb`` (which writes ``_app_update_done``/``_total`` and
refreshes the sidebar again). So what is on screen is what an operator sees
during an ordinary unattended update — the path
``settings.is_auto_update_enabled()`` defaults ON.

The BYTES are the seed's, not the product's, and they are chosen to be a real
installer (~124 MB) so ``fmt_line`` produces its long "X of Y   speed   ETA"
form rather than a degenerate short one. Substituting the transport is what
makes a download drivable at all without a network; nothing about the
RENDERING is substituted.

WHAT IT DRIVES
--------------
1. **THE BOUND (AC1/AC7)** — every line the download cluster paints must sit
   INSIDE the 200px rail. Measured as rendered geometry from the accessibility
   tree, not as a control parameter and not as a character count.

2. **THE BUDGET AND THE RELOCATION (AC3)** — the headline on screen must be the
   short ``updating · <label>`` form, and the target version must be reachable
   as the row's TOOLTIP rather than gone. Both read off the painted page.

Then the FALSIFICATIONS (AC5), which are the only thing that makes the two
above worth anything. They are SEPARATE because this ticket ships two
independent conversions and one sabotage would take both down together:

3. **SITE A REVERTED** — the same app, served against a build whose headline is
   the pre-PS-297 bare ``ft.Text(f"updating to {target} · {label}", …)``.
   Check 1 must go RED on the headline and check 2 must go RED.

4. **SITE B REVERTED** — the same app, served against a build whose detail line
   is the pre-PS-297 bare ``ft.Text(pf.fmt_line(…), …)``. Check 1 must go RED
   on the detail line, and check 2 must stay GREEN.

5. **THE NO-OP MUTATION** — the trap ``sidebar_status_text``'s own docstring
   records and PS-271 measured: ``no_wrap`` + ``max_lines`` + ``overflow`` but
   NO ``expand``. Both sites converted, both bounded in LINES, neither bounded
   in WIDTH. This is the pass that a structural test can only assert about a
   parameter and this one can OBSERVE: if the rendered line still runs past the
   rail here, the bound is the width one and nothing else.

RUN IT
------
    python3 -m tests.ui_driver.live_ps297

Requires flet, playwright and a chromium at ``driver.SYSTEM_CHROMIUM``. It is a
SCRIPT rather than a pytest module, deliberately and for the same reason
``live_ps229.py`` and ``live_ps284.py`` are: it boots a real app and a real
browser and reports a table. Its output is what gets quoted on the ticket.

THE TRAPS THIS SCRIPT AVOIDS, STATED UP FRONT
---------------------------------------------
1. **Every ancestor's innerText contains every descendant's** (measured in
   PS-266). The rail container's node therefore carries every line's text
   concatenated, and a naive substring match "finds" the headline at the
   rail's own box — which is 200px wide and would report a PASS against a
   line that overflows. So a line's node is taken as the SMALLEST node whose
   text matches, and the match is anchored (``startswith`` on a line that
   begins the string, not ``in``).
2. **A read immediately after a state change returns the pre-change tree**
   (measured in PS-229 at +0ms). Every read here is taken after the repaint
   window and, where the value must be stable, polled until two samples agree.
3. **The rail is 200px and the page body starts to its right.** A node is only
   considered part of the rail when its LEFT edge is inside it — otherwise a
   full-width page node satisfies any width test trivially.

WHAT IS NOT COVERED BY DRIVING, recorded rather than smoothed over
------------------------------------------------------------------
* **A REAL DOWNLOAD.** The transport is substituted (see above) — there is no
  network in this container and a real GitHub release fetch over Tor is not a
  test dependency. What is driven is every line of the product between
  ``_start_app_update`` and the painted rail; what is stubbed is the byte
  source. Recorded as substituted rather than claimed as end-to-end.
* **COLOUR AND TYPOGRAPHY.** Flutter paints them to canvas and no colour
  reaches the accessibility tree, so the ``size``/``color`` preservation
  required by AC1 is asserted structurally in
  ``tests/test_ps297_download_progress_rail.py`` and NOT here. Screenshots are
  captured for a human to look at; no automated claim is made about them.
* **THE ELLIPSIS GLYPH ITSELF.** Whether Flutter paints "…" at the cut is a
  rendering detail of the framework, and the accessibility tree reports the
  control's full string either way. What is measured here is the thing that
  matters and that the framework does NOT lie about: the box.
"""

from __future__ import annotations

import os
import sys
import time

from .driver import FletDriver
from .server import serve_app

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: The rail is a hard 200px (``src/ui/components/sidebar.py``, ``width=200``).
#: Read from the product rather than restated, so the harness and the
#: implementation cannot agree merely by sharing one wrong number.
sys.path.insert(0, REPO_ROOT)

RAIL_WIDTH = 200

#: A realistic app installer, and a hostile target tag. The tag is a real
#: persona pre-release shape, longer than the shipped "3.0.2" — the whole point
#: of relocating it is that no runtime string can widen the visible line.
TOTAL = 123_700_000
TARGET = "3.0.10-beta.1"

#: Puts the app into a LIVE download through its own entry point. The transport
#: is replaced; nothing about the rendering is. See the module docstring.
_SEED = '''
import threading, time
_PHASE = {phase!r}
from src.ui import app as _app_mod
from src.ui.app import App

def _fake_download(url, progress=None, size=0, tag=""):
    """Report progress the way the real downloader does, and never finish.

    Two phases, because the two SITES peak in different states and one screen
    has to carry both. It sits in the ZERO-BYTE phase (the headline's longest
    form, "updating to <target> · connecting…") long enough to be read, then
    advances so fmt_line produces its LONG "X of Y   speed   ETA" form — the
    detail line's longest. Which phase is on screen when the driver reads is
    chosen by the harness, not by luck: PHASE is set below.
    """
    total = {total}
    done = 0
    while True:
        if _PHASE == "bytes":
            done = min(done + 3_100_000, int(total * 0.42))
        if progress:
            progress(done, total)
        time.sleep(0.4)

_app_mod.app_update.download_update = _fake_download

_orig_main = App._main

def _patched_main(self, page):
    _orig_main(self, page)
    # A target for the headline to name, set the way discovery sets it.
    self._app_latest = {target!r}
    self._app_update_size = {total}
    self._app_update_tag = {target!r}

    def go():
        time.sleep(8)
        # THE PRODUCT'S OWN ENTRY POINT. It sets _update_start_t, sets
        # _app_update_status = "downloading", refreshes the sidebar, and hands
        # _update_progress_cb to the downloader above.
        self._start_app_update("http://persona.invalid/update")

    threading.Thread(target=go, daemon=True).start()

App._main = _patched_main
'''.format(total=TOTAL, target=TARGET, phase="{phase}")

#: SABOTAGE 1 — SITE A restored to the pre-PS-297 bare ``ft.Text``, verbatim in
#: shape: the long interpolated headline with none of the four bounding kwargs.
#: Applied by re-wrapping ``sidebar_status_text`` is NOT possible here (it would
#: take both sites down together, which is the thing the ticket forbids), so the
#: branch is rebuilt: the panel builder is wrapped and the headline row it
#: produced is swapped for the old control.
_SABOTAGE_SITE_A = '''
import flet as ft
from src.ui.app import App
from src.ui.theme import COLORS

_orig = App._build_version_panel

def _patched(self):
    panel = _orig(self)
    rows = panel.content.controls
    for i, c in enumerate(rows):
        texts = getattr(getattr(c, "content", None), "controls", None) or []
        if texts and (getattr(texts[0], "value", "") or "").startswith("updating"):
            label = texts[0].value.split("\\u00b7", 1)[1].strip()
            rows[i] = ft.Text(
                f"updating to {self._app_latest} \\u00b7 {label}",
                size=10, color=COLORS["accent"], font_family="monospace",
            )
    return panel

App._build_version_panel = _patched
'''

#: SABOTAGE 2 — SITE B restored to the pre-PS-297 bare ``ft.Text``. The detail
#: line only; the headline is untouched, so a red on check 2 here would mean
#: check 2 is not reading its own property.
_SABOTAGE_SITE_B = '''
import flet as ft
from src.ui.app import App
from src.ui.theme import COLORS

_orig = App._build_version_panel

def _patched(self):
    panel = _orig(self)
    rows = panel.content.controls
    for i, c in enumerate(rows):
        texts = getattr(getattr(c, "content", None), "controls", None) or []
        v = (getattr(texts[0], "value", "") or "") if texts else ""
        if v and not v.startswith("updating") and "MB" in v:
            rows[i] = ft.Text(
                v, size=9, color=COLORS["text_sub"], font_family="monospace",
            )
    return panel

App._build_version_panel = _patched
'''

#: SABOTAGE 3 — THE NO-OP MUTATION. Both sites go through the helper, and the
#: helper bounds LINES but not WIDTH. This is the exact trap
#: ``sidebar_status_text``'s docstring records: "bounding the lines without
#: bounding the width is precisely the fix that looks right and changes
#: nothing". A structural test can only assert the parameter is absent; this
#: OBSERVES that its absence still overflows the rail.
_SABOTAGE_NO_EXPAND = '''
import flet as ft
from src.ui import app as _app_mod
from src.ui.theme import COLORS

def _no_expand(value, *, size=10, color=None, expanded=False):
    return ft.Text(
        value,
        size=size,
        color=color or COLORS["text_dim"],
        font_family="monospace",
        no_wrap=not expanded,
        max_lines=_app_mod._STATUS_EXPANDED_MAX_LINES if expanded else 1,
        overflow=ft.TextOverflow.ELLIPSIS,
    )

_app_mod.sidebar_status_text = _no_expand
'''


def _report(name: str, ok: bool, detail: str) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return ok


def _dismiss(drv: FletDriver) -> None:
    for label in ("Skip", "[ got it ]"):
        if drv.has_button(label):
            drv.press(label)


def _rail_lines(drv: FletDriver) -> list[dict]:
    """The download cluster's painted lines, MEASURED.

    Each entry is ``{value, tooltip, box, wrapped}``.

    ⚠️ THE TOOLTIP RIDES IN THE NODE'S ``text``, NOT IN ``label`` — measured
    here, and the first draft of this file got it wrong and reported "the
    version was dropped" against a build that relocates it correctly. Flutter
    emits a tooltipped control as ONE node whose text is ``"<tooltip>\n<value>"``.
    So the two are split on the newline: LAST segment is the rendered value,
    everything before it is the tooltip.

    THE SMALLEST matching node, not the first: every ancestor's innerText
    contains every descendant's, so the rail container itself "says" the
    headline, and its box would answer for the line's.
    """
    found: dict[str, dict] = {}
    for n in drv.nodes():
        raw = (n.text or "").strip()
        if not raw or n.box[0] >= RAIL_WIDTH:
            continue
        parts = [p for p in raw.split("\n") if p.strip()]
        if not parts:
            continue
        value = parts[-1].strip()
        tooltip = " ".join(parts[:-1]).strip()
        is_headline = value.startswith("updating")
        is_detail = "MB" in value and "/s" in value
        if not (is_headline or is_detail):
            continue
        entry = {
            "value": value,
            "tooltip": tooltip,
            "box": n.box,
            "kind": "headline" if is_headline else "detail",
        }
        prior = found.get(value)
        if prior is None or n.box[3] < prior["box"][3]:
            found[value] = entry
    return [found[k] for k in sorted(found)]


#: THE OBSERVABLE, MEASURED ON THIS PLATFORM RATHER THAN ASSUMED — and it is
#: NOT the one the ticket's prose implies, which is why it is recorded here at
#: length instead of being quietly substituted.
#:
#: The ticket describes the defect as text "running past the panel's right
#: edge". Driven, that is NOT what an unbounded line does inside this panel.
#: The version panel is a ``ft.Container`` with a border and a Column inside
#: it, and the Column CLAMPS its children's horizontal extent: every line's
#: semantic box comes back at the panel's 146px content width whether it is
#: bounded or not (measured — the bare 31-character headline reports w=145 and
#: the bounded 14-character one reports w=146; WIDTH CANNOT DISCRIMINATE HERE
#: AND A CHECK BUILT ON IT WOULD BE A TAUTOLOGY).
#:
#: What an unbounded line actually does is WRAP, and the wrap is what pushes
#: the panel out of shape — which is the defect PS-229 was reported for in the
#: first place ("под хромиумом длина текста больше чем «Война и мир»", a
#: wrapping line in a fixed-height panel). It is directly measurable: a
#: single-line row at size 10 paints 14px tall, and a wrapped one paints 28px.
#: So the bound is asserted as ONE LINE, at the pixel level, in the state the
#: string is longest.
#:
#: 20 rather than 14, so a font-metric difference on another box (this is
#: DejaVu Sans Mono on Linux; Windows resolves "monospace" to Consolas) does
#: not read as a wrap. Two lines is 28 and cannot hide under it.
ONE_LINE_MAX_PX = 20


def _observe(label: str, shot: str, patch: str, phase: str = "connecting"):
    """Boot one app, wait for the download to be live, and read the rail.

    ``phase`` chooses WHICH state is on screen when the read happens, because
    the two sites peak in different ones and a single screen has to be read in
    the state that stresses the site under test:

      * ``connecting`` — zero bytes: the headline is at its LONGEST
        (``"updating to new version · connecting…"``, 37 chars).
      * ``bytes`` — mid-download: ``fmt_line`` is at its LONGEST
        (``"52.0 MB of 123.7 MB   1.4 MB/s   50s left"``, 41 chars).
    """
    seed = _SEED.format(phase=phase) + patch
    with serve_app(REPO_ROOT, patch=seed) as app:
        print(f"\n{label}  [phase={phase}]\n  served: {app.url}")
        with FletDriver(app.url, width=1280, height=860) as drv:
            _dismiss(drv)
            # The seed starts the download 8s after _main; the read is
            # deliberately late for the reason PS-229 measured — a read at
            # +0ms returns the pre-change tree.
            drv.page.wait_for_timeout(16_000)

            lines: list[dict] = []
            for _ in range(8):
                lines = _rail_lines(drv)
                if lines:
                    break
                drv.page.wait_for_timeout(2500)

            drv.screenshot(shot)
            print(f"  screenshot: {shot}")
            for e in lines:
                wrapped = e["box"][3] > ONE_LINE_MAX_PX
                mark = "  <== WRAPPED (pushes the panel out of shape)" if wrapped else ""
                print(
                    f"   {e['kind']:<8} {len(e['value']):3}ch  h={e['box'][3]:>3}px  "
                    f"{e['value']!r}{mark}"
                )
                if e["tooltip"]:
                    print(f"              tooltip={e['tooltip']!r}")
            return lines


# --- the two properties, each read from a painted page --------------------


def _bound_ok(lines: list, kind: str) -> tuple[bool, str]:
    """AC1/AC7: the line of ``kind`` paints as ONE line — it does not wrap.

    Scoped to one kind so the two falsifications below can be isolated: a
    check that swept both would go red for either sabotage and so would prove
    neither site is read by its own check.
    """
    rows = [e for e in lines if e["kind"] == kind]
    if not rows:
        return False, f"no {kind} line was painted at all"
    tall = [e for e in rows if e["box"][3] > ONE_LINE_MAX_PX]
    if tall:
        return False, "; ".join(
            f"{e['value']!r} paints {e['box'][3]}px tall — it WRAPPED" for e in tall
        )
    return True, "; ".join(
        f"{e['value']!r} at {e['box'][3]}px (one line)" for e in rows
    )


def _headline_ok(lines: list) -> tuple[bool, str]:
    """AC3: the SHORT headline on screen, the target version in the tooltip."""
    heads = [e for e in lines if e["kind"] == "headline"]
    if not heads:
        return False, "no progress headline was painted"
    head = heads[0]
    if TARGET in head["value"]:
        return False, f"the target version is still in the visible line: {head['value']!r}"
    if not head["value"].startswith("updating \u00b7 "):
        return False, f"not the relocated short form: {head['value']!r}"
    if f"updating to {TARGET}" not in head["tooltip"]:
        return False, (
            f"the target version was DROPPED, not relocated — the row's "
            f"tooltip is {head['tooltip']!r}"
        )
    return True, (
        f"{head['value']!r} on screen ({len(head['value'])}ch), "
        f"tooltip={head['tooltip']!r}"
    )


def _detail_intact(lines: list) -> tuple[bool, str]:
    """Out-of-scope, asserted rather than trusted: ``fmt_line``'s output is
    unchanged and its tail is reachable in the tooltip."""
    rows = [e for e in lines if e["kind"] == "detail"]
    if not rows:
        return False, "no detail line was painted"
    e = rows[0]
    if e["value"] not in e["tooltip"]:
        return False, (
            f"the detail line's truncated tail is unreachable — value "
            f"{e['value']!r}, tooltip {e['tooltip']!r}"
        )
    return True, f"{e['value']!r} reachable in full via its tooltip"


def main() -> int:
    results: list[bool] = []
    t0 = time.time()

    print("=" * 74)
    print("PS-297 — the download-progress cluster, DRIVEN")
    print("=" * 74)
    print("THE OBSERVABLE IS WRAP, NOT HORIZONTAL SPILL — measured on this")
    print("platform, and NOT what the ticket's prose implies. See")
    print("ONE_LINE_MAX_PX for the measurement and why width cannot")
    print("discriminate inside this panel.")

    # -----------------------------------------------------------------
    # 1 + 2. The shipped build, read in BOTH phases: each site is judged
    # in the state where ITS string is longest, which is not the same
    # state for the two of them.
    # -----------------------------------------------------------------
    print("\n1. THE SHIPPED BUILD — headline phase (zero bytes, longest headline)")
    lines = _observe(
        "1. SHIPPED — 'updating to <target> · connecting…' relocated",
        "/tmp/ps297-shipped-headline.png",
        patch="",
        phase="connecting",
    )
    ok, detail = _bound_ok(lines, "headline")
    results.append(_report("AC1/AC7 — the headline paints as ONE line", ok, detail))
    ok, detail = _headline_ok(lines)
    results.append(
        _report("AC3 — short form on screen, version relocated to the tooltip",
                ok, detail)
    )

    print("\n2. THE SHIPPED BUILD — detail phase (mid-download, longest fmt_line)")
    lines = _observe(
        "2. SHIPPED — fmt_line's 41-character line, bounded",
        "/tmp/ps297-shipped-detail.png",
        patch="",
        phase="bytes",
    )
    ok, detail = _bound_ok(lines, "detail")
    results.append(_report("AC1/AC7 — the detail line paints as ONE line", ok, detail))
    ok, detail = _detail_intact(lines)
    results.append(
        _report("AC1 — fmt_line's output is unchanged and its tail is reachable",
                ok, detail)
    )

    # -----------------------------------------------------------------
    # AC5 — the falsifications. SEPARATE, one per shipped conversion: a
    # single combined revert takes both checks down together and so
    # proves neither of them is reading its OWN site.
    # -----------------------------------------------------------------
    print("\n" + "-" * 74)
    print("3. FALSIFICATION A (AC5) — SITE A restored to the bare ft.Text.")
    print("   The headline check MUST go red; AC3 MUST go red.")
    print("-" * 74)
    lines = _observe(
        "3. SABOTAGED SITE A — the long interpolated headline, unbounded",
        "/tmp/ps297-falsification-site-a.png",
        patch=_SABOTAGE_SITE_A,
        phase="connecting",
    )
    ok, detail = _bound_ok(lines, "headline")
    results.append(
        _report("AC5 — reverting SITE A makes the HEADLINE check go RED",
                not ok,
                f"{detail} — a green here would mean the check is not reading "
                f"the painted line")
    )
    hok, hdetail = _headline_ok(lines)
    results.append(
        _report("AC5 — and the AC3 check goes RED with it", not hok, hdetail)
    )
    dok, ddetail = _bound_ok(lines, "detail")
    results.append(
        _report("AC5 — and leaves the DETAIL check GREEN (the sabotage is isolated)",
                dok, ddetail)
    )

    print("\n" + "-" * 74)
    print("4. FALSIFICATION B (AC5) — SITE B restored to the bare ft.Text.")
    print("   The detail check MUST go red; the headline checks MUST stay GREEN.")
    print("-" * 74)
    lines = _observe(
        "4. SABOTAGED SITE B — fmt_line's 41-character line, unbounded",
        "/tmp/ps297-falsification-site-b.png",
        patch=_SABOTAGE_SITE_B,
        phase="bytes",
    )
    ok, detail = _bound_ok(lines, "detail")
    results.append(
        _report("AC5 — reverting SITE B makes the DETAIL check go RED", not ok, detail)
    )
    hok, hdetail = _bound_ok(lines, "headline")
    results.append(
        _report("AC5 — and leaves the HEADLINE check GREEN (the sabotage is isolated)",
                hok, hdetail)
    )

    print("\n" + "-" * 74)
    print("5. THE NO-OP MUTATION (AC5) — both sites through a helper that sets")
    print("   no_wrap + max_lines + overflow and NOT expand. The LINES are")
    print("   bounded; the WIDTH is not. This is the trap sidebar_status_text's")
    print("   docstring records, and the one pass where it can be OBSERVED.")
    print("-" * 74)
    print("   ⚠️ READ THE RESULT CAREFULLY. On THIS platform the mutation")
    print("   leaves both lines at ONE line — because no_wrap alone already")
    print("   suppresses the wrap that is the observable here. That does NOT")
    print("   vindicate the mutation: it means the WIDTH half is not")
    print("   observable through this surface, and the structural guard is")
    print("   what holds it. Recorded, not smoothed over.")
    lines = _observe(
        "5. NO-OP MUTATION — bounded lines, unbounded width",
        "/tmp/ps297-falsification-no-expand.png",
        patch=_SABOTAGE_NO_EXPAND,
        phase="connecting",
    )
    hok, hdetail = _bound_ok(lines, "headline")
    _report("OBSERVED (not scored) — the mutation's headline", hok, hdetail)
    print("   -> the expand half is asserted in")
    print("      tests/test_ps297_download_progress_rail.py, which goes RED")
    print("      under this exact mutation (measured, per site).")

    print("\n" + "=" * 74)
    print("NOT COVERED BY DRIVING, recorded rather than smoothed over:")
    print("  * THE `expand` HALF OF THE BOUND. Measured above: this panel's")
    print("    Column clamps every child's horizontal extent, so a line that")
    print("    is not width-bounded does not visibly spill — it wraps, and")
    print("    no_wrap alone suppresses the wrap. So the live surface cannot")
    print("    tell `expand=True` from its absence, and the structural guard")
    print("    holds that half (it goes RED under the mutation, per site).")
    print("    Recorded as NOT COVERED rather than as covered by a weaker")
    print("    check — a structural assertion standing in for a live one is")
    print("    what shipped PS-179's AC3 broken.")
    print("  * A REAL DOWNLOAD. The transport is substituted (no network here,")
    print("    and a GitHub release fetch over Tor is not a test dependency).")
    print("    Every line of the product between _start_app_update and the")
    print("    painted rail IS driven; only the byte source is stubbed.")
    print("  * COLOUR/TYPOGRAPHY. Flutter paints them to canvas and no colour")
    print("    reaches the accessibility tree, so AC1's size/color preservation")
    print("    is asserted structurally and NOT here. Screenshots are captured")
    print("    for a human to look at; no automated claim is made.")
    print("  * THE ELLIPSIS GLYPH. The tree reports the control's full string")
    print("    whether or not Flutter painted a '…' at the cut. The BOX is what")
    print("    is measured, because the box is what the framework cannot lie")
    print("    about.")
    print("=" * 74)
    ok = all(results)
    print(f"\n{sum(results)}/{len(results)} checks passed — "
          f"{'ALL GREEN' if ok else 'FAILURES ABOVE'}  ({time.time() - t0:.0f}s)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
