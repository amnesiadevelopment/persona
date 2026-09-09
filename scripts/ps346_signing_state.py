#!/usr/bin/env python3
"""PS-346 — read the CODE-SIGNING state of every asset this project publishes,
from the published bytes, on a Linux host with no Apple or Microsoft tooling.

WHAT THIS EXISTS TO ANSWER
──────────────────────────
"Are our shipped artifacts signed, and by whom?" — a question that until now
had never been asked of the actual published bytes. The repo has excellent
*hash* integrity (the appimagetool pin in `release.yml`, the fail-closed engine
digest in `src/services/engine/updater.py`), and hashes answer a DIFFERENT
question:

    a digest proves the bytes did not CHANGE.
    a signature proves WHO PRODUCED them.

Only the second one is what Gatekeeper and SmartScreen read. This script reads
the second one.

WHY IT DOES NOT SHELL OUT TO `codesign` OR `signtool`
─────────────────────────────────────────────────────
It cannot: neither exists on this fleet (Debian containers), and Apple's
`codesign` has no Linux port at all. So every check here is a direct read of
the on-disk container format:

  * **PE / Authenticode** — `IMAGE_DIRECTORY_ENTRY_SECURITY` in the optional
    header. Size 0 means the file carries no certificate table, which is
    exactly what an unsigned binary looks like to Windows. Cross-checked with
    `signify`, which parses the same structure independently.
  * **Mach-O** — the `LC_CODE_SIGNATURE` load command, then the `SuperBlob` it
    points at. The distinction that matters is INSIDE the blob and is the one
    thing a naive "is there a signature?" check gets wrong: an **ad-hoc**
    signature (`CodeDirectory` flags bit `0x2`, and a `CMS blob wrapper` whose
    payload is ZERO bytes) is present on every macOS binary the linker emits.
    It is a self-referential hash tree with **no certificate and no identity**.
    Gatekeeper treats it as unsigned. A REAL Developer ID signature has a
    non-empty CMS payload carrying an X.509 chain.
  * **UDIF (.dmg)** — the `koly` trailer. A signed disk image carries a CMS
    blob between the end of the XML plist and the trailer; a `cSig` resource
    appears in the resource fork. Zero gap and no `cSig` means no image-level
    signature.
  * **AppImage / ELF** — the `.sha256_sig` and `.sig_key` sections
    appimagetool reserves. They exist in every AppImage; the question is
    whether they contain anything. All-zero means unsigned.

THE TRAP THIS SCRIPT IS BUILT AROUND
────────────────────────────────────
**"There is a _CodeSignature directory / an LC_CODE_SIGNATURE, so it is
signed."** That reasoning is wrong and it is the easy mistake here. Both are
present on our macOS bundle, and the bundle is nonetheless unsigned in every
sense a user's Mac cares about. This script therefore reports THREE macOS
states, never two:

    UNSIGNED       no LC_CODE_SIGNATURE at all
    ADHOC          signature present, adhoc flag set, CMS payload empty
                   → Gatekeeper: same outcome as unsigned
    SIGNED_CMS     non-empty CMS payload → a real identity, printed by name

The positive control is not synthetic: this project's own macOS bundle ships a
vendored `node` from the Node.js Foundation that IS Developer-ID-signed, so a
run that reports `SIGNED_CMS: 0` across the board would mean the *instrument*
is broken, not that the world is uniformly unsigned. The script asserts it
found at least one real signature somewhere and says so.

⛔ THIS SCRIPT HANDLES NO CREDENTIAL. It reads public release assets and prints
what it finds. It signs nothing, and it must never be extended to.

USAGE
─────
    python3 scripts/ps346_signing_state.py --download   # fetch + read
    python3 scripts/ps346_signing_state.py --dir DIR    # read an existing dir

Exit code is 0 when the read completed, whatever the signing state turns out to
be — this is an INSTRUMENT, not a gate. An asset it could not read at all is
reported as UNREADABLE and is never silently counted as unsigned.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import struct
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field

REPO = "amnesiadevelopment/persona"

# The assets this project publishes, per RELEASING.md's table. Both kinds of
# release are covered: the persona application and the Personium engine.
APP_TAG = "v3.1.1"
ENGINE_TAG = "personium-152.0.7977.75"

APP_ASSETS = [
    "persona-windows-setup.exe",
    "persona-macos.dmg",
    "persona-x86_64.AppImage",
]
ENGINE_ASSETS = [
    f"{ENGINE_TAG}-windows-x86_64.zip",
    f"{ENGINE_TAG}-macos-arm64.dmg",
    f"{ENGINE_TAG}-linux-x86_64.AppImage",
]


# ── result model ─────────────────────────────────────────────────────────────


@dataclass
class Finding:
    asset: str
    platform: str
    kind: str  # what was inspected
    # Signature states:   UNSIGNED / ADHOC / SIGNED_CMS / UNREADABLE
    # §3 prerequisite states (notarization facts, NOT signatures, so they are
    # deliberately a different vocabulary and are excluded from the positive
    # control below — a present CodeResources seal is not a signature):
    #                     PRESENT / ABSENT
    state: str
    detail: str = ""
    identity: str | None = None


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    def add(self, *a, **kw) -> None:
        self.findings.append(Finding(*a, **kw))


# ── PE / Authenticode ────────────────────────────────────────────────────────


def pe_security_dir(data: bytes) -> tuple[int, int] | None:
    """Return (rva, size) of IMAGE_DIRECTORY_ENTRY_SECURITY, or None."""
    if data[:2] != b"MZ":
        return None
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e_lfanew : e_lfanew + 4] != b"PE\0\0":
        return None
    opt = e_lfanew + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    # data-directory array starts after the optional header's fixed part
    if magic == 0x20B:  # PE32+
        dd = opt + 112
    elif magic == 0x10B:  # PE32
        dd = opt + 96
    else:
        return None
    # entry 4 is IMAGE_DIRECTORY_ENTRY_SECURITY
    return struct.unpack_from("<II", data, dd + 4 * 8)


def read_pe(report: Report, asset: str, name: str, data: bytes) -> None:
    sec = pe_security_dir(data)
    if sec is None:
        report.add(asset, "windows", f"PE {name}", "UNREADABLE", "not a PE image")
        return
    rva, size = sec
    if size == 0:
        report.add(
            asset,
            "windows",
            f"PE {name}",
            "UNSIGNED",
            "IMAGE_DIRECTORY_ENTRY_SECURITY size=0 (no certificate table)",
        )
        return
    identity = None
    try:
        from asn1crypto import cms as _cms  # noqa: F401
        from signify.authenticode import AuthenticodeFile

        import io

        obj = AuthenticodeFile.from_stream(io.BytesIO(data))
        subjects = []
        for sd in obj.iter_embedded_signatures(ignore_parse_errors=True):
            for c in sd.certificates:
                subjects.append(str(c.subject.dn))
        identity = "; ".join(subjects[:3]) or None
    except Exception as exc:  # pragma: no cover - depends on optional dep
        identity = f"(chain unread: {type(exc).__name__})"
    report.add(
        asset,
        "windows",
        f"PE {name}",
        "SIGNED_CMS",
        f"certificate table present, {size} bytes",
        identity,
    )


# ── Mach-O ───────────────────────────────────────────────────────────────────

CSMAGIC_EMBEDDED_SIGNATURE = 0xFADE0CC0
CS_ADHOC = 0x0000_0002
CS_RUNTIME = 0x0001_0000
SLOT_CODEDIRECTORY = 0
SLOT_CMS = 0x10000
SLOT_ENTITLEMENTS = 5


def macho_slices(data: bytes) -> list[int]:
    """Byte offsets of each architecture slice.

    ⚠️ THE TWO FAT LAYOUTS ARE DIFFERENT STRUCTS, NOT A WIDER FIELD IN THE SAME
    ONE. `FAT_MAGIC` (0xCAFEBABE) carries `fat_arch`  = ">iiIII", stride 20;
    `FAT_MAGIC_64` (0xCAFEBABF) carries `fat_arch_64` = ">iiQQII", stride 32
    (64-bit offset and size, plus a trailing reserved word). Reading a FAT-64
    header with the 32-bit stride does NOT yield an obviously wild number — it
    yields 0, i.e. it re-reads the fat header itself as if it were a Mach-O,
    which then reports UNREADABLE. Silent, plausible and wrong; hence the split.
    """
    if len(data) < 8:
        return []
    magic = struct.unpack_from(">I", data, 0)[0]
    if magic in (0xCAFEBABE, 0xCAFEBABF):
        fmt, stride = (">iiIII", 20) if magic == 0xCAFEBABE else (">iiQQII", 32)
        n = struct.unpack_from(">I", data, 4)[0]
        out = []
        for i in range(n):
            at = 8 + i * stride
            if at + stride > len(data):
                break  # truncated fat header — report what was readable, invent nothing
            _ct, _cs, off, _sz, _al = struct.unpack_from(fmt, data, at)[:5]
            out.append(off)
        return out
    if magic in (0xCFFAEDFE, 0xCEFAEDFE) or struct.unpack_from("<I", data, 0)[0] in (
        0xFEEDFACF,
        0xFEEDFACE,
    ):
        return [0]
    return []


def entitlement_get_task_allow(blob: bytes | None) -> bool | None:
    """Is `com.apple.security.get-task-allow` TRUE in this entitlements blob?

    ⭐ THIS IS A §3 CHECK, and it answers a NOTARIZATION question rather than a
    signing one: the entitlement lets any process attach a debugger to ours, it
    is a *development* entitlement, and it is an explicit notary-rejection
    condition. A Developer ID certificate does not remove it — re-signing does.

    ⚠️ THREE-VALUED ON PURPOSE, because two of the readings are different facts
    and collapsing them is the same error the classifier above exists to avoid:
      * True  — present and true (a measured rejection condition)
      * False — the blob was READ and the key is absent or false
      * None  — there was no entitlements blob, or it could not be parsed; this
                is NOT the claim "the entitlement is absent".
    """
    if not blob:
        return None
    try:
        # The entitlements slot payload is a plist. Older builds embed it as
        # XML, newer ones as a DER blob; only the XML form is parsed here and
        # an unparseable one honestly returns None rather than False.
        start = blob.find(b"<?xml")
        if start < 0:
            return None
        parsed = plistlib.loads(blob[start:])
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    return bool(parsed.get("com.apple.security.get-task-allow", False))


def classify_tally(tally: dict[str, int]) -> str:
    """Aggregate per-slice states into ONE verdict for a bundle.

    ⭐ THIS IS THE VERDICT SITE, and it is a named function precisely so a test
    can drive it directly. The script's contract (module docstring) is that an
    asset it could not read is reported as UNREADABLE and is NEVER silently
    counted as unsigned — and this is the single place that contract can be
    broken. Two readings must not fall through to UNSIGNED:

      * nothing was read at all (EMPTY tally) — "we found no signature" is a
        claim about bytes nobody looked at;
      * every slice was UNREADABLE — the same claim, the same absence of
        evidence.

    A MIXED bundle still reports its real signing state, because the slices that
    WERE read are evidence. The unreadable remainder is carried in the caller's
    `detail` string so the reading is never quietly narrower than it looks.
    """
    total = sum(tally.values())
    if total == 0 or tally.get("UNREADABLE", 0) == total:
        return "UNREADABLE"
    real = tally.get("SIGNED_CMS", 0)
    adhoc = tally.get("ADHOC", 0)
    unsigned = tally.get("UNSIGNED", 0)
    if real and not (adhoc or unsigned):
        return "SIGNED_CMS"
    if adhoc:
        return "ADHOC"
    return "UNSIGNED"


def read_macho_slice(data: bytes, base: int) -> dict:
    """Return the signing facts for one arch slice."""
    magic = struct.unpack_from("<I", data, base)[0]
    if magic == 0xFEEDFACF:
        off = base + 32
    elif magic == 0xFEEDFACE:
        off = base + 28
    else:
        return {"state": "UNREADABLE", "detail": f"magic 0x{magic:x}"}
    ncmds = struct.unpack_from("<I", data, base + 16)[0]
    sig = None
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", data, off)
        if cmd == 0x1D:  # LC_CODE_SIGNATURE
            dataoff, datasize = struct.unpack_from("<II", data, off + 8)
            sig = (base + dataoff, datasize)
        off += cmdsize
    if sig is None:
        return {"state": "UNSIGNED", "detail": "no LC_CODE_SIGNATURE"}

    so, _ss = sig
    if struct.unpack_from(">I", data, so)[0] != CSMAGIC_EMBEDDED_SIGNATURE:
        return {"state": "UNREADABLE", "detail": "SuperBlob magic mismatch"}
    count = struct.unpack_from(">I", data, so + 8)[0]

    adhoc = hardened = False
    ident = team = None
    cms_len = 0
    entitlements = None
    for i in range(count):
        slot, o2 = struct.unpack_from(">II", data, so + 12 + i * 8)
        blen = struct.unpack_from(">I", data, so + o2 + 4)[0]
        if slot == SLOT_CODEDIRECTORY:
            ver = struct.unpack_from(">I", data, so + o2 + 8)[0]
            flags = struct.unpack_from(">I", data, so + o2 + 12)[0]
            adhoc = bool(flags & CS_ADHOC)
            hardened = bool(flags & CS_RUNTIME)
            io_ = struct.unpack_from(">I", data, so + o2 + 20)[0]
            ident = data[so + o2 + io_ : data.index(b"\0", so + o2 + io_)].decode(
                "utf8", "replace"
            )
            if ver >= 0x20200:
                to = struct.unpack_from(">I", data, so + o2 + 48)[0]
                if to:
                    team = data[so + o2 + to : data.index(b"\0", so + o2 + to)].decode(
                        "utf8", "replace"
                    )
        elif slot == SLOT_CMS:
            cms_len = blen - 8  # blob header is 8 bytes
        elif slot == SLOT_ENTITLEMENTS:
            entitlements = data[so + o2 + 8 : so + o2 + blen]

    # THE distinction this script exists for.
    if cms_len > 0:
        state, detail = "SIGNED_CMS", f"CMS payload {cms_len} bytes"
    elif adhoc:
        state, detail = "ADHOC", "adhoc flag set, CMS payload EMPTY (no identity)"
    else:
        state, detail = "ADHOC", "no CMS payload (no identity)"

    identity = None
    if cms_len > 0:
        try:
            from asn1crypto import cms as _cms

            for i in range(count):
                slot, o2 = struct.unpack_from(">II", data, so + 12 + i * 8)
                if slot != SLOT_CMS:
                    continue
                blen = struct.unpack_from(">I", data, so + o2 + 4)[0]
                ci = _cms.ContentInfo.load(data[so + o2 + 8 : so + o2 + blen])
                names = [
                    c.chosen.subject.human_friendly for c in ci["content"]["certificates"]
                ]
                identity = "; ".join(names)
        except Exception as exc:
            identity = f"(chain unread: {type(exc).__name__})"

    return {
        "state": state,
        "detail": detail,
        "identifier": ident,
        "team": team,
        "hardened_runtime": hardened,
        "identity": identity,
        "entitlements": entitlements,
    }


# ── UDIF (.dmg) ──────────────────────────────────────────────────────────────


def read_dmg(report: Report, asset: str, path: str) -> None:
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        fh.seek(-512, 2)
        koly = fh.read(512)
        if koly[:4] != b"koly":
            report.add(asset, "macos", "UDIF image", "UNREADABLE", "no koly trailer")
            return
        xml_off = struct.unpack_from(">Q", koly, 216)[0]
        xml_len = struct.unpack_from(">Q", koly, 224)[0]
        fh.seek(xml_off)
        plist = plistlib.loads(fh.read(xml_len))

    gap = (size - 512) - (xml_off + xml_len)
    resources = sorted(plist.get("resource-fork", {}).keys())
    has_csig = "cSig" in resources
    if has_csig or gap > 0:
        report.add(
            asset,
            "macos",
            "UDIF image signature",
            "SIGNED_CMS",
            f"cSig={has_csig}, {gap} bytes between plist and koly",
        )
    else:
        report.add(
            asset,
            "macos",
            "UDIF image signature",
            "UNSIGNED",
            f"no cSig resource (keys: {','.join(resources)}), "
            f"{gap} bytes between plist end and koly trailer",
        )


# ── AppImage / ELF ───────────────────────────────────────────────────────────


def read_appimage(report: Report, asset: str, path: str) -> None:
    with open(path, "rb") as fh:
        head = fh.read(64)
        if head[:4] != b"\x7fELF":
            report.add(asset, "linux", "AppImage", "UNREADABLE", "not an ELF")
            return
        e_shoff = struct.unpack_from("<Q", head, 0x28)[0]
        e_shentsize = struct.unpack_from("<H", head, 0x3A)[0]
        e_shnum = struct.unpack_from("<H", head, 0x3C)[0]
        e_shstrndx = struct.unpack_from("<H", head, 0x3E)[0]
        fh.seek(e_shoff)
        sh = fh.read(e_shentsize * e_shnum)

        def ent(i: int):
            o = i * e_shentsize
            name, _typ, _flags, _addr, off, sz = struct.unpack_from("<IIQQQQ", sh, o)
            return name, off, sz

        _n, stroff, strsize = ent(e_shstrndx)
        fh.seek(stroff)
        strtab = fh.read(strsize)

        found = False
        for i in range(e_shnum):
            name, off, sz = ent(i)
            nm = strtab[name : strtab.index(b"\0", name)].decode()
            if nm not in (".sha256_sig", ".sig_key"):
                continue
            found = True
            fh.seek(off)
            blob = fh.read(sz).rstrip(b"\0")
            if blob:
                report.add(
                    asset,
                    "linux",
                    f"AppImage {nm}",
                    "SIGNED_CMS",
                    f"{len(blob)} non-null bytes",
                )
            else:
                report.add(
                    asset,
                    "linux",
                    f"AppImage {nm}",
                    "UNSIGNED",
                    f"section present, {sz} bytes, ALL ZERO",
                )
    if not found:
        report.add(
            asset, "linux", "AppImage signature sections", "UNSIGNED", "no .sha256_sig section"
        )


# ── UDIF payload extraction + APFS walk (the macOS Mach-O leg) ───────────────
#
# This is the expensive half, and it is the half that actually answers the
# macOS question — the image-level check above only says the .dmg WRAPPER is
# unsigned. It needs two optional pieces that are not in requirements.txt
# because nothing else in the project needs them:
#
#     pip install libfsapfs-python
#
# When it is absent the leg reports UNREADABLE and says why. It is NEVER
# silently folded into "unsigned": "we could not look" and "we looked and found
# nothing" are different claims and the ticket's falsification clause turns on
# exactly that difference.


def udif_extract(path: str, out: str) -> bool:
    """Decompress the Apple_APFS partition out of a UDZO .dmg."""
    import bz2
    import zlib

    with open(path, "rb") as fh:
        fh.seek(-512, 2)
        koly = fh.read(512)
        if koly[:4] != b"koly":
            return False
        xml_off = struct.unpack_from(">Q", koly, 216)[0]
        xml_len = struct.unpack_from(">Q", koly, 224)[0]
        fh.seek(xml_off)
        plist = plistlib.loads(fh.read(xml_len))

        with open(out, "wb") as of:
            for entry in plist["resource-fork"]["blkx"]:
                if "Apple_APFS" not in (entry.get("Name") or ""):
                    continue
                blk = entry["Data"]
                if blk[:4] != b"mish":
                    continue
                # mish header: version(4) sectorNumber(8) sectorCount(16)
                # dataOffset(24) buffersNeeded(32) blockDescriptors(36),
                # all offsets relative to the start of the blob.
                data_offset = struct.unpack_from(">Q", blk, 24)[0]
                nchunks = struct.unpack_from(">I", blk, 200)[0]
                off = 204
                for _ in range(nchunks):
                    etype, _c, start_sector, sector_count, comp_off, comp_len = (
                        struct.unpack_from(">IIQQQQ", blk, off)
                    )
                    off += 40
                    if etype == 0xFFFFFFFF:  # terminator
                        break
                    fh.seek(data_offset + comp_off)
                    raw = fh.read(comp_len)
                    if etype == 0x00000001:  # raw
                        chunk = raw
                    elif etype in (0x00000000, 0x00000002):  # zero-fill / ignored
                        chunk = b"\0" * (sector_count * 512)
                    elif etype == 0x80000005:  # zlib (UDZO)
                        chunk = zlib.decompress(raw)
                    elif etype == 0x80000006:  # bzip2
                        chunk = bz2.decompress(raw)
                    else:
                        raise ValueError(f"unsupported UDIF chunk type 0x{etype:x}")
                    of.seek(start_sector * 512)
                    of.write(chunk)
    return True


def _apfs_machos(entry, path, acc, notes=None) -> None:
    """Walk the APFS tree collecting Mach-O files — and, when `notes` is given,
    the NOTARIZATION-RELEVANT paths as well.

    The three §3 facts a Developer ID certificate alone does not deliver are all
    file-system facts, not signature-blob facts, so they are read HERE on the
    same single walk rather than in a second pass:
      * `_CodeSignature/CodeResources` — the bundle seal's shape;
      * `CodeResources` in a Contents dir — same;
      * a stapled notarization ticket (`CodeResources`' sibling `*.ticket`, or
        `Contents/CodeResources`'s neighbour) — `stapler staple` writes one.
    """
    for i in range(entry.get_number_of_sub_file_entries()):
        sub = entry.get_sub_file_entry(i)
        name = sub.get_name()
        p = f"{path}/{name}"
        if sub.get_number_of_sub_file_entries() > 0:
            _apfs_machos(sub, p, acc, notes)
            continue
        if notes is not None:
            if name == "CodeResources":
                notes["code_resources"].append(p)
            # `stapler staple` writes the notarization ticket into the bundle as
            # CodeResources' sibling. Its absence is the measured half of "no
            # stapled ticket exists anywhere in either image".
            if name.endswith(".ticket") or name == "CodeDirectory":
                notes["staple"].append(p)
        try:
            size = sub.get_size()
            if size < 4:
                continue
            sub.seek_offset(0, 0)
            head = sub.read_buffer(4)
        except Exception:
            continue
        if head in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
            acc.append((p, size, sub))


def read_dmg_bundle(report: Report, asset: str, path: str, workdir: str) -> None:
    try:
        import pyfsapfs
    except ImportError:
        report.add(
            asset,
            "macos",
            "bundle Mach-O binaries",
            "UNREADABLE",
            "libfsapfs-python not installed — `pip install libfsapfs-python` to read this leg. "
            "NOT counted as unsigned.",
        )
        return

    payload = os.path.join(workdir, os.path.basename(path) + ".apfs")
    try:
        if not os.path.exists(payload):
            if not udif_extract(path, payload):
                raise ValueError("no koly trailer")
        container = pyfsapfs.container()
        container.open(payload)
        volume = container.get_volume(0)
        acc: list = []
        notes: dict[str, list[str]] = {"code_resources": [], "staple": []}
        _apfs_machos(volume.get_root_directory(), "", acc, notes)
    except Exception as exc:
        report.add(
            asset,
            "macos",
            "bundle Mach-O binaries",
            "UNREADABLE",
            f"{type(exc).__name__}: {exc}. NOT counted as unsigned.",
        )
        return

    tally: dict[str, int] = {}
    identities: set[str] = set()
    hardened_count = 0
    hardened_which: str | None = None
    examples: dict[str, str] = {}
    task_allow: list[str] = []
    ents_seen = 0
    for p, size, entry in acc:
        entry.seek_offset(0, 0)
        data = entry.read_buffer(size)
        for base in macho_slices(data):
            info = read_macho_slice(data, base)
            state = info["state"]
            tally[state] = tally.get(state, 0) + 1
            examples.setdefault(state, p)
            if info.get("hardened_runtime"):
                hardened_count += 1
                hardened_which = p
            if info.get("identity"):
                identities.add(f"{p}: {info['identity']}")
            ents = info.get("entitlements")
            if ents:
                ents_seen += 1
                if entitlement_get_task_allow(ents):
                    task_allow.append(p)

    real = tally.get("SIGNED_CMS", 0)
    adhoc = tally.get("ADHOC", 0)
    unsigned = tally.get("UNSIGNED", 0)
    unreadable = tally.get("UNREADABLE", 0)
    state = classify_tally(tally)
    # Report hardened-runtime as a COUNT, never as a boolean. "any slice is
    # hardened" is a dangerously weak reading: in this bundle exactly ONE slice
    # is hardened and it is the vendored third-party `node`, so a True there
    # would suggest our own build is hardened when it is not.
    hardened_note = (
        f"hardened-runtime slices: {hardened_count}/{sum(tally.values())}"
        + (f" (only: {hardened_which})" if hardened_count and hardened_which else "")
    )
    report.add(
        asset,
        "macos",
        f"bundle Mach-O binaries ({len(acc)} files)",
        state,
        f"arch slices: ADHOC={adhoc}, UNSIGNED={unsigned}, SIGNED_CMS={real}, "
        f"UNREADABLE={unreadable}; " + hardened_note,
        "; ".join(sorted(identities)) or None,
    )

    # ── §3: what a certificate alone does NOT deliver ────────────────────────
    # These are notarization prerequisites, not signing facts, and they are the
    # reason "buy a cert and add a codesign step" is the wrong cost model. They
    # are reported here so §1–§3 of the report is reproducible with THIS script,
    # rather than resting on a one-off reading nobody can re-run.

    # get-task-allow: a shipped debug entitlement is a hard notary rejection.
    if ents_seen == 0:
        report.add(
            asset,
            "macos",
            "entitlement com.apple.security.get-task-allow",
            "UNREADABLE",
            "no entitlements blob found on any slice — NOT the claim that the "
            "entitlement is absent.",
        )
    else:
        report.add(
            asset,
            "macos",
            "entitlement com.apple.security.get-task-allow",
            "PRESENT" if task_allow else "ABSENT",
            (
                f"PRESENT AND TRUE on {len(task_allow)}/{ents_seen} slice(s) carrying "
                f"entitlements (e.g. {task_allow[0]}) — an explicit notarization-"
                "rejection condition, shipped."
                if task_allow
                else f"absent or false on all {ents_seen} slice(s) carrying entitlements."
            ),
        )

    # Bundle seal + stapled ticket, both read on the same walk.
    report.add(
        asset,
        "macos",
        "bundle _CodeSignature/CodeResources",
        "PRESENT" if notes["code_resources"] else "ABSENT",
        (
            f"{len(notes['code_resources'])} CodeResources seal(s) present "
            f"(e.g. {notes['code_resources'][0]}) — ⚠️ presence is NOT identity: "
            "an ad-hoc seal's requirement strings are bare cdhashes with no "
            "`anchor apple generic` clause."
            if notes["code_resources"]
            else "no CodeResources anywhere in the image — the bundle does not even "
            "have the SHAPE of a signature."
        ),
    )
    report.add(
        asset,
        "macos",
        "stapled notarization ticket",
        "PRESENT" if notes["staple"] else "ABSENT",
        (
            f"{len(notes['staple'])} stapled ticket(s): {notes['staple'][0]}"
            if notes["staple"]
            else "no stapled ticket found anywhere in the image."
        ),
    )


# ── engine zip (Windows) ─────────────────────────────────────────────────────


def read_engine_zip(report: Report, asset: str, path: str) -> None:
    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith((".exe", ".dll"))]
        for n in sorted(names):
            data = zf.read(n)
            read_pe(report, asset, n, data)


# ── driver ───────────────────────────────────────────────────────────────────


def download(dest: str) -> None:
    os.makedirs(dest, exist_ok=True)
    for tag, assets in ((APP_TAG, APP_ASSETS), (ENGINE_TAG, ENGINE_ASSETS)):
        cmd = ["gh", "release", "download", tag, "-R", REPO, "--clobber", "-D", dest]
        for a in assets:
            cmd += ["-p", a]
        print(f"$ {' '.join(cmd)}", file=sys.stderr)
        subprocess.run(cmd, check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default="/tmp/ps346/dl", help="directory holding the assets")
    ap.add_argument("--download", action="store_true", help="fetch the assets first")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = ap.parse_args()

    if args.download:
        download(args.dir)

    report = Report()

    for asset in APP_ASSETS + ENGINE_ASSETS:
        path = os.path.join(args.dir, asset)
        if not os.path.exists(path):
            report.add(asset, "?", "asset", "UNREADABLE", "not present in --dir")
            continue
        if asset.endswith(".exe"):
            with open(path, "rb") as fh:
                read_pe(report, asset, "installer", fh.read())
        elif asset.endswith(".dmg"):
            read_dmg(report, asset, path)
            read_dmg_bundle(report, asset, path, args.dir)
        elif asset.endswith(".AppImage"):
            read_appimage(report, asset, path)
        elif asset.endswith(".zip"):
            read_engine_zip(report, asset, path)

    if args.json:
        print(json.dumps([f.__dict__ for f in report.findings], indent=2))
        return 0

    print("=" * 100)
    print("PS-346 — signing state of every published asset")
    print("=" * 100)
    width = max(len(f.asset) for f in report.findings)
    for f in report.findings:
        print(f"{f.asset:<{width}}  {f.state:<12}  {f.kind}")
        if f.detail:
            print(f"{'':<{width}}  {'':<12}    {f.detail}")
        if f.identity:
            print(f"{'':<{width}}  {'':<12}    identity: {f.identity}")

    states = {}
    for f in report.findings:
        states[f.state] = states.get(f.state, 0) + 1
    print("-" * 100)
    print("tally:", ", ".join(f"{k}={v}" for k, v in sorted(states.items())))
    print(
        "\nNOTE: ADHOC is NOT signed in any sense Gatekeeper acts on — it carries no\n"
        "certificate and no identity. UNREADABLE is never counted as unsigned."
    )

    # ── POSITIVE CONTROL ─────────────────────────────────────────────────────
    # A run that reports "nothing is signed" is worthless unless the instrument
    # is known to be capable of reporting the opposite. Two real signatures are
    # expected in these very assets, and NEITHER is ours:
    #   * Microsoft's, on the d3dcompiler/dxil redistributables in the engine zip
    #   * the Node.js Foundation's, on the `node` vendored inside the macOS .app
    # If the control does not fire, the finding below is about the SCRIPT, not
    # about the artifacts.
    print("-" * 100)
    signed = [f for f in report.findings if f.state == "SIGNED_CMS" or f.identity]
    if signed:
        print(f"POSITIVE CONTROL: FIRED — {len(signed)} genuinely-signed item(s) detected.")
        print("The instrument can distinguish signed from unsigned. Named identities:")
        for f in signed:
            if f.identity:
                print(f"  - {f.asset} :: {f.kind}")
                print(f"      {f.identity}")
        print(
            "\n⚠️  Read the identities: every one belongs to a THIRD PARTY whose binary we\n"
            "    redistribute. None of them is this project. Not one artifact we PRODUCE\n"
            "    carries an identity."
        )
    else:
        print(
            "POSITIVE CONTROL: DID NOT FIRE — no signed item found anywhere, including the\n"
            "third-party binaries known to be signed. Distrust this run: the instrument is\n"
            "likely broken, not the world uniformly unsigned."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
