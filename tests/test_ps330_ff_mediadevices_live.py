"""What a page ACTUALLY receives from ``navigator.mediaDevices.enumerateDevices()``
on a real Firefox launch — and why persona ships no Firefox mediaDevices spoof.

PS-330. Read this file's reason for existing before changing it.

THE QUESTION, and why it had to be MEASURED
-------------------------------------------
Chromium's ``device_ext.py`` installs a FIVE-ENTRY mediaDevices roster whose
``deviceId``/``groupId`` are derived per profile from the seed — the comment in
that file states the contract: *"deviceId/groupId are stable per profile."*
Firefox's launch path installs nothing: ``enumerateDevices`` appears zero times
across ``invisible_launch.py``'s 5,500 lines, and until this ticket no reason
for that asymmetry was written down anywhere.

``tests/test_engine_masking_matrix.py`` therefore recorded the ``firefox:device``
cell as ``position_not_established`` — in its own words, *"the mediaDevices half
has NO position at all"*. This file is the measurement that settles it, and the
matrix cell moved to ``not_covered_recorded`` in the same commit.

THE ANSWER: the engine already answers with a CONSTANT SYNTHETIC roster — one
``audioinput`` and one ``videoinput`` — whose ``deviceId`` and ``groupId`` are
the EMPTY STRING, on a host that has no ``/dev/snd`` and no ``/dev/video*`` at
all. So there is no host device list for a spoof to displace, and no per-profile
identifier for one to randomise. Shipping a JS override would be a NET LOSS
under Invariant #0: ``Function.prototype.toString`` on ``enumerateDevices``
still renders ``[native code]``, and an override is exactly what a detector can
see.

WHY THESE TESTS LAUNCH A REAL BROWSER
-------------------------------------
Because the claim is about WHAT A PAGE RECEIVES, and no cheaper oracle reaches
it. A test asserting ``enumerateDevices`` is absent from the launch path asserts
the half that was never in doubt, and would pass identically whether the engine
leaked the host's real microphones or synthesised a constant. That is this
project's own recurring failure mode (PS-11: *"tests that assert on what was
written, not on what happens"*), and the sibling live suite
``test_ps312_ff_geolocation_live`` exists for exactly the same reason on the
geolocation vector.

``enumerateDevices()`` returns a PROMISE, so every assertion here is on what the
resolved list actually held — never on a builder having been called, and never
on a substring of generated source. The probe settles its own promise in every
branch and times out explicitly, so a HANG is a recorded outcome rather than an
absent one.

THE POSITIVE CONTROL IS NOT OPTIONAL
------------------------------------
"No devices came back" is ambiguous between *the engine reported none* and *my
eval channel is dead*, and the second reads as the first. So the channel is
proved with ``1+1`` before anything is read through it, and ``api_present`` is
recorded separately from the list: *"navigator.mediaDevices is undefined"* and
*"it enumerated zero devices"* are two different findings that look identical in
a bare count.

⭐ THE PERMISSION LEG IS ALSO A CONTROL, and it is the one that makes the empty
ids mean anything. Under the shipped ``prompt`` default, an empty ``deviceId``
is ambiguous between *this is a constant* and *this is a pre-permission
placeholder that fills in once permission is granted* — a real browser blanks
labels and ids before permission precisely so. So one launch runs with camera
and microphone permission GRANTED (a TEST-SIDE pref overlay, monkeypatched over
``_profile_prefs`` and never a product change), ``navigator.permissions.query``
is asserted to actually report ``granted`` so the overlay is proved to have
taken effect, and the ids are read again. They are still empty. PS-312 recorded
the general form of this trap: a permission-gated web API measured under its
shipped ``prompt`` default measures the DOORHANGER, not the engine.

⛔ LABELS ARE NEVER RECORDED. ``probes.py``'s device probe states the
restriction in the tree's own words — *"labels are user-identifying and are
deliberately NOT recorded into a file the operator may share"* — and this suite
honours it: it asserts on ``label.length``, never on the string.

⚠️ AC3'S SECOND LEG IS UNMEASURED, AND IS NOT INFERRED HERE. The discriminator
between *"the engine synthesises"* and *"the host leaks"* wants two hosts: one
with real audio/video devices and one without. No host in this fleet has a
webcam. ``test_the_measured_leg_is_the_device_less_host`` asserts which leg was
taken rather than leaving a reader to assume it; the other stays open. What IS
established is the direction that matters — a host with NO devices is told it
has two, so the roster demonstrably does not track this host.

SKIPPED, NEVER SILENTLY PASSED, wherever the engine, the display or the launcher
is missing. An absent engine must not read as a clean bill of health.
"""

import os
import shutil

import pytest

# --- gates: an absent engine is a provisioning problem, not a green ----------


def _engine_present() -> bool:
    try:
        from src.services.browser.engine_install import is_invisible_installed

        return bool(is_invisible_installed())
    except Exception:
        return False


def _display_present() -> bool:
    return bool(os.environ.get("DISPLAY", "").strip()) or bool(shutil.which("Xvfb"))


def _launcher_present() -> bool:
    try:
        import invisible_playwright  # noqa: F401

        return True
    except Exception:
        return False


requires_engine = pytest.mark.skipif(
    not _engine_present(),
    reason="persona's firefox engine is not installed on this host",
)
requires_display = pytest.mark.skipif(
    not _display_present(),
    reason="no DISPLAY and no Xvfb: persona ships a HEADED browser",
)
requires_launcher = pytest.mark.skipif(
    not _launcher_present(),
    reason="invisible_playwright is not importable on this host",
)

#: Marked per-test because these are REAL browser launches and the suite's
#: 120s default is not sized for one. Inert without pytest-timeout installed —
#: as is the ini bound it raises — so this can only ever relax a bound that
#: exists, never invent one.
_LIVE_TIMEOUT = pytest.mark.timeout(900)

pytestmark = [requires_engine, requires_display, requires_launcher, _LIVE_TIMEOUT]


def _ensure_display():
    """``(display, owned_xvfb_or_None)`` — an inherited DISPLAY, else our own.

    Delegates to ``chromium_tier._ensure_display``, the tree's existing owner of
    exactly this problem. NOT optional alongside the skip gate above: that gate
    is satisfied by Xvfb merely being INSTALLED, so under a plain ``pytest``
    with no DISPLAY exported these tests would RUN rather than skip, and the
    headful engine would wait forever on a display nobody started. PS-312 paid
    a debugging cycle for that exact gap; a gate saying "a display is
    OBTAINABLE" has to be paired with a step that obtains one.
    """
    from src.services.verify.chromium_tier import _ensure_display as _ed

    return _ed()


@pytest.fixture(scope="module", autouse=True)
def _display():
    """ONE display for the whole module, exported for every launch below.

    Module-scoped for the reason PS-312 records: a per-launch display torn down
    in the same ``finally`` leaves the NAME of a dead Xvfb in ``DISPLAY``, and
    ``_ensure_display`` hands a non-empty ``DISPLAY`` straight back — so the
    second launch connects to nothing and the run degrades to skips, which look
    like success.
    """
    inherited = os.environ.get("DISPLAY", "").strip()
    display, xvfb = _ensure_display()
    os.environ["DISPLAY"] = display
    try:
        yield display
    finally:
        if xvfb is not None:
            try:
                xvfb.terminate()
                xvfb.wait(timeout=10)
            except Exception:
                pass
        if inherited:
            os.environ["DISPLAY"] = inherited
        else:
            os.environ.pop("DISPLAY", None)


# --- the readings ------------------------------------------------------------
#
# THREE launches, module-scoped, because each is a real browser start. Two
# ordinary profiles (AC2's two seeds) and one with permission granted (the
# placeholder control). Every assertion below reads one of these three.


def _read(name, tmp_path_factory, *, granted=False):
    from scripts.ps330_ff_devices_reading import read_devices

    home = tmp_path_factory.mktemp(name)
    return read_devices(name, home, granted=granted)


@pytest.fixture(scope="module")
def seed_a(tmp_path_factory) -> dict:
    return _read("ps330-a", tmp_path_factory)


@pytest.fixture(scope="module")
def seed_b(tmp_path_factory) -> dict:
    return _read("ps330-b", tmp_path_factory)


@pytest.fixture(scope="module")
def granted(tmp_path_factory) -> dict:
    return _read("ps330-granted", tmp_path_factory, granted=True)


def _obtained(reading: dict) -> dict:
    """The reading, or a SKIP naming why it was never taken.

    An unobtained reading must not become evidence in either direction — the
    distinction this project's own craft note draws between a claim about the
    product and a claim about the instrument.
    """
    if reading.get("unobtained"):
        pytest.skip(f"UNOBTAINED reading: {reading['unobtained']}")
    return reading


# --- AC1: read the VALUE, with the channel proved ----------------------------


def test_the_eval_channel_carries_a_value(seed_a):
    # THE POSITIVE CONTROL, asserted as its own test rather than buried in a
    # helper. Every null below is a fact about the ENGINE only while this
    # passes; a dead channel answers None to everything, which reads identical
    # to "the engine returned nothing".
    r = _obtained(seed_a)
    assert r["channel_control"] == 2


def test_the_reading_happens_in_a_secure_context(seed_a):
    # enumerateDevices is gated on a secure context. A reading taken in a
    # NON-secure one would measure the CONTEXT and report it as the engine's
    # device posture — the same trap PS-312 guards on the geolocation vector.
    r = _obtained(seed_a)
    assert r["secure_context"] is True
    assert str(r["href"]).startswith("http://127.0.0.1")


def test_the_api_is_present_so_an_empty_list_would_be_a_real_answer(seed_a):
    # "navigator.mediaDevices is undefined" and "it enumerated zero devices"
    # are two different findings that look identical in a bare count. This
    # separates them, so the list below is known to be an ANSWER.
    r = _obtained(seed_a)
    assert r["api_present"] is True
    assert r["enumerate_present"] is True


def test_a_page_receives_one_microphone_and_one_camera(seed_a):
    # THE READING, asserted on the resolved promise's payload. Never on a
    # builder having been called and never on a substring of generated source.
    r = _obtained(seed_a)
    devices = r["devices"]
    assert devices["outcome"] == "list", (
        f"the promise did not resolve to a list: {devices}"
    )
    assert devices["kindCounts"] == {"audioinput": 1, "videoinput": 1}


def test_enumerate_devices_still_renders_as_native(seed_a):
    # The anti-tell, and the concrete price of shipping a spoof: this is what
    # a JS override would cost. It is asserted here so the "net loss under
    # Invariant #0" argument in the recorded reason rests on a reading rather
    # than on an expectation.
    r = _obtained(seed_a)
    assert "[native code]" in str(r["native_source"])


# --- AC2: two seeds, so the constancy is established rather than inherited ---


def test_two_profiles_really_do_carry_two_different_seeds(seed_a, seed_b):
    # The premise of the comparison below, asserted rather than assumed. Two
    # identical readings prove nothing about linkability if both profiles were
    # secretly the same profile — and an earlier draft of the reading script
    # recorded `seed: null` for both, which would have looked like the answer
    # while establishing nothing.
    a, b = _obtained(seed_a), _obtained(seed_b)
    assert a["seed"] is not None and b["seed"] is not None
    assert a["seed"] != b["seed"], (
        f"both profiles carry seed {a['seed']}, so this pair cannot answer a "
        f"cross-profile question"
    )


def test_two_seeds_receive_identical_device_ids(seed_a, seed_b):
    # ⭐ THE LINKABILITY QUESTION, which the kind count does not answer: "1 mic,
    # 1 camera" is what most real machines look like, so two profiles agreeing
    # on it is not by itself a defect. Whether two profiles receive the same
    # deviceId/groupId IS the question, and nothing had ever read those two
    # fields on this engine.
    #
    # They are identical — and they are identical because they are EMPTY, which
    # is why this is not a leak. Asserted as the ids rather than as a boolean so
    # a future engine that starts emitting REAL per-host ids fails here loudly.
    a, b = _obtained(seed_a), _obtained(seed_b)
    ids_a = [(d["deviceId"], d["groupId"]) for d in a["devices"]["devices"]]
    ids_b = [(d["deviceId"], d["groupId"]) for d in b["devices"]["devices"]]
    assert ids_a == ids_b
    assert ids_a == [("", ""), ("", "")], (
        f"the engine now emits non-empty device ids ({ids_a}). RE-MEASURE: if "
        f"they differ per profile the cell may be COVERED, and if they are a "
        f"shared constant they are a cross-profile identifier — either way the "
        f"recorded decision no longer describes this engine."
    )


def test_the_kind_counts_agree_across_seeds(seed_a, seed_b):
    a, b = _obtained(seed_a), _obtained(seed_b)
    assert a["devices"]["kindCounts"] == b["devices"]["kindCounts"]


# --- the placeholder control: are the empty ids merely PRE-PERMISSION? -------


def test_the_permission_overlay_actually_took_effect(granted):
    # The control's OWN control. If the overlay silently failed, the launch
    # below would be a third ordinary `prompt` reading dressed up as a granted
    # one — and it would "confirm" the constancy while measuring nothing. So
    # the page's own permission state is asserted before its ids are read.
    r = _obtained(granted)
    assert r["permission_camera"] == "granted"
    assert r["permission_microphone"] == "granted"


def test_granting_permission_does_not_fill_in_the_ids(granted):
    # ⭐ THE DISCRIMINATOR the shipped `prompt` default cannot supply. A real
    # browser blanks labels and device ids until permission is granted, so an
    # empty id under `prompt` is ambiguous between a CONSTANT and a
    # PRE-PERMISSION PLACEHOLDER. With permission genuinely granted (asserted
    # above) the ids are STILL empty — so they are a constant, and the "no
    # per-profile identifier exists to link" half of the recorded decision
    # rests on a reading rather than on an assumption.
    r = _obtained(granted)
    devices = r["devices"]
    assert devices["outcome"] == "list"
    assert devices["kindCounts"] == {"audioinput": 1, "videoinput": 1}
    assert [(d["deviceId"], d["groupId"]) for d in devices["devices"]] == [
        ("", ""),
        ("", ""),
    ]


def test_labels_are_empty_and_are_read_only_as_a_length(granted):
    # ⛔ The label RESTRICTION, honoured and asserted: `probes.py` records kind
    # counts only because "labels are user-identifying and are deliberately NOT
    # recorded into a file the operator may share". This suite reads
    # `label.length`, never the string — so it can state that the engine
    # returns empty labels (a real anti-tell finding: labels populate on a
    # permission-granted real browser with real devices) without recording one.
    r = _obtained(granted)
    for d in r["devices"]["devices"]:
        assert d["label_len"] == 0
        assert "label" not in d, "a raw label must never be recorded"


# --- AC3: which leg was measured, stated rather than inferred ----------------


def test_the_measured_leg_is_the_device_less_host(seed_a):
    # ⚠️ AC3 asks for TWO hosts — one with real audio/video devices and one
    # without — because that pair is the discriminator between "the engine
    # synthesises" and "the host leaks". No host in this fleet has a webcam, so
    # only ONE leg is measurable and this test NAMES it rather than letting a
    # reader assume the pair was taken.
    #
    # ⭐ The leg that IS measured is the one that carries the finding: a host
    # with NO audio and NO video devices is told it has one microphone and one
    # camera. Whatever that roster is, it is not this host's device list.
    #
    # ⛔ The other leg — whether a host WITH four real microphones would still
    # be told it has one — is UNMEASURED and is NOT inferred here. If it is
    # ever taken and the roster tracks the host, that is a host-fact leak and
    # the matrix cell must be restated.
    r = _obtained(seed_a)
    host = r.get("host_devices") or {}
    assert host.get("has_audio_device") is False
    assert host.get("has_video_device") is False
    assert r["devices"]["kindCounts"] == {"audioinput": 1, "videoinput": 1}, (
        "a host with no /dev/snd and no /dev/video* received something other "
        "than the recorded constant roster — RE-MEASURE before trusting the "
        "cell"
    )


# --- the product must not grow the overlay this suite uses -------------------


def test_the_permission_prefs_this_suite_overlays_are_not_shipped():
    # The overlay above is TEST-SIDE and must stay that way. Shipping
    # `permissions.default.camera = 1` would auto-grant every profile's camera,
    # which is a far larger change than this ticket, and a suite that overlays
    # a pref has an obligation to pin that the product does not.
    from src.services.browser import invisible_launch as il

    prefs = il._profile_prefs({"name": "ps330-shipped-check"})
    for pref in (
        "permissions.default.camera",
        "permissions.default.microphone",
        "media.navigator.permission.disabled",
    ):
        assert pref not in prefs, (
            f"the product now ships {pref!r}. This suite overlays it as a "
            f"TEST-SIDE control; shipping it silently changes every profile's "
            f"permission posture."
        )


# ─────────────────────────────────────────────────────────────────────────────
# AC5 lives in ``tests/test_ps330_device_id_recording.py``, NOT here, and the
# split is deliberate rather than tidiness. Every test in THIS file sits behind
# ``pytestmark`` — engine, display and launcher gates — because every one of
# them launches a browser. AC5's decision is a STATIC claim about the probe
# inventory: it needs no engine, and a decision pinned behind a skip gate is
# unguarded on exactly the hosts that cannot launch a browser, which is most of
# them. A guard that skips where it is needed is not a guard.
# ─────────────────────────────────────────────────────────────────────────────
