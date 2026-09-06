"""PS-343: the engine release provenance record, and the verifier that can go RED.

WHAT THESE TESTS ARE FOR
────────────────────────
The record in `engine/releases/` is only worth having if a *disagreement between
the record and the bytes* is detectable. So the load-bearing tests here are the
**red** ones: each takes a synthetic artifact that a record correctly describes,
alters one recorded value, and asserts the verifier reports RED and exits 1.

A checker that cannot go red is not evidence, however detailed its output — the
project already recorded that lesson (PS-301: a 162-line reproduction script
with no non-zero exit path at all, which "passed" on the defect). These tests
exist so this verifier cannot quietly become that.

WHY THE FIXTURES ARE BUILT, NOT DOWNLOADED
──────────────────────────────────────────
The real assets are ~585 MB across three files and live on a GitHub release. A
test suite that needs the network to have an opinion is a test suite that goes
quiet the day the network does. So every fixture here is **constructed from the
stdlib** in the shape the deriver reads — a real zip with a real Chromium
`Chrome-bin/<v>/<v>.manifest`, a real UDIF disk image with a real `koly`
trailer and zlib-compressed `blkx` chunks carrying a real `Info.plist`.

The one thing that CANNOT be synthesised is the Linux AppImage, which is
extracted by *executing* it. That deriver is exercised against the published
asset by hand (recorded in the PS-343 PR), and here only its UNMEASURABLE path
is pinned — because "could not measure" collapsing into "passed" is the failure
mode that would make this whole file decorative.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import plistlib
import struct
import sys
import zipfile
import zlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RECORDS_DIR = REPO_ROOT / "engine" / "releases"
VERIFIER = REPO_ROOT / "scripts" / "ps343_verify_release_provenance.py"


def _load_verifier():
    """Import the verifier by path, without running its CLI.

    `scripts/` is not a package on this project — `tests/test_ps299_rebase_
    probe_gate.py` loads its probe the same way, and matching that convention
    keeps this file runnable from any working directory.
    """
    spec = importlib.util.spec_from_file_location("ps343_verify_release_provenance", VERIFIER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ps343_verify_release_provenance"] = mod
    spec.loader.exec_module(mod)
    return mod


_V = _load_verifier()

CONFIDENCES = _V.CONFIDENCES
SCHEMA = _V.SCHEMA
Report = _V.Report
Unmeasurable = _V.Unmeasurable
derive_linux_appimage = _V.derive_linux_appimage
derive_macos_dmg = _V.derive_macos_dmg
derive_windows_zip = _V.derive_windows_zip
lint_record = _V.lint_record
load_records = _V.load_records
main = _V.main
normalise_digest = _V.normalise_digest
walk_fields = _V.walk_fields
SHIPPED_TAG = "personium-152.0.7977.75"

SWITCHES = [
    "fingerprint",
    "fingerprint-brand",
    "fingerprint-brand-version",
    "fingerprint-device-scale-factor",
    "fingerprint-hardware-concurrency",
    "fingerprint-location",
    "fingerprint-platform",
    "fingerprint-platform-version",
    "fingerprint-screen-height",
    "fingerprint-screen-width",
    "fingerprinting-canvas-image-data-noise",
]


# ── the record that actually shipped ────────────────────────────────────────
@pytest.fixture(scope="module")
def shipped_record() -> dict:
    return json.loads(
        (RECORDS_DIR / f"{SHIPPED_TAG}.json").read_text(encoding="utf-8")
    )


def test_the_shipped_engine_has_a_record_in_the_repository():
    """The ticket's territory in one assertion: a published engine's provenance
    is answerable FROM THE REPOSITORY, not from a CI artifact that may have
    expired."""
    assert (RECORDS_DIR / f"{SHIPPED_TAG}.json").is_file()


def test_the_record_lints_clean(shipped_record):
    report = Report()
    lint_record(shipped_record, report)
    assert report.red == [], [(c.name, c.detail) for c in report.red]


def test_every_field_declares_how_it_was_established(shipped_record):
    """No field may sit in the record without saying whether it was derived,
    read from the repo, or is a stated gap. That vocabulary IS the honesty
    mechanism — a field with no confidence is a claim with no provenance."""
    leaves = list(walk_fields(shipped_record))
    assert leaves, "the record carries no {value, confidence} fields at all"
    for path, node in leaves:
        assert node["confidence"] in CONFIDENCES, f"{path} has {node['confidence']!r}"


def test_the_unknowns_are_real_and_named(shipped_record):
    """Nothing in this repo built these assets, so build{} must be honestly
    empty. If a future change fills these in, it had better be because an
    automated build started emitting them — and this test is where that
    conversation happens."""
    for key in ("produced_by", "workflow_run", "host", "toolchain"):
        node = shipped_record["build"][key]
        assert node["confidence"] == "unknown", f"build.{key} claims {node!r}"
        assert node["value"] is None


def test_the_macos_version_discrepancy_is_recorded_not_smoothed_over(shipped_record):
    """The measured fact: the macOS asset is named .75 and contains .64.

    A record that quietly wrote '152.0.7977.75' here would look tidier and be
    false. This pins the honest value AND the discrepancy entry that explains
    it, so neither can be dropped without a test failing.
    """
    mac = next(a for a in shipped_record["assets"] if a["os"] == "macos")
    assert mac["derived"]["bundle_short_version"]["value"] == "152.0.7977.64"
    assert mac["derived"]["bundle_short_version"]["confidence"] == "derived_from_artifact"

    ids = {d["id"] for d in shipped_record["discrepancies"]}
    assert "macos-version-mismatch" in ids

    entry = next(
        d for d in shipped_record["discrepancies"] if d["id"] == "macos-version-mismatch"
    )
    # A discrepancy with no stated consequence is a footnote, not a finding.
    assert entry["consequence"].strip()
    assert entry["evidence"].strip()


def test_the_patch_switches_are_claimed_of_every_shipped_asset(shipped_record):
    """The content-level link between the repo's patch set and the published
    binaries. This is what a digest CANNOT tell you."""
    declared = [f["value"] for f in shipped_record["patch_set"]["switches_introduced"]["value"]]
    assert sorted(declared) == sorted(SWITCHES)
    for asset in shipped_record["assets"]:
        present = asset["derived"]["fingerprint_switches_present"]
        assert present["confidence"] == "derived_from_artifact"
        assert sorted(present["value"]) == sorted(SWITCHES), asset["name"]


def test_the_switch_list_matches_the_patch_that_declares_them(shipped_record):
    """The record's switch list is not a hand-typed constant: it must agree with
    `000-add-fingerprint-switches.patch` as it stands in the tree. If a future
    rebase adds or removes a switch, this fails rather than letting the record
    describe a patch set that no longer exists."""
    import re

    patch = (
        REPO_ROOT / "engine/patches/fingerprint/000-add-fingerprint-switches.patch"
    ).read_text(encoding="utf-8", errors="replace")
    in_patch = sorted(set(re.findall(r'"(fingerprint[a-z0-9-]*)"', patch)))
    declared = sorted(
        f["value"] for f in shipped_record["patch_set"]["switches_introduced"]["value"]
    )
    assert declared == in_patch


def test_recorded_asset_names_match_the_releasing_scheme(shipped_record):
    """RELEASING.md fixes the asset names. A record naming an asset the updater
    would never select describes a release nobody can install."""
    names = {a["name"] for a in shipped_record["assets"]}
    assert names == {
        "personium-152.0.7977.75-linux-x86_64.AppImage",
        "personium-152.0.7977.75-windows-x86_64.zip",
        "personium-152.0.7977.75-macos-arm64.dmg",
    }
    for a in shipped_record["assets"]:
        assert a["name"].startswith("personium-")
        assert len(normalise_digest(a["sha256"])) == 64


# ── synthetic artifacts ─────────────────────────────────────────────────────
def _switch_blob() -> bytes:
    """The switch names as they sit in a real binary's string table: NUL
    delimited. The NUL anchoring is what stops `fingerprint` matching inside
    `fingerprint-brand`, so the fixture must reproduce it."""
    return b"".join(b"\x00" + s.encode() + b"\x00" for s in SWITCHES)


def make_windows_zip(path: Path, version: str = "152.0.7977.75") -> None:
    manifest = (
        "<assembly xmlns='urn:schemas-microsoft-com:asm.v1' manifestVersion='1.0'>\n"
        f"  <assemblyIdentity name='{version}' version='{version}' type='win32'/>\n"
        "  <file name='chrome_elf.dll'/>\n"
        "</assembly>\n"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"Chrome-bin/{version}/{version}.manifest", manifest)
        zf.writestr(f"Chrome-bin/{version}/chrome.dll", b"MZ" + _switch_blob() + b"\x00" * 64)
        zf.writestr("Chrome-bin/chrome.exe", b"MZ\x00\x00")


def make_macos_dmg(path: Path, version: str = "152.0.7977.64") -> None:
    """A genuine UDIF image: zlib blkx chunks, an XML plist, a koly trailer.

    Small, but structurally the real thing — the deriver walks the same fields
    on this as it does on the 185 MB published asset.
    """
    info = (
        "<?xml version='1.0' encoding='UTF-8'?>\n<plist version='1.0'><dict>\n"
        "<key>CFBundleExecutable</key><string>Chromium</string>\n"
        "<key>CFBundleIdentifier</key><string>org.chromium.Chromium</string>\n"
        f"<key>CFBundleShortVersionString</key><string>{version}</string>\n"
        "</dict></plist>\n"
    ).encode()
    payload = b"\x00" * 512 + info + _switch_blob() + b"\x00" * 512
    payload += b"\x00" * (-len(payload) % 512)
    sectors = len(payload) // 512

    comp = zlib.compress(payload)
    # mish: 4 magic + 4 ver + 8 start + 8 count + 8 data_off, then to 204.
    mish = b"mish" + struct.pack(">IQQQ", 1, 0, sectors, 0)
    mish += b"\x00" * (200 - len(mish))
    mish += struct.pack(">I", 2)  # chunk count
    mish += struct.pack(">IIQQQQ", 0x80000005, 0, 0, sectors, 0, len(comp))
    mish += struct.pack(">IIQQQQ", 0xFFFFFFFF, 0, sectors, 0, len(comp), 0)

    plist = plistlib.dumps(
        {"resource-fork": {"blkx": [{"Name": "disk image (Apple_APFS : 4)", "ID": "3", "Data": mish}]}}
    )

    with path.open("wb") as fh:
        fh.write(comp)
        xml_off = fh.tell()
        fh.write(plist)
        koly = bytearray(512)
        koly[0:4] = b"koly"
        koly[0xD8:0xE8] = struct.pack(">QQ", xml_off, len(plist))
        fh.write(bytes(koly))


# ── the derivers, on artifacts we control ───────────────────────────────────
def test_windows_deriver_reads_both_independent_version_statements(tmp_path):
    z = tmp_path / "w.zip"
    make_windows_zip(z)
    out = derive_windows_zip(z, SWITCHES)
    assert out["version_dir"] == "152.0.7977.75"
    assert out["manifest_version"] == "152.0.7977.75"
    assert sorted(out["fingerprint_switches_present"]) == sorted(SWITCHES)


def test_windows_deriver_is_unmeasurable_on_a_zip_with_no_chrome_bin(tmp_path):
    """UNMEASURABLE, not a pass and not a failure — the third state."""
    z = tmp_path / "empty.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("readme.txt", "nothing here")
    with pytest.raises(Unmeasurable):
        derive_windows_zip(z, SWITCHES)


def test_macos_deriver_reads_the_bundle_version_out_of_a_udif_image(tmp_path):
    d = tmp_path / "m.dmg"
    make_macos_dmg(d, "152.0.7977.64")
    out = derive_macos_dmg(d, SWITCHES)
    assert out["bundle_short_version"] == "152.0.7977.64"
    assert sorted(out["fingerprint_switches_present"]) == sorted(SWITCHES)


def test_macos_deriver_is_unmeasurable_on_a_file_that_is_not_a_dmg(tmp_path):
    p = tmp_path / "notadmg.bin"
    p.write_bytes(b"just some bytes" * 100)
    with pytest.raises(Unmeasurable):
        derive_macos_dmg(p, SWITCHES)


def test_appimage_deriver_is_unmeasurable_rather_than_green_on_a_bad_file(tmp_path):
    """The deriver that CANNOT be synthesised. What is pinned is that its
    failure path yields UNMEASURABLE — because a deriver that returns nothing
    and is read as agreement is how a record comes to look verified without
    ever having been."""
    p = tmp_path / "fake.AppImage"
    p.write_bytes(b"#!/bin/false\n" + b"\x00" * 1024)
    with pytest.raises(Unmeasurable):
        derive_linux_appimage(p, SWITCHES)


# ── end to end: a record that agrees, and six that do not ───────────────────
@pytest.fixture()
def bench(tmp_path):
    """A synthetic release plus a record that correctly describes it."""
    import hashlib

    assets = tmp_path / "assets"
    assets.mkdir()
    win = assets / "personium-1.2.3.4-windows-x86_64.zip"
    mac = assets / "personium-1.2.3.4-macos-arm64.dmg"
    make_windows_zip(win, "152.0.7977.75")
    make_macos_dmg(mac, "152.0.7977.64")

    def entry(p, fmt, derived):
        return {
            "name": p.name,
            "os": "windows" if fmt == "windows-zip" else "macos",
            "arch": "x86_64",
            "format": fmt,
            "size_bytes": p.stat().st_size,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            "derived": derived,
        }

    def df(v):
        return {"value": v, "confidence": "derived_from_artifact"}

    record = {
        "schema": SCHEMA,
        "tag": "personium-1.2.3.4",
        "patch_set": {"switches_introduced": df([df(s) for s in SWITCHES])},
        "build": {"produced_by": {"value": None, "confidence": "unknown"}},
        "assets": [
            entry(win, "windows-zip", {
                "version_dir": df("152.0.7977.75"),
                "manifest_version": df("152.0.7977.75"),
                "fingerprint_switches_present": df(SWITCHES),
            }),
            entry(mac, "macos-dmg", {
                "bundle_short_version": df("152.0.7977.64"),
                "fingerprint_switches_present": df(SWITCHES),
            }),
        ],
    }
    records = tmp_path / "records"
    records.mkdir()
    (records / "personium-1.2.3.4.json").write_text(
        json.dumps(record, indent=2), encoding="utf-8"
    )
    return records, assets, record


def run(records: Path, assets: Path) -> int:
    return main(["--records", str(records), "--assets", str(assets)])


def rewrite(records: Path, record: dict, mutate) -> None:
    r = copy.deepcopy(record)
    mutate(r)
    (records / "personium-1.2.3.4.json").write_text(
        json.dumps(r, indent=2), encoding="utf-8"
    )


def test_a_truthful_record_passes(bench):
    records, assets, _ = bench
    assert run(records, assets) == 0


def test_missing_asset_is_exit_two_not_exit_zero(bench):
    """The single most important negative result here. A record whose artifacts
    are absent must NOT read as verified."""
    records, assets, _ = bench
    (assets / "personium-1.2.3.4-macos-arm64.dmg").unlink()
    assert run(records, assets) == 2


@pytest.mark.parametrize(
    "name,mutate",
    [
        (
            "altered digest",
            lambda r: r["assets"][0].__setitem__(
                "sha256", "0" * 64
            ),
        ),
        (
            "altered size",
            lambda r: r["assets"][0].__setitem__(
                "size_bytes", r["assets"][0]["size_bytes"] + 1
            ),
        ),
        (
            # THE ONE THIS RECORD EXISTS FOR: the macOS version 'corrected' to
            # what the tag says. It looks right and it is false.
            "macos version smoothed over",
            lambda r: r["assets"][1]["derived"]["bundle_short_version"].__setitem__(
                "value", "152.0.7977.75"
            ),
        ),
        (
            "windows manifest version altered",
            lambda r: r["assets"][0]["derived"]["manifest_version"].__setitem__(
                "value", "152.0.7977.99"
            ),
        ),
        (
            "a patch switch dropped from the claim",
            lambda r: r["assets"][0]["derived"]["fingerprint_switches_present"].__setitem__(
                "value", [s for s in SWITCHES if s != "fingerprint-location"]
            ),
        ),
        (
            # The ticket's ⛔ trap, as a test: manufacturing provenance for a
            # binary we cannot account for.
            "an unknown field given a plausible value",
            lambda r: r["build"]["produced_by"].__setitem__(
                "value", "engine-trial-build.yml run 33972186413"
            ),
        ),
    ],
)
def test_altering_a_recorded_value_turns_the_check_red(bench, name, mutate):
    records, assets, record = bench
    assert run(records, assets) == 0, "the bench must be green before it is sabotaged"
    rewrite(records, record, mutate)
    assert run(records, assets) == 1, f"sabotage went undetected: {name}"


def test_an_unrecognised_confidence_is_red_not_ignored(bench):
    records, assets, record = bench
    rewrite(
        records,
        record,
        lambda r: r["build"]["produced_by"].__setitem__("confidence", "probably"),
    )
    assert run(records, assets) == 1


def test_lint_only_reports_unmeasured_rather_than_passing():
    """`--lint-only` reads no artifact, so it must never claim a green run."""
    assert main(["--records", str(RECORDS_DIR), "--lint-only"]) == 2


def test_load_records_refuses_an_empty_directory(tmp_path):
    with pytest.raises(Unmeasurable):
        load_records(tmp_path)


def test_normalise_digest_accepts_both_shapes():
    """GitHub reports `sha256:…`, `sha256sum` reports the bare hex. A record
    must be comparable against both without the caller remembering which."""
    bare = "6ddb7bbea0a2063b7a3618e6b5d4ebc96301cd80f8b0d6eae486af46a30bb4c3"
    assert normalise_digest(bare) == bare
    assert normalise_digest(f"sha256:{bare}") == bare
