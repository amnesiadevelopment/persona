import json
import pathlib

from src.services.browser.voice_ext import build_voice_extension


def test_builds_manifest_and_js(tmp_path):
    d = build_voice_extension("pl-PL", str(tmp_path / "v"))
    p = pathlib.Path(d)
    man = json.loads((p / "manifest.json").read_text())
    assert man["content_scripts"][0]["world"] == "MAIN"
    js = (p / "voices.js").read_text()
    # locale is embedded and a Windows SAPI set is present (not host voices)
    assert '"pl-PL"' in js
    assert "Microsoft" in js
    assert "getVoices" in js
    assert "speechSynthesis" in js


def test_locale_voice_matches_language(tmp_path):
    js = pathlib.Path(build_voice_extension("de-DE", str(tmp_path / "v")) + "/voices.js").read_text()
    assert '"de-DE"' in js
    # the German locale-voice entry is available in the map
    assert "German" in js


def test_macos_profile_gets_apple_voices(tmp_path):
    # #5 (audit4): a macOS/iOS profile ships an Apple GPU + MacIntel — Microsoft
    # SAPI voices are a hard OS-mismatch tell. It must get Apple voices.
    import pathlib
    from src.services.browser.voice_ext import build_voice_extension
    js = pathlib.Path(
        build_voice_extension("en-US", str(tmp_path / "v"), os_type="macos")
        + "/voices.js"
    ).read_text()
    assert "Samantha" in js and "Alex" in js
    assert 'const OS = "macos"' in js


def test_linux_profile_gets_espeak_voices(tmp_path):
    import pathlib
    from src.services.browser.voice_ext import build_voice_extension
    js = pathlib.Path(
        build_voice_extension("pl-PL", str(tmp_path / "v"), os_type="linux")
        + "/voices.js"
    ).read_text()
    assert 'const OS = "linux"' in js
    assert "English (America)" in js  # eSpeak-style name


def test_windows_still_gets_microsoft_voices(tmp_path):
    import pathlib
    from src.services.browser.voice_ext import build_voice_extension
    js = pathlib.Path(
        build_voice_extension("pl-PL", str(tmp_path / "v"), os_type="windows")
        + "/voices.js"
    ).read_text()
    assert "Microsoft David" in js
    assert "Microsoft Paulina" in js  # pl-PL locale voice
