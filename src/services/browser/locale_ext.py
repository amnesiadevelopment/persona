import json
import pathlib

CONTENT_SCRIPT = """\
const LOCALE = {locale};
const _resolved = (orig) => function (...args) {{
  const r = orig.apply(this, args);
  r.locale = LOCALE;
  return r;
}};
try {{
  const DTF = Intl.DateTimeFormat;
  Intl.DateTimeFormat = function (locales, options) {{
    return new DTF(locales || LOCALE, options);
  }};
  Intl.DateTimeFormat.prototype = DTF.prototype;
  Intl.DateTimeFormat.supportedLocalesOf = DTF.supportedLocalesOf;
  const dtfProto = DTF.prototype.resolvedOptions;
  DTF.prototype.resolvedOptions = _resolved(dtfProto);

  const NF = Intl.NumberFormat;
  Intl.NumberFormat = function (locales, options) {{
    return new NF(locales || LOCALE, options);
  }};
  Intl.NumberFormat.prototype = NF.prototype;
  Intl.NumberFormat.supportedLocalesOf = NF.supportedLocalesOf;
  NF.prototype.resolvedOptions = _resolved(NF.prototype.resolvedOptions);

  if (Intl.RelativeTimeFormat) {{
    const RTF = Intl.RelativeTimeFormat;
    RTF.prototype.resolvedOptions = _resolved(RTF.prototype.resolvedOptions);
  }}

  const origToLocale = Date.prototype.toLocaleString;
  Date.prototype.toLocaleString = function (l, o) {{
    return origToLocale.call(this, l || LOCALE, o);
  }};
  const origToLD = Date.prototype.toLocaleDateString;
  Date.prototype.toLocaleDateString = function (l, o) {{
    return origToLD.call(this, l || LOCALE, o);
  }};
  const origToLT = Date.prototype.toLocaleTimeString;
  Date.prototype.toLocaleTimeString = function (l, o) {{
    return origToLT.call(this, l || LOCALE, o);
  }};
}} catch (e) {{}}
"""

MANIFEST = {
    "manifest_version": 3,
    "name": "persona-locale",
    "version": "1.0",
    "content_scripts": [
        {
            "matches": ["<all_urls>"],
            "js": ["locale.js"],
            "run_at": "document_start",
            "all_frames": True,
            "world": "MAIN",
        }
    ],
}


def build_locale_extension(locale: str, base_dir: str) -> str:
    """Generate an unpacked extension that pins Intl/Date locale to `locale`,
    so date/number formatting matches navigator.language and the proxy region.
    fingerprint-chromium leaves Intl at the host default (en-US) regardless of
    --lang, which contradicts the spoofed language; this closes that gap.
    """
    ext_dir = pathlib.Path(base_dir)
    ext_dir.mkdir(parents=True, exist_ok=True)
    (ext_dir / "locale.js").write_text(
        CONTENT_SCRIPT.format(locale=json.dumps(locale)), encoding="utf-8"
    )
    (ext_dir / "manifest.json").write_text(
        json.dumps(MANIFEST, indent=2), encoding="utf-8"
    )
    return str(ext_dir)
