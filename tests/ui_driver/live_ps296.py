"""Drive PS-296 — the exit country rendered from its CODE — against the REAL app.

WHY THIS FILE EXISTS
--------------------
The standing directive for this project is that everything shipped is exercised
live, and this ticket is entirely a claim about what an operator SEES: what the
network row says, what the Activity Log says, and whether the flag painted
beside the row agrees with either of them. A headless test over the built
control tree passes just as happily against a page that never paints, a meta
line the framework clips, or a flag drawn from a different field than the one
the text used — which is precisely the defect: ``_flag_widget`` keys on the
country CODE, ``_meta_line`` used to key on the country NAME, and each was
individually defensible while the two contradicted each other inside one row.

The ticket also records that NO widget was rendered during research (``flet``
was unimportable in the research container), so every rendering claim it makes
was READ from the source. This file observes them.

WHAT IT DRIVES
--------------
**Pass 1 — THE REAL FALLBACK, end to end.** One served app holding a proxy with
NO stored geography, checked by pressing the real ``[ check ]`` button. The
transport seam (``_geo_via_socks``, the same seam ``_resolve_geo`` takes) is
patched so that ``ipwho.is`` answers **429** and ``ipinfo.io`` answers with its
REAL body shape — ``{"country": "PL", "timezone": ..., "loc": ...}``, a
code-shaped ``country`` and no name field at all. Nothing about the reader, the
provider order, the store or the render is patched: the shape gate in
``_geo_fields_from_payload`` classifies the dialect itself, ``mark_checked``
persists what it produced, and both operator surfaces render from the record.
That is the ipinfo record shape arriving the way it really arrives, rather than
a fixture asserting it exists.

**Pass 2 — THE FOUR STORED SHAPES, side by side on one screen.** A seeded home
holding one proxy per reachable ``(code, name)`` combination, so the agreement
property (AC3) is read across all four at once rather than checked on the one
that broke, and so the two shapes that must NOT change (AC4's ipwho record,
AC5's zone-only partial) are observed on the same painted page as the one that
must.

**Passes 3 and 4 — TWO FALSIFICATIONS (AC6), one per shipped surface.** They
are separate because this ticket changes two independent sites, and a single
combined revert would take both checks down together — giving a red that says
nothing about WHICH check reads the screen. Pass 3 restores ``_meta_line``'s
pre-change body verbatim (the row check must go RED, the log check must stay
GREEN); pass 4 restores ``proxy_ok_message``'s (the log check must go RED, the
row check must stay GREEN).

THE TRAPS THIS SCRIPT AVOIDS, STATED UP FRONT
---------------------------------------------
1. **Every ancestor's ``innerText`` contains every descendant's** (measured in
   PS-266). The page node therefore carries every row's text concatenated, so
   "``[PL]`` is somewhere in the tree" is true of a page that renders it on the
   WRONG ROW, or on no row at all while the rail happens to say it. A row's
   name node is located by EXACT text equality, and its meta line is taken as
   the node painted directly beneath it — geometry, on the page, as painted.
2. **The flag is not asserted from ``flag_path``.** ``flag_path('PL')`` was
   never wrong — it returned the Polish flag throughout the defect — so a test
   calling it would have been green before this fix. The flag is read as
   PIXELS, by screenshotting the flag column beside each row and comparing the
   images: a row painting the same pixels as the known-flagless row has no
   flag, and one painting different pixels has one. That is a claim about what
   was drawn, not about what a helper returned.
3. **The name is asserted ABSENT where it was never known.** The ticket forbids
   synthesizing "Poland" from ``PL``, so the fallback row is checked for the
   marker AND against the word — a fix that quietly added a code->name table
   would pass every "names a country" check and fail here.
4. **The fixture set is not degenerate.** All four ``(code, name)`` corners are
   on screen, and the flag partition is asserted to be a PROPER split (some
   rows flagged, some not) before the agreement claim is made — a set where
   every row is flagged would make AC3 pass against any rule at all.

WHAT IS NOT COVERED, RECORDED RATHER THAN SMOOTHED OVER
-------------------------------------------------------
* **WHICH flag is painted is not identified.** The pixel comparison establishes
  that two rows paint the SAME image and that a third paints a DIFFERENT one —
  it cannot say the image is Poland's. Flutter paints the SVG to canvas and no
  source path reaches the accessibility tree, so the identity is asserted
  headlessly instead (``tests/test_ps296_country_render.py`` reads ``ft.Image.
  src`` off the built row). Screenshots are captured here for a human to look
  at; no automated claim is made about the artwork.
* **COLOUR and TYPOGRAPHY are not driven**, for the same canvas reason.
* **The real ipwho.is 429 is not reached by waiting for a real rate limit.**
  Obviously. It is reached at the transport seam — the same seam the product's
  own two lanes pass in — so everything above it, including the dialect gate
  that decides ``country: "PL"`` is a code and not a name, is the shipped code.
* **The FREQUENCY of this fallback in the wild is unmeasured** and this script
  does not measure it. It reaches the state deterministically; how often an
  operator meets it is unknown, as the ticket records.

RUN IT
------
    python3 -m tests.ui_driver.live_ps296

Requires flet, playwright and a chromium at ``driver.SYSTEM_CHROMIUM``. A
SCRIPT, not a pytest module, for the same reason its siblings are: it boots a
real app and a real browser and reports a table whose output is quoted on the
ticket.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import time

from .driver import FletDriver
from .server import serve_app

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

#: The rail is a hard 200px; the page body starts to the right of it.
RAIL_WIDTH = 200

#: The flag column, measured relative to a row's NAME node rather than typed as
#: an absolute: the flag is 26x18 at ``spacing=14`` to the left of the name
#: column (``_proxy_row``), so it sits at ``name_x - 40 .. name_x - 14``. The
#: clip is widened by a couple of pixels on each side so a sub-pixel layout
#: shift cannot make two identical flags hash differently.
_FLAG_DX, _FLAG_W, _FLAG_H = -42, 30, 40

#: The record shapes, one proxy each: ``(name, code, country_name, ip)``. All
#: four reachable ``(code, name)`` corners are here so the agreement property
#: is read across the whole space on one screen.
_SEED = [
    ("ipwho-pl", "PL", "Poland", "5.5.5.5"),      # AC4 — must not move
    ("ipinfo-pl", "PL", "", "6.6.6.6"),           # AC1 — the defect
    ("degraded-pl", "", "Poland", "7.7.7.7"),     # partial: name, no code
    ("zone-only", "", "", "8.8.8.8"),             # AC5 — must stay silent
]

#: The proxy pass 1 CHECKS for real. Stored with no geography at all, so
#: everything the row and the log end up saying was produced by the check.
_LIVE_NAME = "fallback-pl"

#: The transport seam patched into the child: ``ipwho.is`` rate-limits (the 429
#: this fallback exists to survive) and ``ipinfo.io`` answers with its REAL
#: body — a code-shaped ``country`` and no country-name field at all. This is
#: the ONLY thing patched; the dialect gate, the provider order, the store and
#: both render paths are the shipped ones.
_FALLBACK_TRANSPORT = '''
from src.utils import proxy_checker as _pc

async def _fake_geo(proxy_config, scheme, url):
    if "ipwho" in url:
        return 429, None
    return 200, {
        "ip": "9.9.9.9",
        "country": "PL",
        "timezone": "Europe/Warsaw",
        "loc": "52.23,21.01",
    }

_pc._geo_via_socks = _fake_geo
'''

#: SABOTAGE A (AC6) — ``_meta_line`` restored to its pre-PS-296 body, verbatim:
#: the country segment gated on the NAME, which renders nothing for a record
#: whose provider had no name field. The Activity Log is untouched.
_SABOTAGE_ROW = '''
from src.ui.components import network_page as _np
from src.utils.proxy_parser import split_proxy_url as _split
from src.utils.timefmt import humanize_since as _since

def _old_meta_line(proxy, now):
    parts = [_split(proxy.url)["scheme"]]
    if proxy.country_name:
        code = f"[{proxy.country_code}] " if proxy.country_code else ""
        parts.append(f"{code}{proxy.country_name}")
    if proxy.last_ip:
        parts.append(proxy.last_ip)
    if proxy.last_check_ok is False and proxy.checked_at:
        parts.append(f"check failed {_since(proxy.checked_at, now)}")
    elif proxy.checked_at:
        parts.append(f"checked {_since(proxy.checked_at, now)}")
    else:
        parts.append("not checked yet")
    return "  ·  ".join(parts)

_np._meta_line = _old_meta_line
'''

#: SABOTAGE B (AC6) — ``proxy_ok_message`` restored to its pre-PS-296 body,
#: verbatim: ``where`` gated on the name, which suppresses the ``[PL]`` marker
#: AND the flag emoji together. The network row is untouched.
_SABOTAGE_LOG = '''
from src.utils import proxy_checker as _pc

def _old_ok_message(code, country):
    flag = _pc.flag_from_country_code(code)
    where = f"{flag} [{code}] {country}".strip() if country else ""
    return f"Proxy working. {where}".strip() if where else "Proxy working."

_pc.proxy_ok_message = _old_ok_message
'''


def _seed_home(records: list[tuple[str, str, str, str]], *, with_live: bool) -> str:
    """An isolated PERSONA_HOME whose proxies.json holds these records.

    Written with the SIX geo keys ``ProxyStore._load`` reads and ``Proxy.
    to_dict`` emits, and read back by the real store when ``Container()``
    builds in the child. Nothing here is a test-only code path — the
    ``('PL', '')`` record is exactly what ``mark_checked`` persists after an
    ipinfo answer, which pass 1 then produces for real.
    """
    home = tempfile.mkdtemp(prefix="persona-ps296-")
    now = time.time()
    payload = {}
    for name, code, country, ip in records:
        payload[name] = {
            "name": name,
            "url": f"socks5://u:p@{ip}:1080",
            "rotate_url": "",
            "country_code": code,
            "country_name": country,
            "last_ip": ip,
            "timezone": "Europe/Warsaw" if code or country else "",
            "lat": None,
            "lon": None,
            "checked_at": now - 300,
            "last_check_ok": True,
        }
    if with_live:
        payload[_LIVE_NAME] = {
            "name": _LIVE_NAME,
            "url": "socks5://u:p@9.9.9.9:1080",
            "rotate_url": "",
            "country_code": "",
            "country_name": "",
            "last_ip": "",
            "timezone": "",
            "lat": None,
            "lon": None,
            "checked_at": 0.0,
            "last_check_ok": None,
        }
    path = os.path.join(home, "proxies.json")
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


def _open_network(drv: FletDriver) -> bool:
    """Click the rail's ``network`` nav item — the operator's own route in.

    Geometry first: a nav label propagates into every ancestor's innerText, so
    the item is located INSIDE the 200px rail and then as the SMALLEST such
    node that says "network" (the nav column and the rail container say it too).
    """
    best = None
    for n in drv.nodes():
        if n.box[0] >= RAIL_WIDTH or n.box[0] + n.box[2] > RAIL_WIDTH:
            continue
        if "network" not in f"{n.text or ''} {n.label or ''}".lower():
            continue
        if best is None or n.box[3] < best.box[3]:
            best = n
    if best is None:
        return False
    drv.page.mouse.click(best.box[0] + best.box[2] // 2, best.box[1] + best.box[3] // 2)
    drv.page.wait_for_timeout(3000)
    return True


def _read_rows(drv: FletDriver, names: list[str]) -> dict[str, tuple[str, str]]:
    """Each row as ``name -> (meta line, flag-column pixel digest)``.

    The name node is taken by EXACT text equality (no ancestor satisfies it,
    which is what keeps the concatenated-innerText trap out), the meta line as
    the node painted directly beneath it, and the flag as the IMAGE painted in
    the column to its left. The flag is pixels rather than a source path
    because no source path reaches the accessibility tree — and because
    ``flag_path`` was never the broken half.
    """
    nodes = [n for n in drv.nodes() if n.box[0] >= RAIL_WIDTH]
    metas = [n for n in nodes if (n.text or "").startswith("socks5  ·  ")]
    out: dict[str, tuple[str, str]] = {}
    for name in names:
        hits = [n for n in nodes if (n.text or "").strip() == name]
        if not hits:
            continue
        x, y = hits[0].box[0], hits[0].box[1]
        below = sorted(
            (m for m in metas if 0 <= m.box[1] - y < 60), key=lambda m: m.box[1]
        )
        shot = drv.page.screenshot(
            clip={"x": x + _FLAG_DX, "y": y, "width": _FLAG_W, "height": _FLAG_H}
        )
        out[name] = (
            below[0].text.strip() if below else "<no meta line>",
            hashlib.sha256(shot).hexdigest()[:16],
        )
    return out


def _read_check_log(drv: FletDriver) -> str:
    """The Activity Log entry the proxy check just wrote, as painted.

    Located as the SMALLEST node mentioning the checked proxy and a proxy-check
    verdict — the console's ancestors carry every entry concatenated, so the
    smallest match is the entry itself.
    """
    best = None
    for n in drv.nodes():
        text = (n.text or "").strip()
        if f"[{_LIVE_NAME}]" not in text or "Proxy" not in text:
            continue
        if best is None or len(text) < len(best):
            best = text
    return best or "<no proxy-check entry on screen>"


def _observe_live_check(label: str, shot: str, patch: str = "") -> tuple[str, dict]:
    """Boot an app, press the REAL ``[ check ]``, and read both surfaces."""
    home = _seed_home(_SEED, with_live=True)
    with serve_app(REPO_ROOT, home=home, patch=_FALLBACK_TRANSPORT + patch) as app:
        print(f"\n{label}\n  served: {app.url}\n  home:   {app.home}")
        with FletDriver(app.url, width=1280, height=900) as drv:
            _dismiss(drv)
            drv.page.wait_for_timeout(9000)
            if not _open_network(drv):
                print("  !! could not find the network nav item in the rail")
                drv.screenshot(shot)
                return "<network page not reached>", {}
            # The operator's own gesture. The checked proxy is the FIRST row
            # with no geography, so its [ check ] is the one pressed — located
            # by the row's y, not by button order.
            rows = [n for n in drv.nodes() if (n.text or "").strip() == _LIVE_NAME]
            if not rows:
                print(f"  !! {_LIVE_NAME} is not on the page")
                drv.screenshot(shot)
                return "<row absent>", {}
            y = rows[0].box[1]
            btn = sorted(
                (
                    n for n in drv.nodes()
                    if (n.text or "").strip() == "[ check ]" and abs(n.box[1] - y) < 60
                ),
                key=lambda n: abs(n.box[1] - y),
            )
            if not btn:
                print("  !! no [ check ] button beside the row")
                drv.screenshot(shot)
                return "<check button absent>", {}
            drv.page.mouse.click(
                btn[0].box[0] + btn[0].box[2] // 2, btn[0].box[1] + btn[0].box[3] // 2
            )
            drv.page.wait_for_timeout(9000)
            entry = _read_check_log(drv)
            rows_read = _read_rows(drv, [_LIVE_NAME] + [n for n, *_ in _SEED])
            drv.screenshot(shot)
            print(f"  screenshot: {shot}")
            print(f"  activity log: {entry!r}")
            for name, (meta, digest) in rows_read.items():
                print(f"   {name:<13} {meta!r}  flag={digest}")
            return entry, rows_read


# --- the properties, each read from a painted page ------------------------


def _row_names_country_ok(rows: dict) -> tuple[bool, str]:
    """AC1 (row) — the code-only record names its country on the page."""
    meta = rows.get(_LIVE_NAME, ("<absent>", ""))[0]
    segments = [s.strip() for s in meta.split("  ·  ")]
    if "[PL]" not in segments:
        return False, f"the checked row reads {meta!r} — no country segment"
    if "Poland" in meta:
        return False, f"the row INVENTED a name from the code: {meta!r}"
    return True, f"the checked row reads {meta!r}"


def _log_names_country_ok(entry: str) -> tuple[bool, str]:
    """AC1 (log) — and the Activity Log names it, WITH the flag emoji."""
    if "[PL]" not in entry:
        return False, f"the log entry reads {entry!r} — it names no country"
    if "\U0001F1F5\U0001F1F1" not in entry:
        return False, f"the log entry reads {entry!r} — no flag emoji"
    if "Poland" in entry:
        return False, f"the log INVENTED a name from the code: {entry!r}"
    return True, f"the log entry reads {entry!r}"


def _names_a_country(meta: str) -> bool:
    """Does this painted meta line say anything about an exit country?

    Read off the rendered segments rather than off the record, because the
    record is exactly what the page is being asked about.
    """
    segments = [s.strip() for s in meta.split("  ·  ")]
    return any(
        s for s in segments[1:]
        if s.startswith("[") or (s and not _CLOCK.match(s) and not _IP.match(s))
    )


_CLOCK = re.compile(r"^(checked|check failed|not checked)\b")
_IP = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def _agreement_ok(rows: dict) -> tuple[bool, str]:
    """AC3 — no row paints a country flag beside a line naming no country.

    ONE-DIRECTIONAL, deliberately, and this is the criterion's exact wording
    rather than a weakening of it. The reverse — text without a flag — is
    CORRECT and must stay reachable: the ``degraded-pl`` record carries a name
    and no code, and the flag keys on the code, so there is no flag to paint.
    Requiring a biconditional here would fail the product for rendering that
    row honestly. The direction that matters is the one the defect took: a flag
    asserting a country the text declines to name.

    The flag is read as pixels: the flagless rendering is whatever ``zone-only``
    paints (no code at all, so ``flag_path`` returns None by construction), and
    any row painting a DIFFERENT image has a flag. The partition is asserted to
    be a proper split first — if every row painted the same thing this check
    would pass against any rule.
    """
    if "zone-only" not in rows:
        return False, "the zone-only row (the flagless reference) is not on the page"
    blank = rows["zone-only"][1]
    flagged = {n for n, (_m, d) in rows.items() if d != blank}
    if not flagged or len(flagged) == len(rows):
        return False, (
            f"the flag partition is degenerate ({len(flagged)} of {len(rows)} rows "
            f"differ from the flagless reference) — this check would pass against "
            f"any rule"
        )
    bad = [
        f"{n}: paints a flag beside {m!r}, which names no country"
        for n, (m, _d) in rows.items()
        if n in flagged and not _names_a_country(m)
    ]
    if bad:
        return False, "; ".join(bad)
    silent = sorted(n for n, (m, _d) in rows.items() if not _names_a_country(m))
    return True, (
        f"{sorted(flagged)} paint a flag and every one of them names a country; "
        f"the row(s) naming none ({silent}) paint none either"
    )


def _ipwho_unchanged_ok(rows: dict) -> tuple[bool, str]:
    """AC4 — the ordinary ipwho record renders exactly as it did before."""
    meta = rows.get("ipwho-pl", ("<absent>", ""))[0]
    segments = [s.strip() for s in meta.split("  ·  ")]
    if segments[:3] != ["socks5", "[PL] Poland", "5.5.5.5"]:
        return False, f"the ipwho row reads {meta!r}"
    if len(segments) != 4 or not segments[3].startswith("checked "):
        return False, f"the ipwho row reads {meta!r}"
    return True, f"the ipwho row reads {meta!r}"


def _zone_only_ok(rows: dict) -> tuple[bool, str]:
    """AC5 — the pre-PS-268 partial still says nothing, and paints nothing."""
    meta, digest = rows.get("zone-only", ("<absent>", ""))
    segments = [s.strip() for s in meta.split("  ·  ")]
    if segments[:2] != ["socks5", "8.8.8.8"] or len(segments) != 3:
        return False, f"the zone-only row reads {meta!r}"
    others = {d for n, (_m, d) in rows.items() if n != "zone-only"}
    if digest in others and len(others) == 1:
        return False, "every row paints the same image — the flag read is blind"
    return True, f"the zone-only row reads {meta!r} and paints no flag"


def main() -> int:
    results: list[bool] = []

    print("=" * 74)
    print("PS-296 — the exit country rendered from its CODE, driven live")
    print("ipwho.is 429s at the transport seam; ipinfo.io answers with its real")
    print("body (a code-shaped `country`, no name field). Everything above the")
    print("seam — dialect gate, provider order, store, both renders — is shipped.")
    print("=" * 74)

    entry, rows = _observe_live_check(
        "1. THE REAL FALLBACK — the operator presses [ check ] and ipwho is rate-limited",
        "/tmp/ps296-real-fallback.png",
    )

    ok, detail = _row_names_country_ok(rows)
    results.append(_report("AC1 — the network row NAMES the country", ok, detail))
    ok, detail = _log_names_country_ok(entry)
    results.append(
        _report("AC1 — the Activity Log names it too, with the flag emoji", ok, detail)
    )
    ok, detail = _agreement_ok(rows)
    results.append(
        _report("AC3 — flag and text AGREE in every row on the page", ok, detail)
    )
    ok, detail = _ipwho_unchanged_ok(rows)
    results.append(
        _report("AC4 — the ordinary ipwho row is unchanged", ok, detail)
    )
    ok, detail = _zone_only_ok(rows)
    results.append(
        _report("AC5 — the zone-only partial still names nothing", ok, detail)
    )

    # -----------------------------------------------------------------
    # AC6 — TWO falsifications, one per shipped surface. Separate on
    # purpose: one combined revert takes both checks down together and
    # so proves neither of them is reading its OWN surface.
    # -----------------------------------------------------------------
    print("\n" + "-" * 74)
    print("2. FALSIFICATION A (AC6) — the same check, against a build whose")
    print("   _meta_line is its verbatim pre-PS-296 body. The ROW check MUST go")
    print("   red; the LOG check MUST stay green.")
    print("-" * 74)
    entry_a, rows_a = _observe_live_check(
        "2. SABOTAGED ROW — the meta line gated on the country NAME again",
        "/tmp/ps296-falsification-row.png",
        patch=_SABOTAGE_ROW,
    )
    row_ok, row_detail = _row_names_country_ok(rows_a)
    log_ok, log_detail = _log_names_country_ok(entry_a)
    agree_ok, agree_detail = _agreement_ok(rows_a)
    results.append(
        _report(
            "AC6 — reverting the ROW render makes the AC1 row check go RED",
            not row_ok,
            f"{row_detail} — if this stayed green the check is not reading the page",
        )
    )
    results.append(
        _report(
            "AC6 — and it makes the AC3 agreement check go RED too",
            not agree_ok,
            f"{agree_detail} — this IS the defect: a flag beside a line naming "
            f"no country",
        )
    )
    results.append(
        _report(
            "AC6 — while the LOG check stays GREEN (the sabotage is isolated)",
            log_ok,
            log_detail,
        )
    )

    print("\n" + "-" * 74)
    print("3. FALSIFICATION B (AC6) — the same check, against a build whose")
    print("   proxy_ok_message is its verbatim pre-PS-296 body. The LOG check")
    print("   MUST go red; the ROW check MUST stay green.")
    print("-" * 74)
    entry_b, rows_b = _observe_live_check(
        "3. SABOTAGED LOG — `where` gated on the country NAME again",
        "/tmp/ps296-falsification-log.png",
        patch=_SABOTAGE_LOG,
    )
    row_ok, row_detail = _row_names_country_ok(rows_b)
    log_ok, log_detail = _log_names_country_ok(entry_b)
    results.append(
        _report(
            "AC6 — reverting the LOG message makes the AC1 log check go RED",
            not log_ok,
            f"{log_detail} — this is the defect verbatim: a bare 'Proxy working.' "
            f"for an exit the product had just measured as Poland",
        )
    )
    results.append(
        _report(
            "AC6 — while the ROW check stays GREEN (the sabotage is isolated)",
            row_ok,
            row_detail,
        )
    )

    print("\n" + "=" * 74)
    print("NOT COVERED BY DRIVING, recorded rather than smoothed over:")
    print("  * WHICH flag is painted. The pixel read establishes same/different")
    print("    against the flagless row; it cannot say the image is Poland's.")
    print("    Flutter paints the SVG to canvas and no source path reaches the")
    print("    semantics tree, so the identity is asserted headlessly in")
    print("    tests/test_ps296_country_render.py off ft.Image.src.")
    print("  * COLOUR and TYPOGRAPHY, for the same canvas reason. Screenshots")
    print("    are captured for a human; no automated claim is made.")
    print("  * A REAL ipwho.is 429. Reached at the transport seam instead — the")
    print("    same seam both product lanes pass in — so the dialect gate that")
    print("    decides `country: \"PL\"` is a CODE is the shipped one.")
    print("  * HOW OFTEN an operator meets this fallback. Unmeasured, as the")
    print("    ticket records; this reaches the state deterministically.")
    print("=" * 74)
    ok = all(results)
    print(f"\n{sum(results)}/{len(results)} checks passed — "
          f"{'ALL GREEN' if ok else 'FAILURES ABOVE'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
