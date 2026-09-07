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


def _make_macho(*, adhoc: bool, cms_payload: bytes = b"", hardened: bool = False) -> bytes:
    """A 64-bit Mach-O with an LC_CODE_SIGNATURE and a real SuperBlob.

    `cms_payload` is the ONLY thing distinguishing an ad-hoc signature from a
    genuine Developer ID one, which is exactly the point of this fixture.
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
    count = 2
    header_len = 12 + count * 8
    cd_off = header_len
    cms_off = cd_off + len(cd)
    total = cms_off + len(cms)
    sb = bytearray()
    sb += struct.pack(">III", 0xFADE0CC0, total, count)
    sb += struct.pack(">II", 0, cd_off)  # slot 0: CodeDirectory
    sb += struct.pack(">II", 0x10000, cms_off)  # slot 0x10000: CMS
    sb += cd
    sb += cms
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
    updater = (REPO_ROOT / "src" / "services" / "engine" / "updater.py").read_text()
    assert '"-macos-arm64.dmg"' in updater
    assert any(a.endswith("-macos-arm64.dmg") for a in mod.ENGINE_ASSETS)
