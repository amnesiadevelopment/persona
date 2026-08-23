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

from .worker_wrap import realm_bootstrap_js, realm_guard_js

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
  if (!G || !G.speechSynthesis) return;
__REALM_GUARD__
  const LANG = %LANG%;
  const OS = "%OS%";  // "windows" | "macos" | "linux" | "android"
  const base = (LANG.split('-')[0] || 'en');

  // Per-OS voice roster: a macOS/iOS profile ships an Apple GPU + MacIntel, so a
  // Microsoft SAPI voice list is a hard OS-mismatch tell (real Apple = Samantha/
  // Alex/Daniel). Linux uses eSpeak. Android is NOT Linux here — it reports ICU
  // locale display names (see the android arm below). getVoices() is a
  // top-weight cross-check.
  let BASE, LOCALE_VOICE, mkURI, defaultLocal;
  if (OS === "macos") {
    BASE = [
      {name:'Samantha', lang:'en-US'},
      {name:'Alex', lang:'en-US'},
      {name:'Daniel', lang:'en-GB'},
    ];
    LOCALE_VOICE = {
      'pl': {name:'Zosia', lang:'pl-PL'}, 'de': {name:'Anna', lang:'de-DE'},
      'fr': {name:'Thomas', lang:'fr-FR'}, 'es': {name:'Mónica', lang:'es-ES'},
      'uk': {name:'Lesya', lang:'uk-UA'}, 'ru': {name:'Milena', lang:'ru-RU'},
      'it': {name:'Alice', lang:'it-IT'}, 'nl': {name:'Xander', lang:'nl-NL'},
    }[base];
    mkURI = function (v) { return 'com.apple.speech.synthesis.voice.' + v.name.toLowerCase(); };
    defaultLocal = 'en-US';
  } else if (OS === "linux") {
    BASE = [
      {name:'English (America)', lang:'en-US'},
      {name:'English (Great Britain)', lang:'en-GB'},
    ];
    LOCALE_VOICE = {
      'pl': {name:'Polish', lang:'pl-PL'}, 'de': {name:'German', lang:'de-DE'},
      'fr': {name:'French (France)', lang:'fr-FR'}, 'es': {name:'Spanish', lang:'es-ES'},
      'uk': {name:'Ukrainian', lang:'uk-UA'}, 'ru': {name:'Russian', lang:'ru-RU'},
    }[base];
    mkURI = function (v) { return v.name; };
    defaultLocal = 'en-US';
  } else if (OS === "android") {
    // Android is NOT Linux-with-a-phone-UA. Chromium on Android does not expose
    // Google TTS engine voice names at all: TtsPlatformImpl.java enumerates one
    // voice PER AVAILABLE LOCALE, naming it getDisplayLanguage()+' '+
    // getDisplayCountry() and setting lang to Locale.toString() — the UNDERSCORE
    // form ("en_US"). tts_android.cc copies both verbatim, and
    // speech_synthesis_impl.cc mints voiceURI = name, so the URI is a bare name
    // here exactly as it is under eSpeak. THE URI SHAPE IS THEREFORE NOT THE
    // TELL — the NAME TEXT and the underscore lang are. Full provenance (four
    // Chromium source hops, each quoted) in
    // tests/fixtures/android-voices-reference.md. Read that file, not this
    // comment, and do not re-derive these from either.
    const TAGS = [
      'en_US','en_GB','en_AU','en_IN','es_ES','es_US','fr_FR','de_DE','it_IT',
      'pt_BR','ru_RU','nl_NL','pl_PL','tr_TR','id_ID','ja_JP','ko_KR','zh_CN',
      'hi_IN','ar_EG'
    ];
    // English fallback so a realm without Intl.DisplayNames (or a locale it
    // rejects) degrades to a plausible roster rather than to an empty one.
    const FALLBACK = {
      'en_US':'English United States','en_GB':'English United Kingdom',
      'en_AU':'English Australia','en_IN':'English India',
      'es_ES':'Spanish Spain','es_US':'Spanish United States',
      'fr_FR':'French France','de_DE':'German Germany','it_IT':'Italian Italy',
      'pt_BR':'Portuguese Brazil','ru_RU':'Russian Russia',
      'nl_NL':'Dutch Netherlands','pl_PL':'Polish Poland',
      'tr_TR':'Turkish Türkiye','id_ID':'Indonesian Indonesia',
      'ja_JP':'Japanese Japan','ko_KR':'Korean South Korea',
      'zh_CN':'Chinese China','hi_IN':'Hindi India','ar_EG':'Arabic Egypt'
    };
    // The profile's own locale leads the list: is_default is (i === 0) in
    // speech_synthesis_impl.cc, so the default voice must agree with
    // navigator.language the way it does on a real device set to that locale.
    const want = String(LANG || 'en-US').replace(/-/g, '_');
    const tags = TAGS.filter(function (t) { return t !== want; });
    tags.unshift(want);
    // getDisplayLanguage() localizes to the DEVICE UI locale, so a phone whose
    // UI is Polish says 'angielski Stany Zjednoczone' for en_US. Intl.DisplayNames
    // serves the same CLDR data ICU gives Java; rendering in English on a
    // pl-PL profile would itself be the inconsistent pair this arm exists to close.
    let dnL = null, dnR = null;
    try {
      dnL = new Intl.DisplayNames([LANG], {type: 'language'});
      dnR = new Intl.DisplayNames([LANG], {type: 'region'});
    } catch (e) {}
    const nameFor = function (tag) {
      const p = tag.split('_'), lg = p[0], rg = p[1] || '';
      try {
        if (dnL) {
          const ln = dnL.of(lg);
          if (ln) {
            const rn = (rg && dnR) ? dnR.of(rg) : '';
            // Bare space join, no comma and no parenthesis — that is the
            // literal Java concatenation, and it is what separates this roster
            // from 'English (America)' (eSpeak) and from the SAPI shape.
            return rn ? (ln + ' ' + rn) : ln;
          }
        }
      } catch (e) {}
      return FALLBACK[tag] || tag;
    };
    BASE = tags.map(function (t) { return {name: nameFor(t), lang: t}; });
    // BASE already leads with the profile locale, so the generic prepend below
    // must not run a second time.
    LOCALE_VOICE = null;
    mkURI = function (v) { return v.name; };
    defaultLocal = 'en_US';
  } else {
    BASE = [
      {name:'Microsoft David - English (United States)', lang:'en-US'},
      {name:'Microsoft Zira - English (United States)', lang:'en-US'},
      {name:'Microsoft Mark - English (United States)', lang:'en-US'},
    ];
    LOCALE_VOICE = {
      'pl': {name:'Microsoft Paulina - Polish (Poland)', lang:'pl-PL'},
      'de': {name:'Microsoft Hedda - German (Germany)', lang:'de-DE'},
      'fr': {name:'Microsoft Hortense - French (France)', lang:'fr-FR'},
      'es': {name:'Microsoft Helena - Spanish (Spain)', lang:'es-ES'},
      'uk': {name:'Microsoft Ostap - Ukrainian (Ukraine)', lang:'uk-UA'},
      'ru': {name:'Microsoft Irina - Russian (Russia)', lang:'ru-RU'},
    }[base];
    mkURI = function (v) {
      return 'Microsoft Server Speech Text to Speech Voice (' + v.lang + ', ' +
             v.name.replace(/^Microsoft /, '').split(' - ')[0] + ')';
    };
    defaultLocal = 'en-US';
  }
  const spec = LOCALE_VOICE && LOCALE_VOICE.lang !== defaultLocal
    ? [LOCALE_VOICE].concat(BASE) : BASE.slice();
  const proto = (G.SpeechSynthesisVoice && G.SpeechSynthesisVoice.prototype) || Object.prototype;
  const voices = spec.map(function (v, idx) {
    const o = Object.create(proto);
    Object.defineProperties(o, {
      voiceURI: {value: mkURI(v), enumerable: true},
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


def build_voice_extension(
    locale: str, base_dir: str, os_type: str = "windows"
) -> str:
    """Generate an unpacked extension that replaces the speechSynthesis voice
    list with an OS-appropriate, `locale`-matched set: Apple voices for macOS/iOS,
    ICU locale display names for Android, eSpeak for Linux, Microsoft SAPI for
    Windows. Hardcoding Windows voices on a macOS/iOS profile (which ships an
    Apple GPU) was a hard OS-mismatch tell.

    `android` needs its own arm for the same reason it needs one in `gpu_ext`:
    it was folded into `linux`, so a phone profile — Android UA, Adreno/Mali
    renderer, GLES viewport limits — served the eSpeak Linux DESKTOP roster
    ('English (America)', a voice literally named 'Polish'). Android reports
    neither eSpeak nor Google TTS engine names: Chromium enumerates one voice
    per available locale, named from ICU display names with an underscore lang
    ('en_US'). Provenance: tests/fixtures/android-voices-reference.md.

    `ios` stays folded into `macos` deliberately — real iOS reports the Apple
    roster, so that fold is correct rather than an oversight."""
    ext_dir = pathlib.Path(base_dir)
    ext_dir.mkdir(parents=True, exist_ok=True)
    ot = str(os_type).lower()
    os_norm = (
        "macos" if ot in ("macos", "mac", "darwin", "ios")
        else "android" if ot in ("android",)
        else "linux" if ot in ("linux",)
        else "windows"
    )
    js = (
        CONTENT_SCRIPT
        .replace("%LANG%", json.dumps(locale or "en-US"))
        .replace("%OS%", os_norm)
        .replace("__REALM_BOOTSTRAP__", realm_bootstrap_js("applyVoicePatch"))
        .replace("__REALM_GUARD__", realm_guard_js("voice", indent=2))
    )
    (ext_dir / "voices.js").write_text(js, encoding="utf-8")
    (ext_dir / "manifest.json").write_text(json.dumps(MANIFEST, indent=2), encoding="utf-8")
    return str(ext_dir)
