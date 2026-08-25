"""PS-159: de-confound the STOCK-chromium control arm, ONE AXIS AT A TIME.

PS-150's arm C read pixelscan under Debian's stock ``/usr/bin/chromium`` and got
``masking_detected`` / ``fingerprint_inconsistent`` — the same two verdicts the
packaged engine gets. That looks like "the host does it too", and PS-150
deliberately REFUSED to draw it, because the stock arm carried three tells the
packaged arm did not, each on its own sufficient to produce both verdicts:

1. ``automation_detected: true``  — it announces itself as automated.
2. ``timezone_from_js: Africa/Abidjan`` behind a WARSAW exit — a blatant
   geography contradiction, which is what ``timezone_spoofed: true`` reports.
3. ``webgl_renderer: SwiftShader``  — a software rasteriser, the honest
   "there is no GPU here" of a headless container.

Two runs reaching the same verdict by different routes is NOT a shared cause.
So this script removes those three tells ONE AT A TIME and records the verdicts
after EACH removal. Removing all three at once would tell you the destination
and not the road, which is why ``ALL_THREE`` is recorded as an extra arm and
never as the answer.

THE BASELINE IS RE-RUN FIRST AND IT IS GATING. PS-150's arm C was written to a
``.log`` that ``.gitignore`` swallowed, and the committed JSON is the byte-exact
block lifted from that original run's surviving output — it is NOT a re-run and
has never been reproduced. If the baseline does not reproduce here, THAT is the
finding and the de-confounding is moot.

⚠️ A STOCK READING IS NOT A READING OF PERSONA. Nothing this script produces may
be attributed to persona's product behaviour: its whole subject is a browser
that is not persona. ``chromium_tier._engine_binary`` REFUSES to substitute a
chromium found on PATH precisely so a stock reading can never be mistaken for
the product; that refusal is overridden here deliberately, in a script whose
only purpose is the control arm, and never in the tier itself.

Run it from the repo root:

    .venv/bin/python -m scripts.ps159_deconfound_stock -o readings/ps159-.../
"""

from __future__ import annotations

import argparse
import json
import os
import time

STOCK = "/usr/bin/chromium"
PIXELSCAN = "pixelscan.net"

# The three confounds, as launch-surface changes. Each is applied ALONE.
#
# WHY EACH IS SHAPED THE WAY IT IS — measured, not guessed:
#
# `automation`: stock chromium is driven here over CDP with
#   --remote-debugging-port, which is itself an automation tell, and Blink
#   exposes navigator.webdriver for it. --disable-blink-features=
#   AutomationControlled is the standard suppression. It cannot remove the CDP
#   channel itself (the tier's only way to read a page), so this axis is
#   "suppress what can be suppressed from the command line" and the record says
#   exactly that rather than claiming automation was eliminated.
#
# `timezone`: STOCK IGNORES --timezone. That flag is a fingerprint-chromium
#   patch and stock Chromium has no such switch, which is precisely WHY the
#   baseline read Africa/Abidjan (ICU's canonical UTC+0 zone) behind a Warsaw
#   exit while the harness believed it had pinned the zone. So this axis moves
#   the TZ ENVIRONMENT VARIABLE, which stock DOES honour. Passing --timezone
#   here and calling the axis moved would be a null instrument.
#
# `renderer`: the tier forces --use-gl=angle --use-angle=swiftshader
#   --enable-unsafe-swiftshader on Linux, so the software rasteriser is partly
#   the HARNESS'S doing and not only the host's. This axis strips that trio and
#   lets chromium choose. This host has NO /dev/dri and no GPU, so the axis may
#   be immovable — that is a legitimate recordable outcome (the ticket names it
#   explicitly) and is reported as such rather than worked around.
AXES = ("automation", "timezone", "renderer")


def _pixelscan_checker():
    from src.services.verify.checkers import BROWSER_CHECKERS

    for c in BROWSER_CHECKERS:
        if c.id == PIXELSCAN:
            return c
    raise SystemExit(f"{PIXELSCAN} is not in BROWSER_CHECKERS")


def _read_under(
    binary: str,
    *,
    proxy_url: str,
    timezone: str,
    axes: "tuple[str, ...]",
):
    """Read pixelscan under ``binary`` with ``axes`` de-confounded.

    Returns ``(rows, argv, tz_env)`` — the argv and the TZ actually used are
    returned so the record can state the SURFACE THAT WAS PRESENTED rather than
    the one that was requested. That is the PS-103 discipline the tier already
    applies to --no-sandbox, and the reason it matters here is sharper: an axis
    that silently failed to apply would otherwise read as "removing it changed
    nothing", which is the exact wrong conclusion.
    """
    from src.services.verify import chromium_tier
    from src.services.verify.browser_tier import readings_from_texts

    checker = _pixelscan_checker()

    original_binary = chromium_tier._engine_binary
    original_args = chromium_tier._launch_args
    original_tz = os.environ.get("TZ")

    captured: dict = {}

    def _patched_args(*a, **kw):
        args = original_args(*a, **kw)

        if "automation" in axes:
            # Suppress the automation tell. Inserted after the AppImage
            # runtime's first argument is irrelevant for stock (not an
            # AppImage), but position is kept late and before the start URL.
            args.insert(-1, "--disable-blink-features=AutomationControlled")

        if "renderer" in axes:
            # Strip the harness's forced software rasteriser and let chromium
            # choose. On a host with no GPU this is expected to change little;
            # what it removes is the part of SwiftShader that was OURS.
            args = [
                x
                for x in args
                if x
                not in (
                    "--use-gl=angle",
                    "--use-angle=swiftshader",
                    "--enable-unsafe-swiftshader",
                )
            ]

        captured["argv"] = list(args)
        # Captured HERE, at launch, not after the finally-block restores the
        # ambient value — otherwise every arm records `tz_env: null` and the
        # timezone arm reads as though its axis never applied, which is exactly
        # the wrong conclusion this record exists to prevent.
        captured["tz_env"] = os.environ.get("TZ")
        return args

    chromium_tier._engine_binary = lambda: binary
    chromium_tier._launch_args = _patched_args

    # Axis 2 rides the ENVIRONMENT, because stock has no --timezone flag.
    # Set before the session is constructed: the tier snapshots os.environ when
    # it spawns the browser.
    if "timezone" in axes:
        os.environ["TZ"] = timezone
        time.tzset()
    else:
        # The baseline's own condition, stated rather than inherited: the
        # container clock is UTC, which is what produced Africa/Abidjan.
        os.environ.pop("TZ", None)
        time.tzset()

    try:
        session = chromium_tier.ChromiumSession(
            proxy_url,
            seed=9001,
            declared_machine="windows",
            # Passed for parity with PS-150's arm C. Stock IGNORES it — that is
            # the documented point of axis 2, not an oversight.
            timezone=timezone,
            allow_unsandboxed=True,
            install_layer=False,
        )
        with session as live:
            page = live.new_page()
            page.goto(checker.url, timeout=90000, wait_until="load")
            time.sleep(checker.settle_seconds)
            text = page.inner_text("body")
    finally:
        chromium_tier._engine_binary = original_binary
        chromium_tier._launch_args = original_args
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        time.tzset()

    rows = readings_from_texts({checker.id: {"text": text}}, checkers=(checker,))
    return rows, captured.get("argv", []), captured.get("tz_env")


def _verdicts(rows):
    out = {}
    for r in rows:
        d = r.as_record() if hasattr(r, "as_record") else dict(r)
        out[d.get("item")] = d.get("value")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="", help="directory to write the record into")
    ap.add_argument(
        "--arms",
        default="baseline," + ",".join(AXES) + ",all_three",
        help="comma-separated arms to run",
    )
    opts = ap.parse_args()

    from src.services.verify.exit_guard import prove_exit

    # MANDATORY and there is no fallback. A checker reading over a direct
    # connection hands the operator's real address to every service in the
    # matrix, so a refusal here stops the run rather than degrading it.
    proxy_url, exit_, cred = prove_exit()
    print(f"exit proven: {exit_.ip} {exit_.city}/{exit_.country} {exit_.timezone}")
    print(f"credential source: {cred.source}")
    print()

    plan = [a.strip() for a in opts.arms.split(",") if a.strip()]

    # arm name -> the axes removed in it. baseline removes NOTHING and is run
    # FIRST, because if it does not reproduce the rest of the ticket is moot.
    arm_axes = {
        "baseline": (),
        "automation": ("automation",),
        "timezone": ("timezone",),
        "renderer": ("renderer",),
        # EVERYTHING REMOVABLE REMOVED, and the immovable axis left ALONE.
        #
        # This is the strongest honest arm on this host, and it exists because
        # `all_three` is NOT it. Stripping the forced rasteriser does not yield
        # a hardware renderer here (there is no GPU) — it yields NO WebGL at
        # all, which is a DIFFERENT anomaly rather than a removed one. So
        # `all_three` swaps one confound for another and cannot settle
        # anything. This arm removes the two confounds that genuinely CAN be
        # removed and leaves the software rasteriser exactly as the baseline
        # had it, so the only difference from baseline is the two clean axes.
        "removable_only": ("automation", "timezone"),
        "all_three": AXES,
    }

    record: dict = {
        "ticket": "PS-159",
        "subject": (
            "STOCK chromium (/usr/bin/chromium) — NOT persona. Nothing in this "
            "record may be attributed to persona's product behaviour."
        ),
        "exit": exit_.as_record(),
        "credential_source": cred.source,
        "waivers": {
            "allow_unsandboxed_chromium": (
                "REQUIRED on every arm — this host forbids the unprivileged "
                "user namespace. Not the product's default surface; disclosed "
                "rather than left to pass silently."
            )
        },
        "host": {
            "dev_dri_present": os.path.exists("/dev/dri"),
            "stock_chromium_version": None,
        },
        "arms": {},
    }

    try:
        import subprocess

        record["host"]["stock_chromium_version"] = subprocess.run(
            [STOCK, "--version"], capture_output=True, text=True, timeout=30
        ).stdout.strip()
    except Exception as exc:  # pragma: no cover - provenance only
        record["host"]["stock_chromium_version"] = f"unavailable: {exc}"

    out_path = ""
    if opts.out:
        os.makedirs(opts.out, exist_ok=True)
        out_path = os.path.join(opts.out, "stock-deconfound.json")
        # MERGE rather than clobber. Each arm is a separate live read of a
        # 60-second-settle page, so arms are run incrementally; a run that
        # rewrote the file from scratch would silently discard the arms already
        # completed — and an arm silently missing from a per-axis record is the
        # one defect this method cannot tolerate. Prior arms are preserved and
        # a re-run of the SAME arm overwrites only that arm.
        if os.path.exists(out_path):
            with open(out_path) as fh:
                prior = json.load(fh)
            record["arms"] = prior.get("arms", {})
            record["prior_exits"] = prior.get("prior_exits", [])
            if prior.get("exit", {}).get("ip") != exit_.ip:
                # The exit ROTATES by design (PS-10). Arms taken behind
                # different exits are still comparable on FINGERPRINT-sorted
                # rows but not on EXIT-sorted ones, so every exit this record
                # was built across is kept rather than overwritten.
                record["prior_exits"] = record["prior_exits"] + [prior["exit"]]

    for arm in plan:
        axes = arm_axes[arm]
        label = "NOTHING REMOVED (reproduce the premise)" if not axes else \
            "removed: " + ", ".join(axes)
        print(f"--- arm {arm!r}: {label}")
        entry: dict = {"axes_removed": list(axes)}
        try:
            rows, argv, tz_env = _read_under(
                STOCK, proxy_url=proxy_url, timezone=exit_.timezone, axes=axes
            )
            entry["verdicts"] = _verdicts(rows)
            entry["tz_env"] = tz_env
            # The surface that was PRESENTED, read back off argv.
            entry["applied"] = {
                "automation_suppressed": any(
                    "AutomationControlled" in x for x in argv
                ),
                "swiftshader_forced": any("swiftshader" in x for x in argv),
                "no_sandbox": "--no-sandbox" in argv,
            }
            for k, v in entry["verdicts"].items():
                print(f"    {k:28s} {json.dumps(v)}")
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
            print(f"    ERROR {entry['error']}")

        record["arms"][arm] = entry

        # Written after EVERY arm, so a run that dies part-way leaves the arms
        # it did complete rather than nothing.
        if out_path:
            with open(out_path, "w") as fh:
                json.dump(record, fh, indent=2)
            print(f"    (record updated: {out_path})")
        print()

    print(json.dumps(record["arms"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
