"""PS-346: the signing-state reader, and the one distinction it must never blur.

WHAT THESE TESTS ARE FOR
────────────────────────
`scripts/ps346_signing_state.py` answers "is this artifact signed, and by whom?"
for every asset this project publishes. Its whole value rests on **one
distinction that a naive reader gets backwards**:

    an AD-HOC macOS signature LOOKS signed and IS NOT.

It has an `LC_CODE_SIGNATURE`, it has a `SuperBlob`, it has a CMS blob wrapper,
and the bundle around it has a `_CodeSignature/CodeResources` file. All present.
All irrelevant — the CMS payload is **zero bytes**, so there is no certificate
and no identity, and Gatekeeper treats it exactly as unsigned. Our shipped
macOS build is in precisely this state.

So the load-bearing tests here are the ones that would fail if the reader ever
started calling ad-hoc "signed": `test_adhoc_is_not_reported_as_signed` and
`test_real_cms_is_reported_as_signed` are the same parser driven to opposite
verdicts by the single field that distinguishes them.

AND "COULD NOT MEASURE" MUST NEVER COLLAPSE INTO "UNSIGNED"
───────────────────────────────────────────────────────────
The ticket's falsification clause turns on this: an unreadable asset is
`UNREADABLE`, never a silent `UNSIGNED`. A reader that reports an unparseable
file as unsigned would manufacture a finding out of its own failure — and it
would do so in the direction that makes the report look more thorough, which is
the direction nobody checks. `test_unreadable_is_not_counted_as_unsigned` pins
that.

WHY THE FIXTURES ARE BUILT, NOT DOWNLOADED
──────────────────────────────────────────
The real assets are ~940 MB across six files on a GitHub release. A test suite
that needs the network to have an opinion goes quiet the day the network does.
Every fixture here is constructed from the stdlib in the exact shape the reader
parses: real PE headers with a real optional-header data directory, real Mach-O
load commands, a real `SuperBlob`, a real UDIF `koly` trailer, and a real ELF
section table.

The APFS leg is NOT synthesised — building an APFS volume from scratch is a
disproportionate amount of machinery for one code path — so its **unmeasurable**
path is pinned instead (`test_missing_apfs_reader_reports_unreadable`), because
that is the path whose failure mode is silent.
"""

from __future__ import annotations

import importlib.util
import plistlib
import struct
import sys
import zlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "ps346_signing_state.py"


def _load():
    spec = importlib.util.spec_from_file_location("ps346_signing_state", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # The module is a dataclass-using script; it must be registered before exec
    # or @dataclass cannot resolve its own __module__.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = _load()


# ── fixtures: PE ─────────────────────────────────────────────────────────────


def _make_pe(security_size: int = 0, security_rva: int = 0) -> bytes:
    """A minimal but structurally real PE32+ image."""
    buf = bytearray(4096)
    buf[0:2] = b"MZ"
    e_lfanew = 0x80
    struct.pack_into("<I", buf, 0x3C, e_lfanew)
    buf[e_lfanew : e_lfanew + 4] = b"PE\0\0"
    # COFF header (20 bytes) then optional header at +24
    opt = e_lfanew + 24
    struct.pack_into("<H", buf, opt, 0x20B)  # PE32+ magic
    dd = opt + 112  # data directory array for PE32+
    # entry 4 == IMAGE_DIRECTORY_ENTRY_SECURITY
    struct.pack_into("<II", buf, dd + 4 * 8, security_rva, security_size)
    return bytes(buf)


# ── fixtures: Mach-O ─────────────────────────────────────────────────────────


def _make_macho(
    *,
    adhoc: bool,
    cms_payload: bytes = b"",
    hardened: bool = False,
    entitlements: bytes | None = None,
) -> bytes:
    """A 64-bit Mach-O with an LC_CODE_SIGNATURE and a real SuperBlob.

    `cms_payload` is the ONLY thing distinguishing an ad-hoc signature from a
    genuine Developer ID one, which is exactly the point of this fixture.

    `entitlements` populates the real slot 5 payload, so a test can drive the
    CALLER of `entitlement_get_task_allow` and not merely the reader.
    """
    ident = b"dev.persona.test\0"

    # ── CodeDirectory blob ──
    flags = (0x2 if adhoc else 0) | (0x10000 if hardened else 0)
    cd_body = bytearray(64 + len(ident))
    struct.pack_into(">I", cd_body, 0, 0xFADE0C02)  # magic
    struct.pack_into(">I", cd_body, 8, 0x20400)  # version
    struct.pack_into(">I", cd_body, 12, flags)
    struct.pack_into(">I", cd_body, 20, 64)  # identOffset
    struct.pack_into(">I", cd_body, 48, 0)  # teamOffset (none)
    cd_body[64 : 64 + len(ident)] = ident
    struct.pack_into(">I", cd_body, 4, len(cd_body))  # length
    cd = bytes(cd_body)

    # ── CMS blob wrapper ── (8-byte header + payload; empty payload == ad-hoc)
    cms = struct.pack(">II", 0xFADE0B01, 8 + len(cms_payload)) + cms_payload

    # ── SuperBlob ──
    ent = (
        struct.pack(">II", 0xFADE7171, 8 + len(entitlements)) + entitlements
        if entitlements is not None
        else b""
    )
    count = 3 if ent else 2
    header_len = 12 + count * 8
    cd_off = header_len
    cms_off = cd_off + len(cd)
    ent_off = cms_off + len(cms)
    total = ent_off + len(ent)
    sb = bytearray()
    sb += struct.pack(">III", 0xFADE0CC0, total, count)
    sb += struct.pack(">II", 0, cd_off)  # slot 0: CodeDirectory
    sb += struct.pack(">II", 0x10000, cms_off)  # slot 0x10000: CMS
    if ent:
        sb += struct.pack(">II", 5, ent_off)  # slot 5: entitlements
    sb += cd
    sb += cms
    sb += ent
    superblob = bytes(sb)

    # ── Mach-O header + LC_CODE_SIGNATURE ──
    ncmds = 1
    lc = bytearray(16)
    struct.pack_into("<II", lc, 0, 0x1D, 16)  # cmd, cmdsize
    header_size = 32 + len(lc)
    sig_off = header_size
    struct.pack_into("<II", lc, 8, sig_off, len(superblob))

    mh = bytearray(32)
    struct.pack_into("<I", mh, 0, 0xFEEDFACF)
    struct.pack_into("<i", mh, 4, 0x0100000C)  # arm64
    struct.pack_into("<I", mh, 16, ncmds)
    return bytes(mh) + bytes(lc) + superblob


def _make_macho_unsigned() -> bytes:
    """A Mach-O with NO LC_CODE_SIGNATURE at all — a third, distinct state."""
    mh = bytearray(32)
    struct.pack_into("<I", mh, 0, 0xFEEDFACF)
    struct.pack_into("<i", mh, 4, 0x0100000C)
    struct.pack_into("<I", mh, 16, 0)  # ncmds = 0
    return bytes(mh) + b"\0" * 64


# ── fixtures: UDIF and ELF ───────────────────────────────────────────────────


def _make_dmg(path: Path, *, gap: int = 0, resources=("blkx", "plst")) -> None:
    payload = b"\0" * 512
    plist = {"resource-fork": {k: [] for k in resources}}
    xml = plistlib.dumps(plist)
    body = bytearray()
    body += payload
    xml_off = len(body)
    body += xml
    body += b"\xaa" * gap  # a signed image carries a CMS blob in this gap

    koly = bytearray(512)
    koly[0:4] = b"koly"
    struct.pack_into(">Q", koly, 216, xml_off)
    struct.pack_into(">Q", koly, 224, len(xml))
    path.write_bytes(bytes(body) + bytes(koly))


def _make_appimage(path: Path, *, sig_content: bytes = b"") -> None:
    """A minimal ELF64 with a real section header table and .sha256_sig."""
    shstrtab = b"\0.shstrtab\0.sha256_sig\0"
    name_shstrtab = 1
    name_sig = shstrtab.index(b".sha256_sig")

    sig_size = 1024
    sig_body = sig_content.ljust(sig_size, b"\0")

    ehsize = 64
    sig_off = ehsize
    strtab_off = sig_off + sig_size
    shoff = strtab_off + len(shstrtab)

    shentsize, shnum, shstrndx = 64, 3, 2

    eh = bytearray(ehsize)
    eh[0:4] = b"\x7fELF"
    eh[4] = 2  # 64-bit
    struct.pack_into("<Q", eh, 0x28, shoff)
    struct.pack_into("<H", eh, 0x3A, shentsize)
    struct.pack_into("<H", eh, 0x3C, shnum)
    struct.pack_into("<H", eh, 0x3E, shstrndx)

    def sh(name, off, size):
        e = bytearray(shentsize)
        struct.pack_into("<IIQQQQ", e, 0, name, 1, 0, 0, off, size)
        return bytes(e)

    table = sh(0, 0, 0) + sh(name_sig, sig_off, sig_size) + sh(name_shstrtab, strtab_off, len(shstrtab))
    path.write_bytes(bytes(eh) + sig_body + shstrtab + table)


# ═══════════════════════════════════════════════════════════════════════════
# THE LOAD-BEARING PAIR: ad-hoc vs real CMS
# ═══════════════════════════════════════════════════════════════════════════


def test_adhoc_is_not_reported_as_signed():
    """An ad-hoc signature has every visible trapping of a signature and no identity.

    This is the state our shipped macOS build is actually in. If this test ever
    goes green while reporting SIGNED_CMS, the report built on this tool is
    wrong in the most consequential possible direction — it would tell an owner
    the artifacts are signed when a user's Mac will refuse them.
    """
    data = _make_macho(adhoc=True, cms_payload=b"")
    info = mod.read_macho_slice(data, 0)

    assert info["state"] == "ADHOC"
    assert info["state"] != "SIGNED_CMS"
    assert info["identity"] is None
    assert info["team"] is None
    assert "EMPTY" in info["detail"] or "no CMS" in info["detail"]


def test_real_cms_is_reported_as_signed():
    """The SAME parser, the SAME structure — one non-empty CMS payload apart."""
    data = _make_macho(adhoc=False, cms_payload=b"\x30\x82\x01\x00" + b"\0" * 32)
    info = mod.read_macho_slice(data, 0)

    assert info["state"] == "SIGNED_CMS"
    assert "CMS payload" in info["detail"]


def test_adhoc_flag_alone_does_not_make_it_signed():
    """Belt and braces: adhoc=True WITH a payload is still not our unsigned case.

    Guards the boolean logic in the classifier, which is the one place a future
    edit could invert the verdict without touching any parsing.
    """
    adhoc_empty = mod.read_macho_slice(_make_macho(adhoc=True, cms_payload=b""), 0)
    real = mod.read_macho_slice(_make_macho(adhoc=False, cms_payload=b"\x30\x82"), 0)
    assert adhoc_empty["state"] != real["state"]


def test_missing_code_signature_is_a_distinct_third_state():
    """UNSIGNED (no load command) must not be folded into ADHOC.

    They are different facts about the build and the report distinguishes them:
    28 slices in the shipped bundle are UNSIGNED and 196 are ADHOC.
    """
    info = mod.read_macho_slice(_make_macho_unsigned(), 0)
    assert info["state"] == "UNSIGNED"
    assert "no LC_CODE_SIGNATURE" in info["detail"]


def test_hardened_runtime_flag_is_read():
    """§3 of the report turns on this bit: no hardened runtime == no notarization."""
    off = mod.read_macho_slice(_make_macho(adhoc=True), 0)
    on = mod.read_macho_slice(_make_macho(adhoc=True, hardened=True), 0)
    assert off["hardened_runtime"] is False
    assert on["hardened_runtime"] is True


# ═══════════════════════════════════════════════════════════════════════════
# PE / Authenticode
# ═══════════════════════════════════════════════════════════════════════════


def test_pe_without_certificate_table_is_unsigned():
    report = mod.Report()
    mod.read_pe(report, "asset.exe", "installer", _make_pe(security_size=0))
    (f,) = report.findings
    assert f.state == "UNSIGNED"
    assert "size=0" in f.detail


def test_pe_with_certificate_table_is_signed():
    report = mod.Report()
    mod.read_pe(report, "asset.exe", "installer", _make_pe(security_size=4096, security_rva=0x1000))
    (f,) = report.findings
    assert f.state == "SIGNED_CMS"


def test_non_pe_is_unreadable_not_unsigned():
    """A file that is not a PE at all must not be reported as an unsigned PE."""
    report = mod.Report()
    mod.read_pe(report, "asset.exe", "installer", b"not a PE image at all")
    (f,) = report.findings
    assert f.state == "UNREADABLE"
    assert f.state != "UNSIGNED"


# ═══════════════════════════════════════════════════════════════════════════
# UDIF (.dmg) image-level signature
# ═══════════════════════════════════════════════════════════════════════════


def test_dmg_without_csig_is_unsigned(tmp_path: Path):
    p = tmp_path / "x.dmg"
    _make_dmg(p, gap=0, resources=("blkx", "plst"))
    report = mod.Report()
    mod.read_dmg(report, "x.dmg", str(p))
    (f,) = report.findings
    assert f.state == "UNSIGNED"
    assert "no cSig" in f.detail


def test_dmg_with_csig_resource_is_signed(tmp_path: Path):
    p = tmp_path / "x.dmg"
    _make_dmg(p, gap=0, resources=("blkx", "plst", "cSig"))
    report = mod.Report()
    mod.read_dmg(report, "x.dmg", str(p))
    (f,) = report.findings
    assert f.state == "SIGNED_CMS"


def test_dmg_with_trailing_blob_is_signed(tmp_path: Path):
    """The other signal: a CMS blob between the plist and the koly trailer."""
    p = tmp_path / "x.dmg"
    _make_dmg(p, gap=256, resources=("blkx", "plst"))
    report = mod.Report()
    mod.read_dmg(report, "x.dmg", str(p))
    (f,) = report.findings
    assert f.state == "SIGNED_CMS"


def test_dmg_without_koly_is_unreadable(tmp_path: Path):
    p = tmp_path / "x.dmg"
    p.write_bytes(b"\0" * 2048)
    report = mod.Report()
    mod.read_dmg(report, "x.dmg", str(p))
    (f,) = report.findings
    assert f.state == "UNREADABLE"


# ═══════════════════════════════════════════════════════════════════════════
# AppImage / ELF
# ═══════════════════════════════════════════════════════════════════════════


def test_appimage_with_zeroed_signature_section_is_unsigned(tmp_path: Path):
    """The published AppImages are exactly this: section present, all zero.

    The reserved-but-empty case is the one a careless check calls "has a
    signature section, therefore signed".
    """
    p = tmp_path / "x.AppImage"
    _make_appimage(p, sig_content=b"")
    report = mod.Report()
    mod.read_appimage(report, "x.AppImage", str(p))
    assert [f.state for f in report.findings] == ["UNSIGNED"]
    assert "ALL ZERO" in report.findings[0].detail


def test_appimage_with_populated_signature_section_is_signed(tmp_path: Path):
    p = tmp_path / "x.AppImage"
    _make_appimage(p, sig_content=b"-----BEGIN PGP SIGNATURE-----")
    report = mod.Report()
    mod.read_appimage(report, "x.AppImage", str(p))
    assert [f.state for f in report.findings] == ["SIGNED_CMS"]


def test_non_elf_is_unreadable_not_unsigned(tmp_path: Path):
    p = tmp_path / "x.AppImage"
    p.write_bytes(b"definitely not an ELF")
    report = mod.Report()
    mod.read_appimage(report, "x.AppImage", str(p))
    (f,) = report.findings
    assert f.state == "UNREADABLE"


# ═══════════════════════════════════════════════════════════════════════════
# "COULD NOT MEASURE" MUST NEVER BECOME "UNSIGNED"
# ═══════════════════════════════════════════════════════════════════════════


def test_missing_apfs_reader_reports_unreadable(tmp_path: Path, monkeypatch):
    """Without libfsapfs the macOS bundle leg is UNREADABLE, never UNSIGNED.

    This is the ticket's falsification clause expressed as a test: a platform
    we could not reach is recorded as unmeasured and NAMED, never inferred.
    """
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__

    def fake_import(name, *a, **kw):
        if name == "pyfsapfs":
            raise ImportError("no pyfsapfs")
        return real_import(name, *a, **kw)

    monkeypatch.setitem(sys.modules, "pyfsapfs", None)
    monkeypatch.setattr("builtins.__import__", fake_import)

    p = tmp_path / "x.dmg"
    _make_dmg(p)
    report = mod.Report()
    mod.read_dmg_bundle(report, "x.dmg", str(p), str(tmp_path))
    (f,) = report.findings
    assert f.state == "UNREADABLE"
    assert f.state != "UNSIGNED"
    assert "NOT counted as unsigned" in f.detail


def test_unreadable_asset_is_not_counted_as_unsigned(tmp_path: Path):
    """An undecompressable image reports UNREADABLE and says so explicitly."""
    p = tmp_path / "x.dmg"
    _make_dmg(p)  # a valid koly, but no Apple_APFS partition to extract
    report = mod.Report()
    mod.read_dmg_bundle(report, "x.dmg", str(p), str(tmp_path))
    (f,) = report.findings
    # Either it could not find a partition or could not read one; both must be
    # UNREADABLE and neither may silently become UNSIGNED.
    assert f.state == "UNREADABLE"
    assert "NOT counted as unsigned" in f.detail


# ═══════════════════════════════════════════════════════════════════════════
# Structural: the asset list must track what we actually publish
# ═══════════════════════════════════════════════════════════════════════════


def test_every_shipped_platform_is_covered():
    """All three platforms, for BOTH the app and the engine, or the sweep lies.

    A reader that silently stopped covering a platform would report "nothing
    unsigned found" for it — the same false-clean this whole file guards.
    """
    assets = mod.APP_ASSETS + mod.ENGINE_ASSETS
    assert any(a.endswith(".exe") for a in mod.APP_ASSETS)
    assert any(a.endswith(".dmg") for a in mod.APP_ASSETS)
    assert any(a.endswith(".AppImage") for a in mod.APP_ASSETS)
    assert any(a.endswith(".zip") for a in mod.ENGINE_ASSETS)
    assert any(a.endswith(".dmg") for a in mod.ENGINE_ASSETS)
    assert any(a.endswith(".AppImage") for a in mod.ENGINE_ASSETS)
    assert len(assets) == 6


def test_engine_macos_asset_matches_the_updater_rule():
    """The engine dmg is arm64, per src/services/engine/updater.py.

    RELEASING.md still documents `-macos-x86_64.dmg`; the CODE is right and the
    doc is stale (report §8). This test pins the reader to the code, so that a
    later doc fix cannot quietly retarget this sweep at an asset that does not
    exist.
    """
    # NAME THE ENCODING. A bare read_text() decodes with the platform's
    # preferred encoding — cp1252 on Windows — and updater.py contains
    # non-ASCII bytes, so this raised UnicodeDecodeError on windows-latest
    # rather than merely risking one. tests/test_encoding_discipline.py
    # enforces this project-wide.
    updater = (REPO_ROOT / "src" / "services" / "engine" / "updater.py").read_text(
        encoding="utf-8"
    )
    assert '"-macos-arm64.dmg"' in updater
    assert any(a.endswith("-macos-arm64.dmg") for a in mod.ENGINE_ASSETS)


# ═══════════════════════════════════════════════════════════════════════════
# THE VERDICT SITE ITSELF
#
# ⭐ These drive `classify_tally` DIRECTLY, and that is the whole point of them.
# The pre-existing `test_unreadable_asset_is_not_counted_as_unsigned` above
# exercises `read_dmg_bundle` on an image with no readable partition — which
# takes an EARLY RETURN before the tally is ever built. It guards the
# early-return legs and reaches the classifier not at all, so the aggregate
# verdict could (and did) collapse UNREADABLE into UNSIGNED with that test
# green. A guarded-looking invariant with an unguarded verdict site is exactly
# the shape that survives review.
# ═══════════════════════════════════════════════════════════════════════════


def test_all_unreadable_slices_do_not_aggregate_to_unsigned():
    """The contract in the module docstring, pinned at the place it can break.

    A bundle whose every slice failed to parse was NOT read. Reporting it as
    UNSIGNED would manufacture a finding out of the instrument's own failure —
    and in the direction that makes the report look more thorough.
    """
    assert mod.classify_tally({"UNREADABLE": 3}) == "UNREADABLE"
    assert mod.classify_tally({"UNREADABLE": 3}) != "UNSIGNED"


def test_empty_tally_is_unreadable_not_unsigned():
    """Nothing was read at all — 'we found no signature' is a claim about bytes
    nobody looked at. This is the FAT-64 compounding path's landing site."""
    assert mod.classify_tally({}) == "UNREADABLE"
    assert mod.classify_tally({}) != "UNSIGNED"


def test_mixed_tally_still_reports_the_state_that_was_measured():
    """UNREADABLE must not be so sticky that it erases real evidence.

    Slices that WERE read are evidence. A bundle with two unparseable slices and
    one genuine ad-hoc signature is ad-hoc — the unreadable remainder is carried
    in `detail`, not promoted over a measurement.
    """
    assert mod.classify_tally({"UNREADABLE": 2, "ADHOC": 1}) == "ADHOC"
    assert mod.classify_tally({"UNREADABLE": 9, "SIGNED_CMS": 1}) == "SIGNED_CMS"
    assert mod.classify_tally({"UNREADABLE": 1, "UNSIGNED": 4}) == "UNSIGNED"


def test_classifier_keeps_the_adhoc_distinction_it_exists_for():
    """The original invariant, re-pinned at the extracted function.

    One real CMS signature among ad-hoc ones does NOT make a bundle signed —
    this is the shipped macOS reading (196 ADHOC / 28 UNSIGNED / 1 SIGNED_CMS,
    that 1 being the vendored Node.js) and it must keep reading ADHOC.
    """
    assert mod.classify_tally({"ADHOC": 196, "UNSIGNED": 28, "SIGNED_CMS": 1}) == "ADHOC"
    assert mod.classify_tally({"SIGNED_CMS": 4}) == "SIGNED_CMS"
    assert mod.classify_tally({"UNSIGNED": 9}) == "UNSIGNED"


# ═══════════════════════════════════════════════════════════════════════════
# FAT (universal) binaries — the two layouts are DIFFERENT STRUCTS
#
# ⚠️ Reading a FAT-64 header with the 32-bit stride does not fail loudly. It
# returns offset 0, i.e. it re-reads the fat header as if it were a Mach-O,
# which then reports UNREADABLE — and, before the classifier fix above, that
# became a confident UNSIGNED about a binary that was never read. Silent,
# plausible and wrong. Every real fixture in this file is THIN, which is why
# these paths were previously untested.
# ═══════════════════════════════════════════════════════════════════════════

FAT_MAGIC = 0xCAFEBABE
FAT_MAGIC_64 = 0xCAFEBABF


def _make_fat(*, sixty_four: bool, slice_offset: int, inner: bytes = b"") -> bytes:
    """A universal binary header with ONE slice at `slice_offset`."""
    if sixty_four:
        # fat_arch_64: cputype, cpusubtype, offset(Q), size(Q), align, reserved
        head = struct.pack(">II", FAT_MAGIC_64, 1) + struct.pack(
            ">iiQQII", 0x0100000C, 0, slice_offset, len(inner) or 0x1000, 14, 0
        )
    else:
        # fat_arch: cputype, cpusubtype, offset(I), size(I), align
        head = struct.pack(">II", FAT_MAGIC, 1) + struct.pack(
            ">iiIII", 0x0100000C, 0, slice_offset, len(inner) or 0x1000, 12
        )
    return head.ljust(slice_offset, b"\0") + inner


def test_fat64_slice_offset_is_read_with_the_64_bit_layout():
    """`fat_arch_64` is ">iiQQII" at stride 32, not ">iiIII" at stride 20.

    The failure this pins is NOT a wild number — it is 0, which is why it was
    invisible: offset 0 re-reads the fat header itself.
    """
    data = _make_fat(sixty_four=True, slice_offset=0x4000)
    assert mod.macho_slices(data) == [0x4000]
    assert mod.macho_slices(data) != [0], "the 32-bit stride misparse is back"


def test_fat32_slice_offset_still_reads_correctly():
    """Regression guard: the 32-bit layout must be untouched by the FAT-64 fix."""
    data = _make_fat(sixty_four=False, slice_offset=0x1000)
    assert mod.macho_slices(data) == [0x1000]


def test_fat64_slice_is_actually_parseable_end_to_end():
    """The offset being right is only worth something if the slice then reads.

    Drives the full path: fat header -> correct offset -> a real Mach-O with an
    ad-hoc SuperBlob -> ADHOC, not UNREADABLE.
    """
    inner = _make_macho(adhoc=True)
    data = _make_fat(sixty_four=True, slice_offset=0x4000, inner=inner)
    (base,) = mod.macho_slices(data)
    assert base == 0x4000
    info = mod.read_macho_slice(data, base)
    assert info["state"] == "ADHOC"


def test_truncated_fat_header_reports_what_was_readable_and_invents_nothing():
    """A header claiming 99 slices but carrying none must not read past its end.

    Returning [] here (rather than raising, or fabricating offsets from
    out-of-bounds memory) is what lets the classifier report UNREADABLE.
    """
    data = struct.pack(">II", FAT_MAGIC_64, 99)
    assert mod.macho_slices(data) == []
    assert mod.classify_tally({}) == "UNREADABLE"


def test_fat64_misparse_would_compound_into_a_false_unsigned():
    """⭐ The two blockers compound, and this pins the compounded failure.

    FAT-64 misparse -> offset 0 -> the fat header read as a Mach-O -> UNREADABLE
    -> (with the old classifier) a confident UNSIGNED about a binary that was
    never read. Both halves are now closed; this test asserts the SEAM.
    """
    data = _make_fat(sixty_four=True, slice_offset=0x4000, inner=_make_macho(adhoc=True))
    # If the offset were misparsed to 0, this is what the slice read would say:
    assert mod.read_macho_slice(data, 0)["state"] == "UNREADABLE"
    # ...and that must NOT become UNSIGNED at the verdict site.
    assert mod.classify_tally({"UNREADABLE": 1}) != "UNSIGNED"
    # With the fix, the real offset is used and the true state is reported.
    assert mod.classify_tally({mod.read_macho_slice(data, 0x4000)["state"]: 1}) == "ADHOC"


# ═══════════════════════════════════════════════════════════════════════════
# §3 — what a Developer ID certificate alone does NOT deliver
#
# These are NOTARIZATION prerequisites, not signature facts. They are read by
# the script so that report §1–§3 is genuinely reproducible with the committed
# tool rather than resting on a one-off reading nobody can re-run.
# ═══════════════════════════════════════════════════════════════════════════


def test_get_task_allow_true_is_detected():
    """The shipped debug entitlement — an explicit notary-rejection condition."""
    blob = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "">'
        b'<plist version="1.0"><dict>'
        b"<key>com.apple.security.get-task-allow</key><true/>"
        b"</dict></plist>"
    )
    assert mod.entitlement_get_task_allow(blob) is True


def test_get_task_allow_false_is_distinct_from_absent():
    """⚠️ THREE-VALUED ON PURPOSE — the same discipline as UNREADABLE.

    "we read the entitlements and the key is not set" and "there were no
    entitlements to read" are different facts. Collapsing them would report a
    clean bill of health for a binary nobody parsed.
    """
    blob = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<plist version="1.0"><dict>'
        b"<key>com.apple.security.cs.allow-jit</key><true/>"
        b"</dict></plist>"
    )
    assert mod.entitlement_get_task_allow(blob) is False  # read, key absent
    assert mod.entitlement_get_task_allow(None) is None  # nothing to read
    assert mod.entitlement_get_task_allow(b"") is None  # nothing to read
    assert mod.entitlement_get_task_allow(b"\xde\xad\xbe\xef") is None  # unparseable


def test_get_task_allow_unparseable_is_not_reported_as_absent():
    """A DER-encoded (non-XML) entitlements blob is NOT parsed here, and must
    say so by returning None rather than claiming the entitlement is absent."""
    assert mod.entitlement_get_task_allow(b"0\x82\x01\x00\x30\x82") is None


# ═══════════════════════════════════════════════════════════════════════════
# §3, THE WALK — the half of the notarization check nothing was driving
#
# ⚠️ THE TESTS ABOVE ALL DRIVE READERS; THIS SECTION DRIVES THE WALK THAT
# FEEDS THEM. That gap is how a defect got in twice: `entitlement_get_task_allow`
# is carefully three-valued and pinned by three tests, and its CALLER threw the
# third value away; `_apfs_machos` decides what counts as a notarization ticket
# and had no test at all. A guard is only in force where something exercises the
# site, not merely the function.
#
# `_apfs_machos` takes a duck-typed pyfsapfs entry, so a real APFS volume is not
# needed to drive it — a small fake with the four methods it calls is.
# ═══════════════════════════════════════════════════════════════════════════


class _FakeEntry:
    """A pyfsapfs-shaped directory/file entry, built from a nested dict.

    `_apfs_machos` calls exactly four things on an entry:
    `get_number_of_sub_file_entries`, `get_sub_file_entry`, `get_name`, and —
    for leaves — `get_size` / `seek_offset` / `read_buffer`. That is the whole
    contract, so this is the whole fake.
    """

    def __init__(self, name: str, content):
        self._name = name
        # dict => directory; bytes => file
        self._children = (
            [_FakeEntry(k, v) for k, v in content.items()] if isinstance(content, dict) else []
        )
        self._data = content if isinstance(content, bytes) else b""
        self._pos = 0

    def get_name(self):
        return self._name

    def get_number_of_sub_file_entries(self):
        return len(self._children)

    def get_sub_file_entry(self, i):
        return self._children[i]

    def get_size(self):
        return len(self._data)

    def seek_offset(self, off, whence):
        self._pos = off

    def read_buffer(self, n):
        out = self._data[self._pos : self._pos + n]
        self._pos += n
        return out


def _walk(tree: dict):
    """Run the real `_apfs_machos` over a fake volume; return (machos, notes)."""
    acc: list = []
    notes: dict[str, list[str]] = {"code_resources": [], "staple": [], "unclassified": []}
    mod._apfs_machos(_FakeEntry("", tree), "", acc, notes)
    return acc, notes


def test_signed_but_not_notarized_bundle_reports_no_stapled_ticket():
    """⛔ THE CASE A STAPLE CHECK EXISTS FOR, and the one that fails CLEAN.

    A bundle with a real DETACHED signature carries `_CodeSignature/CodeDirectory`
    — written by `codesign`, a SIGNATURE slot. A notarization ticket is written
    by `stapler staple` and is a different file entirely. Signing is not
    notarizing, and a checker that reads the first as the second reports
    "notarized ✓" about an artifact that was never sent to the notary.

    ⚠️ READ THE FAILURE DIRECTION: this fixture is what our own bundles become
    the first day someone buys a certificate — so the wrong answer here is not
    latent forever, it arrives exactly when the answer starts to matter.
    """
    _, notes = _walk(
        {
            "A.app": {
                "Contents": {
                    "_CodeSignature": {
                        "CodeDirectory": b"\xfa\xde\x0c\x02",  # codesign's, not stapler's
                        "CodeResources": b"<plist/>",
                    },
                    "MacOS": {"A": _make_macho(adhoc=False, cms_payload=b"\x30\x82")},
                }
            }
        }
    )
    assert notes["staple"] == [], f"signed-but-unnotarized reported a ticket: {notes['staple']}"
    # The seal IS present — that half of the reading is unchanged and correct.
    assert notes["code_resources"] == ["/A.app/Contents/_CodeSignature/CodeResources"]


def test_a_real_stapled_ticket_is_still_detected():
    """⛔ THE POSITIVE CONTROL — a genuinely NOTARIZED AND STAPLED `.app`.

    This is the single case a staple check exists to answer PRESENT for, and it
    is the one the previous fixture did not actually contain. `stapler staple`
    does NOT write a `*.ticket` file into an app bundle: it writes the raw
    ticket blob to the bundle subpath `Contents/CodeResources`. Verified in
    `apple-platform-rs`'s `staple_ticket_to_bundle`, which resolves the bundle
    path `"CodeResources"` and writes `ticket_data` into it verbatim.

    ⚠️ SO ONE FILENAME CARRIES TWO DIFFERENT FACTS, and this fixture holds both
    at once — which is the real shape of a stapled bundle:
        Contents/CodeResources                 -> the TICKET (DER blob)
        Contents/_CodeSignature/CodeResources  -> the SEAL   (a plist)

    Both assertions matter and they fail independently:
      * a name-only match reports this bundle's ticket as ABSENT (a false
        negative that reads to an operator as "the stapling did not take");
      * and the SAME line files that ticket as a second seal, moving the seal
        count from 1 to 2 with nothing to say why.
    """
    _, notes = _walk(
        {
            "A.app": {
                "Contents": {
                    # DER-encoded ASN.1 — what a notarization ticket actually is
                    "CodeResources": b"\x30\x82\x0a\x1f\x02\x01\x01",
                    "_CodeSignature": {
                        "CodeResources": b'<?xml version="1.0"?><plist/>',
                        "CodeDirectory": b"\xfa\xde\x0c\x02",
                    },
                }
            }
        }
    )
    assert notes["staple"] == ["/A.app/Contents/CodeResources"], (
        f"a genuinely stapled bundle was not detected: {notes['staple']}"
    )
    # ...and the ticket was NOT absorbed into the seal count.
    assert notes["code_resources"] == ["/A.app/Contents/_CodeSignature/CodeResources"], (
        f"the ticket was counted as a seal: {notes['code_resources']}"
    )


def test_binary_plist_seal_is_a_seal_not_a_ticket():
    """A seal may be a BINARY plist, and `bplist00` is not XML.

    The discriminator is "is this a plist", not "is this text" — reading it as
    the latter would report every binary-plist seal as a stapled ticket, which
    is the CLEAN-direction failure this whole check exists to avoid.
    """
    _, notes = _walk({"A.app": {"Contents": {"CodeResources": b"bplist00\xd1\x01\x02"}}})
    assert notes["staple"] == []
    assert notes["code_resources"] == ["/A.app/Contents/CodeResources"]


def test_unreadable_code_resources_falls_back_to_seal_not_ticket():
    """⚠️ AN UNREAD FILE MUST NOT BECOME A NOTARIZATION CLAIM.

    When the bytes cannot be read at all, the fallback is SEAL — the reading
    that asserts nothing new — never TICKET, which would claim notarization
    about a file nobody looked at.
    """

    class _Unreadable(_FakeEntry):
        def read_buffer(self, n):
            raise OSError("simulated read failure")

    root = _FakeEntry("", {"A.app": {"Contents": {"CodeResources": b"\x30\x82"}}})
    contents = root._children[0]._children[0]
    contents._children = [_Unreadable("CodeResources", b"\x30\x82")]

    acc: list = []
    notes: dict[str, list[str]] = {"code_resources": [], "staple": [], "unclassified": []}
    mod._apfs_machos(root, "", acc, notes)

    assert notes["staple"] == [], "an unreadable file was claimed as a notarization ticket"
    assert notes["code_resources"] == ["/A.app/Contents/CodeResources"]


def test_a_codeResources_that_is_neither_plist_nor_der_is_claimed_as_neither():
    """⚠️ THE THIRD OUTCOME, and the reason both sides are tested POSITIVELY.

    "Not a plist" is not the same claim as "is a ticket". A file called
    `CodeResources` whose bytes match neither format — a stray, a symlink
    target, a truncation — is reported as NEITHER: counting it as a ticket
    would fabricate a notarization claim, and counting it as a seal would
    inflate a figure the report quotes.
    """
    _, notes = _walk({"A.app": {"Contents": {"CodeResources": b"not a plist or der"}}})
    assert notes["staple"] == []
    assert notes["code_resources"] == []
    assert notes["unclassified"] == ["/A.app/Contents/CodeResources"]


def test_dot_ticket_match_is_kept_for_non_bundle_entities():
    """The narrower `*.ticket` match still fires — it costs nothing, and it is
    the shape `stapler` uses for entities that are not app bundles. It simply
    cannot be the ONLY match, because a `.app` never gets one."""
    _, notes = _walk({"thing": {"A.ticket": b"\x30\x82\x00"}})
    assert notes["staple"] == ["/thing/A.ticket"]


def test_walk_finds_machos_at_any_depth():
    """The walk's other job — the Mach-O accumulator — still works, and the
    notes are collected on the SAME single pass rather than a second one.

    ⚠️ The seal content here is a REAL plist, not a placeholder byte. Since the
    seal/ticket discriminator reads bytes rather than the filename, a stand-in
    like `b"x"` is no longer a seal — it is an unidentified binary blob at the
    bundle-root ticket path, which is exactly what a ticket looks like.
    """
    macho = _make_macho(adhoc=True)
    acc, notes = _walk(
        {"A.app": {"Contents": {"MacOS": {"A": macho}, "CodeResources": b"<?xml ?><plist/>"}}}
    )
    assert [p for p, _s, _e in acc] == ["/A.app/Contents/MacOS/A"]
    assert notes["code_resources"] == ["/A.app/Contents/CodeResources"]


# ── the caller of the three-valued entitlements reader ───────────────────────


def _bundle_report(tree: dict, monkeypatch, tmp_path: Path):
    """Drive `read_dmg_bundle`'s reporting over a fake APFS volume.

    Everything up to the walk (UDIF extraction, pyfsapfs) is stubbed; the tally
    loop, the entitlement accounting and every `report.add` below it are the
    REAL code — which is the part these tests exist to reach.
    """

    class _Vol:
        def get_root_directory(self):
            return _FakeEntry("", tree)

    class _Container:
        def open(self, _p):
            pass

        def get_volume(self, _i):
            return _Vol()

    fake = type("pyfsapfs", (), {"container": _Container})
    monkeypatch.setitem(sys.modules, "pyfsapfs", fake)
    monkeypatch.setattr(mod, "udif_extract", lambda _p, out: Path(out).write_bytes(b"") or True)

    report = mod.Report()
    mod.read_dmg_bundle(report, "x.dmg", str(tmp_path / "x.dmg"), str(tmp_path))
    return report


def _ents_finding(report):
    (f,) = [x for x in report.findings if "get-task-allow" in x.kind]
    return f


_DER_ENTS = b"0\x82\x01\x00\x30\x82\xde\xad"  # DER plist: real shape, not parsed here
_XML_NO_KEY = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<plist version="1.0"><dict>'
    b"<key>com.apple.security.cs.allow-jit</key><true/>"
    b"</dict></plist>"
)


def test_unparseable_entitlements_are_not_reported_as_absent(tmp_path: Path, monkeypatch):
    """⛔ THE CALLER MUST NOT COLLAPSE THE READER'S THIRD VALUE.

    `entitlement_get_task_allow` returns None for a DER blob and its docstring
    says that is NOT the claim "the entitlement is absent". A caller that treats
    None as False makes exactly that claim — a clean bill of health for a blob
    nobody parsed, which is the UNREADABLE → UNSIGNED collapse one level down.
    """
    report = _bundle_report(
        {"A": _make_macho(adhoc=True, entitlements=_DER_ENTS)}, monkeypatch, tmp_path
    )
    f = _ents_finding(report)
    assert f.state == "UNREADABLE", f"unparseable entitlements reported as {f.state}"
    assert f.state != "ABSENT"
    assert "unparseable" in f.detail


def test_mixed_readable_and_unparseable_entitlements_state_the_unread_count(
    tmp_path: Path, monkeypatch
):
    """A partly-readable bundle reports what it MEASURED and sizes what it did
    not — rather than quietly widening the denominator to cover both."""
    report = _bundle_report(
        {
            "readable": _make_macho(adhoc=True, entitlements=_XML_NO_KEY),
            "opaque": _make_macho(adhoc=True, entitlements=_DER_ENTS),
        },
        monkeypatch,
        tmp_path,
    )
    f = _ents_finding(report)
    assert f.state == "ABSENT"  # of the ONE slice actually read
    assert "all 1 READABLE slice(s)" in f.detail
    assert "1/2 further blob(s) were unparseable" in f.detail


def test_readable_entitlements_still_report_the_shipped_debug_entitlement(
    tmp_path: Path, monkeypatch
):
    """The published §3 finding — get-task-allow PRESENT AND TRUE — is
    unchanged by the unreadable accounting above."""
    xml_true = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<plist version="1.0"><dict>'
        b"<key>com.apple.security.get-task-allow</key><true/>"
        b"</dict></plist>"
    )
    report = _bundle_report(
        {"A": _make_macho(adhoc=True, entitlements=xml_true)}, monkeypatch, tmp_path
    )
    f = _ents_finding(report)
    assert f.state == "PRESENT"
    assert "1/1 readable slice(s)" in f.detail


# ── the REPORTED rows for seal / ticket, driven end to end ───────────────────
#
# ⚠️ Round 3's lesson, applied again: the walk's `notes` dict is not the thing a
# human reads — the two `report.add` rows below it are. Those rows have their
# own logic (the count, the example path, the bound statement), and asserting on
# `notes` alone leaves all of it undriven. These tests reach it.


def _row(report, kind_fragment: str):
    (f,) = [x for x in report.findings if kind_fragment in x.kind]
    return f


def test_reported_rows_split_a_stapled_bundle_correctly(tmp_path: Path, monkeypatch):
    """⛔ THE POSITIVE CONTROL AT THE REPORTING LAYER.

    A notarized+stapled bundle must produce `PRESENT` on the ticket row AND a
    seal count of exactly 1 — not `ABSENT` plus a count of 2.
    """
    report = _bundle_report(
        {
            "A.app": {
                "Contents": {
                    "CodeResources": b"\x30\x82\x0a\x1f\x02\x01\x01",  # the ticket
                    "_CodeSignature": {"CodeResources": b"<?xml ?><plist/>"},  # the seal
                }
            }
        },
        monkeypatch,
        tmp_path,
    )
    ticket = _row(report, "stapled notarization ticket")
    assert ticket.state == "PRESENT", f"a stapled bundle reported {ticket.state}"
    assert "/A.app/Contents/CodeResources" in ticket.detail

    seal = _row(report, "CodeResources")
    assert seal.state == "PRESENT"
    assert "1 CodeResources seal(s)" in seal.detail, f"seal count absorbed the ticket: {seal.detail}"


def test_the_dmg_bound_is_stated_on_every_ticket_row(tmp_path: Path, monkeypatch):
    """⚠️ A BOUND THAT IS ONLY TRUE AND NOT SAID IS NOT A BOUND.

    This is a BUNDLE-level check. An image-level `.dmg` staple lives in the UDIF
    code-signature superblob, not in the filesystem, so no walk can reach it —
    and a future re-runner reading `ABSENT` must be told that, on the row
    itself, in both the PRESENT and the ABSENT branch.
    """
    for tree in (
        {"A.app": {"Contents": {"CodeResources": b"\x30\x82\x0a\x1f"}}},  # PRESENT
        {"A.app": {"Contents": {"_CodeSignature": {"CodeResources": b"<?xml ?><plist/>"}}}},  # ABSENT
    ):
        report = _bundle_report(tree, monkeypatch, tmp_path)
        detail = _row(report, "stapled notarization ticket").detail
        assert "BUND" in detail.upper(), f"no bound stated: {detail}"
        assert "UDIF" in detail, f"the image-level staple is not named: {detail}"


def test_an_unclassified_codeResources_is_disclosed_on_the_seal_row(tmp_path: Path, monkeypatch):
    """A file matching NEITHER format is counted as neither — and SAID so,
    rather than vanishing between the two buckets."""
    report = _bundle_report(
        {
            "A.app": {
                "Contents": {
                    "CodeResources": b"neither plist nor der",
                    "_CodeSignature": {"CodeResources": b"<?xml ?><plist/>"},
                }
            }
        },
        monkeypatch,
        tmp_path,
    )
    seal = _row(report, "CodeResources")
    assert "1 CodeResources seal(s)" in seal.detail
    assert "NEITHER a plist nor DER" in seal.detail, f"the unclassified file vanished: {seal.detail}"
    assert _row(report, "stapled notarization ticket").state == "ABSENT"
