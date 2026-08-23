import json
import pathlib

from src.services.browser.locale_ext import build_locale_extension

# locale_ext.py writes both artifacts with an explicit encoding="utf-8"
# (locale_ext.py:158-160), and locale.js legitimately carries non-ASCII bytes —
# it embeds locale sample text, and the module's own comments quote the
# host-locale leak it fixes («доллар США»). Reading it back MUST name the same
# encoding: pathlib's default is locale.getencoding(), which is UTF-8 on Linux
# and macOS but cp1252 on Windows, where the decode dies on the first non-Latin-1
# byte. The guarantee under test is the JS content, identical on all three
# platforms; only the way the file is read back differs.
_UTF8 = {"encoding": "utf-8"}


def test_creates_manifest_and_js(tmp_path):
    d = build_locale_extension("en-CA", str(tmp_path / "ext"))
    p = pathlib.Path(d)
    assert (p / "manifest.json").exists()
    assert (p / "locale.js").exists()


def test_manifest_injects_main_world_at_start(tmp_path):
    d = build_locale_extension("de-DE", str(tmp_path / "ext"))
    m = json.loads((pathlib.Path(d) / "manifest.json").read_text(**_UTF8))
    cs = m["content_scripts"][0]
    assert cs["world"] == "MAIN"
    assert cs["run_at"] == "document_start"
    assert "<all_urls>" in cs["matches"]


def test_js_embeds_locale(tmp_path):
    d = build_locale_extension("fr-FR", str(tmp_path / "ext"))
    js = (pathlib.Path(d) / "locale.js").read_text(**_UTF8)
    assert '"fr-FR"' in js
    # every Intl constructor that takes a locale is wrapped to default to the
    # pinned locale (not just DateTimeFormat/NumberFormat — DisplayNames /
    # ListFormat / RelativeTimeFormat etc. leaked the host locale otherwise).
    for ctor in ("DateTimeFormat", "NumberFormat", "RelativeTimeFormat",
                 "DisplayNames", "ListFormat", "Collator", "PluralRules",
                 "Segmenter"):
        assert ctor in js
    # Date.toString/toTimeString re-render the timezone name in the pinned locale
    # (the host-locale tz-name leak fix).
    assert "toTimeString" in js


def test_locale_on_shared_recursive_registry(tmp_path):
    # #2: locale was the last major module with its OWN worker-wrap + a
    # non-recursive iframe getter, so a NESTED iframe (grandchild) reported the
    # host locale ("ru"/«доллар США») — the exact leak this module exists to
    # fix. Route it through the shared recursive registry (applyLocalePatch),
    # like gpu/webgl/audio/screen — one bootstrap reaches every nested realm.
    d = build_locale_extension("pl-PL", str(tmp_path / "ext"))
    js = (pathlib.Path(d) / "locale.js").read_text(**_UTF8)
    assert "applyLocalePatch" in js
    assert "__pnaInstall(SELF, applyLocalePatch)" in js
    # the shared bootstrap supplies the worker/iframe carry; it re-blobs
    # blob:/data: workers under the same scheme and recurses iframes.
    assert "XMLHttpRequest" in js
    assert "G.Worker" in js and "G.SharedWorker" in js
    assert "HTMLIFrameElement" in js
    # LOCALE lives INSIDE applyLocalePatch so .toString() carries it per realm
    body = js.split("function applyLocalePatch(G)", 1)[1].split("__pnaInstall", 1)[0]
    assert '"pl-PL"' in body


def test_js_is_iife_no_globals(tmp_path):
    # #233: in the MAIN world a bare top-level const (LOCALE, _resolved) becomes a
    # page global and collides with the page's own bundle ("Identifier '…' has
    # already been declared"), killing the script — Google Sheets' calc worker
    # died and the sheet stuck on "Working". Wrap everything in an IIFE.
    d = build_locale_extension("fr-FR", str(tmp_path / "ext"))
    js = (pathlib.Path(d) / "locale.js").read_text(**_UTF8).strip()
    assert js.startswith("(function"), f"locale.js must start with an IIFE, got: {js[:30]!r}"
    assert js.endswith(("})();", "})()")), f"locale.js must end by invoking the IIFE, got: {js[-10:]!r}"
