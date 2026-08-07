"""MAIN-world extension that spoofs the Web Speech API voice list.

fingerprint-chromium leaves speechSynthesis.getVoices() at the HOST OS list — on
macOS that is ~180 voices led by the host UI locale (e.g. ru-RU on a Russian
host), which betrays BOTH the real OS (macOS, not the spoofed Windows) and the
host locale, inside an otherwise coherent Windows/proxy identity. The stealth-
Firefox engine already returns a small Windows-plausible set; this brings chromium
to parity by replacing getVoices() with a fixed Windows SAPI voice list plus a
locale-matched entry, so a scanner reads a normal Windows machine.
"""

import json
import pathlib

from .worker_wrap import realm_bootstrap_js

# A stock Windows 10/11 SAPI5 voice set (what Edge/Chrome expose on Windows),
# plus a voice for the spoofed language so the list agrees with navigator.language.
CONTENT_SCRIPT = r"""
(function () {
// Patch one realm G. A fresh about:blank iframe otherwise returns the host voice
// list (or an empty list) while the page reports the spoofed set — a page/iframe
// mismatch a scanner flags. LANG lives inside so applyVoicePatch.toString()
// carries it into every realm.
function applyVoicePatch(G) {
 try {
  if (!G || G.__personaVoice || !G.speechSynthesis) return;
  G.__personaVoice = true;
  const LANG = %LANG%;
  const base = (LANG.split('-')[0] || 'en');
  const WIN = [
    {name:'Microsoft David - English (United States)', lang:'en-US'},
    {name:'Microsoft Zira - English (United States)', lang:'en-US'},
    {name:'Microsoft Mark - English (United States)', lang:'en-US'},
  ];
  const LOCALE_VOICE = {
    'pl': {name:'Microsoft Paulina - Polish (Poland)', lang:'pl-PL'},
    'de': {name:'Microsoft Hedda - German (Germany)', lang:'de-DE'},
    'fr': {name:'Microsoft Hortense - French (France)', lang:'fr-FR'},
    'es': {name:'Microsoft Helena - Spanish (Spain)', lang:'es-ES'},
    'uk': {name:'Microsoft Ostap - Ukrainian (Ukraine)', lang:'uk-UA'},
    'ru': {name:'Microsoft Irina - Russian (Russia)', lang:'ru-RU'},
  }[base];
  const spec = LOCALE_VOICE && LOCALE_VOICE.lang !== 'en-US' ? [LOCALE_VOICE].concat(WIN) : WIN.slice();
  const proto = (G.SpeechSynthesisVoice && G.SpeechSynthesisVoice.prototype) || Object.prototype;
  const voices = spec.map(function (v, idx) {
    const o = Object.create(proto);
    Object.defineProperties(o, {
      voiceURI: {value: 'Microsoft Server Speech Text to Speech Voice (' + v.lang + ', ' + v.name.replace(/^Microsoft /, '').split(' - ')[0] + ')', enumerable: true},
      name: {value: v.name, enumerable: true},
      lang: {value: v.lang, enumerable: true},
      localService: {value: true, enumerable: true},
      default: {value: idx === 0, enumerable: true},
    });
    return o;
  });
  const ss = G.speechSynthesis;
  const gv = function () { return voices.slice(); };
  // Read as native under the native_ext Function.prototype.toString patch.
  try { Object.defineProperty(gv, '__pnaName', {value: 'getVoices'}); } catch (e) {}
  try { Object.defineProperty(gv, 'name', {value: 'getVoices'}); } catch (e) {}
  Object.defineProperty(ss, 'getVoices', {value: gv, configurable: true});
  // fire voiceschanged so late listeners re-read the spoofed list
  try { ss.dispatchEvent(new Event('voiceschanged')); } catch (e) {}
  try { G.setTimeout(function () { try { ss.dispatchEvent(new Event('voiceschanged')); } catch (e) {} }, 0); } catch (e) {}
 } catch (e) {}
}
__REALM_BOOTSTRAP__
})();
"""

MANIFEST = {
    "manifest_version": 3,
    "name": "persona-voices",
    "version": "1.0",
    "content_scripts": [
        {
            "matches": ["<all_urls>"],
            "js": ["voices.js"],
            "run_at": "document_start",
            "all_frames": True,
            "world": "MAIN",
        }
    ],
}


def build_voice_extension(locale: str, base_dir: str) -> str:
    """Generate an unpacked extension that replaces the speechSynthesis voice
    list with a Windows-plausible set matching `locale`, hiding the host OS
    voices (a macOS + host-locale tell that chromium otherwise leaks)."""
    ext_dir = pathlib.Path(base_dir)
    ext_dir.mkdir(parents=True, exist_ok=True)
    js = CONTENT_SCRIPT.replace("%LANG%", json.dumps(locale or "en-US")).replace(
        "__REALM_BOOTSTRAP__", realm_bootstrap_js("applyVoicePatch")
    )
    (ext_dir / "voices.js").write_text(js, encoding="utf-8")
    (ext_dir / "manifest.json").write_text(json.dumps(MANIFEST, indent=2), encoding="utf-8")
    return str(ext_dir)
