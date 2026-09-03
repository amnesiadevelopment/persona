"""Drive PS-285 — the unresolvable-certificate marker on the profile card —
against the REAL app.

WHY THIS FILE EXISTS
--------------------
The standing directive for this project is that everything shipped is exercised
live. The claim this ticket makes is *visual and negative in two of its three
cases*: "the card says something when the profile's certificate does not
resolve, and says NOTHING when it resolves and NOTHING when there is none". A
unit test over the built control tree passes just as happily against a chip
that never paints, is clipped out of the row, or is drawn on the wrong card. So
it is driven.

WHAT IT DRIVES
--------------
ONE real served app holding all THREE states at once, which is a strictly
stronger comparison than three apps would be: the three cards are painted by
the same process from the same code in the same repaint, so the only thing that
can differ between them is the profile's certificate.

  1. ``dangling``  — assigned ``corp-ca``, which the store does NOT hold.
  2. ``resolving`` — assigned ``ops-ca``, which the store DOES hold.
  3. ``plain``     — no certificate at all.

The marker must be on 1 and on NEITHER of the others. Cases 2 and 3 are the
half that separates "a signal" from "a decoration", and they are AC4's shape.

THE UNRESOLVABLE STATE IS REACHED THE WAY THE PRODUCT REACHES IT. Nothing is
hand-deleted: ``certificates.json`` is written with TWO records, one of which
is MALFORMED (an unknown key), and the real ``CertStore._load`` skips it and
carries on — the shipped protection, firing, which is one of the two routes the
ticket names. So the served app genuinely has a populated dropdown with one
name missing, rather than a fixture pretending to.

Then the FALSIFICATION pass (AC6), which is the only thing that makes the above
worth anything:

  4. **SABOTAGED** — the same home, served against a build whose
     ``_unresolved_cert_chip`` is reverted to its pre-PS-285 body (returning
     nothing at all). Everything else is untouched, INCLUDING the flag the app
     computes and passes in. Pass 1's check is re-run and MUST go RED. If it
     stays green, pass 1's green says nothing about the rendered card and this
     whole file is decoration.

THE TRAPS THIS SCRIPT AVOIDS, STATED UP FRONT
---------------------------------------------
1. **A tooltip's string propagates into every ANCESTOR's ``innerText``**
   (measured in PS-266, where counting a tip counted the depth of the widget
   tree). So the whole page matches any needle at all. The evidence is
   therefore read PER CARD — the smallest node that carries the profile's name
   — and the three cards are compared against each other, so a needle found on
   the page but not on a card is not evidence.
2. **"The words are in the tree" is a weak check on its own.** The load-bearing
   assertion is the COMPARISON: three cards, one code path, one repaint. The
   difference between them can only be the certificate.
3. **A pass that only checks the positive half.** Two of the three states must
   be SILENT, and that is asserted as strongly as the positive one.

WHAT IS NOT COVERED BY DRIVING, recorded rather than smoothed over
------------------------------------------------------------------
* **COLOUR is not driven.** The chip is drawn in ``COLORS["warning"]``, and
  Flutter paints colour to canvas — no colour reaches the accessibility tree
  and no DOM node carries it. A screenshot is captured so a human can look, but
  this script makes no automated colour claim. Asserted structurally in
  ``tests/test_ps285_unresolved_certificate_visible.py``.
* **The TOOLTIP is asserted, but only ON THE ROW.** Its string propagates into
  every ancestor's innerText (trap 1), so finding it "on the page" would prove
  nothing — it is therefore read off the same chip-sized node inside the card's
  own row band, which is where being one hover away actually means something.
  A HOVER GESTURE is not driven: that is Flutter's own tooltip machinery, not
  this ticket's claim.
* **The LAUNCH LINE (AC3) is NOT DRIVEN AT ALL, and no weaker check is
  substituted for it.** Route (i) was taken, so the line lands in the
  persistent log and in the NEXT session's Activity Log seed — by construction
  it is not in the current session's panel, so there is nothing on this screen
  to drive. It is asserted on the real logger over the real
  ``_cert_session_for`` in the suite above. Recorded as NOT COVERED here.
* **The QUARANTINE route** (an unreadable ``certificates.json``, every name
  absent) is not driven; the SKIPPED-RECORD route is. They converge on the same
  branch — ``CertStore.get`` answering ``None`` — and the skip route is the one
  that also proves the store is otherwise healthy and populated, which is the
  harder case for the marker.

RUN IT
------
    python3 -m tests.ui_driver.live_ps285

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

from .driver import FletDriver
from .server import serve_app

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

#: The profile whose certificate does not resolve, and the name it references.
DANGLING_PROFILE = "dangling-cert"
DANGLING_CERT = "corp-ca"
#: The profile whose certificate DOES resolve.
RESOLVING_PROFILE = "resolving-cert"
RESOLVING_CERT = "ops-ca"
#: The profile with no certificate at all.
PLAIN_PROFILE = "no-cert"

#: The chip's VISIBLE LABEL — the words an operator reads off the row. It is
#: the needle because it is what the ticket's criterion is about.
#:
#: WHERE IT LIVES IN THE TREE, measured rather than assumed. The chip is a
#: ``Container`` carrying a tooltip, and Flutter MERGES its child ``Text``'s
#: semantics INTO that tooltip's node — so the label is NOT a node of its own,
#: and the first run of this script (which read each card's smallest
#: name-bearing node) saw every card as its bare name and reported a false
#: negative. The merged node is chip-sized and sits inside the card's row, so
#: it is located by GEOMETRY first (``_marker_in_row``) and only then read.
MARKER = "cert not found"

#: The tooltip's opening sentence, asserted on the SAME merged node — the full
#: settled wording is meant to be one hover away, and finding it anywhere on
#: the page would prove nothing (its string propagates into every ancestor's
#: innerText, measured in PS-266). Found on a chip-sized node inside the row,
#: it is anchored where it belongs.
TOOLTIP_OPENER = "is assigned to this profile but was not found"

#: How the SABOTAGE reverts the marker: ``_unresolved_cert_chip`` restored to
#: its pre-PS-285 behaviour — nothing at all. Everything else about the build
#: is untouched, INCLUDING the flag `_refresh_profiles` computes from the
#: certificate store and passes in. That is deliberate: it isolates the RENDER,
#: so a green in this pass would mean pass 1's evidence never came from the
#: card at all.
_SABOTAGE = '''
from src.ui.components import profile_card as _pc

_pc._unresolved_cert_chip = lambda unresolved, name: []
'''


def _seed_home() -> str:
    """An isolated PERSONA_HOME holding the three profiles and a certificate
    store with ONE GENUINELY SKIPPED RECORD.

    ``certificates.json`` is written as the product writes it, plus one record
    that is not a mapping at all. The real ``CertStore._load`` raises on that
    record, logs it, skips it and carries on with the rest — the shipped
    skip-one-malformed protection, firing. So the served app reaches the state
    under test the way an operator's install reaches it: a populated store with
    exactly one name absent, and a profile still referencing that name.
    """
    home = tempfile.mkdtemp(prefix="persona-ps285-")
    os.makedirs(home, exist_ok=True)

    from src.models.profile import Profile

    profiles = {}
    for name, cert in (
        (DANGLING_PROFILE, DANGLING_CERT),
        (RESOLVING_PROFILE, RESOLVING_CERT),
        (PLAIN_PROFILE, None),
    ):
        p = Profile(name=name, proxy="", os_type="windows")
        p.certificate = cert
        profiles[name] = p.to_dict()
    with open(os.path.join(home, "profiles.json"), "w", encoding="utf-8") as fh:
        json.dump(profiles, fh)

    certs = {
        RESOLVING_CERT: {
            "name": RESOLVING_CERT,
            "p12_path": os.path.join(home, "certificates", "ops.p12"),
            "password": "",
            "url": "https://ops.invalid/login",
        },
        # SKIPPED BY THE REAL LOADER. `_load` reads each record with
        # ``d.get(...)``, so a record that is not a MAPPING raises inside the
        # per-record try/except: this one is skipped and logged, and the other
        # is kept. An unknown EXTRA key would NOT do it — `.get` simply ignores
        # it — which the premise check below caught on the first attempt at
        # this fixture, and is exactly why that check exists.
        DANGLING_CERT: "a record this build cannot read",
    }
    path = os.path.join(home, "certificates.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(certs, fh)
    os.chmod(path, 0o600)
    return home


def _verify_the_fixture_reaches_the_state(home: str) -> tuple[bool, str]:
    """Read the seeded store with the REAL loader before driving anything.

    Without this, a fixture that failed to reach the state would make the
    negative halves pass for the wrong reason — every card silent because no
    card SHOULD speak. The premise is measured, not assumed.
    """
    os.environ["PERSONA_CERTS_FILE"] = os.path.join(home, "certificates.json")
    os.environ["PERSONA_CERTS_DIR"] = os.path.join(home, "certificates")
    from src.services.cert.store import CertStore

    store = CertStore()
    names = sorted(store.names())
    ok = (
        store.get(RESOLVING_CERT) is not None
        and store.get(DANGLING_CERT) is None
    )
    return ok, (
        f"the real CertStore loaded {names} — {RESOLVING_CERT!r} resolves, "
        f"{DANGLING_CERT!r} does not"
    )


def _report(name: str, ok: bool, detail: str) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return ok


def _dismiss(drv: FletDriver) -> None:
    for label in ("Skip", "[ got it ]"):
        if drv.has_button(label):
            drv.press(label)


#: How tall a card's row is, in pixels, for the purpose of deciding which
#: nodes belong to it. Measured against the served app (rows are ~88px apart;
#: the name sits at the top of its row and the meta line ~24px below it) and
#: deliberately TIGHTER than the row pitch, so a band cannot swallow its
#: neighbour's chip and report it as its own.
_BAND_ABOVE = 20
_BAND_BELOW = 60

#: A node wider than this is an ANCESTOR (the profile list, the page), not a
#: chip. Nothing on a card's meta row comes close.
_CHIP_MAX_W = 400


def _name_node(drv: FletDriver, profile_name: str):
    """The SMALLEST node carrying this profile's name — its row's anchor.

    Smallest, deliberately: a tooltip's string propagates into every ancestor's
    innerText (measured in PS-266), so the page and the profile LIST match any
    needle at all. The smallest node bearing the name is the name Text itself,
    and its box is what locates the row.
    """
    best = None
    for n in drv.nodes():
        blob = f"{n.text or ''} {n.label or ''}"
        if profile_name not in blob:
            continue
        if best is None or (n.box[2] * n.box[3]) < (best.box[2] * best.box[3]):
            best = n
    return best


def _marker_in_row(drv: FletDriver, anchor):
    """The marker node painted INSIDE this card's row, or None.

    GEOMETRY FIRST, and it is not optional here: the chip's tooltip string
    appears verbatim in the innerText of the profile LIST and of the page, so a
    text search alone cannot tell "THIS card carries the marker" from "the
    document contains the sentence". A node qualifies only if it sits inside
    the anchor's own row band AND is chip-sized — which excludes every ancestor
    that inherited the string.
    """
    if anchor is None:
        return None
    top = anchor.box[1] - _BAND_ABOVE
    bottom = anchor.box[1] + _BAND_BELOW
    for n in drv.nodes():
        x, y, w, h = n.box
        if y < top or y + h > bottom or w > _CHIP_MAX_W:
            continue
        blob = f"{n.text or ''} {n.label or ''}"
        if MARKER in blob:
            return n
    return None


def _reads(node) -> str:
    if node is None:
        return "<node absent>"
    return f"{node.text or ''} {node.label or ''}".replace("\n", " | ").strip()


def _observe(home: str, label: str, shot: str, patch: str = "") -> dict:
    """Boot one app against ``home`` and read all three cards."""
    with serve_app(REPO_ROOT, home=home, patch=patch) as app:
        print(f"\n{label}\n  served: {app.url}\n  home:   {app.home}")
        with FletDriver(app.url, width=1400, height=900) as drv:
            _dismiss(drv)
            drv.page.wait_for_timeout(9000)
            out = {}
            for name in (DANGLING_PROFILE, RESOLVING_PROFILE, PLAIN_PROFILE):
                anchor = _name_node(drv, name)
                marker = _marker_in_row(drv, anchor)
                out[name] = {
                    "blob": _reads(marker) if marker is not None else _reads(anchor),
                    "marked": marker is not None,
                    "box": marker.box if marker is not None else (
                        anchor.box if anchor is not None else None
                    ),
                    "anchor": anchor.box if anchor is not None else None,
                }
                assert anchor is not None, (
                    f"the card for {name!r} is not on screen at all; nothing "
                    f"below would be measuring the marker"
                )
            drv.screenshot(shot)
            print(f"  screenshot: {shot}")
            for name, r in out.items():
                print(
                    f"    {name:<16} marked={r['marked']!s:<5} "
                    f"row={r['anchor']} marker={r['box']} reads {r['blob'][:90]!r}"
                )
            return out


def main() -> int:
    results: list[bool] = []

    print("=" * 74)
    print("PS-285 — the unresolvable-certificate marker, driven against the real app")
    print("=" * 74)

    home = _seed_home()
    ok, detail = _verify_the_fixture_reaches_the_state(home)
    results.append(
        _report(
            "premise — the fixture really reaches the state via the SHIPPED loader",
            ok,
            detail,
        )
    )
    if not ok:
        print("\nthe fixture did not reach the state; driving it would measure "
              "nothing. Stopping.")
        return 1

    live = _observe(
        home,
        "1. THREE STATES, ONE APP, ONE REPAINT",
        "/tmp/ps285-three-states.png",
    )

    results.append(
        _report(
            "AC1 — the card whose certificate does NOT resolve carries the marker",
            live[DANGLING_PROFILE]["marked"],
            f"{DANGLING_PROFILE} reads {live[DANGLING_PROFILE]['blob']!r} "
            f"(box={live[DANGLING_PROFILE]['box']})",
        )
    )
    results.append(
        _report(
            "AC1 — the marker NAMES the certificate",
            DANGLING_CERT in (live[DANGLING_PROFILE]["blob"] or ""),
            f"{DANGLING_PROFILE} reads {live[DANGLING_PROFILE]['blob']!r} — "
            f"without the name, the operator is sent to a page where the "
            f"missing record is by construction absent",
        )
    )
    results.append(
        _report(
            "AC1 — the full sentence is one hover away, ANCHORED on the row",
            TOOLTIP_OPENER in (live[DANGLING_PROFILE]["blob"] or ""),
            f"the chip-sized node inside {DANGLING_PROFILE}'s row carries the "
            f"tooltip as well as the label — found on the ROW, not merely "
            f"somewhere on the page",
        )
    )
    results.append(
        _report(
            "AC4 — a card whose certificate RESOLVES is silent",
            not live[RESOLVING_PROFILE]["marked"],
            f"{RESOLVING_PROFILE} reads {live[RESOLVING_PROFILE]['blob']!r}",
        )
    )
    results.append(
        _report(
            "AC4 — a card with NO certificate is silent",
            not live[PLAIN_PROFILE]["marked"],
            f"{PLAIN_PROFILE} reads {live[PLAIN_PROFILE]['blob']!r}",
        )
    )
    results.append(
        _report(
            "AC1 — the three states are mutually distinguishable ON SCREEN",
            (
                live[DANGLING_PROFILE]["marked"]
                and not live[RESOLVING_PROFILE]["marked"]
                and not live[PLAIN_PROFILE]["marked"]
            ),
            "one repaint, one code path — the only difference between these "
            "three cards is the profile's certificate",
        )
    )

    # -----------------------------------------------------------------
    # AC6 — THE FALSIFICATION. Same home, same three profiles, same
    # skipped record, same flag computed and passed in — with the RENDER
    # reverted. Pass 1's check is re-run and must go RED.
    # -----------------------------------------------------------------
    print("\n" + "-" * 74)
    print("2. FALSIFICATION (AC6) — the SAME home, served against a build whose")
    print("   _unresolved_cert_chip is reverted to its pre-PS-285 body.")
    print("   The AC1 check above is re-run and MUST FAIL here.")
    print("-" * 74)
    broken = _observe(
        home,
        "2. SABOTAGED BUILD — the marker removed from the rendered card",
        "/tmp/ps285-falsification.png",
        patch=_SABOTAGE,
    )
    results.append(
        _report(
            "AC6 — removing the chip makes the AC1 check go RED",
            not broken[DANGLING_PROFILE]["marked"],
            f"sabotaged {DANGLING_PROFILE} reads "
            f"{broken[DANGLING_PROFILE]['blob']!r} — if the marker is still "
            f"reported here, AC1's green above is worthless because it is not "
            f"reading the rendered card at all",
        )
    )

    print("\n" + "=" * 74)
    print("NOT COVERED BY DRIVING, recorded rather than smoothed over:")
    print("  * COLOUR. The chip is COLORS['warning']; Flutter paints colour to")
    print("    canvas and no colour reaches the accessibility tree. Screenshots")
    print("    are captured for a human to look at; no automated claim is made.")
    print("  * THE LAUNCH LINE (AC3) IS NOT DRIVEN, and nothing weaker stands")
    print("    in for it. Route (i) puts it in the persistent log and the NEXT")
    print("    session's Activity Log seed, so by construction it is not on")
    print("    this screen. Asserted on the real logger over the real")
    print("    _cert_session_for in the suite above.")
    print("  * The QUARANTINE route (unreadable certificates.json, every name")
    print("    absent). The SKIPPED-RECORD route is driven instead: both reach")
    print("    the same branch, and the skip route is the harder case because")
    print("    the store is otherwise healthy and populated.")
    print("=" * 74)
    ok_all = all(results)
    print(f"\n{sum(results)}/{len(results)} checks passed — "
          f"{'ALL GREEN' if ok_all else 'FAILURES ABOVE'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
