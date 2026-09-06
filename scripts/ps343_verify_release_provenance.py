#!/usr/bin/env python3
"""PS-343 — check a published Personium engine asset against its in-repo
provenance record, and go RED when they disagree.

WHAT THIS EXISTS TO STOP
────────────────────────
Before this, "which base, which patches, which build produced the browser our
users run?" was answerable for a CI *trial* run and not for the *shipped*
engine. The per-run manifest `scripts/ps218_manifest.sh` emits is real and
good, but it describes a trial build, and it lives in a CI artifact with 30-day
retention. **A provenance record that expires is not a provenance record.** The
records this script reads live in `engine/releases/*.json`, in git, and outlive
every runner.

THE TWO TRAPS THIS SCRIPT IS BUILT AROUND
─────────────────────────────────────────
1. **A digest attests to IDENTITY, not to CONTENT.** `sha256` says "these are
   the bytes that were published"; it says nothing about what is inside them.
   The project has already been bitten by this — three seats verified a
   Chromium binary by hash and none of them ever executed it. So every asset
   here carries a second, *content-level* derivation alongside its digest: a
   version string read out of the artifact's own structure (a Windows
   `.manifest`, a macOS `Info.plist`, an AppImage `.desktop`), and the list of
   our own fingerprint switches found in the shipped machine code. The digest
   check and the derivation check are reported as SEPARATE rows and neither is
   allowed to stand in for the other.

2. **A field that cannot be established is `unknown`, never inferred.** None of
   the three published assets was produced by any workflow in this repository
   (`RELEASING.md`: "Building and packaging the engine artifacts is not
   automated yet"), so for several fields the honest recorded value is
   `unknown`. This script treats `unknown` as a first-class value: it neither
   passes it off as verified nor fails the run for it. What it will NOT do is
   let a record quietly assert a provenance it does not have — every field
   carries an explicit `confidence`, and `derived_from_artifact` is the ONLY
   one this script can and does check.

THREE EXIT STATUSES, AND THE THIRD IS THE ONE TO READ CAREFULLY
──────────────────────────────────────────────────────────────
    0   every derivable check agreed with the record
    1   a check went RED — the artifact and the record disagree
    2   the measurement COULD NOT BE MADE (asset absent, host cannot extract
        this format, record unreadable). This is NOT "the record is fine".
        Nothing was measured. Do not let an automation read it as a pass.

That is deliberately the same three-status vocabulary
`scripts/ps299_rebase_probe.py` already uses on this project.

USAGE
─────
    # verify every record against assets already on disk
    python3 scripts/ps343_verify_release_provenance.py --assets /path/to/dir

    # download the release's assets first (needs `gh`), then verify
    python3 scripts/ps343_verify_release_provenance.py --download

    # record-only structural check (no artifacts) — reports every
    # artifact-level check as UNMEASURED and exits 2
    python3 scripts/ps343_verify_release_provenance.py --lint-only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import re
import struct
import subprocess
import sys
import tempfile
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RECORDS_DIR = REPO_ROOT / "engine" / "releases"

SCHEMA = "persona.engine.release-provenance/1"

# The three confidence values a record field may carry. This vocabulary is the
# honesty mechanism, so it is closed and validated rather than free text.
#
#   derived_from_artifact — re-derived from the shipped bytes. THE ONLY ONE
#                           THIS SCRIPT CAN CHECK, and it does.
#   from_repository       — read out of this repo at the recorded commit. A
#                           true statement ABOUT THE REPOSITORY. It is *not* an
#                           attestation that this is what built the binary, and
#                           this script deliberately does not pretend to check
#                           it — see `unattested_note` on each such field.
#   unknown               — not established. Recorded as a gap on purpose.
CONFIDENCES = {"derived_from_artifact", "from_repository", "unknown"}

# Read from a chunked stream rather than into memory: the macOS image
# decompresses to ~457 MB and the Windows `chrome.dll` is ~325 MB, and this
# script is meant to be runnable on an ordinary machine.
_CHUNK = 8 << 20


# ── outcome vocabulary ──────────────────────────────────────────────────────
GREEN = "GREEN"
RED = "RED"
UNMEASURED = "UNMEASURED"
NOTED = "NOTED"  # a recorded gap, reported and not scored


@dataclass
class Check:
    subject: str
    name: str
    verdict: str
    detail: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, subject: str, name: str, verdict: str, detail: str = "") -> None:
        self.checks.append(Check(subject, name, verdict, detail))

    @property
    def red(self) -> list[Check]:
        return [c for c in self.checks if c.verdict == RED]

    @property
    def unmeasured(self) -> list[Check]:
        return [c for c in self.checks if c.verdict == UNMEASURED]

    def exit_code(self) -> int:
        if self.red:
            return 1
        if self.unmeasured:
            return 2
        return 0


# ── primitives ──────────────────────────────────────────────────────────────
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def normalise_digest(value: str) -> str:
    """`sha256:abcd…` and `abcd…` are the same digest.

    GitHub's release API reports the prefixed form and `sha256sum` the bare
    one; a record must be comparable against both without a caller having to
    remember which it is holding.
    """
    return value.split(":", 1)[1] if value.startswith("sha256:") else value


def find_needles(stream, needles: list[bytes]) -> set[bytes]:
    """Which of `needles` occur anywhere in a byte stream.

    Chunked with an overlap the width of the longest needle, so a needle
    straddling a chunk boundary is still found. Reading these files whole is
    ~800 MB of resident memory across the three assets.
    """
    found: set[bytes] = set()
    longest = max((len(n) for n in needles), default=0)
    tail = b""
    while True:
        block = stream.read(_CHUNK)
        if not block:
            break
        window = tail + block
        for n in needles:
            if n not in found and n in window:
                found.add(n)
        if len(found) == len(needles):
            break
        tail = window[-longest:] if longest else b""
    return found


# `--fingerprint-platform` appears in the binary as the bare switch name with no
# leading dashes, NUL-delimited in the string table. Anchoring on the NULs is
# what keeps `fingerprint` from matching inside `fingerprint-brand`.
def switch_needle(name: str) -> bytes:
    return b"\x00" + name.encode("ascii") + b"\x00"


# ── per-format derivation ───────────────────────────────────────────────────
# Each returns a dict of derived field -> value, or raises Unmeasurable.
class Unmeasurable(RuntimeError):
    """The derivation could not be attempted on this host / this file.

    Deliberately distinct from "the derivation disagreed with the record".
    Collapsing the two is precisely how a record that was never checked comes
    to read as a record that passed.
    """


def derive_windows_zip(path: Path, switches: list[str]) -> dict[str, object]:
    """Chromium's Windows package names its own version twice, structurally:
    the versioned `Chrome-bin/<v>/` directory and the `<v>.manifest` inside it,
    whose `assemblyIdentity/@version` is the authoritative one. Both are read
    and required to agree — a rename of the directory alone will not satisfy
    this.
    """
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise Unmeasurable(f"not a readable zip: {exc}") from exc

    with zf:
        names = zf.namelist()
        dirs = sorted(
            {
                m.group(1)
                for n in names
                if (m := re.match(r"^Chrome-bin/([0-9][0-9.]*)/", n))
            }
        )
        if len(dirs) != 1:
            raise Unmeasurable(f"expected one Chrome-bin/<version>/, found {dirs}")
        version_dir = dirs[0]

        manifest_name = f"Chrome-bin/{version_dir}/{version_dir}.manifest"
        if manifest_name not in names:
            raise Unmeasurable(f"no {manifest_name} in the archive")
        manifest = zf.read(manifest_name).decode("utf-8", "replace")
        m = re.search(r"<assemblyIdentity\b[^>]*?version='([^']+)'", manifest, re.S)
        if not m:
            m = re.search(r'<assemblyIdentity\b[^>]*?version="([^"]+)"', manifest, re.S)
        if not m:
            raise Unmeasurable("assemblyIdentity/@version not found in the manifest")
        manifest_version = m.group(1)

        dll = f"Chrome-bin/{version_dir}/chrome.dll"
        if dll not in names:
            raise Unmeasurable(f"no {dll} in the archive")
        with zf.open(dll) as fh:
            found = find_needles(fh, [switch_needle(s) for s in switches])

    return {
        "version_dir": version_dir,
        "manifest_version": manifest_version,
        "fingerprint_switches_present": sorted(
            s for s in switches if switch_needle(s) in found
        ),
    }


def _udif_apfs_image(path: Path):
    """Yield the decompressed bytes of a UDIF (.dmg) image's Apple_APFS partition.

    Stdlib only — no `hdiutil`, no `7z`, no `dmg2img`. A DMG is a UDIF: a
    `koly` trailer in the last 512 bytes points at an XML plist whose `blkx`
    resources are per-partition chunk tables, each chunk raw / zeroed / zlib.
    Reading it here rather than shelling out is what lets a Linux CI box check
    the macOS asset at all.
    """
    data_len = path.stat().st_size
    with path.open("rb") as fh:
        fh.seek(max(0, data_len - 512))
        koly = fh.read(512)
        if not koly.startswith(b"koly"):
            raise Unmeasurable("no koly trailer — not a UDIF disk image")
        xml_off, xml_len = struct.unpack(">QQ", koly[0xD8:0xE8])
        fh.seek(xml_off)
        try:
            pl = plistlib.loads(fh.read(xml_len))
        except Exception as exc:  # noqa: BLE001 - any malformed plist is unmeasurable
            raise Unmeasurable(f"UDIF plist unreadable: {exc}") from exc

        blkx = pl.get("resource-fork", {}).get("blkx", [])
        parts = [e for e in blkx if "Apple_APFS" in (e.get("Name") or "")]
        if not parts:
            parts = [e for e in blkx if "Apple_HFS" in (e.get("Name") or "")]
        if not parts:
            raise Unmeasurable("no Apple_APFS/Apple_HFS partition in the image")

        b = parts[0]["Data"]
        if b[:4] != b"mish":
            raise Unmeasurable("blkx table is not a mish block")
        data_off = struct.unpack(">Q", b[24:32])[0]
        chunk_count = struct.unpack(">I", b[200:204])[0]

        p = 204
        for _ in range(chunk_count):
            ctype, _comment, _csec, csecs, coff, clen = struct.unpack(
                ">IIQQQQ", b[p : p + 40]
            )
            p += 40
            if ctype in (0xFFFFFFFF, 0x7FFFFFFE):  # terminator / comment
                continue
            if ctype in (0x00000000, 0x00000002):  # zero fill / ignored
                yield b"\0" * (csecs * 512)
                continue
            fh.seek(data_off + coff)
            raw = fh.read(clen)
            if ctype == 0x00000001:  # raw
                yield raw
            elif ctype == 0x80000005:  # zlib
                yield zlib.decompress(raw)
            else:
                raise Unmeasurable(
                    f"unsupported UDIF chunk type {ctype:#x} — cannot read this image"
                )


class _StreamOfChunks:
    """Adapt the chunk generator to the `.read(n)` shape `find_needles` wants."""

    def __init__(self, chunks):
        self._chunks = chunks
        self._buf = b""

    def read(self, n: int) -> bytes:
        while len(self._buf) < n:
            try:
                self._buf += next(self._chunks)
            except StopIteration:
                break
        out, self._buf = self._buf[:n], self._buf[n:]
        return out


_PLIST_ID = re.compile(
    rb"<key>CFBundleIdentifier</key>\s*<string>org\.chromium\.Chromium</string>"
)
_PLIST_SHORTVER = re.compile(
    rb"<key>CFBundleShortVersionString</key>\s*<string>([^<]*)</string>"
)
_PLIST_EXEC = re.compile(
    rb"<key>CFBundleExecutable</key>\s*<string>([^<]*)</string>"
)


def derive_macos_dmg(path: Path, switches: list[str]) -> dict[str, object]:
    """The browser bundle's own `CFBundleShortVersionString`, plus which of our
    switches are in the shipped Mach-O.

    THIS IS THE ASSET THE TICKET WARNED ABOUT, and the check is why the warning
    is now a measurement: the version this returns is what the *bundle* claims,
    which on `personium-152.0.7977.75` is **not** what the filename claims.
    """
    image = b"".join(_udif_apfs_image(path))

    version = None
    for m in _PLIST_ID.finditer(image):
        seg = image[max(0, m.start() - 1500) : m.start() + 1500]
        ex = _PLIST_EXEC.search(seg)
        sv = _PLIST_SHORTVER.search(seg)
        # The top-level browser bundle: identifier exactly org.chromium.Chromium
        # AND executable "Chromium". The helper bundles and the app-mode loader
        # template carry a suffixed identifier, so this is not ambiguous.
        if ex and sv and ex.group(1) == b"Chromium":
            version = sv.group(1).decode("utf-8", "replace")
            break
    if version is None:
        raise Unmeasurable(
            "no org.chromium.Chromium browser bundle Info.plist in the image"
        )

    needles = [switch_needle(s) for s in switches]
    found: set[bytes] = set()
    for n in needles:
        if n in image:
            found.add(n)

    return {
        "bundle_short_version": version,
        "fingerprint_switches_present": sorted(
            s for s in switches if switch_needle(s) in found
        ),
    }


def derive_linux_appimage(path: Path, switches: list[str]) -> dict[str, object]:
    """`--appimage-extract`, then read the packaging tag out of the extracted
    `.desktop` and the switches out of the extracted `chrome`.

    The `.desktop`'s `X-AppImage-Version` is the **ungoogled-chromium-portable
    linux tag** (`152.0.7977.75-1`) — the packaging revision, one field more
    than the bare Chromium version, and the single most useful provenance fact
    recoverable from any of the three assets.

    Requires a Linux host that can execute the AppImage runtime; anywhere else
    this is UNMEASURABLE rather than failed.
    """
    if not sys.platform.startswith("linux"):
        raise Unmeasurable(f"AppImage self-extraction needs Linux, host is {sys.platform}")

    with tempfile.TemporaryDirectory(prefix="ps343-appimage-") as tmp:
        try:
            path.chmod(path.stat().st_mode | 0o100)
            proc = subprocess.run(  # noqa: S603 - fixed argv, path is ours
                [str(path), "--appimage-extract"],
                cwd=tmp,
                capture_output=True,
                timeout=900,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise Unmeasurable(f"could not run --appimage-extract: {exc}") from exc
        if proc.returncode != 0:
            raise Unmeasurable(
                f"--appimage-extract exited {proc.returncode}: "
                f"{proc.stderr.decode('utf-8', 'replace')[:300]}"
            )

        root = Path(tmp) / "squashfs-root"
        desktops = sorted(root.glob("*.desktop"))
        if not desktops:
            raise Unmeasurable("no .desktop in the extracted AppImage")
        desktop = desktops[0].read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^X-AppImage-Version=(.+)$", desktop, re.M)
        if not m:
            raise Unmeasurable("no X-AppImage-Version in the .desktop")
        appimage_version = m.group(1).strip()

        chromes = sorted(root.glob("opt/*/chrome"))
        if not chromes:
            raise Unmeasurable("no opt/*/chrome in the extracted AppImage")
        with chromes[0].open("rb") as fh:
            found = find_needles(fh, [switch_needle(s) for s in switches])

    return {
        "appimage_version": appimage_version,
        "fingerprint_switches_present": sorted(
            s for s in switches if switch_needle(s) in found
        ),
    }


DERIVERS = {
    "windows-zip": derive_windows_zip,
    "macos-dmg": derive_macos_dmg,
    "linux-appimage": derive_linux_appimage,
}


# ── record handling ─────────────────────────────────────────────────────────
def load_records(records_dir: Path) -> list[dict]:
    if not records_dir.is_dir():
        raise Unmeasurable(f"no records directory at {records_dir}")
    out = []
    for p in sorted(records_dir.glob("personium-*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            raise Unmeasurable(f"{p.name} is not valid JSON: {exc}") from exc
    if not out:
        raise Unmeasurable(f"no personium-*.json records in {records_dir}")
    return out


def lint_record(record: dict, report: Report) -> None:
    """Structural checks that need no artifact.

    These exist because the record's VALUE is that its gaps are declared. A
    field carrying an unrecognised confidence, or an `unknown` field carrying a
    value anyway, is a record drifting back toward quiet assertion — so both
    are RED, not warnings.
    """
    subject = record.get("tag", "<untagged>")

    if record.get("schema") != SCHEMA:
        report.add(subject, "schema", RED, f"expected {SCHEMA!r}, got {record.get('schema')!r}")
        return
    report.add(subject, "schema", GREEN, SCHEMA)

    for path, node in walk_fields(record):
        conf = node.get("confidence")
        if conf not in CONFIDENCES:
            report.add(subject, f"confidence:{path}", RED, f"unrecognised confidence {conf!r}")
            continue
        if conf == "unknown" and node.get("value") not in (None, ""):
            report.add(
                subject,
                f"confidence:{path}",
                RED,
                f"declared unknown but carries a value {node.get('value')!r} — "
                "an unknown field must not assert one",
            )
            continue
        if conf == "unknown":
            report.add(subject, f"confidence:{path}", NOTED, "declared unknown (a stated gap)")
        elif conf == "from_repository":
            report.add(
                subject,
                f"confidence:{path}",
                NOTED,
                "from the repository — true of the repo, NOT attested of the build",
            )


def walk_fields(node, prefix: str = ""):
    """Every `{value, confidence}` leaf in the record, with its dotted path."""
    if isinstance(node, dict):
        if "confidence" in node:
            yield prefix or "<root>", node
            return
        for k, v in node.items():
            yield from walk_fields(v, f"{prefix}.{k}" if prefix else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk_fields(v, f"{prefix}[{i}]")


def verify_asset(record: dict, asset: dict, assets_dir: Path, report: Report) -> None:
    subject = asset["name"]

    path = assets_dir / asset["name"]
    if not path.is_file():
        report.add(subject, "present", UNMEASURED, f"not found under {assets_dir}")
        return

    # ── 1. IDENTITY. What was published. ────────────────────────────────────
    actual_size = path.stat().st_size
    if actual_size != asset["size_bytes"]:
        report.add(
            subject, "size", RED, f"record {asset['size_bytes']}, on disk {actual_size}"
        )
    else:
        report.add(subject, "size", GREEN, str(actual_size))

    expected = normalise_digest(asset["sha256"])
    actual = sha256_file(path)
    if actual != expected:
        report.add(subject, "sha256", RED, f"record {expected}, computed {actual}")
    else:
        report.add(subject, "sha256", GREEN, expected)

    # ── 2. CONTENT. What is inside them. A digest cannot answer this. ───────
    derived_spec = asset.get("derived") or {}
    if not derived_spec:
        report.add(subject, "derived", NOTED, "record declares nothing derivable")
        return

    deriver = DERIVERS.get(asset.get("format", ""))
    if deriver is None:
        report.add(
            subject, "derived", UNMEASURED, f"no deriver for format {asset.get('format')!r}"
        )
        return

    switches = [f["value"] for f in record["patch_set"]["switches_introduced"]["value"]]
    try:
        actual_derived = deriver(path, switches)
    except Unmeasurable as exc:
        report.add(subject, "derived", UNMEASURED, str(exc))
        return

    for key, node in derived_spec.items():
        if node.get("confidence") != "derived_from_artifact":
            report.add(subject, f"derived.{key}", NOTED, "not claimed as artifact-derived")
            continue
        if key not in actual_derived:
            report.add(
                subject, f"derived.{key}", UNMEASURED, "the deriver produced no such field"
            )
            continue
        want, got = node["value"], actual_derived[key]
        if want == got:
            report.add(subject, f"derived.{key}", GREEN, repr(got))
        else:
            report.add(
                subject, f"derived.{key}", RED, f"record {want!r}, artifact {got!r}"
            )


# ── driver ──────────────────────────────────────────────────────────────────
def download_assets(tag: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(  # noqa: S603,S607 - fixed argv
        ["gh", "release", "download", tag, "--repo", "amnesiadevelopment/persona",
         "--pattern", "*", "--dir", str(dest), "--clobber"],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise Unmeasurable(
            f"gh release download {tag} failed: "
            f"{proc.stderr.decode('utf-8', 'replace')[:400]}"
        )


def render(report: Report) -> str:
    lines = []
    width = max((len(f"{c.subject} :: {c.name}") for c in report.checks), default=0)
    icon = {GREEN: "✅", RED: "❌", UNMEASURED: "⚠️ ", NOTED: "·"}
    for c in report.checks:
        lines.append(f"{icon[c.verdict]} {f'{c.subject} :: {c.name}':<{width}}  {c.detail}")
    lines.append("")
    counts = {v: sum(1 for c in report.checks if c.verdict == v) for v in (GREEN, RED, UNMEASURED, NOTED)}
    lines.append(
        f"{counts[GREEN]} green, {counts[RED]} RED, "
        f"{counts[UNMEASURED]} UNMEASURED, {counts[NOTED]} noted"
    )
    if report.red:
        lines.append("")
        lines.append("❌ THE RECORD AND THE ARTIFACT DISAGREE. Do not publish or trust")
        lines.append("   this record until the disagreement is explained — a record that")
        lines.append("   has drifted from the bytes it describes is worse than none.")
    elif report.unmeasured:
        lines.append("")
        lines.append("⚠️  SOME CHECKS COULD NOT BE MADE. This is NOT a pass. Nothing was")
        lines.append("   measured for the rows above marked UNMEASURED.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--records", type=Path, default=RECORDS_DIR)
    ap.add_argument("--assets", type=Path, default=None, help="directory holding the published assets")
    ap.add_argument("--download", action="store_true", help="fetch the assets with `gh` first")
    ap.add_argument("--lint-only", action="store_true", help="structural record checks only")
    ap.add_argument("--tag", default=None, help="verify only this tag")
    args = ap.parse_args(argv)

    report = Report()
    try:
        records = load_records(args.records)
    except Unmeasurable as exc:
        print(f"⚠️  {exc}", file=sys.stderr)
        return 2

    if args.tag:
        records = [r for r in records if r.get("tag") == args.tag]
        if not records:
            print(f"⚠️  no record for tag {args.tag!r}", file=sys.stderr)
            return 2

    tmpdir = None
    for record in records:
        lint_record(record, report)
        if args.lint_only:
            for a in record.get("assets", []):
                report.add(a["name"], "artifact", UNMEASURED, "--lint-only: no artifact was read")
            continue

        assets_dir = args.assets
        if args.download:
            tmpdir = tmpdir or tempfile.TemporaryDirectory(prefix="ps343-assets-")
            assets_dir = Path(tmpdir.name) / record["tag"]
            try:
                download_assets(record["tag"], assets_dir)
            except Unmeasurable as exc:
                report.add(record["tag"], "download", UNMEASURED, str(exc))
                continue
        if assets_dir is None:
            report.add(record["tag"], "artifact", UNMEASURED, "no --assets directory given")
            continue

        for a in record.get("assets", []):
            verify_asset(record, a, assets_dir, report)

    print(render(report))
    if tmpdir:
        tmpdir.cleanup()
    return report.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
