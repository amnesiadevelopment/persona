# Android speechSynthesis voice reference — what a real Android Chrome reports

Reference values behind the `android` arm of `src/services/browser/voice_ext.py`
(ticket PS-65). This file exists so the constants in that module have a
checkable provenance and the next person does not re-derive them from scratch.

**Read the provenance column before trusting any value.** Every entry below is
labelled either **[source]** (derived from published Chromium source, fetched
this session from `chromium.googlesource.com` at `refs/heads/main`) or
**[derived]** (computed by executing the rule that the source establishes).
**No value here is a capture from a physical Android handset** — see "What is
NOT established" at the bottom. That bound is the same one the ticket carried,
and it is not laundered here.

## ⚠️ The ticket's own guess was wrong, and the source says so

PS-65 said: *"Prefer Google TTS-shaped names with `voiceURI`s in the Android
shape."* It flagged that as to-be-established rather than measured, and it is
**refuted** by the source chain below. Chromium on Android does **not** surface
Google TTS engine voice names (`en-us-x-tpf-local`, `Google US English`, …) to
`speechSynthesis.getVoices()` at all. It surfaces one entry per **available
locale**, named from ICU display names, with a **bare-name `voiceURI`** — the
same `voiceURI` *shape* as eSpeak.

That last point is the trap, and it is why the discriminator had to change:

> **The `voiceURI` shape does NOT distinguish Android from Linux. The voice
> NAMES and the underscore `lang` form do.**

A test asserting "the URI is not a bare name" would therefore be asserting
something false about real Android. The tests assert on the roster **content**
instead (AC3's requirement, satisfied for a reason stronger than style).

## The chain, end to end — four hops, each read at source

### 1. The names are ICU locale display names, not engine voice names [source]

`content/public/android/java/src/org/chromium/content/browser/TtsPlatformImpl.java`,
`TtsEngine.initializeDefault()`:

```java
Locale[] locales = Locale.getAvailableLocales();
final List<TtsVoice> voices = new ArrayList<>();
for (Locale locale : locales) {
    if (!locale.getVariant().isEmpty()) continue;
    try {
        if (mTextToSpeech.isLanguageAvailable(locale) > 0) {
            String name = locale.getDisplayLanguage();
            if (!locale.getCountry().isEmpty()) {
                name += " " + locale.getDisplayCountry();
            }
            TtsVoice voice = new TtsVoice(name, locale.toString());
            voices.add(voice);
        }
    } catch (Exception e) {
        // Just skip the locale if it's invalid. ...
    }
}
```

Three facts fall straight out of it, and all three are load-bearing:

- **name** = `getDisplayLanguage()` + `" "` + `getDisplayCountry()` — e.g.
  `English United States`. Note there is **no comma, no parenthesis, no
  hyphen**: a bare space join. This is not the `English (America)` eSpeak shape
  and not the `Microsoft David - English (United States)` SAPI shape.
- **lang** = `locale.toString()` — Java's `Locale.toString()` joins with an
  **underscore**: `en_US`, not `en-US`. This is the single most distinctive
  field in the whole roster, and no other platform in this file emits it.
- **The roster is one entry per available locale**, not per installed voice —
  so it is long and locale-shaped, not a hand-picked celebrity list.

### 2. Display names are localized to the DEVICE UI locale [source]

`Locale.getDisplayLanguage()` with no argument is documented as returning the
name *"localized for the default locale"*
(<https://developer.android.com/reference/java/util/Locale>). So a phone whose
UI is Polish reports `angielski Stany Zjednoczone` for `en_US`, not
`English United States`.

This matters here because a persona profile declares a locale
(`navigator.language`), and a roster whose display names are in a *different*
language than the declared UI locale is itself an inconsistent pair — exactly
the defect class this ticket exists to close. The arm therefore renders the
names through `Intl.DisplayNames([LANG])`, which is the same CLDR data ICU
serves Java. **[derived]**

Corroborated by execution (node 20, ICU 74):

```
$ node -e '...Intl.DisplayNames(["pl"],...)'
en_US => angielski Stany Zjednoczone
pl_PL => polski Polska
de_DE => niemiecki Niemcy
```

`Intl.DisplayNames` is Chrome 81+, so it is present in every engine this
project ships; the arm still carries an English fallback table for the roster
locales so a missing/throwing `Intl` degrades to a plausible list rather than
to an empty one.

### 3. The browser process copies name and lang VERBATIM [source]

`content/browser/speech/tts_android.cc`, `TtsPlatformImplAndroid::GetVoices()`:

```cpp
data.native = true;
data.name = base::android::ConvertJavaStringToUTF8(
    Java_TtsPlatformImpl_getVoiceName(env, java_ref_, i));
data.lang = base::android::ConvertJavaStringToUTF8(
    Java_TtsPlatformImpl_getVoiceLanguage(env, java_ref_, i));
```

No transformation. In particular **the underscore is not rewritten to a
hyphen** anywhere on the read path — the only `replace("_", "-")` in the Java
file is on the *speak* path (`setLanguage(...)`), not on voice enumeration.
`TtsPlatformImpl::FinalizeVoiceOrdering()` is an empty base implementation and
`tts_android.h` does not override it, so nothing reorders the list either.

### 4. `voiceURI` IS the name — there is no separate URI on any platform [source]

`content/public/browser/tts_controller.h`, `struct VoiceData`, has fields
`name`, `lang`, `engine_id`, `events`, `remote`, `native`,
`native_voice_identifier` — and **no URI field at all**. The URI is minted at
the mojo boundary, in `content/browser/speech/speech_synthesis_impl.cc`,
`SendVoiceListToObserver()`:

```cpp
out_voice->voice_uri = voices[i].name;
out_voice->name      = voices[i].name;
out_voice->lang      = voices[i].lang;
out_voice->is_local_service = !voices[i].remote;
out_voice->is_default = (i == 0);
```

Blink then returns both verbatim
(`third_party/blink/renderer/modules/speech/speech_synthesis_voice.h:44,46`:
`voiceURI()` → `mojom_voice_->voice_uri`, `lang()` → `mojom_voice_->lang`).

Consequences the arm encodes:

| Property | Value | Why |
|---|---|---|
| `voiceURI` | **equal to `name`** | `voice_uri = voices[i].name` **[source]** |
| `localService` | `true` | `is_local_service = !remote`; `tts_android.cc` sets `native = true` and never sets `remote` **[source]** |
| `default` | first entry only | `is_default = (i == 0)` **[source]** |

## Roster membership — the locale list

**[derived]**, and this is the weakest link in the file, deliberately flagged.
The real list is whatever `isLanguageAvailable()` returns on that handset, which
depends on which Google TTS language packs are installed. There is no single
correct answer, and no capture was available to sample a typical one.

The arm ships the locales below: the Google TTS **pre-installed/default
download set** for en/es/fr/de/it/pt/ru/nl/pl/tr/id/ja/ko/zh/hi/ar, which is
the common shape of a stock device. English display names for reference (the
runtime value is localized per §2):

| Tag | English display name |
|---|---|
| `en_US` | English United States |
| `en_GB` | English United Kingdom |
| `en_AU` | English Australia |
| `en_IN` | English India |
| `es_ES` | Spanish Spain |
| `es_US` | Spanish United States |
| `fr_FR` | French France |
| `de_DE` | German Germany |
| `it_IT` | Italian Italy |
| `pt_BR` | Portuguese Brazil |
| `ru_RU` | Russian Russia |
| `nl_NL` | Dutch Netherlands |
| `pl_PL` | Polish Poland |
| `tr_TR` | Turkish Türkiye |
| `id_ID` | Indonesian Indonesia |
| `ja_JP` | Japanese Japan |
| `ko_KR` | Korean South Korea |
| `zh_CN` | Chinese China |
| `hi_IN` | Hindi India |
| `ar_EG` | Arabic Egypt |

The profile's own locale is prepended when it is not already present, so the
roster always agrees with `navigator.language` — the same rule the other three
arms already follow.

## What is NOT established

- **No physical-handset capture.** Every value above is source-derived or
  computed from a rule the source establishes. The *shape* (bare-space display
  names, underscore `lang`, name-as-URI, `localService: true`, first-is-default)
  rests on Chromium source and is strong. The *membership* of the locale list
  rests on the pre-installed language set and is a plausible reconstruction, not
  a measurement. If a capture ever becomes available, reconcile the list here
  and drop this caveat.
- **Voice count.** A real device commonly reports more entries than 20 (every
  locale with an installed pack, including many `en_*` regions). The list is
  deliberately not padded with invented locales.
- **Non-default TTS engines.** `initializeNonDefault()` in the Java file does
  not enumerate voices at all, so a device whose default engine is not Google
  TTS can report an **empty** list. Not modelled: an empty roster is a worse
  tell than a plausible one.

## What does not depend on any of that

`English (America)` with an `English (America)` bare-name URI — the eSpeak
Linux desktop roster — beside an Adreno/Mali renderer and an Android UA is
impossible **whatever** the exact right Android roster turns out to be. That is
the claim the fix rests on, and it is settled independently of every open
question above.
