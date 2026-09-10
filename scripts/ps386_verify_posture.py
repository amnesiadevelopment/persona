#!/usr/bin/env python3
"""Measure the notarization posture of a LOCAL `.app` — with PS-346's readers.

PS-386 outcomes 2 and 3. This is the instrument that answers "did the bytes
actually change?", which the hardener structurally cannot: `codesign` exiting 0
says the tool accepted its arguments, not that the produced CodeDirectory
carries `CS_RUNTIME` and not that the entitlements blob lost `get-task-allow`.
Those are questions about bytes, and this reads the bytes.

⛔ THIS IS A CALLER, NOT A SECOND READER — AND THAT DISTINCTION IS THE WHOLE
DESIGN. PS-386 forbids modifying `scripts/ps346_signing_state.py`'s readers,
because that script is the CONTROL for this change and was hardened through five
rework rounds specifically against false-CLEAN readings (its own history records
a *signature* slot misread as a notarization ticket, and a `_CodeSignature`
directory nearly read as "signed"). It also forbids hand-rolling a second
reader, for the obvious reason: a fresh parser written by the same person making
the change is a parser that agrees with the change.

So this file imports `macho_slices`, `read_macho_slice` and
`entitlement_get_task_allow` from that module BY PATH and uses them unmodified.
It adds a driver — walking a local directory instead of downloading published
release assets — and a verdict. It changes no reading.

⭐ WHY A DRIVER IS NEEDED AT ALL. The control in its committed form takes
`APP_TAG`/`APP_ASSETS` and `gh release download`s the PUBLISHED v3.1.1 assets.
Run unchanged it re-measures the OLD published bundle, which is the correct
"before" side and is NOT a reading of what was just built. Pointing it at a
local path would mean editing its readers, which is forbidden. Adding a caller
is explicitly allowed, and this is that caller.

WHAT "OUR SLICES" MEANS, AND WHY THE TARGET IS NOT 225/225
-----------------------------------------------------------
PS-386's honest target is "every slice we build", never a bundle-wide
`225/225` — which would misreport vendored third-party code we did not sign and
did not produce. A slice carrying a real CMS payload was signed by somebody with
an actual certificate (Node.js Foundation's `node` is the known one in this
bundle), and we neither touch it nor count it against ourselves. So:

    OURS   = slices with no CMS payload (ad-hoc or unsigned)
    THEIRS = slices with a CMS payload  (a real identity)

and the verdict is computed over OURS alone, with THEIRS reported by name so the
split is auditable rather than asserted.

THE THREE-VALUED ENTITLEMENT READING IS PRESERVED, NOT COLLAPSED
-----------------------------------------------------------------
`entitlement_get_task_allow` returns True / False / None on purpose, and None
means "there was no entitlements blob, or it could not be parsed" — which is
NOT the claim that the entitlement is absent. This driver keeps those three
apart. A slice with no entitlements blob at all cannot carry `get-task-allow`
and is not a failure; a slice whose blob could not be PARSED is reported as
UNREADABLE and fails the gate, because an unread blob is not a clean one.

Exit codes:
    0  every slice we build carries the hardened runtime and none carries
       get-task-allow=true
    1  at least one slice we build is missing the runtime flag, or carries
       get-task-allow=true, or has an unreadable entitlements blob
    2  the environment cannot do this (bad path, control not importable)

⚠️ EXIT 2 IS NOT A PASS. It means the question was not asked.
⛔ A PASS HERE IS NOT OUTCOME 4. It says the bytes carry the posture; it says
   nothing about whether the app launches or spawns an engine.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path


def _pin_stdio_to_utf8() -> None:
    """Make this script's own output survive a non-UTF-8 console.

    ⚠️ NOT COSMETIC. Every refusal and every "this is NOT a pass" line here
    carries a ⚠️ or a ⛔, and on Windows `sys.stderr` resolves to the ANSI code
    page (cp1252), which cannot encode either — so the write raises inside the
    refusal path and the reason never reaches the log. Same fix, same reason, as
    the hardener's; `errors="replace"` degrades rather than raising.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):  # a substituted stream in a test
                pass


CONTROL = Path(__file__).resolve().parent / "ps346_signing_state.py"

MACHO_MAGICS = {
    b"\xcf\xfa\xed\xfe",
    b"\xce\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
}


def load_control():
    """Import PS-346's instrument by path and use its readers unmodified.

    By path rather than by package import because `scripts/` is not a package
    and this must work from any cwd. The module is imported for its READERS
    only; its `main()` (which downloads published assets) is never called.
    """
    spec = importlib.util.spec_from_file_location("ps346_signing_state", CONTROL)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load the control at {CONTROL}")
    module = importlib.util.module_from_spec(spec)
    # ⚠️ REGISTERED BEFORE EXECUTION, AND THAT IS NOT BOILERPLATE. The control
    # defines `@dataclass` types, and dataclasses resolves a field annotation by
    # looking the defining class's module up in `sys.modules`. A module executed
    # out of `sys.modules` therefore raises AttributeError on a NoneType at
    # class-definition time — i.e. the control would be unimportable here, and
    # the "use the audited reader" rule would quietly become unimplementable.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    for symbol in ("macho_slices", "read_macho_slice", "entitlement_get_task_allow"):
        if not hasattr(module, symbol):
            raise ImportError(
                f"{CONTROL} has no {symbol!r} — the control's readers changed, "
                "so this caller's readings would no longer be the control's"
            )
    return module


def is_macho(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(4) in MACHO_MAGICS
    except OSError:
        return False


def read_app(app: Path, control) -> list[dict]:
    """One row per architecture slice of every Mach-O file in the bundle.

    Every signing fact on a row comes from `control.read_macho_slice`; the only
    thing added here is the file path it came from.
    """
    rows: list[dict] = []
    for path in sorted(app.rglob("*")):
        if path.is_symlink() or not path.is_file() or not is_macho(path):
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            rows.append(
                {
                    "path": str(path.relative_to(app)),
                    "state": "UNREADABLE",
                    "detail": f"{type(exc).__name__}",
                    "hardened_runtime": None,
                    "get_task_allow": None,
                }
            )
            continue
        for base in control.macho_slices(data):
            try:
                info = control.read_macho_slice(data, base)
            except Exception as exc:  # a malformed slice is UNREADABLE, not clean
                rows.append(
                    {
                        "path": str(path.relative_to(app)),
                        "state": "UNREADABLE",
                        "detail": f"{type(exc).__name__}",
                        "hardened_runtime": None,
                        "get_task_allow": None,
                    }
                )
                continue
            rows.append(
                {
                    "path": str(path.relative_to(app)),
                    "state": info.get("state"),
                    "detail": info.get("detail", ""),
                    "identity": info.get("identity"),
                    "hardened_runtime": info.get("hardened_runtime"),
                    "get_task_allow": control.entitlement_get_task_allow(
                        info.get("entitlements")
                    ),
                    "has_entitlements": info.get("entitlements") is not None,
                }
            )
    return rows


def split_ours(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """OURS vs THEIRS, on the CMS payload — never on a path heuristic.

    A name-based split ("anything under playwright/ is theirs") would be a guess
    that silently mis-scopes the verdict the moment the bundle's layout moves.
    The presence of a CMS payload is the fact itself: it is what a real identity
    leaves behind, and it is what the control already reports as SIGNED_CMS.
    """
    theirs = [r for r in rows if r.get("state") == "SIGNED_CMS"]
    ours = [r for r in rows if r.get("state") != "SIGNED_CMS"]
    return ours, theirs


def verdict(ours: list[dict]) -> tuple[bool, list[str]]:
    """Did every slice WE build reach the posture? Returns (ok, reasons)."""
    reasons: list[str] = []

    # ⛔ ZERO OF OUR SLICES JUDGED IS NOT A PASS. Every other check below is a
    # "no offenders found" test, and over an empty list every one of them is
    # vacuously satisfied — so a bundle whose Mach-Os are ALL third-party would
    # print `0/0` and exit 0, which reads as success and measured nothing. That
    # is the exact shape this ticket's own AC forbids: "a guard only ever
    # observed passing is indistinguishable from a broken one." It is
    # unreachable for persona today (113 files, exactly one SIGNED_CMS), and it
    # is refused anyway, because the reason it is unreachable is a fact about
    # the bundle rather than about this function.
    if not ours:
        return False, [
            "0 slices were judged as ours — every Mach-O read carried a "
            "third-party CMS signature. That is not a pass: the posture was "
            "never measured on anything we build."
        ]

    unreadable = [r for r in ours if r["state"] == "UNREADABLE"]
    if unreadable:
        reasons.append(
            f"{len(unreadable)} slice(s) UNREADABLE — an unread slice is not a clean "
            f"one (e.g. {unreadable[0]['path']})"
        )

    not_hardened = [
        r for r in ours if r["state"] != "UNREADABLE" and not r.get("hardened_runtime")
    ]
    if not_hardened:
        reasons.append(
            f"{len(not_hardened)}/{len(ours)} of our slices do NOT carry the hardened "
            f"runtime (e.g. {not_hardened[0]['path']})"
        )

    debuggable = [r for r in ours if r.get("get_task_allow") is True]
    if debuggable:
        reasons.append(
            f"{len(debuggable)} slice(s) still carry get-task-allow=true "
            f"(e.g. {debuggable[0]['path']})"
        )

    # None means "no entitlements blob, or unparseable". A slice with NO blob is
    # fine — most loose dylibs have none by design. A slice WITH a blob we could
    # not parse is not fine, and the control keeps those two apart for us.
    unparsed = [
        r
        for r in ours
        if r.get("has_entitlements") and r.get("get_task_allow") is None
    ]
    if unparsed:
        reasons.append(
            f"{len(unparsed)} slice(s) carry an entitlements blob that could not be "
            f"parsed — NOT the claim that the entitlement is absent "
            f"(e.g. {unparsed[0]['path']})"
        )

    return (not reasons), reasons


def main() -> int:
    _pin_stdio_to_utf8()
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--app", required=True, help="path to the .app bundle to read")
    ap.add_argument("--json", action="store_true", help="emit the rows as JSON")
    args = ap.parse_args()

    app = Path(args.app)
    if not app.is_dir():
        print(f"CANNOT RUN: not a directory: {app}", file=sys.stderr)
        print("⚠️  This is NOT a pass — the question was not asked.", file=sys.stderr)
        return 2

    try:
        control = load_control()
    except ImportError as exc:
        print(f"CANNOT RUN: {exc}", file=sys.stderr)
        print("⚠️  This is NOT a pass — the question was not asked.", file=sys.stderr)
        return 2

    rows = read_app(app, control)
    if not rows:
        print(f"CANNOT RUN: no Mach-O slices found under {app}", file=sys.stderr)
        print("⚠️  This is NOT a pass — nothing was read.", file=sys.stderr)
        return 2

    ours, theirs = split_ours(rows)

    if args.json:
        print(json.dumps({"ours": ours, "theirs": theirs}, indent=2))

    hardened = sum(1 for r in ours if r.get("hardened_runtime"))
    print("=" * 78)
    print("PS-386 — notarization posture of a LOCAL build")
    print("=" * 78)
    print(f"app: {app}")
    print(f"slices read: {len(rows)}   ours: {len(ours)}   third-party: {len(theirs)}")
    print(f"hardened runtime, OUR slices: {hardened}/{len(ours)}")
    print(
        "  ⚠️  read against OUR slices, never bundle-wide — a 'N/N of everything'\n"
        "      figure would credit us with vendored code we did not sign."
    )

    if theirs:
        print("\nthird-party slices left alone (a real CMS identity):")
        for r in theirs[:10]:
            print(f"  - {r['path']}")
            if r.get("identity"):
                print(f"      {str(r['identity'])[:120]}")

    ok, reasons = verdict(ours)

    # ── POSITIVE CONTROL ─────────────────────────────────────────────────────
    # Inherited from the instrument this calls, and for the same reason: a run
    # that reports a clean posture is worth nothing unless the reader is known
    # to be capable of reporting the opposite. The Node.js Foundation signature
    # inside this very bundle is the known real one. If it is not found, the
    # finding below is about THIS READER, not about the artifact.
    print("-" * 78)
    if theirs:
        print(f"POSITIVE CONTROL: FIRED — {len(theirs)} genuinely-signed slice(s) found.")
        print("The reader can tell a real identity from an ad-hoc one.")
    else:
        print(
            "POSITIVE CONTROL: DID NOT FIRE — no CMS-signed slice found anywhere,\n"
            "including the vendored binaries known to carry one. Distrust this run:\n"
            "the reader is more likely broken than the bundle uniformly ad-hoc."
        )

    print("-" * 78)
    if ok:
        print("PASS: every slice we build carries the hardened runtime, and none")
        print("      carries get-task-allow=true.")
        print(
            "\n⛔ THIS IS NOT A SIGNED OR NOTARIZED ARTIFACT. The signature is still\n"
            "   AD-HOC and carries no certificate. Notarization needs an Apple\n"
            "   Developer Program membership under a chosen legal identity — a\n"
            "   purchase decision reserved to the owner, and out of scope here.\n"
            "   What this says is that the two purchase-independent rejection\n"
            "   conditions are gone from the bytes.\n"
            "\n⛔ AND IT IS NOT OUTCOME 4. It says nothing about whether the app\n"
            "   launches or spawns an engine."
        )
        return 0

    print("FAIL: the posture was not reached.")
    for reason in reasons:
        print(f"  - {reason}")
    print(
        "\n⛔ DO NOT 'FIX' THIS BY WEAKENING THE POSTURE. If the app cannot run\n"
        "   hardened, that is a genuine delivered outcome and belongs in a\n"
        "   report, not in a relaxed checker."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
