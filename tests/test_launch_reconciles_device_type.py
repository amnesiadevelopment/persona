"""PS-236 — a stored-incoherent ``windows`` + ``mobile`` record must launch
presenting ONE machine.

THE DEFECT. A launched Chromium profile answers "what OS am I?" on four vectors,
and only TWO of them read ``device_type``:

===========================  ================================  ===============
vector                       computed from                     reads
===========================  ================================  ===============
device preset (UA/screen)    ``is_mobile_profile(os, device)``  BOTH fields
``--fingerprint-platform``   ``engine_platform_for(os, dev)``   BOTH fields
GPU pool arm                 ``gpu_ext._os_norm(os_type)``      ``os_type`` alone
voice roster arm             ``voice_ext``'s fold on os_type    ``os_type`` alone
===========================  ================================  ===============

Rule 3 (``device_type == "mobile"`` requires a mobile ``os_type``) is refused at
the two AUTHORING doors and at NONE of the three RECOVERY doors — the decision
PS-188 recorded is accept-and-record, so the pair really is reachable by import,
restore, a legacy disk record, and the unguarded REST lane. Unreconciled, such a
record launched an **Android Pixel-class UA and screen** over a **Windows**
Direct3D11 GPU pool and **Microsoft SAPI** voices, told
``--fingerprint-platform=linux``. Three answers, one machine; any pair of them is
a contradiction a checker reads directly.

WHAT THESE TESTS ASSERT, AND WHAT THEY DELIBERATELY DO NOT
-----------------------------------------------------------
Per PS-11 and this ticket's AC1: **no test here asserts that a helper was
CALLED.** Every assertion below is on one of the four values a REAL
``spawn_browser`` computes, read back out of what the launch actually produced —
the argv it handed to ``Popen``, and the extension scripts it wrote to disk:

* device preset → ``--user-agent`` / ``--window-size`` in argv (a desktop launch
  passes NEITHER at all, which is the observable that distinguishes the arms)
* engine platform → ``--fingerprint-platform=`` in argv
* GPU pool arm → the baked ``var OS = "..."`` in ``.persona-gpu-ext/gpu.js``
* voice roster arm → the baked ``const OS = "..."`` in
  ``.persona-voice-ext/voices.js``

⚠️ The voice and GPU arms are read from the BAKED substitution, not from the
presence of roster data. Every roster is embedded in both scripts and the arm is
selected at runtime, so grepping for a vendor name returns the same answer for
every ``os_type`` — a probe that cannot distinguish a correct build from a broken
one. (That exact broken instrument was caught on this ticket's research pass and
is recorded in its comments.)

⚠️ AC4 — THE STORED RECORD IS NOT REWRITTEN. The reconciliation is a LOCAL value
at the launch path; ``Profile.device_type_incoherence`` is derived from the
stored fields on every read and must keep reporting the incoherence after a
launch. ``test_the_stored_record_is_not_rewritten_by_a_launch`` pins that, and it
is the assertion that fails if someone "repairs" the field in place.
"""
import os
import pathlib
import re

import pytest

from src.models.profile import Profile
from src.services.browser import process
from src.services.browser.engine_version import ChromiumVersion


class _Store:
    def resolve(self, name):
        return ""

    def get(self, name):
        return None


class _Bookmarks:
    def resolve_selection(self, pool, names):
        return []


def _launch(monkeypatch, tmp_path, profile):
    """Drive the REAL ``spawn_browser`` with only ``Popen`` swapped, and return
    the four vectors as the launch actually computed them.

    Nothing about the reconciliation is stubbed: the argv is the real argv and
    the extension scripts are the real files this launch wrote.
    """
    captured = {}

    class _FakePopen:
        def __init__(self, args, **kwargs):
            captured["args"] = args
            self.pid = os.getpid()

    # ⚠️ INSTRUMENT NOTE, the same one tests/test_gpu_ext.py carries and for the
    # same measured reason: a mobile launch REFUSES unless it can read the
    # installed engine's Chromium version, and CI provisions Firefox only. With
    # no stub this file passes in a dev container that has the engine and fails
    # on every runner that does not — PS-14's lesson arriving from the green
    # direction. Only the version READ is replaced; the refusal path itself is
    # untouched, so this cannot turn a real engine-version regression green, and
    # it cannot affect the four vectors under test (none of them is derived from
    # the version).
    monkeypatch.setattr(
        process,
        "installed_chromium_version",
        lambda: ChromiumVersion("148.0.7778.215"),
    )
    monkeypatch.setattr(process, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(process, "ProxyStore", _Store)
    monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)
    monkeypatch.setattr(process, "write_window_entry", lambda name: None)
    monkeypatch.setattr(process.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(process._platform, "IS_LINUX", True)
    process.spawn_browser(profile)

    args = captured["args"]
    profile_dir = pathlib.Path(tmp_path) / profile.name

    def _flag(prefix):
        for a in args:
            if a.startswith(prefix):
                return a[len(prefix):]
        return None

    gpu_js = (profile_dir / ".persona-gpu-ext" / "gpu.js").read_text()
    voice_js = (profile_dir / ".persona-voice-ext" / "voices.js").read_text()

    return {
        # vector 1 — the device preset. A desktop launch passes NO --user-agent
        # at all (the engine's own UA is what the page sees) and no
        # --window-size; a mobile launch passes both, from the preset.
        "user_agent": _flag("--user-agent="),
        "window_size": _flag("--window-size="),
        "mobile_ext": (profile_dir / ".persona-mobile-ext").is_dir(),
        "device_ext": (profile_dir / ".persona-device-ext").is_dir(),
        # vector 3 — the one string the engine is told
        "engine_platform": _flag("--fingerprint-platform="),
        # vector 2 — the GPU pool arm, read from the BAKED substitution
        "gpu_arm": re.search(r'var OS = "(\w+)"', gpu_js).group(1),
        # vector 4 — the voice roster arm, likewise BAKED
        "voice_arm": re.search(r'const OS = "(\w+)"', voice_js).group(1),
    }


def _machine(v):
    """The four vectors reduced to the OS FAMILY each one presents.

    This is the shape AC1 is actually about: "one machine" means these four
    agree, and the individual strings differ in vocabulary (``linux`` at the
    engine for a phone) without disagreeing about the machine.
    """
    return (
        "mobile" if v["user_agent"] else "desktop",
        v["engine_platform"],
        v["gpu_arm"],
        v["voice_arm"],
    )


# ---------------------------------------------------------------------------
# AC1 — the incoherent record presents ONE machine
# ---------------------------------------------------------------------------


def test_a_stored_windows_mobile_record_launches_one_coherent_machine(
    monkeypatch, tmp_path
):
    """AC1. Every one of the four vectors agrees on WINDOWS DESKTOP.

    RED at c70a3c7: there the same launch produced a mobile UA
    (``--user-agent`` set from an Android preset, ``--window-size=360,780``),
    ``--fingerprint-platform=linux``, a ``windows`` GPU arm and a ``windows``
    voice arm — three different answers.
    """
    v = _launch(
        monkeypatch,
        tmp_path,
        Profile(name="ps236-win-mobile", os_type="windows", device_type="mobile"),
    )

    # vector 1 — NOT assembled as a phone. A desktop launch passes neither flag.
    assert v["user_agent"] is None, (
        "a windows record launched with a mobile UA -- the device preset arm "
        "is still reading the unreconciled device_type"
    )
    assert v["window_size"] is None
    assert v["mobile_ext"] is False
    assert v["device_ext"] is True

    # vector 3 — the engine is told windows, not the mobile-backing linux
    assert v["engine_platform"] == "windows"

    # vectors 2 and 4 — unchanged, and now AGREED WITH rather than contradicted
    assert v["gpu_arm"] == "windows"
    assert v["voice_arm"] == "windows"

    # ...stated once more as the whole machine, which is what AC1 is about
    assert _machine(v) == ("desktop", "windows", "windows", "windows")


def test_the_windows_desktop_counterpart_is_indistinguishable_at_launch(
    monkeypatch, tmp_path
):
    """The reconciled record launches the SAME machine as the coherent one it
    was reconciled to — otherwise "reconciled" would be a third answer rather
    than agreement with the record's own os_type.
    """
    incoherent = _launch(
        monkeypatch,
        tmp_path,
        Profile(name="ps236-a", os_type="windows", device_type="mobile"),
    )
    coherent = _launch(
        monkeypatch,
        tmp_path,
        Profile(name="ps236-b", os_type="windows", device_type="desktop"),
    )
    assert _machine(incoherent) == _machine(coherent)


# ---------------------------------------------------------------------------
# AC3 — coherent records are byte-identical
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "os_type,device_type,expect",
    [
        # a phone that says it is a phone
        ("android", "mobile", ("mobile", "linux", "android", "android")),
        # the DEFAULT shape: every android profile the UI has ever created, since
        # "desktop" is the model default and the dialog has no device_type
        # control. Reconciling this the OTHER way would rewrite the normal case,
        # which is why the coercion is one-directional.
        ("android", "desktop", ("mobile", "linux", "android", "android")),
        # iOS is backed by macos at the engine, not linux
        ("ios", "mobile", ("mobile", "macos", "ios", "macos")),
        ("windows", "desktop", ("desktop", "windows", "windows", "windows")),
    ],
)
def test_coherent_records_are_unchanged_on_all_four_vectors(
    monkeypatch, tmp_path, os_type, device_type, expect
):
    """AC3. The baseline table from the ticket, asserted against a real launch.

    These four rows are byte-identical before and after the reconciliation
    because ``coherent_device_type`` is a NO-OP on every one of them.
    """
    v = _launch(
        monkeypatch,
        tmp_path,
        Profile(
            name=f"ps236-{os_type}-{device_type}",
            os_type=os_type,
            device_type=device_type,
        ),
    )
    assert _machine(v) == expect


def test_the_android_desktop_default_still_launches_as_a_phone(
    monkeypatch, tmp_path
):
    """The one-directional asymmetry, stated on its own because getting it
    backwards is the failure that would break every android profile on every
    machine: ``os_type`` alone already flips ``is_mobile``, so the defaulted
    ``"desktop"`` makes no competing claim and must NOT be coerced to match it.
    """
    v = _launch(
        monkeypatch,
        tmp_path,
        Profile(name="ps236-and-def", os_type="android", device_type="desktop"),
    )
    assert v["user_agent"] is not None, (
        "the android+desktop DEFAULT stopped launching as a phone -- the "
        "reconciliation is coercing in the wrong direction and has just "
        "desktop-ed every android profile the UI ever created"
    )
    assert v["mobile_ext"] is True
    assert v["engine_platform"] == "linux"


# ---------------------------------------------------------------------------
# AC4 — the stored record is NOT rewritten
# ---------------------------------------------------------------------------


def test_the_stored_record_is_not_rewritten_by_a_launch(monkeypatch, tmp_path):
    """AC4, and the assertion that fails if someone assigns the reconciled value
    back to ``profile.device_type``.

    The accept-and-record decision PS-188 made is only worth anything while the
    record keeps SAYING it is incoherent. A pair rule has no safe repair at rest
    — nothing in the record says which of the two fields is the lie — so the
    launch presents one coherent machine WITHOUT claiming to have fixed
    anything.
    """
    p = Profile(name="ps236-record", os_type="windows", device_type="mobile")
    before = p.device_type_incoherence
    assert before is not None  # the premise: it is incoherent to begin with

    v = _launch(monkeypatch, tmp_path, p)

    # the launch really did reconcile...
    assert v["engine_platform"] == "windows"
    assert v["user_agent"] is None
    # ...and the record still says exactly what it said, verbatim
    assert p.device_type == "mobile", (
        "the launch path assigned the reconciled value back onto the profile -- "
        "this silences device_type_incoherence and risks persisting through any "
        "downstream save"
    )
    assert p.device_type_incoherence == before
    assert p.device_type_incoherence is not None
