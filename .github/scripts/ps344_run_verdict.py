#!/usr/bin/env python3
"""Run the PS-344 published-engine verdict end to end, from a plan.

WHY THIS EXISTS (PS-370)
─────────────────────────
``.github/scripts/ps344_gate_plan.py`` decides WHAT to measure. This runs it:
downloads both binaries, stages them the way PS-344's REPORT records, launches
the three arms through the ONE instrument, and runs the judge twice — once on
the product arm (which must come back LIVE) and once on the falsification arm
(which must come back ABSENT).

⭐ THE STAGING IS PS-344'S, NOT A NEW ONE
──────────────────────────────────────────
``chromium_tier._engine_binary()`` refuses to fall back to a chromium on PATH.
That is a real guard and it is respected here exactly as the REPORT records:
each arm is a DIRECTORY holding a **symlink** under the name
``fingerprint_chromium_filename()`` expects, with ``PERSONA_ENGINE_DIR`` pointed
at it. The resolver is not edited, the filename function is not edited, no
artifact is renamed and nothing lands on PATH.

⭐ BOTH VERDICTS ARE REQUIRED, AND THE RED ONE IS NOT WAIVABLE
───────────────────────────────────────────────────────────────
The product arm alone can only ever show that the instrument reported a pass.
It cannot show the instrument is CAPABLE of reporting absence — and a check only
ever observed passing is not coverage. So the falsification arm (the stock
control run a second time, labelled as if it were the product) is run through
the same judge with ``--expect absent`` and is required to agree.

⛔ ``--skip-falsification`` exists on the launcher so a re-run can RESUME, not so
the falsification can be waived. This script never passes it, and the workflow
never passes it, and that is deliberate: the day the red arm stops running is
the day the green one stops meaning anything.

⚠️ ORDER: THE FALSIFICATION IS JUDGED FIRST. If the judge cannot go red, a green
on the product arm is worthless, so there is no point reporting it. This is the
same ordering ``engine-gpu-variance.yml`` uses for its selftest — prove the gate
can fail before trusting a green from it.

EXIT CODES
──────────
    0  PATCHES LIVE, and the falsification arm was correctly reported ABSENT.
    1  A FINDING: the patches are not present/functioning in the published
       binary (or the falsification arm did NOT come back red, which condemns
       the run just as loudly).
    2  INDETERMINATE — an arm could not be produced or could not be read.
       NOT a pass. "We failed to look" is not "we looked and it was fine".

Those are the verdict's own three codes, deliberately. A wrapper that invented a
fourth vocabulary would be a second place for the meaning of a number to drift.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import urllib.request
import zipfile

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

LIVE = 0
FINDING = 1
INDETERMINATE = 2


def _log(msg: str) -> None:
    print(msg, flush=True)


def _download(url: str, dest: pathlib.Path, timeout: int = 300) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
        with open(dest, "wb") as fh:
            shutil.copyfileobj(resp, fh)


def _extract_preserving_mode(zip_path: pathlib.Path, dest: pathlib.Path) -> None:
    """Unzip, KEEPING the unix permission bits the archive recorded.

    ⚠️ FOUND BY RUNNING IT, not predicted. ``ZipFile.extractall`` discards the
    high 16 bits of ``external_attr`` — the unix mode — so every extracted file
    lands at the process umask, i.e. NOT executable. The Chrome for Testing
    archive ships ``chrome`` beside helper binaries it spawns itself, and
    chmod'ing only ``chrome`` produces a browser that starts and then dies:

        FATAL: posix_spawn .../chrome_crashpad_handler: Permission denied (13)

    Which reaches the judge as INDETERMINATE on all four control cells — the
    correct report of a staging failure, and a permanently red gate. The
    ``unzip(1)`` PS-344's recipe uses preserves the bit, which is why the
    manual reproduction never met this.
    """
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            extracted = pathlib.Path(zf.extract(info, dest))
            mode = info.external_attr >> 16
            if mode:
                extracted.chmod(mode & 0o7777)


def stage_engine(plan: dict, root: pathlib.Path) -> pathlib.Path:
    """Download the PUBLISHED engine and stage it under the expected name.

    Uses the product's OWN downloader (``updater.download_engine``), which is
    what makes this a reading of the bytes an operator receives rather than of
    a file this script happened to fetch: same digest check, same install path.
    """
    from src.services.engine import updater

    engine_dir = root / "engine-published"
    engine_dir.mkdir(parents=True, exist_ok=True)
    # ENGINE_BINARY is computed at import time from PERSONA_ENGINE_DIR, so the
    # env var must be set before `updater` is imported for download_engine to
    # write here. The workflow sets it; assert rather than silently write to
    # the operator's real engine directory.
    target = pathlib.Path(updater.ENGINE_BINARY)
    if target.parent.resolve() != engine_dir.resolve():
        raise RuntimeError(
            "PERSONA_ENGINE_DIR must be set to "
            f"{engine_dir} BEFORE this script imports the updater; it currently "
            f"resolves to {target.parent}"
        )
    if not updater.download_engine(
        plan["engine_url"], digest=plan["engine_digest"], tag=plan["engine_tag"]
    ):
        raise RuntimeError(f"could not download engine build {plan['engine_tag']}")
    # download_engine does not write version.txt — the UI does that after a
    # successful install. Without it the record's engine_build reads "unknown",
    # and a finding you cannot attribute to a TAG cannot be acted on.
    updater.write_version(plan["engine_version"])
    _log(f"staged engine  : {target} ({target.stat().st_size} bytes)")
    return engine_dir


def stage_control(plan: dict, root: pathlib.Path) -> pathlib.Path:
    """Download the stock Chrome for Testing control and SYMLINK it into place.

    A symlink under ``fingerprint_chromium_filename()``'s expected name — the
    resolver is not edited and the artifact is not renamed. See the docstring.
    """
    from src.core import platform as _platform

    zip_path = root / "cft.zip"
    _log(f"downloading control: {plan['control_url']}")
    _download(plan["control_url"], zip_path)
    extract_root = root / "cft"
    if extract_root.exists():
        shutil.rmtree(extract_root)
    _extract_preserving_mode(zip_path, extract_root)
    chrome = extract_root / "chrome-linux64" / "chrome"
    if not chrome.exists():
        raise RuntimeError(
            f"the Chrome for Testing archive did not contain {chrome} — "
            "the control cannot be staged and nothing can be concluded"
        )
    chrome.chmod(0o755)

    stock_dir = root / "engine-stock"
    stock_dir.mkdir(parents=True, exist_ok=True)
    link = stock_dir / _platform.fingerprint_chromium_filename()
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(chrome)
    _log(f"staged control : {link} -> {chrome}")
    return stock_dir


def _run(cmd: "list[str]", env: "dict | None" = None) -> int:
    _log("\n$ " + " ".join(cmd))
    return subprocess.call(cmd, cwd=str(REPO), env=env)


def run(plan: dict, root: pathlib.Path, out_dir: pathlib.Path) -> int:
    engine_dir = stage_engine(plan, root)
    stock_dir = stage_control(plan, root)
    out_dir.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    # This host may have no FUSE, which is what PS-344's own recipe records; the
    # AppImage runtime consumes this flag itself.
    env.setdefault("APPIMAGE_EXTRACT_AND_RUN", "1")
    env["PERSONA_ENGINE_DIR"] = str(engine_dir)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")

    code = _run(
        [
            sys.executable,
            "-m",
            "scripts.ps344_launch_published",
            "--published-dir",
            str(engine_dir),
            "--stock-dir",
            str(stock_dir),
            "--published-label",
            plan["product_label"],
            "--stock-label",
            plan["stock_label"],
            # THE DERIVED IDS. Arm id -> readings-<id>.json -> the judge's
            # --product/--control below. All three follow the resolved version.
            "--product-id",
            plan["product_id"],
            "--control-id",
            plan["control_id"],
            "--falsification-id",
            plan["falsification_id"],
            "-o",
            str(out_dir),
        ],
        env=env,
    )
    if code != 0:
        _log(
            f"\nINDETERMINATE: the harness exited {code}. No arm was produced, "
            "so nothing is claimed about the engine. This is NOT a pass."
        )
        return INDETERMINATE

    for key in ("product_file", "control_file", "falsification_file"):
        path = out_dir / plan[key]
        if not path.exists():
            _log(
                f"\nINDETERMINATE: {path} was not written. The arm ids and the "
                "verdict filenames have drifted apart — see "
                ".github/scripts/ps344_gate_plan.py. Nothing is claimed."
            )
            return INDETERMINATE

    verdict_cmd = [
        sys.executable,
        str(REPO / "scripts" / "ps344_verdict.py"),
        "--dir",
        str(out_dir),
        "--control",
        plan["control_file"],
    ]

    # THE RED ARM FIRST. Prove the judge can report absence before trusting a
    # green from it — see the module docstring.
    _log("\n" + "=" * 72)
    _log("FALSIFICATION ARM — the stock control labelled as the product.")
    _log("It MUST come back ABSENT; if it does not, the green below is worthless.")
    _log("=" * 72)
    falsification = _run(
        verdict_cmd + ["--product", plan["falsification_file"], "--expect", "absent"],
        env=env,
    )
    if falsification == INDETERMINATE:
        _log(
            "\nINDETERMINATE: the falsification arm could not be judged. "
            "Nothing is claimed about the product arm either."
        )
        return INDETERMINATE
    if falsification != 0:
        _log(
            "\nFINDING: the falsification arm did NOT come back red. The "
            "instrument cannot be shown capable of reporting 'patches absent', "
            "so no verdict it produces means anything. Do NOT read the product "
            "arm below as coverage."
        )
        return FINDING

    _log("\n" + "=" * 72)
    _log("PRODUCT ARM — the engine an operator downloads unattended.")
    _log("=" * 72)
    product = _run(verdict_cmd + ["--product", plan["product_file"]], env=env)
    return product


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plan", required=True, help="Path to the JSON plan")
    ap.add_argument("--work", required=True, help="Scratch dir for the binaries")
    ap.add_argument("--out", required=True, help="Where the readings are written")
    args = ap.parse_args(argv)

    plan = json.loads(pathlib.Path(args.plan).read_text(encoding="utf-8"))
    if plan.get("outcome") != "PLAN_OK":
        _log(
            f"INDETERMINATE: the plan is {plan.get('outcome')}, not PLAN_OK. "
            "Nothing was measured."
        )
        return INDETERMINATE

    _log(f"engine  : {plan['engine_tag']}")
    _log(f"control : Chrome for Testing {plan['control_version']} "
         f"({plan['control_match'].upper()} match)")

    try:
        return run(plan, pathlib.Path(args.work), pathlib.Path(args.out))
    except Exception as exc:  # noqa: BLE001
        _log(f"\nINDETERMINATE: {type(exc).__name__}: {exc}")
        return INDETERMINATE


if __name__ == "__main__":
    raise SystemExit(main())
