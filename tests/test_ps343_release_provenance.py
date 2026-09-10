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

AND A TEST THAT ASSERTS ONLY AN EXIT CODE CAN GUARD NOTHING
───────────────────────────────────────────────────────────
Two of these tests originally checked `run(...) == 2` and passed even with the
fix they guard reverted, because *something else* in the same run was
unmeasured. They now assert the specific ROW — verdict and name — so the
assertion is about the behaviour and not about the run's mood. Every test here
was verified by reverting its fix and confirming it fails; the five reversions
and the tests they take down are recorded in the PS-343 PR.

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
verify_asset = _V.verify_asset

# ⭐ DERIVED, NOT A LITERAL (PS-385). This was `SHIPPED_TAG =
# "personium-152.0.7977.75"` — a compile-time literal, which meant the territory
# test below asserted that ONE HARDCODED RELEASE has a record. "One hardcoded
# release has a record" and "every published release has a record" are two
# different claims, and only the second is the sentence
# `engine/releases/README.md` opens with.
#
# It is derived from the RECORD DIRECTORY and deliberately NOT from the
# published set: this fixture is read by every record-lint test in this file, so
# a network-dependent derivation here would make ~30 currently-hermetic tests
# depend on GitHub. The published-set quantifier is evaluated by
# `.github/scripts/ps343_release_audit.py`, on its own schedule, and pinned by
# `tests/test_ps385_release_provenance_audit.py`.
def _newest_record_tag(records_dir: Path | None = None) -> str:
    """The newest provenance record on disk, by version rather than by string.

    Numeric, because lexicographically `personium-99.…` sorts above
    `personium-152.…` — the same reason `updater.engine_versions_newest_first`
    sorts by `parse_version` rather than taking the API's ref order.

    ⚠️ `records_dir` IS A TEST HOOK AND NOTHING ELSE. Today this repository
    carries exactly one record, so the derived value and the literal it replaced
    are the SAME STRING — which means reverting the derivation does not fail
    anything, and a derivation nothing can falsify is decoration. The parameter
    exists so `tests/test_ps385_release_provenance_audit.py` can drive it at a
    record set this repository does not have (two records, and a `99` that must
    not outsort a `152`) and observe that it actually follows the directory.
    """
    directory = records_dir or RECORDS_DIR
    tags = sorted(p.stem for p in directory.glob("personium-*.json"))
    if not tags:
        raise AssertionError(
            f"no personium-*.json provenance record in {directory} — the "
            "record set this suite lints is empty"
        )

    def _key(tag: str):
        version = tag[len("personium-"):]
        parts = []
        for chunk in version.split("."):
            digits = "".join(c for c in chunk if c.isdigit())
            parts.append(int(digits) if digits else 0)
        return parts

    return max(tags, key=_key)


SHIPPED_TAG = _newest_record_tag()

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


def test_every_engine_release_this_repository_records_is_answerable_from_it():
    """The ticket's territory, WIDENED (PS-385).

    This used to read `assert (RECORDS_DIR / f"{SHIPPED_TAG}.json").is_file()`
    against a hardcoded tag, which asserted that ONE release has a record. The
    territory sentence `engine/releases/README.md` opens with is a QUANTIFIER —
    *"For every Personium engine we have **published**, a record here says…"* —
    and a literal cannot express it.

    So this half asserts what can be asserted HERMETICALLY: the record set is
    non-empty and every record in it is a real, readable file, answerable from
    the repository rather than from a CI artifact that may have expired.

    ⭐ THE PUBLISHED HALF OF THE QUANTIFIER IS EVALUATED ELSEWHERE, and
    deliberately: it needs the network, and this suite must stay hermetic. It is
    `.github/scripts/ps343_release_audit.py`, which reconciles
    `updater.engine_versions_newest_first()` against this same directory and
    goes RED naming any published release with no record. The test below drives
    that judgement — still without a network call — so this file does not merely
    point at a gate it never exercises.
    """
    records = sorted(RECORDS_DIR.glob("personium-*.json"))
    assert records, f"no provenance record at all in {RECORDS_DIR}"
    for record in records:
        assert record.is_file()
        assert json.loads(record.read_text(encoding="utf-8")).get("tag") == record.stem


def test_the_published_side_of_the_quantifier_has_a_gate_that_can_go_red():
    """The half a literal could never assert, driven hermetically.

    A published release with no record must be a NAMED red row, never an
    absence — which is what it was for this verifier, whose `load_records()`
    globs the RECORD SET and whose `main()` iterates that list.
    """
    import importlib.util as _ilu

    audit_path = REPO_ROOT / ".github" / "scripts" / "ps343_release_audit.py"
    assert audit_path.is_file(), "the published-set reconciliation does not exist"
    spec = _ilu.spec_from_file_location("ps343_release_audit_from_ps343_suite", audit_path)
    audit = _ilu.module_from_spec(spec)
    spec.loader.exec_module(audit)

    on_disk = audit.record_tags(RECORDS_DIR)
    recorded = [t[len("personium-"):] for t in on_disk]

    # QUIET: every published release recorded.
    assert audit.classify(recorded, on_disk)[0] == audit.QUIET
    # RED: one more published release than we have records for, and NAMED.
    code, body = audit.classify(recorded + ["999.0.0.0"], on_disk)
    assert code == audit.UNRECORDED_RELEASE
    assert body["unrecorded"] == ["personium-999.0.0.0"]


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
        "base": {
            # Witnessed by the Windows asset's two independent version
            # statements. NOT by the macOS bundle version, which is the
            # recorded discrepancy — see test_the_macos_bundle_version_is_not_
            # admitted_as_a_witness_for_the_base_version.
            "chromium_version": df("152.0.7977.75"),
        },
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
        (
            # THE RECORD'S HEADLINE CLAIM. `base.chromium_version` is where a
            # reader looks for the ticket's "which Chromium version", and it
            # used to have no red arm at all: altered to nonsense, the run
            # stayed at exit 0 because no code path compared it to anything.
            "base.chromium_version altered",
            lambda r: r["base"]["chromium_version"].__setitem__(
                "value", "152.0.7977.99"
            ),
        ),
        (
            # The other half of the same hole, one level down: an inner
            # declaration inside `switches_introduced.value`, which the linter
            # never descended into.
            "a nested switch declaration given an unrecognised confidence",
            lambda r: r["patch_set"]["switches_introduced"]["value"][3].__setitem__(
                "confidence", "probably-fine"
            ),
        ),
        (
            "a nested switch declaration declared unknown but carrying a value",
            lambda r: r["patch_set"]["switches_introduced"]["value"][5].__setitem__(
                "confidence", "unknown"
            ),
        ),
        (
            # `switches_introduced` is the deriver's own needle list, so an
            # invented switch used to appear on NEITHER side of the per-asset
            # comparison and could not be falsified at all.
            "a switch invented in the top-level claim",
            lambda r: r["patch_set"]["switches_introduced"]["value"].append(
                {"value": "fingerprint-invented-switch",
                 "confidence": "derived_from_artifact"}
            ),
        ),
    ],
)
def test_altering_a_recorded_value_turns_the_check_red(bench, name, mutate):
    records, assets, record = bench
    assert run(records, assets) == 0, "the bench must be green before it is sabotaged"
    rewrite(records, record, mutate)
    assert run(records, assets) == 1, f"sabotage went undetected: {name}"


def test_a_base_field_with_no_artifact_witness_is_unmeasured_not_green(bench):
    """A `base` field claiming `derived_from_artifact` that this verifier
    defines no witness for must NOT read as verified.

    The confidence vocabulary promises the verifier checks `derived_from_
    artifact`; a field wearing that label with no code path behind it makes the
    promise false, which is worse than an honest `from_repository`.
    """
    records, assets, record = bench
    rewrite(
        records,
        record,
        lambda r: r["base"].__setitem__(
            "some_future_field",
            {"value": "whatever", "confidence": "derived_from_artifact"},
        ),
    )
    assert run(records, assets) == 2


def test_the_macos_bundle_version_is_not_admitted_as_a_witness_for_the_base_version(bench):
    """The asymmetry, pinned rather than left incidental.

    The macOS asset's bundle version disagrees with the release tag — that IS
    the record's headline finding. If it were admitted as a witness for
    `base.chromium_version`, the record would go RED on the very discrepancy it
    exists to preserve, and the pressure would be to 'correct' the finding away.
    The bench's macOS asset carries .64 against a base claim of .75, and the
    run must still be green.
    """
    records, assets, record = bench
    mac = next(a for a in record["assets"] if a["os"] == "macos")
    assert mac["derived"]["bundle_short_version"]["value"] == "152.0.7977.64"
    assert record["base"]["chromium_version"]["value"] == "152.0.7977.75"
    assert run(records, assets) == 0

    excluded = _V.BASE_WITNESSES_EXCLUDED["chromium_version"]
    assert "bundle_short_version" in excluded
    assert excluded["bundle_short_version"].strip()


def test_an_asset_with_nothing_derivable_is_unmeasured_not_a_green_digest_pass(tmp_path):
    """The digest-is-not-content trap coming back through the front door.

    An asset whose record entry declares no `derived` block used to report a
    NOTED gap — unscored — so a zip containing one readme.txt under a correct
    name, size and digest passed with three green rows and exit 0. `UNMEASURED`
    is the state the vocabulary already has for "nothing was measured".

    Asserted on the ROW, not only on the exit code: an exit-code-only assertion
    passes whenever anything else in the run happens to be unmeasured, which is
    exactly how a test comes to guard nothing.
    """
    import hashlib

    assets = tmp_path / "assets"
    assets.mkdir()
    # Not a Chromium package at all — one readme, under a correct name, size
    # and digest.
    p = assets / "personium-1.2.3.4-windows-x86_64.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("readme.txt", "nothing here")

    record = {
        "tag": "personium-1.2.3.4",
        "patch_set": {"switches_introduced": {"value": [], "confidence": "unknown"}},
    }
    asset = {
        "name": p.name,
        "format": "windows-zip",
        "size_bytes": p.stat().st_size,
        "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
    }

    report = Report()
    assert verify_asset(record, asset, assets, report) is None
    rows = {c.name: c.verdict for c in report.checks}
    assert rows["size"] == _V.GREEN
    assert rows["sha256"] == _V.GREEN
    assert rows["derived"] == _V.UNMEASURED, rows
    assert report.exit_code() == 2


def test_a_structurally_incomplete_record_is_unmeasured_not_a_traceback(tmp_path):
    """The README tells the next author to hand-copy and edit an existing
    record, so a missing key is the likeliest failure this script will ever
    see. It belongs in the UNMEASURED lane beside unreadable JSON, not in an
    uncaught KeyError with no report at all."""
    assets = tmp_path / "assets"
    assets.mkdir()
    p = assets / "personium-1.2.3.4-windows-x86_64.zip"
    make_windows_zip(p)

    record = {"tag": "personium-1.2.3.4", "patch_set": {}}
    for missing in ("size_bytes", "sha256"):
        asset = {"name": p.name, "format": "windows-zip", "size_bytes": 1, "sha256": "x"}
        asset.pop(missing)

        report = Report()
        # The point of the test: this call must RETURN, not raise.
        assert verify_asset(record, asset, assets, report) is None
        rows = {c.name: c.verdict for c in report.checks}
        assert rows == {"record-shape": _V.UNMEASURED}, (missing, rows)
        assert report.exit_code() == 2


def test_a_malformed_record_does_not_crash_the_whole_run(bench):
    """And the same thing end to end: the CLI reports rather than tracebacks."""
    records, assets, record = bench
    rewrite(records, record, lambda r: r["assets"][0].pop("sha256"))
    assert run(records, assets) == 2


def test_the_nested_switch_declarations_are_linted_too(shipped_record):
    """`walk_fields` used to return at the first `confidence` key, so the
    eleven declarations inside `patch_set.switches_introduced.value` were
    invisible to the linter — a third of the shipped record's fields sat
    outside the vocabulary the record's whole value rests on."""
    paths = {p for p, _ in walk_fields(shipped_record)}
    assert "patch_set.switches_introduced" in paths
    inner = {p for p in paths if p.startswith("patch_set.switches_introduced.value[")}
    assert len(inner) == 11, sorted(paths)


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
