#!/usr/bin/env python3
"""PS-185 — take the loopback GPU-unlinkability readings, both authorship arms.

WHY THIS DRIVER EXISTS RATHER THAN A BARE CLI CALL
--------------------------------------------------
Two reasons, and both are measurement-integrity reasons rather than convenience.

**1. The documented command measures ONE of the two authorship arms.**

``engine_gpu_variance.measure()`` hardcodes ``install_layer=False``. That is
correct for what that module polices — it guards the arms where the ENGINE
authors the WebGL identity — but ``ENGINE_AUTHORED_IDENTITY_ARMS`` is
``frozenset({"windows"})``. On macos / linux / android persona's OWN pool
authors the pair, through ``gpu_ext``'s ``pick(POOL, 0x67900)``.

PS-16's three "theoretical" figures (linux 12.5%, android 25.0%, macos 50.0%)
are counted from OUR pool sizes, so they are claims about the LAYER-ON draw.
A layer-OFF sweep on those arms measures a different quantity — what the engine
would do if we deferred, which on those arms we do not. Writing a layer-OFF
number into a layer-ON cell would retire the assumption with the wrong evidence.

So this driver takes BOTH arms and keeps them apart in the record:

  ``layer-off``  the documented instrument, unmodified. Engine behaviour.
                 Re-confirms windows (the one arm that ships this way) and
                 re-tests PS-161's linux/macos engine figures.
  ``layer-on``   persona's layer installed — the real ``pick()`` selection path
                 that macos/linux/android profiles actually ship. THIS is the
                 arm whose uniform-selection assumption has never been checked.

The JUDGEMENT is ``engine_gpu_variance.classify()`` in both cases — imported,
never re-implemented — so the bar, the skew sensitivity and the ``MIN_SEEDS``
floor are the module's own and cannot drift from it.

**2. The tier leaks the engine process tree, and a long sweep dies of it.**

Measured here 2026-08-26: a plain ``check --arms windows,macos,linux,android
--seeds <24>`` left ~15 chromium processes alive PER LAUNCH. After 7 launches
107 were resident and free memory had fallen from 10.8 GB to ~0.4 GB. That
matters beyond tidiness: an out-of-memory chromium dies mid-page with exactly
the contentless ``TargetClosedError`` that PS-133 once recorded as a property of
fingerprint seed 4242. A sweep that exhausts the box does not fail loudly, it
produces a plausible, WRONG reading — the shape this project keeps hitting.

So each chunk of seeds runs in its OWN PROCESS GROUP (``start_new_session``) and
the group is killed when the chunk returns. Reaping by process group rather than
by name is what makes ``--jobs > 1`` safe: a global ``pkill chrome`` would shoot
a sibling job's browser and record its seeds as unreadable.

This is a WORKAROUND, not a fix. The leak is in the tier and is reported, not
repaired, in PS-185 (fixing found defects is out of that ticket's scope).

USAGE
-----
    python3 sweep.py measure --mode layer-off --arms windows,macos,linux,android \
        --seeds 9001,4242,... --chunk 3 --jobs 2 --output record.json

A seed that cannot be read is recorded ``None`` and EXCLUDED from the statistics
by ``classify`` — an unreadable cell and a colliding cell are different findings.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import hashlib
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.services.verify import engine_gpu_variance as egv  # noqa: E402

LAYER_OFF = "layer-off"
LAYER_ON = "layer-on"


# ---------------------------------------------------------------------------
# Provenance — recorded on every record, because a number whose instrument is
# unknown is not a measurement (PS-14).
# ---------------------------------------------------------------------------

def _sha256(path: str) -> "str | None":
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest()
    except OSError:
        return None


def engine_provenance() -> dict:
    """What chromium actually ran, proven rather than asserted."""
    from src.core import platform as _platform
    from src.core.config import ENGINE_DIR

    binary = os.path.join(ENGINE_DIR, _platform.fingerprint_chromium_filename())
    version_file = os.path.join(ENGINE_DIR, "version.txt")
    builds_file = os.path.join(ENGINE_DIR, "builds.json")

    version = None
    if os.path.isfile(version_file):
        version = open(version_file, encoding="utf-8").read().strip()

    declared_digest = None
    if os.path.isfile(builds_file):
        try:
            declared_digest = (
                json.load(open(builds_file, encoding="utf-8"))
                .get("current", {})
                .get("digest")
            )
        except (ValueError, OSError):
            pass

    actual_digest = _sha256(binary) if os.path.isfile(binary) else None
    return {
        "engine": "fingerprint-chromium",
        "binary": binary,
        "present": os.path.isfile(binary),
        "build": version,
        "sha256": actual_digest,
        "declared_digest": declared_digest,
        # The point of carrying both: a build number is a claim, a digest that
        # matches the manifest is evidence the product's own engine ran.
        "digest_matches_manifest": (
            actual_digest is not None
            and declared_digest is not None
            and actual_digest == declared_digest
        ),
    }


def dev_shm_provenance() -> dict:
    """The 256 MiB ceiling PS-133 was misdiagnosed under."""
    try:
        st = os.statvfs("/dev/shm")
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
    except OSError:
        return {"readable": False}
    return {
        "readable": True,
        "total_bytes": total,
        "total_mib": round(total / (1 << 20), 1),
        "free_mib": round(free / (1 << 20), 1),
        "above_256mib_floor": total >= 256 * (1 << 20),
        "disable_dev_shm_usage_used": False,
    }


# ---------------------------------------------------------------------------
# The X server — including the one MODIFIED INSTRUMENT in this ticket.
#
# The tier refuses ``--headless`` deliberately (it presents a different surface
# to a fingerprinting checker), so these readings need a real display. In the
# container they were taken in there was no Xvfb and no root, so one was
# unpacked into $HOME with ``apt-get download`` + ``dpkg-deb -x``.
#
# Xvfb resolves ``xkbcomp`` through a path prefix COMPILED INTO THE BINARY —
# it is not read from the environment and no flag overrides it, so an unpacked
# copy cannot find the compiler and refuses to start. A PRIVATE COPY had that
# 8-byte prefix rewritten in place, ``/usr/bin`` -> ``/tmp/xkb``. Both are
# exactly 8 bytes, which is the entire reason the patch works without
# relinking: the string is overwritten, never moved.
#
# WHY IT CANNOT AFFECT ANY READING HERE, stated so a reader meeting a patched
# binary in the provenance has the argument beside it rather than having to
# reconstruct it: the patched bytes are on the KEYBOARD-INITIALISATION path
# only. The vectors these records carry — UNMASKED_RENDERER_WEBGL, the WebGL
# readback hash and the canvas readback hash — are produced by the GPU/canvas
# stack and never consult XKB. A failure of that path is also LOUD, not silent:
# Xvfb refuses to start, so there is no display, no browser and no record at
# all. There is no route by which it returns a WRONG pixel.
#
# It is disclosed rather than hidden because a modified instrument is exactly
# what PS-14 says to declare before attributing anything to the product.
# ---------------------------------------------------------------------------

XKB_PREFIX_DISTRO = b"/usr/bin"
XKB_PREFIX_PATCHED = b"/tmp/xkb"


def _xkbcomp_prefixes(binary: str) -> "list[str]":
    """Which xkbcomp path prefixes are present in this Xvfb image.

    Reads the fact out of the binary rather than trusting a note about it, so
    a future run states what it ACTUALLY ran under.
    """
    try:
        with open(binary, "rb") as fh:
            blob = fh.read()
    except OSError:
        return []
    return [
        prefix.decode()
        for prefix in (XKB_PREFIX_PATCHED, XKB_PREFIX_DISTRO)
        if prefix + b"\x00" in blob
    ]


def xserver_provenance() -> dict:
    """The display, and whether the Xvfb behind it was patched."""
    binary = shutil.which("Xvfb")
    prefixes = _xkbcomp_prefixes(binary) if binary else []
    patched = XKB_PREFIX_PATCHED.decode() in prefixes
    return {
        "display": os.environ.get("DISPLAY", ""),
        "headless": False,
        # Headless is REFUSED by the tier, not merely unused — a headless
        # engine presents a different surface, so a record taken under one
        # would not be the shipped configuration.
        "headless_refused_by_tier": True,
        "binary": binary,
        "sha256": _sha256(binary) if binary else None,
        "xkbcomp_prefixes_present": prefixes,
        "xkbcomp_path_patched": patched,
        "patch_rationale": (
            "keyboard-init path only: XKB is never consulted by the WebGL or "
            "canvas readback vectors these records carry, and a failure of it "
            "is loud (Xvfb refuses to start, yielding no record) rather than a "
            "wrong pixel"
        ),
        # See PROVENANCE.md: the 2026-08-26 records were taken under a
        # patched PRIVATE copy that no longer exists on this container, so a
        # live probe today reports today's server, NOT theirs.
        "reading_set_note": "see PROVENANCE.md for the server the committed records were taken under",
    }


def run_provenance(mode: str) -> dict:
    return {
        "mode": mode,
        "authorship": (
            "engine authors the identity (persona's layer NOT installed)"
            if mode == LAYER_OFF
            else "persona's gpu_ext pool authors the identity (layer installed)"
        ),
        "engine_authored_arms": sorted(egv.ENGINE_AUTHORED_IDENTITY_ARMS),
        "min_seeds": egv.MIN_SEEDS,
        "venue": "loopback (127.0.0.1) — no proxy, no exit, no third party",
        "display": os.environ.get("DISPLAY", ""),
        "headless": False,
        "engine": engine_provenance(),
        "dev_shm": dev_shm_provenance(),
        "xserver": xserver_provenance(),
    }


# ---------------------------------------------------------------------------
# The layer-ON reading. Same page, same vector, same judgement — the ONLY
# difference from the documented instrument is who authors the identity.
# ---------------------------------------------------------------------------

def measure_layer_on(
    arms: "tuple[str, ...]", seeds: "tuple[int, ...]"
) -> "dict[str, dict[int, str | None]]":
    """Read UNMASKED_RENDERER_WEBGL with persona's masking layer INSTALLED.

    This is the path a real macos/linux/android profile ships: ``gpu_ext``
    selects from its own pool with ``pick(POOL, 0x67900)``. Reading it is the
    only way to check the uniform-selection assumption behind PS-16's
    "theoretical" cells against an actual draw.
    """
    from src.services.verify import chromium_tier

    readings: "dict[str, dict[int, str | None]]" = {a: {} for a in arms}
    with egv._serve() as server:
        for arm in arms:
            for seed in seeds:
                value = None
                try:
                    session = chromium_tier.ChromiumSession(
                        "",
                        seed=seed,
                        declared_machine=arm,
                        allow_unsandboxed=True,
                        allow_no_proxy=True,
                        install_layer=True,
                    )
                    with session as live:
                        page = live.new_page()
                        page.goto(server.url, timeout=90000, wait_until="load")
                        time.sleep(egv.SETTLE_SECONDS)
                        data = json.loads(page.inner_text("body"))
                    if data.get("vendor") and data.get("renderer"):
                        value = f"{data['vendor']} | {data['renderer']}"
                except Exception as exc:
                    print(
                        f"[layer-on] {arm}/{seed}: {type(exc).__name__}: {exc}",
                        file=sys.stderr,
                    )
                readings[arm][seed] = value
                print(f"[layer-on] {arm}/seed{seed}: {value}", flush=True)
    return readings


# ---------------------------------------------------------------------------
# Chunk worker — one process group per chunk, killed on return.
# ---------------------------------------------------------------------------

def _cmd_chunk(args: argparse.Namespace) -> int:
    arms = tuple(a for a in args.arms.split(",") if a)
    seeds = tuple(int(s) for s in args.seeds.split(",") if s)
    readings = (
        egv.measure(arms, seeds)
        if args.mode == LAYER_OFF
        else measure_layer_on(arms, seeds)
    )
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(
            {a: {str(s): v for s, v in by.items()} for a, by in readings.items()},
            fh,
        )
    return 0


def _chunks(seq, size):
    return [seq[i:i + size] for i in range(0, len(seq), size)]


def _reap_orphaned_engines() -> int:
    """Kill engine processes that outlived the chunk that started them.

    ⚠️ SAFE ONLY AT ``--jobs 1``, and that is why the caller passes a flag
    rather than this being unconditional. Killing by NAME cannot tell one
    chunk's engine from a concurrent chunk's, so under parallelism this would
    shoot a sibling's browser mid-page and its seeds would be recorded
    unreadable — manufacturing exactly the "we failed to look" cells that
    ``classify`` is careful to keep separate from real collisions.

    It is needed because ``killpg`` on the chunk's own group does NOT drain the
    tree: the AppImage re-execs the real chrome, and the zygote/crashpad
    children reparent out of the group. Measured on this box: ~35 processes
    survive per chunk, and left alone they take free memory from 10.8 GB to
    ~0.4 GB within seven chunks. An out-of-memory chromium does not fail
    loudly — it dies mid-page with a contentless ``TargetClosedError``, which
    is precisely the error PS-133 once recorded as a property of fingerprint
    seed 4242. So reaping is measurement integrity, not housekeeping.
    """
    killed = 0
    for pattern in ("ungoogled-chromium", "appimage_extracted"):
        try:
            out = subprocess.run(
                ["pgrep", "-f", pattern],
                capture_output=True, text=True, timeout=30,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        for line in out.split():
            try:
                pid = int(line)
            except ValueError:
                continue
            if pid == os.getpid():
                continue
            try:
                os.kill(pid, signal.SIGKILL)
                killed += 1
            except (ProcessLookupError, PermissionError):
                pass
    if killed:
        time.sleep(1.0)
    return killed


def _run_chunk(
    mode: str, arm: str, seeds: "list[int]", workdir: str,
    *, reap_global: bool = False,
) -> dict:
    """Run one chunk in its OWN process group and reap the group afterwards.

    By group, never by name: a name-based sweep would kill a concurrent chunk's
    engine and silently record its seeds as unreadable.
    """
    tag = f"{mode}.{arm}.{seeds[0]}"
    out = os.path.join(workdir, f"chunk.{tag}.json")
    cmd = [
        sys.executable, os.path.abspath(__file__), "_chunk",
        "--mode", mode, "--arms", arm,
        "--seeds", ",".join(str(s) for s in seeds),
        "--out", out,
    ]
    proc = subprocess.Popen(
        cmd, cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, start_new_session=True,
    )
    try:
        stdout, _ = proc.communicate(timeout=90 * max(1, len(seeds)) + 240)
    except subprocess.TimeoutExpired:
        stdout = "TIMEOUT"
        proc.kill()
        proc.communicate()
    finally:
        # Reap the engine tree this chunk leaked, and nothing else.
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        if reap_global:
            _reap_orphaned_engines()
    for line in (stdout or "").splitlines():
        if "seed" in line or "Error" in line:
            print(f"  {line}", flush=True)
    if os.path.isfile(out):
        return json.load(open(out, encoding="utf-8"))
    return {arm: {str(s): None for s in seeds}}


def _cmd_measure(args: argparse.Namespace) -> int:
    arms = tuple(a.strip() for a in args.arms.split(",") if a.strip())
    seeds = [int(s.strip()) for s in args.seeds.split(",") if s.strip()]
    workdir = os.path.join(
        os.path.dirname(os.path.abspath(args.output)) or ".", ".chunks")
    os.makedirs(workdir, exist_ok=True)

    tasks = [(arm, chunk) for arm in arms for chunk in _chunks(seeds, args.chunk)]
    merged: "dict[str, dict[int, str | None]]" = {a: {} for a in arms}
    started = time.time()

    # Global name-based reaping is safe ONLY when nothing else is running an
    # engine concurrently. See _reap_orphaned_engines for why this is a flag
    # rather than unconditional.
    reap_global = args.jobs == 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {
            pool.submit(
                _run_chunk, args.mode, arm, chunk, workdir,
                reap_global=reap_global,
            ): (arm, chunk)
            for arm, chunk in tasks
        }
        done = 0
        for fut in concurrent.futures.as_completed(futures):
            arm, chunk = futures[fut]
            result = fut.result()
            for a, by in result.items():
                for s, v in by.items():
                    merged.setdefault(a, {})[int(s)] = v
            done += 1
            print(
                f"[sweep] {done}/{len(tasks)} chunks "
                f"({int(time.time() - started)}s elapsed)",
                flush=True,
            )

    result = egv.classify(merged)
    print(egv.format_result(result))

    record = {
        "ticket": "PS-185",
        "measured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "provenance": run_provenance(args.mode),
        "seeds_requested": seeds,
        "readings": {
            a: {str(s): v for s, v in sorted(by.items())}
            for a, by in merged.items()
        },
        "result": result,
    }
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, sort_keys=False)
    print(f"\nwrote {args.output}")
    shutil.rmtree(workdir, ignore_errors=True)
    return egv.exit_code_for(result)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="sweep", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("measure", help="sweep arms x seeds and verdict them")
    m.add_argument("--mode", choices=(LAYER_OFF, LAYER_ON), required=True)
    m.add_argument("--arms", required=True)
    m.add_argument("--seeds", required=True)
    m.add_argument("--chunk", type=int, default=3)
    m.add_argument("--jobs", type=int, default=2)
    m.add_argument("--output", required=True)
    m.set_defaults(func=_cmd_measure)

    c = sub.add_parser("_chunk", help=argparse.SUPPRESS)
    c.add_argument("--mode", choices=(LAYER_OFF, LAYER_ON), required=True)
    c.add_argument("--arms", required=True)
    c.add_argument("--seeds", required=True)
    c.add_argument("--out", required=True)
    c.set_defaults(func=_cmd_chunk)
    return ap


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
