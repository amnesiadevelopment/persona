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
