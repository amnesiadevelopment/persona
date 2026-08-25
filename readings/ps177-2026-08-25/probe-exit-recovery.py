#!/usr/bin/env python3
"""PS-177 — the exit-recovery instrument, and the verifier for the figures §2 quotes.

WHY THIS FILE EXISTS. Round 2 of this ticket committed `exit-recovery-probe.log`
and wrote, in EVIDENCE.md, "Everything in §2 can be checked against them." The
reviewer checked, and three figures disagreed with the log: the run was reported
as "20 recovery probes over 38 minutes ... logged one per minute" when the log
holds 20 probes over 24.8 minutes at a ~78s cadence. The numbers were typed from
a terminal scrollback rather than derived from the artifact.

So this file does two separate jobs, and the split matters:

  --verify    RE-DERIVES every figure §2 quotes FROM the committed log, and
              exits non-zero if the document and the log disagree. This is the
              job that makes "can be checked against them" a true sentence
              instead of an assertion. It is offline, deterministic, and needs
              no proxy — anyone can run it, today or in a year.

  --variants  RE-RUNS the live credential probes of §2 measurements 1-3
              (outside-the-harness / direct-egress / three session variants).
              ⚠️ THIS IS NOT WHAT PRODUCED THOSE READINGS. It was written after
              the fact, in round 3. Those three measurements were taken ad hoc
              at a terminal, no artifact was captured, and none is committed.
              This makes them RE-RUNNABLE GOING FORWARD; it does not
              retroactively evidence them, and §2 marks them ⚠️ accordingly.

PS-14 (check the instrument before attributing anything to the product) is the
reason --verify is the default: the cheapest way to stop a document drifting
from its own evidence is to make the drift fail a command.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROBE_LOG = HERE / "exit-recovery-probe.log"
SWEEP_LOG = HERE / "sweep.log"
EVIDENCE = HERE / "EVIDENCE.md"
RECORD = HERE / "reading.firefox.windows.seed5150.json"

# The credential is operator-owned and lives OUTSIDE the repo. It is never read
# by --verify, and it is never printed by --variants.
CREDENTIAL_PATH = "/workspace/_secrets/test-proxy.txt"


# ---------------------------------------------------------------- derivation


def parse_probe_log(path: Path = PROBE_LOG) -> list[tuple[datetime, str]]:
    """Parse the recovery log into (timestamp, outcome) rows.

    The log is one line per probe: ``HH:MM:SS FAIL <error>``. Times carry no
    date; the run did not cross midnight, so a bare time is unambiguous.
    """
    rows: list[tuple[datetime, str]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(\d{2}:\d{2}:\d{2})\s+(\S+)\s*(.*)$", line)
        if not m:
            raise ValueError(f"unparseable probe row: {line!r}")
        stamp = datetime.strptime(m.group(1), "%H:%M:%S")
        rows.append((stamp, m.group(2)))
    return rows


def derive_figures() -> dict:
    """Every number §2 quotes about the recovery run, derived from the log."""
    rows = parse_probe_log()
    if not rows:
        raise ValueError("recovery log is empty — nothing to derive")

    stamps = [r[0] for r in rows]
    outcomes = [r[1] for r in rows]
    gaps = [
        (stamps[i + 1] - stamps[i]).total_seconds() for i in range(len(stamps) - 1)
    ]
    span = (stamps[-1] - stamps[0]).total_seconds()
    text = PROBE_LOG.read_text()

    return {
        "probes": len(rows),
        "first": stamps[0].strftime("%H:%M:%S"),
        "last": stamps[-1].strftime("%H:%M:%S"),
        "span_seconds": span,
        "span_minutes": round(span / 60, 1),
        "mean_gap_seconds": round(sum(gaps) / len(gaps), 1) if gaps else 0.0,
        "min_gap_seconds": min(gaps) if gaps else 0.0,
        "max_gap_seconds": max(gaps) if gaps else 0.0,
        "successes": sum(1 for o in outcomes if o != "FAIL"),
        "failures": sum(1 for o in outcomes if o == "FAIL"),
        "socks5_auth_failures": text.count("SOCKS5 authentication failed"),
        # sweep.log's independent corroboration, and its LIMIT as a source.
        "sweep_refusals": SWEEP_LOG.read_text().count("REFUSED:"),
        "sweep_timestamp_lines": len(
            re.findall(r"\d{2}:\d{2}", SWEEP_LOG.read_text())
        ),
        # The ONE clock time in §2 that has a committed source, and where it is.
        "record_observed_at": _record_observed_at(),
    }


def _record_observed_at() -> str | None:
    m = re.search(r'"observed_at"\s*:\s*"([^"]+)"', RECORD.read_text())
    return m.group(1) if m else None


# ------------------------------------------------------------------- verify


def verify() -> int:
    """Assert EVIDENCE.md §2 agrees with the committed artifacts.

    Each check is a claim the DOCUMENT makes, re-derived from the LOG. A
    failure here means the prose drifted from its evidence — which is exactly
    the defect round 3 was opened to fix, so it must fail loudly.
    """
    f = derive_figures()
    doc = EVIDENCE.read_text()
    checks: list[tuple[str, bool, str]] = []

    def check(label: str, ok: bool, detail: str) -> None:
        checks.append((label, ok, detail))

    # --- the figures themselves -------------------------------------------
    check(
        "20 probes in the log",
        f["probes"] == 20,
        f"derived {f['probes']}",
    )
    check(
        "every probe failed (zero successes)",
        f["successes"] == 0 and f["failures"] == f["probes"],
        f"{f['failures']} FAIL / {f['successes']} other",
    )
    check(
        "every failure is SOCKS5 auth",
        f["socks5_auth_failures"] == f["probes"],
        f"{f['socks5_auth_failures']} of {f['probes']}",
    )
    check(
        "span is 24.8 minutes",
        f["span_minutes"] == 24.8,
        f"derived {f['span_minutes']} min ({f['span_seconds']}s)",
    )
    check(
        "window is 22:01:07 - 22:25:55",
        (f["first"], f["last"]) == ("22:01:07", "22:25:55"),
        f"derived {f['first']} - {f['last']}",
    )
    check(
        "mean cadence is 78s (not 60s)",
        77.0 <= f["mean_gap_seconds"] <= 79.0,
        f"derived {f['mean_gap_seconds']}s "
        f"(min {f['min_gap_seconds']}, max {f['max_gap_seconds']})",
    )

    # --- the document quotes those figures, and not the withdrawn ones ----
    check(
        "EVIDENCE.md quotes 24.8 minutes",
        "24.8 minutes" in doc,
        "the derived span must appear in the prose",
    )
    check(
        "EVIDENCE.md quotes the real window",
        "22:01:07" in doc and "22:25:55" in doc,
        "the derived window must appear in the prose",
    )
    check(
        "EVIDENCE.md quotes the 78s cadence",
        "78s" in doc,
        "the derived cadence must appear in the prose",
    )

    # The round-2 figures must NOT come back AS CLAIMS. They are still quoted,
    # deliberately, inside the withdrawal notes — a correction that deletes the
    # wrong number leaves a reader unable to tell a fixed document from one that
    # never had the defect. So the guard is scoped to the PROSE: blockquote
    # lines (`>`) are the withdrawal record and are excluded, and each stale
    # figure must appear THERE and nowhere else.
    prose = "\n".join(
        ln for ln in doc.splitlines() if not ln.lstrip().startswith(">")
    )
    withdrawal = "\n".join(
        ln for ln in doc.splitlines() if ln.lstrip().startswith(">")
    )
    # A blockquote-stripped comparison is only meaningful if the withdrawal
    # notes actually survived the strip.
    check(
        "withdrawal notes are present to scope the guards against",
        len(withdrawal.splitlines()) >= 10 and "Withdrawn in round 3" in doc,
        f"{len(withdrawal.splitlines())} blockquote lines",
    )
    for stale, why in [
        ("38 minutes", "contradicted by the log (24.8)"),
        ("one per minute", "contradicted by the log (~78s)"),
        ("21:49–22:29", "contradicted by the log (22:01:07-22:25:55)"),
        ("96 seconds", "rested on a stamp with no committed source"),
    ]:
        check(
            f"withdrawn as a claim: {stale!r}",
            stale not in prose,
            why,
        )
        check(
            f"...but still recorded as withdrawn: {stale!r}",
            stale in withdrawal,
            "a correction must show what it corrected",
        )

    # --- provenance claims -------------------------------------------------
    check(
        "sweep.log is asserted to carry no timestamps, and carries none",
        f["sweep_timestamp_lines"] == 0,
        f"found {f['sweep_timestamp_lines']} timestamp-shaped matches",
    )
    check(
        "sweep.log independently corroborates 7 refusals",
        f["sweep_refusals"] == 7,
        f"derived {f['sweep_refusals']}",
    )
    check(
        "the one committed clock time is the record's observed_at",
        f["record_observed_at"] == "2026-08-25T21:48:53Z",
        f"derived {f['record_observed_at']!r}",
    )
    check(
        "the ad hoc measurements are marked unevidenced",
        doc.count("ad hoc") >= 3,
        "measurements 1-3 must each say so",
    )
    # The false sentence that triggered round 3 must not be restored.
    check(
        "the round-2 blanket claim is not restored",
        "Everything in §2 can be checked against them." not in doc,
        "that sentence was false; §2 now names a source per row instead",
    )

    width = max(len(c[0]) for c in checks)
    failed = 0
    for label, ok, detail in checks:
        mark = "ok  " if ok else "FAIL"
        if not ok:
            failed += 1
        print(f"  [{mark}] {label.ljust(width)}   {detail}")

    print()
    print(f"  {len(checks) - failed} passed, {failed} failed")
    if failed:
        print()
        print("  EVIDENCE.md §2 disagrees with its own committed artifacts.")
        print("  Fix the PROSE to match the LOG — never the other way round.")
    return 1 if failed else 0


def show() -> int:
    f = derive_figures()
    print("PS-177 — figures re-derived from the committed artifacts")
    print()
    for k, v in f.items():
        print(f"  {k:24} {v}")
    return 0


# ----------------------------------------------------------------- variants


def variants() -> int:
    """Re-run the live credential probes of §2 measurements 1-3.

    ⚠️ Written in round 3, AFTER those measurements were taken. It does not
    evidence them retroactively — it makes the same probes re-runnable.
    """
    try:
        import requests
    except ImportError:
        print("requests is not importable in this interpreter (PS-14: the "
              "instrument, not the product)", file=sys.stderr)
        return 1

    cred_file = Path(CREDENTIAL_PATH)
    if not cred_file.exists():
        print(f"credential file absent: {CREDENTIAL_PATH}", file=sys.stderr)
        print("This probe needs the operator's credential and cannot run "
              "without it.", file=sys.stderr)
        return 1

    raw = cred_file.read_text().strip()

    def redact(url: str) -> str:
        return re.sub(r"//[^@]+@", "//<redacted>@", url)

    # measurement 3: as-stored, rotated session token, session stripped.
    rotated = re.sub(r"(session-)[A-Za-z0-9]+", r"\g<1>" + "r" * 8, raw)
    stripped = re.sub(r"-session-[A-Za-z0-9]+", "", raw)
    stripped = re.sub(r"-sessionduration-\d+", "", stripped)

    print("measurement 1+3 — the credential, three variants, through the proxy")
    proxied_ok = 0
    for label, cred in [
        ("as-stored", raw),
        ("rotated-session", rotated),
        ("session-stripped", stripped),
    ]:
        try:
            r = requests.get(
                "https://ipwho.is/",
                proxies={"http": cred, "https": cred},
                timeout=30,
            )
            print(f"  [ok  ] {label:18} -> {r.json().get('ip')}")
            proxied_ok += 1
        except Exception as exc:  # noqa: BLE001 - the failure IS the reading
            print(f"  [FAIL] {label:18} -> {type(exc).__name__}: "
                  f"{redact(str(exc))[:110]}")

    print()
    print("measurement 2 — direct egress, no proxy (is the NETWORK up?)")
    try:
        r = requests.get("https://ipwho.is/", timeout=30)
        print(f"  [ok  ] direct             -> {r.json().get('ip')}")
        direct_ok = True
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] direct             -> {type(exc).__name__}")
        direct_ok = False

    print()
    if proxied_ok == 0 and direct_ok:
        print("  Same shape as the round-2 reading: every proxied variant "
              "fails while direct egress works.")
        print("  => account-level credential failure, operator-owned. Not the "
              "network, not persona.")
    elif proxied_ok:
        print("  The credential is working again — the account-level failure "
              "of 2026-08-25 has been resolved.")
        print("  RE-RUN THE SWEEP: ./take-sweep.sh <output-directory>")
    else:
        print("  Direct egress is ALSO down. This says nothing about the "
              "credential — fix the network first (PS-14).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--verify", action="store_true",
                   help="check EVIDENCE.md §2 against the committed logs "
                        "(default; offline, no credential needed)")
    g.add_argument("--show", action="store_true",
                   help="print every figure re-derived from the artifacts")
    g.add_argument("--variants", action="store_true",
                   help="⚠️ LIVE: re-run the §2 credential probes (needs the "
                        "operator credential; did NOT produce the round-2 "
                        "readings)")
    args = ap.parse_args()

    if args.show:
        return show()
    if args.variants:
        return variants()
    return verify()


if __name__ == "__main__":
    raise SystemExit(main())
