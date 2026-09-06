"""PS-333 — the four launch-path sites must not destroy the operator's bytes.

`src/services/browser/invisible_launch.py` had six sites reading a
profile-owned text file with ``encoding="utf-8", errors="replace"``. FOUR of
them wrote the decoded text BACK to the same file. ``errors="replace"`` is
lossy in one direction, so the write-back persisted U+FFFD **over bytes the
reader could not decode** — the file was not merely misread, it was
overwritten and the operator's original bytes were gone.

Both file kinds are third-party-authored, not persona-authored:

* ``chrome/userChrome.css`` is OPERATOR-authored — the product's own docstrings
  say so ("a zoom sheet a user added", "a user's own customization"). A
  hand-written stylesheet with a Latin-1 comment is exactly the shape this
  destroys.
* ``prefs.js`` is written by Firefox and carries operator-visible strings
  (homepage, search keywords) that routinely hold non-ASCII.

WHAT THESE TESTS ASSERT, AND WHY IT IS THE FILE'S BYTES
--------------------------------------------------------
Every test here plants a non-UTF-8 byte in a real temp profile directory,
calls the REAL SHIPPED FUNCTION, and asserts on **the bytes on disk after the
call**. Never that a helper was called, never on generated source, never on a
decoded string — because the defect IS the encode/decode round trip, and any
assertion that goes through `str` cannot see it.

⚠️ EACH DESTRUCTIVE-CASE TEST IS PAIRED WITH A UTF-8 POSITIVE CONTROL that
asserts the file survives byte-for-byte AND the function still did its job. A
test that passes because the function stopped doing anything is not coverage —
and "stop doing anything" was a genuinely available (wrong) fix here, which is
what makes the control load-bearing rather than ceremonial.

⛔ THE TRAP THIS FILE EXISTS TO PIN — READ BEFORE "SIMPLIFYING" ANY OF IT
-------------------------------------------------------------------------
Six prior tickets (PS-61, PS-166, PS-169, PS-211, PS-270, PS-280) established
one idiom for this territory: widen ``except OSError`` to
``except (OSError, UnicodeDecodeError)`` and let the step decline. That is
right where the handler leaves the file alone. **Applying it mechanically at
`_upsert_prefs_js` destroys MORE operator data than the bug it fixes**, because
that site's read handler is ``lines = []`` and the write below it is
UNCONDITIONAL — so an empty read is not a decline, it is a truncation.

Measured before the fix was written:

    bug (errors="replace")   109-byte prefs.js -> 1 character destroyed
    family idiom applied     109-byte prefs.js -> 34 bytes, 0 of 3 operator
                                                  pref lines surviving

``lines = []`` is correct for the FILE-ABSENT case (a fresh profile has no
prefs.js, where empty genuinely is the truth) and wrong for a DECODE failure,
where the file exists and is full of data. ``test_a_prefs_js_with_an
_undecodable_byte_keeps_its_unrelated_operator_lines`` pins that distinction so
the two cases cannot be collapsed back into one arm.

And declining at that site is unacceptable for a second, independent reason:
one of its callers seeds ``_startup_decided_prefs(cfg)``, which folds in
``_PROXY_ANTILEAK_PINS`` on a proxied profile. Skipping those because an
unrelated pref line failed to decode would make leak protection conditional on
an encoding, silently. ``test_the_proxy_antileak_pins_still_reach_disk...``
pins that.
"""

import pathlib

import pytest

from src.services.browser import invisible_launch as IL

# A Latin-1 comment in a hand-written stylesheet: "Menü anpassen © 2026".
# 0xfc and 0xa9 are not valid UTF-8, so a lossy read destroys both.
NON_UTF8_CSS = b"/* Men\xfc anpassen \xa9 2026 */\n#nav-bar { background: red; }\n"
UTF8_CSS = b"/* Menu anpassen (c) 2026 */\n#nav-bar { background: red; }\n"

# 0xe9 is Latin-1 "é" — a homepage of "café.example" as Firefox may have
# written it. Undecodable as UTF-8.
OPERATOR_PREF = b'user_pref("browser.startup.homepage", "caf\xe9.example");\n'
UTF8_PREF = b'user_pref("browser.startup.homepage", "cafe.example");\n'
KEEP_PREF = b'user_pref("keep.me", 1);\n'
ALSO_KEEP = b'user_pref("also.keep", "x");\n'

SCALE_PREF = b'user_pref("layout.css.devPixelsPerPx", "1.5");\n'
CLOAK_PREF = b'user_pref("zoom.stealth.cloak_windows", true);\n'

REPLACEMENT = "\ufffd".encode("utf-8")


def _prefs(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "prefs.js"


def _user_chrome(tmp_path: pathlib.Path) -> pathlib.Path:
    d = tmp_path / "chrome"
    d.mkdir(parents=True, exist_ok=True)
    return d / "userChrome.css"


# ---------------------------------------------------------------------------
# A — _show_bookmarks_toolbar (chrome/userChrome.css)
# ---------------------------------------------------------------------------


def test_a_user_authored_stylesheet_keeps_its_undecodable_bytes(tmp_path):
    """RED before the fix: 2 characters of the operator's comment became
    U+FFFD on disk, permanently, on every launch."""
    css = _user_chrome(tmp_path)
    css.write_bytes(NON_UTF8_CSS)

    IL._show_bookmarks_toolbar(str(tmp_path))

    after = css.read_bytes()
    assert NON_UTF8_CSS in after, (
        "the operator's stylesheet bytes were not carried through verbatim; "
        f"got {after[:80]!r}"
    )
    assert REPLACEMENT not in after, (
        "U+FFFD was written to the operator's own stylesheet — the read "
        "decoded lossily and the decoded text was written back"
    )


def test_the_bookmarks_rule_is_still_appended_to_a_non_utf8_stylesheet(tmp_path):
    """The fix must not be "stop doing the work". The rule still lands."""
    css = _user_chrome(tmp_path)
    css.write_bytes(NON_UTF8_CSS)

    IL._show_bookmarks_toolbar(str(tmp_path))

    assert b"#PersonalToolbar" in css.read_bytes(), (
        "the bookmarks-toolbar rule was not written — declining the step "
        "reintroduces #242's symptom for any operator whose stylesheet is "
        "not UTF-8"
    )


def test_a_utf8_stylesheet_survives_and_still_gets_the_rule(tmp_path):
    """POSITIVE CONTROL for the pair above."""
    css = _user_chrome(tmp_path)
    css.write_bytes(UTF8_CSS)

    IL._show_bookmarks_toolbar(str(tmp_path))

    after = css.read_bytes()
    assert UTF8_CSS in after
    assert b"#PersonalToolbar" in after
    assert REPLACEMENT not in after


def test_the_rule_is_not_appended_twice(tmp_path):
    """The early-return on an already-patched sheet still works through the
    byte path — the marker is matched as bytes now, not as text."""
    css = _user_chrome(tmp_path)
    css.write_bytes(NON_UTF8_CSS)

    IL._show_bookmarks_toolbar(str(tmp_path))
    once = css.read_bytes()
    IL._show_bookmarks_toolbar(str(tmp_path))
    twice = css.read_bytes()

    assert once == twice, "a second call appended the rule again"
    assert twice.count(b"#PersonalToolbar") == 1


# ---------------------------------------------------------------------------
# B — _upsert_prefs_js  (the trap site)
# ---------------------------------------------------------------------------


def test_an_undecodable_prefs_js_keeps_its_bytes_through_an_upsert(tmp_path):
    """RED before the fix: the homepage's 0xe9 became U+FFFD on disk."""
    p = _prefs(tmp_path)
    p.write_bytes(OPERATOR_PREF + KEEP_PREF)

    IL._upsert_prefs_js(str(tmp_path), {"some.new.pref": True})

    after = p.read_bytes()
    assert OPERATOR_PREF in after, (
        f"the operator's homepage line was rewritten; got {after[:80]!r}"
    )
    assert REPLACEMENT not in after


def test_a_prefs_js_with_an_undecodable_byte_keeps_its_unrelated_operator_lines(
    tmp_path,
):
    """⛔ THE TRAP, PINNED — this is the AC that the family's own established
    idiom FAILS.

    Widening the handler to ``except (OSError, UnicodeDecodeError): lines = []``
    reads as a decline and is a truncation: measured, a 109-byte prefs.js came
    back 34 bytes with none of its three operator prefs surviving. This test is
    what makes that regression impossible to land quietly.
    """
    p = _prefs(tmp_path)
    planted = OPERATOR_PREF + KEEP_PREF + ALSO_KEEP
    p.write_bytes(planted)

    IL._upsert_prefs_js(str(tmp_path), {"some.new.pref": True})

    after = p.read_bytes()
    for line in (OPERATOR_PREF, KEEP_PREF, ALSO_KEEP):
        assert line in after, (
            f"operator pref line {line!r} was lost. If this failed after a "
            "change to the read handler, the `lines = []` arm is being reached "
            "on a DECODE failure — that arm exists for the FILE-ABSENT case "
            "only, and the file here exists and is full of data."
        )
    assert len(after) > len(planted), "the new pref was not appended"


def test_the_new_pref_is_still_written_to_an_undecodable_prefs_js(tmp_path):
    """The fix must not be "stop doing the work"."""
    p = _prefs(tmp_path)
    p.write_bytes(OPERATOR_PREF)

    IL._upsert_prefs_js(str(tmp_path), {"some.new.pref": True})

    assert b'user_pref("some.new.pref", true);' in p.read_bytes()


def test_an_upsert_replaces_an_existing_key_rather_than_duplicating_it(tmp_path):
    """The filter still matches its own keys through the byte path."""
    p = _prefs(tmp_path)
    p.write_bytes(OPERATOR_PREF + b'user_pref("some.new.pref", false);\n')

    IL._upsert_prefs_js(str(tmp_path), {"some.new.pref": True})

    after = p.read_bytes()
    assert after.count(b'"some.new.pref"') == 1, "the old line was not replaced"
    assert b'user_pref("some.new.pref", true);' in after
    assert OPERATOR_PREF in after


def test_a_utf8_prefs_js_survives_an_upsert_byte_for_byte(tmp_path):
    """POSITIVE CONTROL."""
    p = _prefs(tmp_path)
    p.write_bytes(UTF8_PREF + KEEP_PREF)

    IL._upsert_prefs_js(str(tmp_path), {"some.new.pref": True})

    after = p.read_bytes()
    assert UTF8_PREF in after
    assert KEEP_PREF in after
    assert b'user_pref("some.new.pref", true);' in after
    assert REPLACEMENT not in after


def test_an_absent_prefs_js_is_still_created(tmp_path):
    """The `lines = []` arm's LEGITIMATE case — a fresh profile has no
    prefs.js, and an empty read genuinely is the truth there. Pinned so the
    fix for the decode case cannot break the file-absent case."""
    p = _prefs(tmp_path)
    assert not p.exists()

    IL._upsert_prefs_js(str(tmp_path), {"some.new.pref": True})

    assert p.exists()
    assert b'user_pref("some.new.pref", true);' in p.read_bytes()


def test_the_proxy_antileak_pins_still_reach_disk_on_an_undecodable_prefs_js(
    tmp_path,
):
    """AC5 — the constraint that rules OUT declining at this site.

    A proxied profile's anti-leak pins are seeded through this function. If it
    declined on a decode failure, leak protection would become conditional on
    whether an unrelated pref line happened to be UTF-8 — silently. That is a
    strictly worse outcome than a mojibaked comment byte, which is why this
    site takes the byte path rather than the family's decline idiom.
    """
    p = _prefs(tmp_path)
    p.write_bytes(OPERATOR_PREF)

    IL._upsert_prefs_js(str(tmp_path), dict(IL._PROXY_ANTILEAK_PINS))

    after = p.read_bytes()
    for key in IL._PROXY_ANTILEAK_PINS:
        assert f'"{key}"'.encode("utf-8") in after, (
            f"anti-leak pin {key} did not reach disk on a profile whose "
            "prefs.js is not valid UTF-8"
        )
    assert OPERATOR_PREF in after


# ---------------------------------------------------------------------------
# C — _scrub_prefs_js, BOTH ARMS
# ---------------------------------------------------------------------------


def test_a_scrub_that_fires_keeps_the_operators_other_lines(tmp_path):
    """RED before the fix. The write-back path is the one that corrupted."""
    p = _prefs(tmp_path)
    p.write_bytes(OPERATOR_PREF + SCALE_PREF)

    IL._scrub_prefs_js(str(tmp_path), ["layout.css.devPixelsPerPx"])

    after = p.read_bytes()
    assert OPERATOR_PREF in after
    assert REPLACEMENT not in after


def test_a_scrub_that_fires_still_removes_the_stale_key(tmp_path):
    """The fix must not be "stop scrubbing" — a stale devPixelsPerPx opens the
    first window at the sampled scale rather than the host's."""
    p = _prefs(tmp_path)
    p.write_bytes(OPERATOR_PREF + SCALE_PREF)

    IL._scrub_prefs_js(str(tmp_path), ["layout.css.devPixelsPerPx"])

    assert b"layout.css.devPixelsPerPx" not in p.read_bytes()


def test_a_scrub_whose_key_is_absent_leaves_the_file_untouched(tmp_path):
    """THE OTHER ARM. The corruption was conditional — with no matching key
    the function early-returns and never writes. Pinned so the conditional
    nature is recorded rather than assumed."""
    p = _prefs(tmp_path)
    planted = OPERATOR_PREF + KEEP_PREF
    p.write_bytes(planted)

    IL._scrub_prefs_js(str(tmp_path), ["layout.css.devPixelsPerPx"])

    assert p.read_bytes() == planted, "the file was rewritten despite no match"


def test_a_utf8_prefs_js_scrubs_cleanly(tmp_path):
    """POSITIVE CONTROL."""
    p = _prefs(tmp_path)
    p.write_bytes(UTF8_PREF + SCALE_PREF)

    IL._scrub_prefs_js(str(tmp_path), ["layout.css.devPixelsPerPx"])

    after = p.read_bytes()
    assert UTF8_PREF in after
    assert b"layout.css.devPixelsPerPx" not in after
    assert REPLACEMENT not in after


# ---------------------------------------------------------------------------
# D — _scrub_headless_cloak_prefs, BOTH ARMS
# ---------------------------------------------------------------------------


def test_a_cloak_scrub_that_fires_keeps_the_operators_other_lines(tmp_path):
    """RED before the fix."""
    p = _prefs(tmp_path)
    p.write_bytes(OPERATOR_PREF + CLOAK_PREF)

    IL._scrub_headless_cloak_prefs(str(tmp_path))

    after = p.read_bytes()
    assert OPERATOR_PREF in after
    assert REPLACEMENT not in after


def test_a_cloak_scrub_that_fires_still_removes_the_stale_key(tmp_path):
    """The fix must not be "stop scrubbing", and at THIS site the cost of
    declining is the sharpest in the group: a surviving cloak pref means the
    patched binary DWM-cloaks the window it just opened, so the operator's
    browser never appears — with no error, because the launch "succeeded"."""
    p = _prefs(tmp_path)
    p.write_bytes(OPERATOR_PREF + CLOAK_PREF)

    IL._scrub_headless_cloak_prefs(str(tmp_path))

    assert b"zoom.stealth.cloak_windows" not in p.read_bytes()


def test_a_cloak_scrub_with_no_stale_keys_leaves_the_file_untouched(tmp_path):
    """THE OTHER ARM."""
    p = _prefs(tmp_path)
    planted = OPERATOR_PREF + KEEP_PREF
    p.write_bytes(planted)

    IL._scrub_headless_cloak_prefs(str(tmp_path))

    assert p.read_bytes() == planted


def test_a_utf8_prefs_js_cloak_scrubs_cleanly(tmp_path):
    """POSITIVE CONTROL."""
    p = _prefs(tmp_path)
    p.write_bytes(UTF8_PREF + CLOAK_PREF)

    IL._scrub_headless_cloak_prefs(str(tmp_path))

    after = p.read_bytes()
    assert UTF8_PREF in after
    assert b"zoom.stealth.cloak_windows" not in after
    assert REPLACEMENT not in after


# ---------------------------------------------------------------------------
# AC7 — the two read-only siblings must stay as they are
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fn_name", ["_scrub_chrome_zoom_css", "_profile_last_engine_dir"]
)
def test_the_read_only_siblings_still_read_leniently(fn_name):
    """OUT OF SCOPE, and pinned so a later sweep does not "finish the job".

    These two read the same file kinds with errors="replace" and are CORRECT
    as-is: neither writes the decoded text back, so neither can lose anything.
    `_scrub_chrome_zoom_css` only ever `os.remove`s, and
    `_profile_last_engine_dir` returns a string compared against a pure-ASCII
    `firefox-NN` token. Changing them would be churn, not a fix.
    """
    import inspect

    src = inspect.getsource(getattr(IL, fn_name))
    assert 'errors="replace"' in src, (
        f"{fn_name} no longer reads leniently. It is READ-ONLY and was "
        "deliberately left alone by PS-333 — if this was a sweep, revert it; "
        "if it was deliberate, this test is the place to record why."
    )
