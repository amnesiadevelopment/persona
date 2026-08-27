"""The `android` arm of voice_ext — PS-65.

SEPARATE FILE ON PURPOSE. AC5 pins the three shipped os-arm tests in
`tests/test_voice_ext.py` as unmodified, and keeping the new work out of that
file collapses AC5 into one `git status` line: proof by absence of diff.

THE TRAP THIS FILE IS BUILT AROUND. Every roster is emitted as a literal into
EVERY generated `voices.js` — only the baked `const OS` marker selects among
them at runtime. So `"English (America)" in js` is True for an ANDROID profile
and means nothing at all. A structural assertion ("an android branch exists")
is just as empty: an arm that still emitted the eSpeak list would satisfy it.
AC3 therefore requires assertions on the ROSTER CONTENT, and every assertion
below is made on what `getVoices()` actually RETURNS after the emitted script
has run in node.

Reference values + full provenance: tests/fixtures/android-voices-reference.md
"""

import json
import pathlib
import re
import shutil
import subprocess

import pytest

from src.services.browser.engine_platform import engine_platform_for
from src.services.browser.gpu_ext import build_gpu_extension
from src.services.browser.voice_ext import build_voice_extension

# --------------------------------------------------------------------------
# Probe: run the emitted extension in node and report what a page really sees.
# --------------------------------------------------------------------------

_VOICE_PROBE = r"""
// Stub speechSynthesis, run the emitted extension against it, then CALL the
// patched getVoices() — what a page sees, not what the file contains. The stub
// returns a sentinel so a patch that silently failed to install is visible as
// the sentinel rather than as an empty list.
const G = {};
G.__events = [];
function Event(type) { this.type = type; }
G.Event = Event;
G.speechSynthesis = {
  getVoices() { return ["HOST_VALUE_NOT_SPOOFED"]; },
  dispatchEvent(e) { G.__events.push(e && e.type); return true; },
  addEventListener() {},
};
function SpeechSynthesisVoice() {}
G.SpeechSynthesisVoice = SpeechSynthesisVoice;
// Record deferred callbacks instead of dropping them. A no-op setTimeout makes
// the SECOND voiceschanged dispatch invisible, and that is the half that exists
// for listeners registered after document_start — so stubbing it away would
// leave the load-bearing behaviour unobservable and untestable.
G.__deferred = [];
G.setTimeout = function (fn) { G.__deferred.push(fn); return 0; };
G.Intl = Intl;
G.Object = Object;
G.self = G; G.window = G; G.globalThis = G;

const src = require('fs').readFileSync(process.argv[2], 'utf8');
require('vm').createContext(G);
require('vm').runInContext(src, G, { filename: 'voices.js' });

// Snapshot BEFORE draining: these are the events a listener present at
// document_start saw synchronously.
const immediate = G.__events.slice();
// Now run what setTimeout deferred — this is what a LATE listener depends on.
for (const fn of G.__deferred.splice(0)) { fn(); }
const deferred = G.__events.slice(immediate.length);

console.log(JSON.stringify({
  voices: G.speechSynthesis.getVoices().map((v) => ({
    name: v.name, lang: v.lang, voiceURI: v.voiceURI,
    localService: v.localService, default: v.default,
  })),
  events: G.__events,
  immediateEvents: immediate,
  deferredEvents: deferred,
}));
"""


def _probe(tmp_path, locale, os_type):
    """Build the extension for `os_type`, execute it, return the whole probe result.

    Returns the full dict: the selected roster plus the `voiceschanged` events,
    split into the ones dispatched synchronously and the ones `setTimeout`
    deferred. Kept separate from `_voices` so the event plumbing is actually
    READ by a test rather than threaded out and discarded.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    d = pathlib.Path(
        build_voice_extension(locale, str(tmp_path / f"v_{os_type}_{locale}"), os_type=os_type)
    )
    harness = d / "harness.js"
    harness.write_text(_VOICE_PROBE, encoding="utf-8")
    out = subprocess.run(
        [node, str(harness), str(d / "voices.js")],
        capture_output=True, text=True, timeout=60, encoding="utf-8",
    )
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert data["voices"] != ["HOST_VALUE_NOT_SPOOFED"], (
        "the patch did not install — the page would see the HOST voice list"
    )
    return data


def _voices(tmp_path, locale, os_type):
    """Build the extension for `os_type`, execute it, return the roster."""
    return _probe(tmp_path, locale, os_type)["voices"]


def _os_marker(tmp_path, locale, os_type):
    js = pathlib.Path(
        build_voice_extension(locale, str(tmp_path / f"m_{os_type}"), os_type=os_type)
        + "/voices.js"
    ).read_text(encoding="utf-8")
    m = re.search(r'const OS = "([a-z]+)"', js)
    assert m, "no baked OS marker in the emitted voices.js"
    return m.group(1)


# The eSpeak roster an android profile was served before this arm existed. These
# are the SELECTED-value strings to stay away from, not substrings of the file.
_ESPEAK_NAMES = {
    "English (America)", "English (Great Britain)", "Polish", "German",
    "French (France)", "Spanish", "Ukrainian", "Russian",
}


# --------------------------------------------------------------------------
# AC1 / AC2 — an android profile gets an Android roster, not the eSpeak one.
#
# AC2 (premise inversion): every assertion in this section FAILS on `main`
# today, because `os_norm` folded "android" into the "linux" arm and the
# emitted roster was literally the eSpeak set.
# --------------------------------------------------------------------------

def test_android_profile_bakes_its_own_os_marker(tmp_path):
    # On main this returns "linux" — the fold, stated as a value.
    assert _os_marker(tmp_path, "en-US", "android") == "android"


def test_android_profile_is_not_served_the_espeak_desktop_roster(tmp_path):
    """AC1's negative half, asserted on the SELECTED roster.

    'English (America)' beside an Adreno/Mali renderer is the impossible pair
    this ticket exists to close. This is the assertion that goes red if the
    android arm is deleted — the file still CONTAINS the eSpeak strings either
    way, so only the returned values can tell the two apart.
    """
    voices = _voices(tmp_path, "pl-PL", "android")
    names = {v["name"] for v in voices}
    leaked = names & _ESPEAK_NAMES
    assert not leaked, f"android profile served eSpeak Linux desktop voices: {leaked}"


def test_android_roster_is_icu_locale_display_names(tmp_path):
    """AC1's positive half — the shape Chromium actually reports on Android.

    TtsPlatformImpl.java names each voice getDisplayLanguage()+' '+
    getDisplayCountry() — a BARE SPACE join, so no comma, no parenthesis and no
    ' - ' separator. That is precisely what distinguishes it from the eSpeak
    'English (America)' and the SAPI 'Microsoft David - English (United States)'
    shapes. Provenance: tests/fixtures/android-voices-reference.md.
    """
    voices = _voices(tmp_path, "en-US", "android")
    assert len(voices) >= 10, "a real device reports one voice per available locale"
    names = [v["name"] for v in voices]
    assert "English United States" in names
    assert "English United Kingdom" in names
    for n in names:
        assert "(" not in n and ")" not in n, f"parenthesised name is not the Android shape: {n}"
        assert " - " not in n, f"SAPI-style name leaked into the Android roster: {n}"
        assert not n.startswith("Microsoft "), f"SAPI voice leaked: {n}"


def test_android_lang_is_the_java_underscore_form(tmp_path):
    """The single most distinctive field in the roster.

    lang = Locale.toString(), which joins with an UNDERSCORE ('en_US'), and
    nothing on the read path rewrites it to a hyphen (tts_android.cc copies it
    verbatim; the only replace('_','-') in the Java file is on the speak path).
    No other arm in voice_ext emits an underscore lang, so this is the field a
    cross-check reads to tell an Android roster from a desktop one.
    """
    voices = _voices(tmp_path, "en-US", "android")
    for v in voices:
        assert "_" in v["lang"], f"expected the Java underscore lang form, got {v['lang']!r}"
        assert "-" not in v["lang"], f"hyphenated lang is the desktop form: {v['lang']!r}"
    assert "en_US" in {v["lang"] for v in voices}


def test_android_voice_uri_equals_the_name(tmp_path):
    """The counter-intuitive one, and the reason it is asserted explicitly.

    PS-65 expected 'voiceURIs in the Android shape' — but VoiceData carries no
    URI field at all, and speech_synthesis_impl.cc mints `voice_uri =
    voices[i].name`. So Android's voiceURI IS a bare name, exactly like eSpeak's.
    The URI shape is therefore NOT a discriminator, and a test asserting
    otherwise would be pinning something false about real hardware. Pinned so a
    future edit does not "fix" this into an invented com.google.android.tts URI.
    """
    voices = _voices(tmp_path, "en-US", "android")
    for v in voices:
        assert v["voiceURI"] == v["name"], (
            "speech_synthesis_impl.cc sets voice_uri = name on every platform"
        )


def test_android_roster_agrees_with_navigator_language(tmp_path):
    """is_default is (i == 0), so the default voice must match the profile locale.

    The display names are also localized to the device UI locale
    (getDisplayLanguage() localizes to the default locale), so a pl-PL profile
    reporting English display names would itself be the inconsistent pair this
    ticket is about.
    """
    voices = _voices(tmp_path, "pl-PL", "android")
    assert voices[0]["lang"] == "pl_PL"
    assert voices[0]["default"] is True
    assert [v["default"] for v in voices[1:]] == [False] * (len(voices) - 1)
    # Rendered through the profile locale's CLDR data, not English.
    assert voices[0]["name"] == "polski Polska", voices[0]["name"]
    assert "angielski Stany Zjednoczone" in {v["name"] for v in voices}
    # And no duplicate entry for the locale that was prepended.
    langs = [v["lang"] for v in voices]
    assert len(langs) == len(set(langs)), f"duplicate locale in roster: {langs}"


def test_android_voices_are_local_and_patch_announces_itself(tmp_path):
    """Both halves of the name, asserted — the roster IS local, and the patch
    DOES announce itself.

    `localService`: native = true in tts_android.cc, and is_local_service =
    !remote, so every Android voice is local. A remote voice here would imply a
    network TTS engine the device never reported.

    `voiceschanged`: voice_ext.py:172-174 dispatches TWICE on purpose —
    synchronously, then again through setTimeout. The deferred one is not
    redundant: getVoices() is famously empty on first call, so real pages
    register a voiceschanged listener and re-read. A listener attached AFTER
    document_start misses the synchronous dispatch entirely and would be left
    holding the HOST roster — the exact leak this extension exists to prevent.
    Both phases are therefore asserted separately; asserting only the union
    would let the deferred dispatch be deleted silently.
    """
    data = _probe(tmp_path, "en-US", "android")
    voices = data["voices"]
    assert all(v["localService"] is True for v in voices)

    assert "voiceschanged" in data["immediateEvents"], (
        "no synchronous voiceschanged: a listener present at document_start is "
        f"never told the roster changed. events={data['events']}"
    )
    assert "voiceschanged" in data["deferredEvents"], (
        "no deferred voiceschanged: a listener registered AFTER document_start "
        "re-reads getVoices() on this event only, so without it the page keeps "
        f"the HOST roster. events={data['events']}"
    )


# --------------------------------------------------------------------------
# AC4 — cross-vector coherence, asserted JOINTLY from one seed.
#
# This is the AC that encodes the actual defect. A per-file test would have
# passed on `main` for the GPU half (gpu_ext already had its android arm); only
# building BOTH vectors from the SAME os_type catches the disagreement.
# --------------------------------------------------------------------------

_GPU_PROBE = r"""
function makeRealm() {
  function WebGLRenderingContext() {}
  function WebGL2RenderingContext() {}
  for (const C of [WebGLRenderingContext, WebGL2RenderingContext]) {
    C.prototype.getParameter = function () { return "HOST_VALUE_NOT_SPOOFED"; };
    C.prototype.getExtension = function () { return null; };
    C.prototype.getSupportedExtensions = function () { return ["HOST_EXT"]; };
    C.prototype.getShaderPrecisionFormat = function () { return null; };
  }
  return { WebGLRenderingContext, WebGL2RenderingContext };
}
const src = require('fs').readFileSync(process.argv[2], 'utf8');
const G = makeRealm();
const sandbox = { self: G, window: G, ...G };
require('vm').createContext(sandbox);
require('vm').runInContext(src, sandbox);
const gl2 = new G.WebGL2RenderingContext();
const dims = gl2.getParameter(3386);
console.log(JSON.stringify({
  unmaskedVendor: gl2.getParameter(0x9245),
  unmaskedRenderer: gl2.getParameter(0x9246),
  maxViewportDims: ArrayBuffer.isView(dims) ? Array.from(dims) : dims,
}));
"""


def _gpu(tmp_path, seed, os_type):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    d = pathlib.Path(build_gpu_extension(seed, os_type, str(tmp_path / f"g_{seed}_{os_type}"), 0, engine_platform=engine_platform_for(os_type, "desktop")))
    harness = d / "harness.js"
    harness.write_text(_GPU_PROBE, encoding="utf-8")
    out = subprocess.run(
        [node, str(harness), str(d / "gpu.js")],
        capture_output=True, text=True, timeout=60, encoding="utf-8",
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.parametrize("seed", [1, 0x5EED, 777123])
def test_one_android_profile_is_coherent_across_gpu_and_voices(tmp_path, seed):
    """ONE os_type, BOTH vectors, executed — the pair the defect broke.

    On `main` this profile shipped: Android UA + Adreno/Mali renderer + GLES
    viewport limits + the eSpeak Linux DESKTOP voice roster. The GPU half was
    already right, which is exactly why a per-file test could not see the bug.
    """
    os_type = "android"
    gpu = _gpu(tmp_path, seed, os_type)
    voices = _voices(tmp_path, "en-US", os_type)

    # --- GPU half: from ANDROID_GPUS, i.e. an Adreno/Mali GLES device.
    renderer = gpu["unmaskedRenderer"]
    assert renderer != "HOST_VALUE_NOT_SPOOFED"
    assert ("Adreno" in renderer) or ("Mali" in renderer), renderer
    assert "OpenGL ES" in renderer, renderer
    assert "Direct3D" not in renderer, renderer
    # The giveaway gpu_ext's own comment names: a phone reports 16384, not 32767.
    assert gpu["maxViewportDims"] == [16384, 16384], gpu["maxViewportDims"]

    # --- Voice half, from the SAME os_type: must be a phone roster too.
    names = {v["name"] for v in voices}
    assert not (names & _ESPEAK_NAMES), (
        f"a profile with renderer {renderer!r} was served eSpeak desktop voices: "
        f"{names & _ESPEAK_NAMES}"
    )
    assert "English United States" in names
    assert all("_" in v["lang"] for v in voices)


# --------------------------------------------------------------------------
# AC5 / AC6 — the other arms are narrowed, never repointed.
# --------------------------------------------------------------------------

def test_linux_profile_still_gets_espeak_after_the_split(tmp_path):
    """This slice narrows the linux arm's INPUT SET; it does not repoint it.

    tests/test_voice_ext.py::test_linux_profile_gets_espeak_voices asserts the
    same thing on the emitted text and is deliberately left untouched (AC5).
    This restates it on the SELECTED roster, which is the stronger claim.
    """
    voices = _voices(tmp_path, "pl-PL", "linux")
    names = {v["name"] for v in voices}
    assert "English (America)" in names
    assert "Polish" in names
    assert all("-" in v["lang"] for v in voices), "linux keeps the hyphenated BCP-47 form"
    assert _os_marker(tmp_path, "pl-PL", "linux") == "linux"


def test_ios_still_resolves_to_the_apple_roster(tmp_path):
    """AC6 — the ios→macos fold is CORRECT and stays.

    Real iOS reports the Apple roster (Samantha/Alex/Daniel), so folding it into
    the macos arm is deliberate, not an oversight of the same class this ticket
    fixes. Pinned by an explicit test so the out-of-scope decision cannot be
    silently "fixed for symmetry" later.
    """
    assert _os_marker(tmp_path, "en-US", "ios") == "macos"
    voices = _voices(tmp_path, "en-US", "ios")
    names = {v["name"] for v in voices}
    assert {"Samantha", "Alex", "Daniel"} <= names, names
    assert not (names & _ESPEAK_NAMES)
    assert all(v["voiceURI"].startswith("com.apple.speech.synthesis.voice.") for v in voices)


def test_windows_and_macos_rosters_are_untouched_by_the_split(tmp_path):
    """The two arms this slice does not aim at, asserted on selected values."""
    win = {v["name"] for v in _voices(tmp_path, "pl-PL", "windows")}
    assert "Microsoft David - English (United States)" in win
    assert "Microsoft Paulina - Polish (Poland)" in win

    mac = {v["name"] for v in _voices(tmp_path, "pl-PL", "macos")}
    assert {"Samantha", "Alex", "Daniel", "Zosia"} <= mac, mac


@pytest.mark.parametrize("os_type", ["windows", "macos", "linux", "android", "ios"])
def test_every_arm_emits_syntactically_valid_js(tmp_path, os_type):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    d = build_voice_extension("pl-PL", str(tmp_path / f"s_{os_type}"), os_type=os_type)
    out = subprocess.run(
        [node, "--check", str(pathlib.Path(d) / "voices.js")],
        capture_output=True, text=True, timeout=60, encoding="utf-8",
    )
    assert out.returncode == 0, out.stderr


def test_unknown_os_still_falls_through_to_windows(tmp_path):
    # The else-arm is unchanged: only the linux arm's input set was narrowed.
    assert _os_marker(tmp_path, "en-US", "haiku") == "windows"
    assert _os_marker(tmp_path, "en-US", "darwin") == "macos"
