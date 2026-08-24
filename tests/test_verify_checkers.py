"""The checker-matrix reader, proven against the pages it actually reads.

Every pattern in the catalogue is asserted against a REAL captured page under
``tests/fixtures/checker-pages/``, taken through the mobile exit on
2026-08-21. That is deliberate and it is the point of this file: a reader
observed only agreeing has not been observed, and four of the catalogue's
original patterns were WRONG against the real pages in ways that a
hand-written "does it match?" test would have happily confirmed.

The two that matter most, both pinned below:

* **The negation trap.** Pixelscan renders its clean verdicts as ``No proxy
  detected`` / ``No masking detected`` / ``No automated behavior detected``.
  The obvious adverse patterns (``proxy detected``) match all three of those,
  so a reader built on them reports a CLEAN page as three red flags — with a
  real match and a real quote behind it. ``test_pixelscan_clean_page_*``
  asserts the negated forms do not match, and
  ``test_naive_pattern_would_have_misread_the_clean_page`` demonstrates the
  bug the lookbehind fixes, so the guard cannot be deleted as decoration.

* **The false ABSENT.** Sannysoft renders ``WebDriver\\n(New)\\n\\tmissing
  (passed)``. A pattern that assumed one line read that page as ABSENT — the
  page said "passed" and the reader recorded "the page did not say this".

* **The token that is not a verdict (PS-121).** Worse than the negation trap
  and found inside it: ``bot-detector.rebrowser.net`` was read with a bare
  ``\\bdetected\\b``, but that word appears ONLY on that checker's GREEN rows
  ("No leak detected.") and never on a red one, identically on a clean page and
  a caught one. The reader therefore returned READ on every page it ever saw,
  with a byte-identical capture — it could report neither a real detection nor
  a clean result. ``test_rebrowser_*`` pins both directions.

PROVENANCE OF THE FIXTURES — they are not all one capture
---------------------------------------------------------
``sannysoft/iphey/pixelscan/creepjs.txt`` are the 2026-08-21 mobile-exit run.
The two ``rebrowser-*.txt`` files are a SEPARATE capture (2026-08-23, this
container's Firefox, no proxy), because that checker refused the mobile exit —
which is why it had no fixture, and why the defect survived. They are real
``inner_text("body")`` captures rather than hand-written text, taken in both
directions: ``rebrowser-clean.txt`` from a browser with the webdriver tell
hidden on the PROTOTYPE and a non-default viewport, ``rebrowser-caught.txt``
after firing the leaks the page itself asks an automation script to trigger.
The exit does not matter to either: every row this checker publishes is
fingerprint-driven, and none of them is about the address.
"""

from __future__ import annotations

import json
import os

import pytest

from src.services.verify.browser_tier import readings_from_texts
from src.services.verify.checkers import (
    ALL_SORTS,
    HARNESS,
    BROWSER_CHECKERS,
    CHECKERS,
    EXIT,
    FINGERPRINT,
    GPU_CLAIMED,
    GPU_RENDERED,
    HOST,
    JSON_CHECKERS,
    JsonItem,
    TIER_UNREADABLE,
    TextItem,
    checker_by_id,
)
from src.services.verify.matrix import (
    ABSENT,
    READ,
    UNOBTAINABLE,
    build_record,
    dumps,
    extract_json_item,
    extract_text_item,
    read_json_tier,
    read_unreadable_tier,
    write,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "checker-pages")

# Which fixture file carries which checker's captured page.
#
# The rebrowser entry is the CLEAN capture on purpose: this mapping feeds
# ``test_no_adverse_row_matched_on_the_clean_captured_pages``, so a checker
# listed here is one whose clean page is asserted to produce no adverse row.
# Its CAUGHT twin is loaded explicitly by the tests that need the other
# direction — see ``rebrowser_page``.
PAGES = {
    "bot.sannysoft.com": "sannysoft.txt",
    "iphey.com": "iphey.txt",
    "pixelscan.net": "pixelscan.txt",
    "creepjs": "creepjs.txt",
    "bot-detector.rebrowser.net": "rebrowser-clean.txt",
}


def page(checker_id: str) -> str:
    with open(os.path.join(FIXTURES, PAGES[checker_id]), encoding="utf-8") as fh:
        return fh.read()


def reading_for(checker_id: str, item_id: str, text: str | None = None):
    checker = checker_by_id(checker_id)
    assert checker is not None, checker_id
    body = page(checker_id) if text is None else text
    item = next(i for i in checker.items if i.id == item_id)
    return extract_text_item(checker, item, body)


# --- the negation trap ------------------------------------------------------


@pytest.mark.parametrize(
    "item_id", ["proxy_detected", "masking_detected", "automation_detected"]
)
def test_pixelscan_clean_page_does_not_read_as_a_detection(item_id):
    """The clean page contains the adverse phrase, prefixed by "No"."""
    reading = reading_for("pixelscan.net", item_id)
    assert reading.state == ABSENT, (
        f"{item_id} matched a page that says 'No ...' — a clean verdict is "
        f"being recorded as a detection (matched {reading.matched_text!r})"
    )


def test_the_clean_page_really_does_contain_the_adverse_phrases():
    """Guard the guard: if the page stopped saying "No proxy detected", the
    test above would pass for the wrong reason (nothing to trap)."""
    body = page("pixelscan.net").lower()
    assert "no proxy detected" in body
    assert "no masking detected" in body
    assert "no automated behavior detected" in body


def test_naive_pattern_would_have_misread_the_clean_page():
    """Show the bug the lookbehind fixes, so it cannot be deleted as noise.

    This is the version that was written first. It matches the CLEAN page.
    """
    import re

    naive = re.search(r"proxy detected", page("pixelscan.net"), re.IGNORECASE)
    assert naive is not None, (
        "the naive pattern no longer matches, so this guard proves nothing"
    )
    assert naive.group(0).lower() == "proxy detected"
    # ...and the catalogue's pattern does NOT.
    assert reading_for("pixelscan.net", "proxy_detected").state == ABSENT


def test_pixelscan_detection_is_read_when_the_page_really_says_it():
    """The other direction: an adverse pattern must still FIRE on a real
    detection. A lookbehind that suppressed everything would pass every test
    above while reading nothing at all."""
    dirty = page("pixelscan.net").replace("No proxy detected", "Proxy detected")
    reading = reading_for("pixelscan.net", "proxy_detected", dirty)
    assert reading.state == READ
    assert reading.value is True
    assert reading.adverse is True


# --- the negation trap, part two: the SEPARATOR (PS-119) --------------------
#
# The tests above all rest on the committed fixture, and the fixture renders
# every clean verdict with a SINGLE SPACE ("No masking detected"). That is the
# one separator the old fixed-width `(?<!no )` could express — so the fixture
# could never have caught the case below, and passed while the reader was
# capable of reporting a clean page as an antidetect detection.
#
# `inner_text` is what the browser tier reads with, and pixelscan renders each
# verdict in a component tree: "No" and "masking detected" landing in different
# elements is exactly what puts a NEWLINE between them.


@pytest.mark.parametrize("separator", ["\n", "  ", "\t", " \n  ", "\n\n"])
@pytest.mark.parametrize(
    "item_id,phrase",
    [
        ("proxy_detected", "proxy detected"),
        ("masking_detected", "masking detected"),
        ("automation_detected", "automated behavior detected"),
        ("timezone_spoofed", "timezone spoofed"),
    ],
)
def test_a_clean_verdict_split_by_whitespace_is_still_clean(
        item_id, phrase, separator):
    """THE PS-119 REGRESSION, and the reason the negation is no longer spelled
    as a lookbehind.

    A clean "No <verdict>" whose separator is anything but one space must still
    read ABSENT. Every one of these fired as a DETECTION before the fix — on
    `masking_detected` that is the single verdict in the whole catalogue that
    says "this browser is running an antidetect tool", reported off a page that
    said the opposite.
    """
    reading = reading_for("pixelscan.net", item_id, f"No{separator}{phrase}")
    assert reading.state == ABSENT, (
        f"{item_id}: a CLEAN page separated by {separator!r} read as a "
        f"detection (matched {reading.matched_text!r})"
    )


@pytest.mark.parametrize("separator", ["\n", "  ", "\t"])
def test_the_old_lookbehind_really_did_misread_those_pages(separator):
    """Guard the guard, in the same spirit as the naive-pattern test above: if
    the old spelling did NOT misread a split page, the test above proves
    nothing and would be deleted as noise."""
    import re

    old = re.search(
        r"(?<!no )masking detected",
        f"No{separator}masking detected",
        re.IGNORECASE,
    )
    assert old is not None, (
        "the old fixed-width lookbehind no longer misreads a split clean "
        "page, so the regression above is not the one being pinned"
    )
    # ...and the shipped catalogue does NOT.
    assert reading_for(
        "pixelscan.net", "masking_detected", f"No{separator}masking detected"
    ).state == ABSENT


@pytest.mark.parametrize(
    "text",
    [
        "Masking detected",
        "Foo\nMasking detected\nBar",
        # The page says BOTH — a clean verdict somewhere and a real detection
        # elsewhere. Skipping a negated occurrence must not stop the walk: the
        # detection is the reading.
        "No masking detected\n...\nMasking detected",
    ],
)
def test_a_real_detection_still_fires_whatever_else_the_page_says(text):
    """The fail-safe direction. A negation guard that suppressed everything
    would pass every ABSENT test above while reading nothing at all — which is
    the failure that would matter most here, because it reads as good news."""
    reading = reading_for("pixelscan.net", "masking_detected", text)
    assert reading.state == READ
    assert reading.value is True
    assert reading.adverse is True


@pytest.mark.parametrize(
    "text", ["casino masking detected", "unmasking detected"]
)
def test_a_word_merely_ENDING_in_the_negator_does_not_negate(text):
    """The negator must be the preceding WORD, not any text ending in it.
    Anchoring this loosely would silently suppress real detections — the same
    fail-safe direction as above, arrived at from the other side."""
    assert reading_for("pixelscan.net", "masking_detected", text).state == READ


def test_an_all_negated_absence_says_so_rather_than_looking_unlooked_at():
    """Both are ABSENT — the verdict is identical and must not read as "we
    could not look" — but a later reader can tell the guard DID work from one
    and not the other."""
    guarded = reading_for(
        "pixelscan.net", "masking_detected", "No\nmasking detected"
    )
    silent = reading_for("pixelscan.net", "masking_detected", "nothing here")

    assert guarded.state == ABSENT and silent.state == ABSENT
    assert "negated" in guarded.reason
    assert "negated" not in silent.reason


def test_the_bot_checker_is_NOT_exposed_to_the_separator_defect():
    """The one place I expected the same defect and MEASURED that it is not.

    deviceandbrowserinfo renders "You are NOT a bot", and its adverse pattern
    carries the same fixed-width `(?<!not )`. The obvious move was to convert it
    alongside pixelscan's four. Measured first, and the conversion would have
    been theatre: the adverse pattern is `(?:you are|you're) (?:a )?(?:bot)`,
    which CANNOT match "You are not a bot" whatever the whitespace — the
    intervening "not" breaks the pattern itself, so the lookbehind never fires
    and neither would a negated_by. Both guards are inert here.

    Pinned as a test rather than left as a comment because the danger is a
    later reader "fixing" this for symmetry and believing they closed a hole.
    The assertion is on the BARE pattern with no guard at all: if that ever
    starts matching a clean page, this checker really does need the guard and
    this test fails loudly.
    """
    import re

    bare = r"(?:you are|you're) (?:a )?(?:bot|robot)"
    for clean in ["You are not a bot", "You are not\na bot",
                  "You're not a robot", "You are\nnot a bot"]:
        assert re.search(bare, clean, re.IGNORECASE) is None, (
            f"{clean!r} now matches the UNGUARDED bot pattern — this checker "
            "has acquired the separator defect and needs negated_by"
        )

    # ...and the real verdict is still read, via the shipped catalogue.
    assert reading_for(
        "deviceandbrowserinfo.com", "bot_verdict_positive", "You are a bot"
    ).state == READ


# --- the negation trap, part three: A TOKEN THAT IS NOT A VERDICT (PS-121) --
#
# The two sections above are about a pattern that matches its own NEGATION.
# This one is worse and was found inside it: a pattern aimed at a token that
# carries NO VERDICT AT ALL, so it matched every page in both directions.
#
# `bot-detector.rebrowser.net` was read with a bare `\bdetected\b`. Measured
# against both captured pages, the word "detected" appears TWICE, in the same
# two places, on a clean page AND on a caught one — and both are GREEN rows
# ("No leak detected.", "No window.__pwInitScripts detected."). Every RED row
# is prose that never uses the word at all.
#
# So the old reader returned READ on both, with byte-identical `matched_text`.
# It could not report a real detection and could not report a clean one. The
# fixtures below are real `inner_text("body")` captures of both directions,
# and they are the reason this is asserted on the READER'S OUTPUT rather than
# on the pattern: every table above passes against the broken pattern too.

REBROWSER = "bot-detector.rebrowser.net"


def rebrowser_page(which: str) -> str:
    """A real capture of the rebrowser page. ``which`` is 'clean' or 'caught'."""
    with open(
        os.path.join(FIXTURES, f"rebrowser-{which}.txt"), encoding="utf-8"
    ) as fh:
        return fh.read()


def test_rebrowser_clean_page_does_not_read_as_a_detection():
    """The defect, stated in the direction that manufactured findings."""
    reading = reading_for(REBROWSER, "detected", rebrowser_page("clean"))
    assert reading.state == ABSENT, (
        "a page whose every row is green/white read as a DETECTION "
        f"(matched {reading.matched_text!r}) — an adverse verdict "
        "manufactured by the reader"
    )


def test_rebrowser_caught_page_is_read_as_a_detection():
    """The other direction, and the half whose absence let this survive: a
    reader that never fires looks exactly as healthy as a correct one."""
    reading = reading_for(REBROWSER, "detected", rebrowser_page("caught"))
    assert reading.state == READ
    assert reading.adverse is True
    # ...and it names EVERY test that caught us, not a bare True and not the
    # first of three. The capture is sorted, so this is the whole value.
    assert reading.value == (
        "exposeFunctionLeak,mainWorldExecution,navigatorWebdriver"
    ), reading.value


def test_rebrowser_records_every_red_row_not_just_the_first():
    """A browser that gets CAUGHT MORE must not read as unchanged.

    ``extract_text_item`` takes the first non-negated match and
    ``matrix_diff._verdict`` compares ``(state, value)`` only — so a
    first-match reading records the SAME value whether one row or three are
    red, and the comparator's "read on both sides and agreed" branch returns
    None. A detection count that triples in silence is exactly the regression
    Level 3 exists to catch.

    Asserted on the READER'S OUTPUT against row text, per the ticket: a test
    on the pattern string would pass against the first-match spelling too.
    """
    one_red = (
        "Test name\tTime since load\tNotes\n"
        "\U0001F534 mainWorldExecution\t2 ms\tYou've called …ByClassName().\n"
    )
    three_reds = one_red + (
        "\U0001F534 exposeFunctionLeak\t3 ms\tYou're using unpatched Playwright.\n"
        "\U0001F534 navigatorWebdriver\t4 ms\tnavigator.webdriver = true.\n"
    )

    worse = reading_for(REBROWSER, "detected", three_reds)
    fewer = reading_for(REBROWSER, "detected", one_red)

    assert fewer.value == "mainWorldExecution"
    assert worse.value == (
        "exposeFunctionLeak,mainWorldExecution,navigatorWebdriver"
    ), worse.value
    # The verdict the comparator reads — (state, value) — must MOVE.
    assert (fewer.state, fewer.value) != (worse.state, worse.value), (
        "one detection and three record an identical verdict, so a browser "
        "getting caught by two more tests reports no change at all"
    )
    # ...and the quote must back the value it sits beside, not one third of it.
    for name in ("mainWorldExecution", "exposeFunctionLeak", "navigatorWebdriver"):
        assert name in worse.matched_text, name


def test_rebrowser_reading_does_not_depend_on_row_ORDER():
    """The value must say WHICH tests fired, never in what order they rendered.

    An unsorted join would report a reshuffled table as a changed verdict —
    the same false-positive class this ticket is about, wearing new clothes.
    """
    rows = [
        "\U0001F534 navigatorWebdriver\t4 ms\tnavigator.webdriver = true.\n",
        "\U0001F534 mainWorldExecution\t2 ms\tYou've called …ByClassName().\n",
        "\U0001F534 exposeFunctionLeak\t3 ms\tunpatched Playwright.\n",
    ]
    header = "Test name\tTime since load\tNotes\n"
    forward = reading_for(REBROWSER, "detected", header + "".join(rows))
    backward = reading_for(REBROWSER, "detected", header + "".join(reversed(rows)))

    assert forward.value == backward.value, (
        "the same three detections in a different row order read as two "
        "different verdicts"
    )


@pytest.mark.parametrize(
    "probe",
    [
        # A 🔴 in PROSE. The first spelling of this fix used `\s*`, which
        # matches across the space here and reads 'means' as a caught test.
        "Legend: \U0001F534 means the test caught you.",
        # `\s*` also crosses NEWLINES, so a marker in a legend LIST fired too.
        "Icons:\n\U0001F534\nfailed\n",
        # A marker with no row structure behind it at all.
        "Our \U0001F534 badge indicates a detection.",
    ],
)
def test_a_red_marker_outside_a_verdict_ROW_is_not_a_detection(probe):
    """The adverse arm must not manufacture a finding from a stray marker.

    This item reads a MARKER rather than prose, so the thing that makes it a
    verdict is the ROW it sits in — `<marker> <name>\\t<time>\\t<note>`. A 🔴
    anywhere else on the page is decoration, and reading it would produce an
    adverse FINGERPRINT row with a real match and a real quote behind it: the
    exact failure the module docstring says this subsystem exists to prevent.

    Latent rather than live — today's captures carry no 🔴 outside the table —
    but "the current capture happens not to contain it" is the same reasoning
    that left the bare `\\bdetected\\b` in place for as long as it survived.
    """
    reading = reading_for(REBROWSER, "detected", probe)
    assert reading.state == ABSENT, (
        f"a red marker in prose read as a detection (matched "
        f"{reading.matched_text!r}, value {reading.value!r})"
    )


def test_the_two_captured_rebrowser_pages_really_are_different_verdicts():
    """Guard the guard. If both fixtures carried the same verdict, the pair of
    tests above could both pass while proving nothing."""
    clean, caught = rebrowser_page("clean"), rebrowser_page("caught")
    assert "\U0001F534" not in clean, "the 'clean' capture contains a red row"
    assert "\U0001F534" in caught, "the 'caught' capture has no red row"


def test_the_old_bare_pattern_read_BOTH_rebrowser_pages_as_adverse():
    """Guard the guard, part two — and the whole argument for this ticket.

    The old pattern is run against both real captures here. It matches both,
    and its capture is IDENTICAL in both directions, so an adverse verdict
    from it was unfalsifiable: no reader of the record could tell a real
    detection from a clean page.
    """
    import re

    hits = [
        re.search(r"\bdetected\b", rebrowser_page(w), re.IGNORECASE)
        for w in ("clean", "caught")
    ]
    assert all(h is not None for h in hits), (
        "the old pattern no longer matches both pages, so this guard proves "
        "nothing and the tests above are pinning a defect that moved"
    )
    assert hits[0].group(0) == hits[1].group(0), (
        "the old pattern's capture now differs between a clean and a caught "
        "page; it was byte-identical when this was measured"
    )
    # Both occurrences it matched are on GREEN rows — the token is not a
    # verdict, which is why no lookbehind could have fixed it.
    assert "No leak detected." in rebrowser_page("clean")
    assert "No window.__pwInitScripts detected." in rebrowser_page("clean")


def test_a_rebrowser_page_that_never_rendered_is_not_read_as_clean():
    """The failure direction this fix INTRODUCES, closed in the same change.

    Reading a marker makes ABSENCE the clean verdict, so an unsettled page —
    one whose JavaScript never populated the table — would read as a perfect
    browser. The static shell survives the tier's settle guard, because it
    carries the title and the table HEADER and so is not blank.

    ``verdicts_rendered`` is READ exactly when the table exists, so an adverse
    ABSENT beside THIS absent is "the page never rendered", not "clean".
    """
    shell = (
        "\U0001F575\uFE0F rebrowser-bot-detector\n"
        "Modern tests to detect automated browser behavior. See github repo "
        "for more details. How to properly run the tests?\n"
        "Test name\tTime since load\tNotes\n"
        "JSON\nSponsored by rebrowser.net\n"
    )
    assert shell.strip(), "this shell must survive the settle guard to be a trap"
    assert reading_for(REBROWSER, "detected", shell).state == ABSENT
    assert reading_for(REBROWSER, "verdicts_rendered", shell).state == ABSENT, (
        "an unrendered page claims its verdict table rendered; a shell would "
        "then be indistinguishable from a clean browser"
    )
    # ...whereas both REAL pages did render, whatever their verdict.
    for which in ("clean", "caught"):
        assert reading_for(
            REBROWSER, "verdicts_rendered", rebrowser_page(which)
        ).state == READ, which


def test_the_liveness_arm_is_row_anchored_too():
    """The quieter half of the same defect, and the worse one to get wrong.

    ``verdicts_rendered`` was first written with ``\\s*`` as well, so a legend
    rendered as a LIST satisfied it: ``'Legend:\\n🟢\\npassed\\n\\t'`` read as a
    rendered verdict table. That direction manufactures a CLEAN verdict rather
    than an adverse one — a shell page claiming its table rendered, sitting
    beside an adverse ABSENT, is exactly the "perfect browser" this arm exists
    to make impossible. It may not be the looser of the two patterns.
    """
    legend = "Legend:\n\U0001F7E2\npassed\n\tmeaning the test did not fire\n"
    assert reading_for(REBROWSER, "verdicts_rendered", legend).state == ABSENT, (
        "a colour legend read as a rendered verdict table; beside an adverse "
        "ABSENT that is indistinguishable from a clean browser"
    )
    # ...and the real pages still prove the instrument was working.
    for which in ("clean", "caught"):
        assert reading_for(
            REBROWSER, "verdicts_rendered", rebrowser_page(which)
        ).state == READ, which


@pytest.mark.parametrize(
    "note",
    [
        "No leak detected.",
        "No window.__pwInitScripts detected.",
        # The wordings the ticket ASSUMED this checker renders. It does not —
        # but a reader keyed on the word would misread them too, so they are
        # pinned as the class rather than as observed strings.
        "not detected",
        "no bots detected",
    ],
)
def test_no_clean_rebrowser_wording_reads_as_a_detection(note):
    """The class, not just the two observed strings: no CLEAN note may fire
    this item, whatever the checker's wording does next."""
    green = f"\U0001F7E2 someTest\t2 ms\t{note}\n"
    assert reading_for(REBROWSER, "detected", green).state == ABSENT, note


# --- the false ABSENT -------------------------------------------------------


def test_sannysoft_webdriver_passed_is_read_not_absent():
    reading = reading_for("bot.sannysoft.com", "webdriver_missing_passed")
    assert reading.state == READ, (
        "the page says 'missing (passed)' — recording ABSENT here is a reader "
        "that missed a verdict, not a page that lacked one"
    )
    assert "missing (passed)" in reading.matched_text


def test_sannysoft_webdriver_present_does_not_match_the_clean_page():
    assert reading_for("bot.sannysoft.com", "webdriver_present").state == ABSENT


def test_sannysoft_phantom_probe_names_are_not_detections():
    """The page names every probe PHANTOM_* whatever the outcome, so a loose
    pattern reads a clean page as a PhantomJS detection."""
    assert "PHANTOM_UA" in page("bot.sannysoft.com")
    assert reading_for("bot.sannysoft.com", "phantom_js").state == ABSENT


def test_phantom_probe_label_is_not_a_detection():
    """The defect the FIRST LIVE RUN caught, which the fixture could not.

    The live page carries a probe label spelled ``phantomJS`` — no space. The
    original pattern used ``\\s*``, which matches zero spaces, so the run
    recorded a clean browser as a PhantomJS DETECTION: an adverse verdict
    manufactured entirely by the reader, with a real match to back it up.

    The fixture does not contain that label, so this asserts against the
    string directly rather than pretending the fixture proves it.
    """
    checker = checker_by_id("bot.sannysoft.com")
    item = next(i for i in checker.items if i.id == "phantom_js")
    assert extract_text_item(checker, item, "phantomJS").state == ABSENT
    # ...and the real prose form still reads.
    assert extract_text_item(
        checker, item, "Phantom JS detected"
    ).state == READ


def test_creepjs_gpu_capture_is_not_truncated_at_an_inner_paren():
    """Also caught by the first live run, not by the fixture.

    ``ANGLE (Intel, Intel(R) HD Graphics 400 ...)`` contains a ``)`` INSIDE
    ``Intel(R)``, so a ``[^)]+`` capture stopped there and recorded the
    truncated ``Intel, Intel(R``. A truncated renderer silently defeats the
    machine comparison this row exists for.
    """
    checker = checker_by_id("creepjs")
    item = next(i for i in checker.items if i.id == "gpu_renderer")
    live = "gpu:\nANGLE (Intel, Intel(R) HD Graphics 400 Direct3D11 vs_5_0)\n"
    reading = extract_text_item(checker, item, live)
    assert reading.state == READ
    assert reading.value.endswith(")")
    assert "HD Graphics 400" in reading.value


# --- values, not booleans ---------------------------------------------------


def test_creepjs_ratings_are_captured_as_values():
    """A rating must be a VALUE. As a boolean, 0% -> 40% reads as "still
    matching", i.e. as no change at all."""
    assert reading_for("creepjs", "headless_rating").value == "0"
    assert reading_for("creepjs", "like_headless_rating").value == "6"
    assert reading_for("creepjs", "stealth_rating").value == "0"


def test_creepjs_records_the_claimed_gpu_string_as_a_product_row():
    """WAS ``sort == HOST``, asserting the renderer was the container's fault.

    That exemption is WITHDRAWN (owner, 2026-08-22, PS-10): there will be no
    dev-VM and no GPU machine in the loop, and the engine is expected to
    present a plausible GPU wherever it runs. So this row is FINGERPRINT — a
    red on it is a masking finding filed against ``undetectable-masking``, not
    an environment note written off as "the runner has no GPU".
    """
    reading = reading_for("creepjs", "gpu_renderer")
    assert reading.state == READ
    assert reading.sort == FINGERPRINT
    assert reading.vector == GPU_CLAIMED
    assert "GeForce GTX 980" in reading.value


# --- the two GPU vectors ----------------------------------------------------
#
# The owner ruled the bar wants BOTH a plausible renderer string AND pixels
# that match it, and that verification must report WHICH of the two a red came
# from. These tests hold that seam open: they are what stops a later edit
# quietly collapsing the pair back into one "GPU red" nobody can act on.


def test_the_gpu_is_read_as_two_separate_vectors_never_one():
    """The ticket's hard requirement, and the reason the rows are split.

    A merged "GPU red" cannot be acted on: the CLAIMED strings are fixed by
    changing what the spoofer declares, and the RENDERED hashes are fixed — if
    at all — at the rendering layer. Different fixes, different tickets.
    """
    claimed, rendered = [], []
    for checker in BROWSER_CHECKERS:
        for item in checker.items:
            if item.vector == GPU_CLAIMED:
                claimed.append(f"{checker.id}.{item.id}")
            elif item.vector == GPU_RENDERED:
                rendered.append(f"{checker.id}.{item.id}")

    assert claimed, "no row records what the renderer CLAIMS to be"
    assert rendered, (
        "no row records what the checker's OWN RENDERING produced — without "
        "it 'the string is right but the render gives us away' is unreadable"
    )
    # Both vectors on BOTH prose checkers: either one alone would leave a
    # checker able to fingerprint pixels we never read.
    for checker_id in ("pixelscan.net", "creepjs"):
        vectors = {i.vector for i in checker_by_id(checker_id).items if i.vector}
        assert vectors == {GPU_CLAIMED, GPU_RENDERED}, (
            f"{checker_id} reads {vectors or 'neither vector'}; it publishes "
            "both, so reading one is a half-answer that looks complete"
        )


def test_both_gpu_vectors_are_read_from_the_measured_pages():
    """Grounded in the real captures, not in hand-written strings.

    A pattern that matches nothing records as ABSENT — i.e. as *the checker
    stopped reporting it* rather than *the reader broke*. PS-59 was bitten by
    exactly that shape twice, so every row here is asserted against the page
    as it actually rendered.
    """
    expected = {
        ("pixelscan.net", "webgl_vendor"): "Google Inc. (NVIDIA)",
        ("pixelscan.net", "webgl_renderer"): "GeForce GTX 980",
        ("pixelscan.net", "webgl_hash"): "d2931cb9b32cff4a5324218b21ec0f35",
        ("pixelscan.net", "canvas_hash"): "ebb68942c2501078e8309706d0f270e9",
        ("creepjs", "gpu_vendor"): "Google Inc. (NVIDIA)",
        ("creepjs", "gpu_renderer"): "GeForce GTX 980",
        ("creepjs", "webgl_image_hash"): "439417c4",
        ("creepjs", "webgl_pixel_hash"): "a8ee71dc",
        ("creepjs", "canvas_data_hash"): "8d1ce292",
    }
    for (checker_id, item_id), substring in expected.items():
        reading = reading_for(checker_id, item_id)
        assert reading.state == READ, (
            f"{checker_id}.{item_id} did not match its own measured page — "
            "that records as ABSENT, which reads as the checker going quiet"
        )
        assert substring in reading.value


def test_a_gpu_reading_carries_its_vector_into_the_record():
    """The split has to survive into the DATA, not just the catalogue.

    ``vector`` rides on the reading for the same reason ``sort`` does: a
    record must stay interpretable after the catalogue moves, and the report
    that names which vector a red came from is written from the record.
    """
    record = _record(
        readings_from_texts({cid: {"text": page(cid)} for cid in PAGES})
    )
    rows = {
        (r["checker"], r["item"]): r
        for r in record["readings"]
        if r.get("vector")
    }
    assert rows, "no reading carried a vector into the record"
    assert rows[("creepjs", "gpu_renderer")]["vector"] == GPU_CLAIMED
    assert rows[("creepjs", "webgl_pixel_hash")]["vector"] == GPU_RENDERED
    # And a row that is not about the GPU must not acquire one.
    non_gpu = [
        r for r in record["readings"]
        if r["item"] in ("timezone_from_js", "headless_rating")
    ]
    assert non_gpu, "expected some non-GPU rows in the record"
    assert all("vector" not in r for r in non_gpu), (
        "a non-GPU row carried a GPU vector; the key means 'which GPU "
        "question this answers' and is meaningless elsewhere"
    )


def test_no_gpu_row_is_tagged_host_which_would_reinstate_the_exemption():
    """A regression guard on an OWNER DECISION, not on a style preference.

    HOST means "driven by the machine the engine ran on", and for a GPU row
    that is precisely the "the runner has no GPU, so it does not count"
    reading the owner withdrew. Retagging one back to HOST would silently
    restore the exemption, so it fails here loudly instead.
    """
    for checker in CHECKERS:
        for item in checker.items:
            if item.vector:
                assert item.sort != HOST, (
                    f"{checker.id}.{item.id} is a GPU row tagged HOST; the "
                    "GPU-less exemption was withdrawn (PS-10) and a red here "
                    "is a product finding for undetectable-masking"
                )


def test_creepjs_gpu_vendor_does_not_capture_webgpu_unsupported():
    """A near-miss found by over-matching my own pattern against the capture.

    CreepJS also prints ``webgpu: unsupported`` (creepjs.txt:207) and a bare
    ``gpu:`` matches INSIDE it. On the captured page the real block renders
    first, so first-match-wins hides it — by luck of ordering, not by
    construction. On a page where the gpu block does not populate (CreepJS's
    blocks are routinely partial) the unanchored form records ``unsupported``
    AS THE GPU VENDOR: a missing verdict recorded as a real value, which is
    the quiet failure direction rather than the loud ABSENT it should be.
    """
    checker = checker_by_id("creepjs")
    item = next(i for i in checker.items if i.id == "gpu_vendor")
    gpu_block_missing = "vendor: blocked\nwebgpu: unsupported\nuserAgentData:\n"
    assert extract_text_item(checker, item, gpu_block_missing).state == ABSENT
    # ...and the real block still reads.
    live = "gpu:\nconfidence: high\nGoogle Inc. (NVIDIA)\n"
    assert extract_text_item(checker, item, live).value == "Google Inc. (NVIDIA)"


def test_creepjs_canvas_hash_cannot_capture_the_audio_blocks_hash():
    """The same class of near-miss, one level quieter.

    A bare ``data:`` matches TWICE on the real page: Canvas's ``data:
    8d1ce292`` (creepjs.txt:120) and AUDIO's ``data:2dcdf6c2``
    (creepjs.txt:157). It reads correctly today only because Canvas happens to
    render first. Reordered — or with the Canvas block absent while Audio
    populates — the unanchored form records THE AUDIO HASH as the canvas
    rendering: a READ reading with a corrupted value, on a row whose entire
    purpose is being compared across runs.
    """
    checker = checker_by_id("creepjs")
    item = next(i for i in checker.items if i.id == "canvas_data_hash")

    audio_only = "Audio17546399\nunique: 4736\ndata:2dcdf6c2\ncopy:2dcdf6c2\n"
    assert extract_text_item(checker, item, audio_only).state == ABSENT

    # Audio BEFORE canvas: the anchored row must still pick the canvas hash.
    reordered = (
        "Audio17546399\ndata:2dcdf6c2\ncopy:2dcdf6c2\n\n"
        "Canvas 2d711bcdbe\ndata: 8d1ce292\nrendering:\n"
    )
    assert extract_text_item(checker, item, reordered).value == "8d1ce292"


def test_pixelscan_timezone_agrees_with_the_polish_exit():
    reading = reading_for("pixelscan.net", "timezone_from_js")
    assert reading.state == READ
    assert reading.sort == EXIT
    assert reading.value == "Europe/Warsaw"


def test_iphey_reads_the_trustworthy_verdict_across_its_newline():
    reading = reading_for("iphey.com", "trustworthy")
    assert reading.state == READ
    assert reading.adverse is False
    assert reading_for("iphey.com", "not_trustworthy").state == ABSENT


def test_a_capturing_item_with_no_group_is_unobtainable_not_true():
    """Our own defect must not publish a boolean where a number is expected."""
    checker = checker_by_id("creepjs")
    broken = TextItem("broken", r"\d+% headless", FINGERPRINT, capture=True)
    reading = extract_text_item(checker, broken, page("creepjs"))
    assert reading.state == UNOBTAINABLE
    assert "no group" in reading.reason


def test_a_malformed_pattern_is_unobtainable_not_absent():
    """We did not look, so we cannot say the page lacks the verdict."""
    checker = checker_by_id("creepjs")
    bad = TextItem("bad", r"( unclosed", FINGERPRINT)
    reading = extract_text_item(checker, bad, "anything")
    assert reading.state == UNOBTAINABLE
    assert reading.state != ABSENT


# --- a reading that did not happen ------------------------------------------


def test_an_unreachable_checker_keeps_its_full_width_as_unobtainable():
    """A checker that refused the connection must not shrink the matrix."""
    checker = checker_by_id("deviceandbrowserinfo.com")
    readings = readings_from_texts(
        {"deviceandbrowserinfo.com": {"error": "NS_ERROR_CONNECTION_REFUSED"}},
        checkers=(checker,),
    )
    assert len(readings) == len(checker.items)
    assert all(r.state == UNOBTAINABLE for r in readings)
    assert all("CONNECTION_REFUSED" in r.reason for r in readings)
    # ...and the reason carries the standing note about this checker.
    assert any("mobile exit" in r.reason for r in readings)


def test_a_checker_absent_from_the_run_is_unobtainable_not_skipped():
    checker = checker_by_id("iphey.com")
    readings = readings_from_texts({}, checkers=(checker,))
    assert len(readings) == len(checker.items)
    assert all(r.state == UNOBTAINABLE for r in readings)


def test_an_empty_page_is_unobtainable_not_a_clean_verdict():
    """A page read before it settled says nothing. Recording its adverse items
    as ABSENT would read exactly like a clean checker."""
    checker = checker_by_id("pixelscan.net")
    readings = readings_from_texts(
        {"pixelscan.net": {"text": "   \n  "}}, checkers=(checker,)
    )
    assert all(r.state == UNOBTAINABLE for r in readings)
    assert all("settle" in r.reason for r in readings)


def test_every_browser_checker_reads_or_is_recorded_unobtainable():
    """Whole-tier completeness against the captured run: every catalogued item
    appears exactly once, whatever happened to its checker."""
    pages = {cid: {"text": page(cid)} for cid in PAGES}
    pages["deviceandbrowserinfo.com"] = {"error": "NS_ERROR_CONNECTION_REFUSED"}
    pages["bot-detector.rebrowser.net"] = {"error": "NS_ERROR_CONNECTION_REFUSED"}
    readings = readings_from_texts(pages)
    expected = sum(len(c.items) for c in BROWSER_CHECKERS)
    assert len(readings) == expected
    assert {(r.checker, r.item) for r in readings} == {
        (c.id, i.id) for c in BROWSER_CHECKERS for i in c.items
    }


# --- the JSON tier ----------------------------------------------------------


def test_json_path_that_does_not_exist_is_absent_not_none():
    checker = checker_by_id("tls.peet.ws")
    item = JsonItem("gone", ("tls", "nope"), FINGERPRINT)
    reading = extract_json_item(checker, item, {"tls": {"ja4": "x"}})
    assert reading.state == ABSENT
    assert reading.value is None
    assert "tls.nope" in reading.reason


def test_json_null_is_a_value_not_an_absence():
    """A field the checker published AS null is a reading. Folding it into
    ABSENT would erase the difference between "it said null" and "it stopped
    publishing this"."""
    checker = checker_by_id("ipleak.net")
    item = JsonItem("postal", ("postal_code",), EXIT)
    reading = extract_json_item(checker, item, {"postal_code": None})
    assert reading.state == READ
    assert reading.value is None


def test_a_json_checker_that_fails_yields_unobtainable_for_every_item():
    checker = checker_by_id("tools.scrapfly.io")

    def boom(url, **kw):
        raise TimeoutError("timed out")

    readings = read_json_tier(
        "socks5h://127.0.0.1:1", checkers=(checker,), fetch_json=boom
    )
    assert len(readings) == len(checker.items)
    assert all(r.state == UNOBTAINABLE for r in readings)
    assert all("TimeoutError" in r.reason for r in readings)


def test_a_json_checker_that_answers_an_error_page_is_not_read():
    """An HTML error page is not a verdict. This is the shape that most looks
    like success: HTTP 200, bytes on the wire, nothing to parse."""
    from src.services.verify.socks_fetch import FetchFailed

    checker = checker_by_id("tls.peet.ws")

    def html(url, **kw):
        raise FetchFailed("the checker answered HTTP 200 with a body that is "
                          "not JSON")

    readings = read_json_tier(
        "socks5h://127.0.0.1:1", checkers=(checker,), fetch_json=html
    )
    assert all(r.state == UNOBTAINABLE for r in readings)


def test_json_readings_carry_their_sort():
    checker = checker_by_id("ipleak.net")
    payload = {"country_code": "PL", "ip": "1.2.3.4", "as_number": 9141,
               "city_name": "Warsaw", "isp_name": "Play",
               "time_zone": "Europe/Warsaw"}
    readings = read_json_tier(
        "socks5h://127.0.0.1:1", checkers=(checker,),
        fetch_json=lambda url, **kw: payload,
    )
    assert {r.sort for r in readings} == {EXIT}


# --- the unreadable tier is a RESULT ----------------------------------------


def test_hostile_checkers_are_recorded_as_unobtainable_with_a_reason():
    readings = read_unreadable_tier()
    assert readings, "the hostile checkers must appear in the record"
    assert all(r.state == UNOBTAINABLE for r in readings)
    assert all(r.reason for r in readings)
    assert {r.checker for r in readings} >= {"whoer.net", "amiunique.org"}


# --- the catalogue itself ---------------------------------------------------


def test_every_pattern_compiles():
    import re

    for checker in CHECKERS:
        for item in checker.items:
            if isinstance(item, TextItem):
                re.compile(item.pattern)


def test_every_item_declares_a_known_sort():
    for checker in CHECKERS:
        for item in checker.items:
            assert item.sort in ALL_SORTS, (
                f"{checker.id}.{item.id} has sort {item.sort!r}; without a "
                "sort a rotating exit makes every run look changed"
            )


def test_the_python_fetchers_tls_shape_is_not_tagged_as_personas_fingerprint():
    """The FIRST LIVE RUN recorded ``user_agent: curl/8.14.1`` and
    ``http_version: HTTP/1.1`` on rows tagged FINGERPRINT.

    Those endpoints are fetched by this repository's own Python client, so
    their TLS shape describes THE INSTRUMENT. Left tagged FINGERPRINT, a
    future Python or OpenSSL upgrade would read as PERSONA'S FINGERPRINT
    MOVING — the exact false alarm that makes a real one unbelievable.
    """
    for checker in JSON_CHECKERS:
        for item in checker.items:
            assert item.sort != FINGERPRINT, (
                f"{checker.id}.{item.id} claims to read persona's "
                "fingerprint, but this tier is fetched by the Python client"
            )


def test_the_harness_sort_is_used_and_is_not_a_synonym_for_fingerprint():
    """Guard the guard: HARNESS must actually be carried by real rows, or the
    test above passes because the tier was silently emptied."""
    harness = [
        (c.id, i.id)
        for c in JSON_CHECKERS
        for i in c.items
        if i.sort == HARNESS
    ]
    assert harness, "no row carries HARNESS; the distinction is not being made"
    assert ("tls.peet.ws", "ja4") in harness


def test_exit_driven_geography_stays_exit_driven():
    """ipleak's items are geography — a property of the EXIT, identical
    whichever client asked — so they must NOT be relabelled harness."""
    ipleak = checker_by_id("ipleak.net")
    assert {i.sort for i in ipleak.items} == {EXIT}


def test_ja3_is_not_read_anywhere():
    """JA3 moves with TLS extension permutation and manufactures false drift.
    ja4 / ja3n are read instead."""
    for checker in JSON_CHECKERS:
        for item in checker.items:
            leaf = item.path[-1]
            assert leaf not in ("ja3_hash", "ja3_text"), (
                f"{checker.id}.{item.id} reads raw JA3"
            )


def test_item_ids_are_unique_within_a_checker():
    for checker in CHECKERS:
        ids = [i.id for i in checker.items]
        assert len(ids) == len(set(ids)), checker.id


def test_unreadable_checkers_carry_a_reason_and_no_items():
    for checker in CHECKERS:
        if checker.tier == TIER_UNREADABLE:
            assert checker.unreadable_reason
            assert checker.items == ()


# --- the record -------------------------------------------------------------


def _record(readings):
    from src.services.verify.exit_guard import Exit

    return build_record(
        readings,
        exit_=Exit(ip="1.2.3.4", country="PL", city="Warsaw",
                   org="AS9141 P4", timezone="Europe/Warsaw"),
        engine="invisible_playwright/firefox-20",
        observed_at="2026-08-21T23:00:00Z",
        environment="linux-x86_64 (agent sandbox)",
    )


def test_the_record_carries_the_exit_beside_the_readings():
    """Without the address in the record, "a fingerprint reading moved when
    only the address moved" cannot be asked at all."""
    record = _record(readings_from_texts({cid: {"text": page(cid)} for cid in PAGES}))
    assert record["exit"]["country"] == "PL"
    assert record["exit"]["ip"] == "1.2.3.4"


def test_the_record_counts_unobtainable_separately_from_read():
    readings = readings_from_texts(
        {"pixelscan.net": {"error": "boom"}, "creepjs": {"text": page("creepjs")}},
        checkers=(checker_by_id("pixelscan.net"), checker_by_id("creepjs")),
    )
    counts = _record(readings)["counts"]
    assert counts["unobtainable"] == len(checker_by_id("pixelscan.net").items)
    assert counts["read"] > 0
    assert counts["total"] == len(readings)


def test_an_unobtainable_reading_carries_no_value_key():
    """A value key on an unread row is exactly how "we did not look" starts
    reading as "it said nothing"."""
    readings = readings_from_texts(
        {"iphey.com": {"error": "boom"}}, checkers=(checker_by_id("iphey.com"),)
    )
    for row in _record(readings)["readings"]:
        assert "value" not in row
        assert row["reason"]


def test_the_record_round_trips_as_json_with_sorted_keys():
    record = _record(readings_from_texts({"creepjs": {"text": page("creepjs")}},
                                         checkers=(checker_by_id("creepjs"),)))
    text = dumps(record)
    assert json.loads(text) == record
    assert text.endswith("\n")


def test_write_is_atomic_and_leaves_no_temp_file(tmp_path):
    record = _record([])
    target = tmp_path / "sub" / "reading.json"
    write(record, str(target))
    assert json.loads(target.read_text()) == record
    assert [p.name for p in (tmp_path / "sub").iterdir()] == ["reading.json"]


# --- the browser tier proves its OWN exit ------------------------------------
#
# The Python fetcher's proof (exit_guard.prove_exit) is made on a DIFFERENT
# SOCKET IN A DIFFERENT PROCESS and does not transfer to the engine. Without
# the engine's own proof, an engine whose proxy silently failed would render
# every page, parse every verdict and land every row as READ — a
# complete-looking reading of the OPERATOR'S REAL ADDRESS taken against every
# checker in the matrix. These pin that it cannot.


class _FakePage:
    """The two methods the tier uses, and a record of what was asked."""

    def __init__(self, texts, log, fail_on=()):
        self._texts = texts
        self._log = log
        self._fail_on = fail_on
        self._url = None

    def goto(self, url, **kwargs):
        self._log.append(url)
        self._url = url
        for fragment in self._fail_on:
            if fragment in url:
                raise RuntimeError(f"NS_ERROR_CONNECTION_REFUSED for {url}")

    def inner_text(self, _selector):
        for fragment, text in self._texts.items():
            if fragment in (self._url or ""):
                return text
        return ""

    def close(self):
        pass


class _FakeLive:
    """A BrowserContext-shaped double: what the Firefox arm actually drives.

    ``add_init_script`` and ``pages`` are here because persona's masking layer
    is installed through them (PS-103), and because the REAL object the arm
    works on is a context. A double carrying only ``new_page`` would model an
    engine this tier no longer has: `InvisiblePlaywright.__enter__` hands back a
    playwright ``Browser``, which the arm converts to ONE explicit context via
    ``masking_layer.context_for`` precisely because a Browser can carry no init
    script and its ``new_page()`` opens a throwaway context per call.

    The registered scripts are KEPT rather than discarded, so a test can assert
    the layer really reached the context instead of trusting a report.
    """

    def __init__(self, texts, fail_on=()):
        self.texts = texts
        self.visited = []
        self.scripts = []
        self.pages = []
        self._fail_on = fail_on

    def add_init_script(self, js):
        self.scripts.append(js)

    def new_page(self):
        return _FakePage(self.texts, self.visited, self._fail_on)


def _exit_json(country="PL", ip="91.150.1.1"):
    return json.dumps({
        "ip": ip, "country": country, "city": "Warsaw",
        "org": "AS9141 P4 Sp. z o.o.", "timezone": "Europe/Warsaw",
    }, indent=2)


def _ipwho_json(country_code="PL", ip="95.49.113.111"):
    """The SECOND provider's dialect, copied from a real body captured through
    the mobile exit on 2026-08-23 (PS-128).

    It differs from ipinfo's shape in three ways that all matter to the rows
    above: ``country`` is the full NAME with the code in ``country_code``,
    ``org``/``isp`` are nested under ``connection``, and ``timezone`` is an
    OBJECT rather than a string. A double that flattened these would prove the
    patterns work against a shape the provider never sends.
    """
    return json.dumps({
        "ip": ip,
        "success": True,
        "country": "Poland",
        "country_code": country_code,
        "region": "Masovian Voivodeship",
        "city": "Warsaw",
        "continent_code": "EU",
        "connection": {
            "asn": 5617,
            "org": "Orange Polska Spolka Akcyjna",
            "isp": "Orange Polska Spolka Akcyjna",
        },
        "timezone": {"id": "Europe/Warsaw", "abbr": "CEST", "utc": "+02:00"},
    }, indent=2)


def test_the_engine_observes_its_own_exit_and_reads_it_as_rows():
    from src.services.verify.browser_tier import _observe_engine_exit

    live = _FakeLive({"ipinfo.io": _exit_json()})
    text, country = _observe_engine_exit(live)
    assert country == "PL"
    readings = readings_from_texts(
        {"engine-exit": {"text": text}}, checkers=(checker_by_id("engine-exit"),)
    )
    by_id = {r.item: r for r in readings}
    assert by_id["observed_ip"].value == "91.150.1.1"
    assert by_id["country"].value == "PL"
    assert by_id["timezone"].value == "Europe/Warsaw"
    # It is EXIT-sorted: it is supposed to move between runs.
    assert all(r.sort == EXIT for r in readings)


def test_an_engine_leaving_through_the_wrong_country_refuses_the_tier():
    """The scenario the reviewer named: the Python fetcher's exit was proven,
    the engine's proxy silently failed, and every page still renders."""
    from src.services.verify.browser_tier import (
        ExitNotProvenInEngine,
        _observe_engine_exit,
    )

    live = _FakeLive({"ipinfo.io": _exit_json(country="DE", ip="1.2.3.4")})
    with pytest.raises(ExitNotProvenInEngine) as exc:
        _observe_engine_exit(live)
    assert "DE" in str(exc.value)
    assert "PL" in str(exc.value)


def test_an_engine_exit_with_no_country_refuses_rather_than_assuming():
    from src.services.verify.browser_tier import (
        ExitNotProvenInEngine,
        _observe_engine_exit,
    )

    live = _FakeLive({"ipinfo.io": json.dumps({"ip": "91.150.1.1"})})
    with pytest.raises(ExitNotProvenInEngine) as exc:
        _observe_engine_exit(live)
    assert "no country" in str(exc.value).lower()


def test_an_unreachable_exit_observation_refuses_rather_than_reading_on():
    """EVERY provider unreachable — the engine never saw its own exit at all.

    ``fail_on`` names both providers rather than only ipinfo: since PS-128 the
    observation walks a list, so "unreachable" means the whole list failed. A
    double that downed only the first would be modelling the single-oracle
    world this no longer is, and would assert nothing about the real refusal.
    """
    from src.services.verify.browser_tier import (
        ExitNotProvenInEngine,
        _observe_engine_exit,
    )

    live = _FakeLive({}, fail_on=("ipinfo.io", "ipwho.is"))
    with pytest.raises(ExitNotProvenInEngine) as exc:
        _observe_engine_exit(live)
    assert "could not observe its own exit" in str(exc.value)
    # Both providers are named, so an operator can tell "the whole list is
    # down" from "one of them is".
    assert "ipinfo.io" in str(exc.value)
    assert "ipwho.is" in str(exc.value)


# --- PS-128: one dead oracle must not refuse a provably good exit -----------


def test_a_rate_limited_first_provider_falls_through_to_the_second():
    """THE REGRESSION THIS EXISTS FOR, and it is a behavioural test.

    ipinfo.io answered `HTTP 429 Rate limit hit` through the mobile exit — the
    limit attaches to the exit's SHARED address, so it is not ours to clear
    and not retryable. Because this row is the browser tier's PRECONDITION,
    all 37 rows in all 4 configurations were marked unobtainable and the run
    refused itself over an exit that was provably Polish.

    The assertion is that the observation SUCCEEDS and returns PL — not merely
    that a second URL was visited, which would pass even if the country never
    reached the caller.
    """
    from src.services.verify.browser_tier import _observe_engine_exit

    rate_limited = json.dumps(
        {"status": 429, "error": {"title": "Rate limit hit"}}
    )
    live = _FakeLive({
        "ipinfo.io": rate_limited,
        "ipwho.is": _ipwho_json(),
    })

    text, country = _observe_engine_exit(live)

    assert country == "PL"
    # It really did fall through rather than reading the 429 as an answer.
    assert any("ipinfo.io" in url for url in live.visited)
    assert any("ipwho.is" in url for url in live.visited)
    assert "95.49.113.111" in text


def test_the_second_providers_dialect_is_read_as_PL_not_as_Poland():
    """ipwho.is says `"country": "Poland"` and puts the CODE in
    `country_code`. Read with ipinfo's key layout the row captures "Poland",
    which is not "PL" — so the guard would refuse a healthy Polish exit while
    reporting the wrong country. That is worse than the 429 the fallback
    exists to survive, because the message would be actively false.
    """
    from src.services.verify.checkers import ENGINE_EXIT_CHECKER
    from src.services.verify.matrix import extract_text_item

    by_id = {
        item.id: extract_text_item(ENGINE_EXIT_CHECKER, item, _ipwho_json())
        for item in ENGINE_EXIT_CHECKER.items
    }
    assert by_id["country"].value == "PL"
    # The nested dialect is reached for the other captured rows too, rather
    # than landing ABSENT and quietly shrinking the record.
    assert by_id["timezone"].value == "Europe/Warsaw"
    assert by_id["org"].value == "Orange Polska Spolka Akcyjna"
    assert by_id["observed_ip"].value == "95.49.113.111"


def test_a_wrong_country_is_NOT_retried_against_a_friendlier_provider():
    """The fallback is redundancy for REACHABILITY, never a second opinion on
    geography. A provider that answers is authoritative: if the first says US,
    the run ends there. Asking the next one until a Polish answer turns up is
    how a fallback becomes a way to launder a bad exit.
    """
    from src.services.verify.browser_tier import (
        ExitNotProvenInEngine,
        _observe_engine_exit,
    )

    live = _FakeLive({
        "ipinfo.io": _exit_json(country="US", ip="8.8.8.8"),
        # Polish, and must never be consulted.
        "ipwho.is": _ipwho_json(),
    })
    with pytest.raises(ExitNotProvenInEngine) as exc:
        _observe_engine_exit(live)

    assert "US" in str(exc.value)
    assert not any("ipwho.is" in url for url in live.visited), (
        "a wrong-country answer was shopped to the next provider"
    )


def test_an_empty_exit_observation_refuses_it_is_not_a_clean_reading():
    """A page that rendered nothing proves nothing. Reading on would take the
    whole matrix through an address nobody established."""
    from src.services.verify.browser_tier import (
        ExitNotProvenInEngine,
        _observe_engine_exit,
    )

    live = _FakeLive({"ipinfo.io": "   \n  "})
    with pytest.raises(ExitNotProvenInEngine):
        _observe_engine_exit(live)


def test_the_exit_is_observed_BEFORE_any_checker_page_is_loaded():
    """Ordering is the whole guarantee: a checker that has already been asked
    cannot be un-asked, so the proof must precede the first page load."""
    from src.services.verify.browser_tier import _observe_engine_exit

    live = _FakeLive({"ipinfo.io": _exit_json()})
    _observe_engine_exit(live)
    assert len(live.visited) == 1
    assert "ipinfo.io" in live.visited[0]


def test_an_unproven_engine_exit_makes_the_WHOLE_tier_unobtainable():
    """Not a partial record and not a crash: every catalogued browser row is
    present and unobtainable, carrying the reason. The matrix keeps its width
    on exactly the run where something went wrong."""
    import src.services.verify.browser_tier as bt

    live = _FakeLive({"ipinfo.io": _exit_json(country="DE")})

    class _Engine:
        def __enter__(self):
            return live

        def __exit__(self, *a):
            return False

    # Drive the real body with a stubbed engine constructor.
    import types
    fake_module = types.SimpleNamespace(InvisiblePlaywright=lambda **kw: _Engine())
    import sys as _sys
    saved = _sys.modules.get("invisible_playwright")
    _sys.modules["invisible_playwright"] = fake_module
    try:
        out = bt.read_page_texts("socks5h://u:p@host:1080")
    finally:
        if saved is None:
            _sys.modules.pop("invisible_playwright", None)
        else:
            _sys.modules["invisible_playwright"] = saved

    readings = readings_from_texts(out)
    expected = sum(len(c.items) for c in BROWSER_CHECKERS)
    assert len(readings) == expected
    assert all(r.state == UNOBTAINABLE for r in readings)
    assert all("DE" in r.reason for r in readings)
    # And no checker page was ever requested.
    assert live.visited == [checker_by_id("engine-exit").url]


def test_a_proven_engine_exit_then_reads_the_checker_pages():
    """The positive half: with the exit proven, the tier goes on to load the
    pages — so the refusal above is a real gate and not a broken tier."""
    import sys as _sys
    import types
    import src.services.verify.browser_tier as bt

    live = _FakeLive({
        "ipinfo.io": _exit_json(),
        "sannysoft": page("bot.sannysoft.com"),
    })

    class _Engine:
        def __enter__(self):
            return live

        def __exit__(self, *a):
            return False

    fake_module = types.SimpleNamespace(InvisiblePlaywright=lambda **kw: _Engine())
    saved = _sys.modules.get("invisible_playwright")
    _sys.modules["invisible_playwright"] = fake_module
    try:
        out = bt.read_page_texts(
            "socks5h://u:p@host:1080",
            checkers=(checker_by_id("engine-exit"),
                      checker_by_id("bot.sannysoft.com")),
            sleep=lambda _s: None,
        )
    finally:
        if saved is None:
            _sys.modules.pop("invisible_playwright", None)
        else:
            _sys.modules["invisible_playwright"] = saved

    assert "text" in out["engine-exit"]
    assert "text" in out["bot.sannysoft.com"]
    # The exit was asked FIRST, and asked exactly once.
    assert live.visited[0] == checker_by_id("engine-exit").url
    assert live.visited.count(checker_by_id("engine-exit").url) == 1


def test_the_engine_exit_is_not_asked_twice_in_one_run():
    """Asking again would record a SECOND, later address for one run — on a
    rotating exit that makes the record contradict itself."""
    import sys as _sys
    import types
    import src.services.verify.browser_tier as bt

    live = _FakeLive({"ipinfo.io": _exit_json()})

    class _Engine:
        def __enter__(self):
            return live

        def __exit__(self, *a):
            return False

    fake_module = types.SimpleNamespace(InvisiblePlaywright=lambda **kw: _Engine())
    saved = _sys.modules.get("invisible_playwright")
    _sys.modules["invisible_playwright"] = fake_module
    try:
        bt.read_page_texts(
            "socks5h://u:p@host:1080",
            checkers=(checker_by_id("engine-exit"),),
            sleep=lambda _s: None,
        )
    finally:
        if saved is None:
            _sys.modules.pop("invisible_playwright", None)
        else:
            _sys.modules["invisible_playwright"] = saved

    assert live.visited == [checker_by_id("engine-exit").url]


def test_the_tier_pins_firefoxs_proxy_failover_off():
    """The pref that makes a SILENT wrong reading possible: with failover on,
    Firefox answers a dead SOCKS proxy by retrying DIRECTLY, and the pages
    load, parse and record as a clean run on the operator's real address."""
    from src.services.verify.browser_tier import _prefs

    assert _prefs()["network.proxy.failover_direct"] is False
    assert _prefs()["network.proxy.socks_remote_dns"] is True


def test_the_exit_proof_reads_raw_json_not_the_firefox_json_viewer():
    """The proof's patterns are written against RAW JSON. Firefox's viewer
    renders a JSON body as a DOM tree with UNQUOTED keys, which the quoted
    patterns do not match — so the viewer is turned off."""
    from src.services.verify.browser_tier import _prefs

    assert _prefs()["devtools.jsonview.enabled"] is False

    # And the fail direction is SAFE: viewer-style text does not read as a pass.
    from src.services.verify.browser_tier import _observe_engine_exit
    from src.services.verify.browser_tier import ExitNotProvenInEngine

    viewer_text = "ip: 91.150.1.1\ncountry: PL\ncity: Warsaw"
    live = _FakeLive({"ipinfo.io": viewer_text})
    with pytest.raises(ExitNotProvenInEngine):
        _observe_engine_exit(live)


# --- a skipped tier, and the seed -------------------------------------------


def _read(argv, tmp_path, monkeypatch, exit_country="PL", expect_rc=3):
    """Drive the real CLI `read` path with the network stubbed out.

    ``expect_rc`` defaults to 3, not 0, and the default is the interesting
    part. Every caller below passes ``--skip-browser`` and/or ``--skip-json``,
    so these runs gather NO fingerprint evidence whatever — and since PS-110 a
    run that gathered no evidence exits 3 (INCONCLUSIVE) instead of reporting
    like a clean one. The assertion is kept rather than dropped, and kept
    EXPLICIT rather than loosened to "non-2", because it is the thing PS-110
    changed: a helper that shrugged at the exit code would let the floor
    regress to 0 without a single test noticing.

    These tests' SUBJECT is unaffected — a skipped tier keeps its full width,
    the header names it, the seed is recorded. That is why they still assert on
    the record and only the code moved.
    """
    import src.services.verify.checker_cli as cli
    from src.services.verify.exit_guard import Credential, Exit, SOURCE_FILE

    monkeypatch.setattr(
        cli, "prove_exit",
        lambda **kw: ("socks5h://u:p@host:1080",
                      Exit(ip="91.150.1.1", country=exit_country, city="Warsaw",
                           org="AS9141 P4", timezone="Europe/Warsaw"),
                      Credential(
                          proxy_url="socks5h://u:p@host:1080",
                          source=SOURCE_FILE,
                          detail="used the file (/stub/test-proxy.txt)",
                      )),
    )
    monkeypatch.setattr(cli, "read_json_tier", lambda *a, **k: [])
    target = tmp_path / "reading.json"
    rc = cli.main(["read", "-o", str(target)] + argv)
    assert rc == expect_rc
    return json.loads(target.read_text())


def test_a_skipped_tier_keeps_its_full_width_as_unobtainable_rows(
        tmp_path, monkeypatch):
    """--skip-browser must not make the record silently NARROWER. A later run
    diffing two records could not otherwise tell "the tier was skipped" from
    "those checkers were dropped" from "that schema had no such tier"."""
    record = _read(["--skip-browser", "--skip-json"], tmp_path, monkeypatch)

    rows = {(r["checker"], r["item"]) for r in record["readings"]}
    for checker in BROWSER_CHECKERS:
        for item in checker.items:
            assert (checker.id, item.id) in rows
    for checker in JSON_CHECKERS:
        for item in checker.items:
            assert (checker.id, item.id) in rows

    skipped = [r for r in record["readings"]
               if "tier skipped" in r.get("reason", "")]
    assert skipped, "a skipped tier must leave rows behind"
    assert all(r["state"] == UNOBTAINABLE for r in skipped)
    # Never as a pass.
    assert not any(r["state"] == READ for r in skipped)


def test_the_header_names_which_tiers_were_skipped(tmp_path, monkeypatch):
    record = _read(["--skip-browser"], tmp_path, monkeypatch)
    assert record["skipped_tiers"] == ["browser"]

    record = _read(["--skip-json"], tmp_path, monkeypatch)
    assert record["skipped_tiers"] == ["json"]


def test_a_run_that_skipped_nothing_says_so_rather_than_omitting_the_key(
        tmp_path, monkeypatch):
    """An absent key would have to be guessed at; an empty list is a statement."""
    import src.services.verify.checker_cli as cli
    monkeypatch.setattr(cli, "read_json_tier", lambda *a, **k: [])
    record = _read(["--skip-browser"], tmp_path, monkeypatch)
    assert "skipped_tiers" in record
    assert isinstance(record["skipped_tiers"], list)


def test_the_record_carries_the_seed_that_drove_the_fingerprint(
        tmp_path, monkeypatch):
    """The engine's fingerprint is SEED-DERIVED. Without the seed in the
    header, a comparison cannot tell a real coupling from a different seed —
    measured: the renderer moved NVIDIA GTX 980 -> Intel HD Graphics 400
    between two runs purely because the seed differed."""
    record = _read(["--skip-browser", "--skip-json", "--seed", "4242"],
                   tmp_path, monkeypatch)
    assert record["seed"] == 4242


def test_the_seed_is_recorded_even_when_it_is_the_engine_default(
        tmp_path, monkeypatch):
    record = _read(["--skip-browser", "--skip-json"], tmp_path, monkeypatch)
    assert record["seed"] == 0


def test_the_record_still_carries_the_exit_and_engine_beside_the_seed(
        tmp_path, monkeypatch):
    record = _read(["--skip-browser", "--skip-json"], tmp_path, monkeypatch)
    assert record["exit"]["country"] == "PL"
    assert record["engine"]
    assert record["observed_at"]


# --- the LIVE control arm (PS-119) ------------------------------------------
#
# The discriminating measurement for "is persona's masking layer what pixelscan
# is reacting to?" is a live reading with the layer ON and one with it OFF. The
# second arm had NO ROUTE before this: `read` installed the layer
# unconditionally, and `differential --axis layer` only reads a LOCAL loopback
# page, which publishes no verdict at all.
#
# These tests drive the real CLI. They assert on what the RECORD says about its
# own subject, because that is the thing a later comparison reads — a record
# that cannot say which arm it is, is not usable as an arm.


def _browser_arm(argv, tmp_path, monkeypatch, expect_rc=3):
    """Drive `read` through the BROWSER tier with the engine stubbed out.

    The layer question lives in the browser tier, so unlike ``_read`` above
    this one must actually reach it. The engine is replaced at the seam
    ``_read_one`` imports, so no browser is launched and no network is touched;
    what is exercised is the wiring from the flag through to the record.
    """
    import src.services.verify.checker_cli as cli
    import src.services.verify.browser_tier as bt
    from src.services.verify.exit_guard import Credential, Exit, SOURCE_FILE
    from src.services.verify.masking_layer import LayerReport, absent_layer

    monkeypatch.setattr(
        cli, "prove_exit",
        lambda **kw: ("socks5h://u:p@host:1080",
                      Exit(ip="91.150.1.1", country="PL", city="Warsaw",
                           org="AS9141 P4", timezone="Europe/Warsaw"),
                      Credential(
                          proxy_url="socks5h://u:p@host:1080",
                          source=SOURCE_FILE,
                          detail="used the file (/stub/test-proxy.txt)",
                      )),
    )
    monkeypatch.setattr(cli, "read_json_tier", lambda *a, **k: [])

    seen = {}

    def fake_read_browser_tier(proxy_url, *, layer_sink=None,
                               install_layer=True, **kw):
        # Record what the CLI asked for, and report the layer the SHIPPED code
        # would report for that arm — including the real control-arm wording,
        # which is the string the record has to carry.
        seen["install_layer"] = install_layer
        if layer_sink is not None:
            layer_sink(
                LayerReport(route="init_scripts",
                            installed=("audio", "locale", "webgl"))
                if install_layer
                else absent_layer(
                    "install_layer=False: this reading is of the PACKAGED "
                    "ENGINE ONLY, with none of persona's masking layer. It is "
                    "the control arm of a differential, not a reading of the "
                    "product."
                )
            )
        return []

    monkeypatch.setattr(bt, "read_browser_tier", fake_read_browser_tier)

    target = tmp_path / "reading.json"
    rc = cli.main(["read", "-o", str(target), "--skip-json"] + argv)
    assert rc == expect_rc
    return json.loads(target.read_text()), seen


def test_the_control_arm_reaches_the_engine_with_the_layer_suppressed(
        tmp_path, monkeypatch):
    """The flag must actually turn the layer OFF at the engine seam. A record
    that SAID control-arm while the layer was still installed would be the
    worst possible artefact for a differential."""
    _, seen = _browser_arm(["--no-masking-layer"], tmp_path, monkeypatch)
    assert seen["install_layer"] is False


def test_the_product_arm_is_still_the_default(tmp_path, monkeypatch):
    """Off by default and never inferred: a reading WITHOUT the layer does not
    describe the product, so it has to be asked for."""
    _, seen = _browser_arm([], tmp_path, monkeypatch)
    assert seen["install_layer"] is True


def test_the_control_arm_record_states_that_it_is_not_the_product(
        tmp_path, monkeypatch):
    """The record must be unmistakable on its own, without knowing which flags
    were typed — it outlives the terminal, and a later reader comparing two
    records is the consumer that matters."""
    record, _ = _browser_arm(["--no-masking-layer"], tmp_path, monkeypatch)

    assert record["masking_layer"]["route"] == "none"
    assert record["masking_layer"]["installed"] == []
    assert record["masking_layer"]["complete"] is False

    notes = " ".join(record["notes"]).lower()
    assert "packaged engine only" in notes
    assert "control arm" in notes
    assert "not a reading of the product" in notes


def test_the_product_arm_record_is_not_labelled_as_a_control_arm(
        tmp_path, monkeypatch):
    """Guard the guard: if the note were emitted unconditionally, the test
    above would pass while telling a reader nothing."""
    record, _ = _browser_arm([], tmp_path, monkeypatch)

    assert record["masking_layer"]["route"] == "init_scripts"
    assert record["masking_layer"]["complete"] is True
    assert "control arm" not in " ".join(record["notes"]).lower()


def test_the_control_arm_still_records_the_exit_it_was_taken_through(
        tmp_path, monkeypatch):
    """Both arms must carry their address. An arm whose exit is unrecorded
    cannot be checked against the other for rotation — and two arms taken
    through different addresses are not a comparison."""
    record, _ = _browser_arm(["--no-masking-layer"], tmp_path, monkeypatch)
    assert record["exit"]["ip"] == "91.150.1.1"
    assert record["exit"]["country"] == "PL"


def test_suppressing_the_layer_while_skipping_the_browser_is_refused(
        tmp_path, monkeypatch):
    """The layer is installed in the BROWSER tier. A run that skips that tier
    never installs it and never would have, so a record claiming to be a
    deliberate control arm would be indistinguishable from a real engine-only
    reading — while carrying no browser reading to be the control arm OF."""
    import src.services.verify.checker_cli as cli

    target = tmp_path / "reading.json"
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["read", "-o", str(target),
                  "--no-masking-layer", "--skip-browser"])

    assert "contradict" in str(excinfo.value).lower()
    # And it refused BEFORE writing anything, rather than leaving a record
    # behind that a later run would read as an arm.
    assert not target.exists()


# --- two catalogue defects the first live run through a ROTATING exit found --
#
# Both were found by MEASUREMENT, not review: the fixture pages could not have
# caught either, because both fixtures were captured on the one exit
# (Warsaw) and the one page state that happens to hide them. They are reader
# defects, which are in scope; a product fix would not be.


def test_a_rotating_polish_exit_still_reads_as_poland():
    """DEFECT 1. `geo_poland` was `poland\\s*/\\s*warsaw` — a CITY hardcoded
    into an EXIT-sorted item. The exit rotates within Poland BY DESIGN, so the
    moment it moved to Ursynów/Krakow (measured 2026-08-22) a perfectly clean
    Polish page read ABSENT, which looks exactly like the checker having
    stopped reporting Poland."""
    item = next(i for i in checker_by_id("pixelscan.net").items
                if i.id == "geo_country_city")
    checker = checker_by_id("pixelscan.net")

    # MULTI-WORD cities are in this loop deliberately. Poland is full of them
    # (Nowy Sącz, Zielona Góra, Nowy Targ, Gorzów Wielkopolski) and the exit
    # rotates by design, so they are reachable rather than theoretical — but
    # every city here was single-token until 2026-08-22, which is exactly why
    # the truncation defect below survived a round of review.
    for city in ("Warsaw", "Krakow", "Ursynów", "Gdansk",
                 "Nowy Sacz", "Zielona Gora", "Gorzów Wielkopolski"):
        reading = extract_text_item(checker, item, f"Check Geo API\n\nPoland / {city}\n")
        assert reading.state == READ, f"a Polish exit in {city} must still read"
        assert reading.value == f"Poland / {city}", (
            f"{city!r} must be captured WHOLE, as the item's note promises"
        )

    # The country is what is asserted: a non-Polish exit does NOT read.
    assert extract_text_item(
        checker, item, "Check Geo API\n\nGermany / Berlin\n").state == ABSENT


def test_the_naive_city_pattern_would_have_missed_the_rotated_exit():
    """Pins the BUG, so the fix cannot be reverted as a cosmetic loosening."""
    naive = TextItem("geo_poland", r"poland\s*/\s*warsaw", EXIT, adverse=False)
    checker = checker_by_id("pixelscan.net")
    # The clean page from the rotated exit — Poland, just not Warsaw.
    assert extract_text_item(checker, naive, "Poland / Krakow").state == ABSENT
    # ...while the corrected item reads it.
    fixed = next(i for i in checker.items if i.id == "geo_country_city")
    assert extract_text_item(checker, fixed, "Poland / Krakow").state == READ


def test_the_naive_word_pattern_would_have_TRUNCATED_a_multi_word_city():
    """DEFECT 1b — the same defect class as 1, one level down, and it survived
    a round of review because every city in the test loop was single-token.

    ``\\S+`` stops at the first space, so a two-word city was captured as its
    first token only. This fails in the QUIETER direction than the hardcoded
    city did: that one read ABSENT (loud — it looks like the checker stopped
    reporting Poland), this one reads READ with a silently CORRUPTED value.
    That is the worse outcome on a ``capture=True`` row, because this record
    exists precisely so a later run can tell "the verdict changed" from "the
    wording changed" — and ``Poland / Nowy`` -> ``Poland / Zielona`` reads as
    a genuine geo change when both are just multi-word cities.
    """
    checker = checker_by_id("pixelscan.net")
    naive = TextItem("geo_naive", r"(poland\s*/\s*\S+)", EXIT,
                     adverse=False, capture=True)
    fixed = next(i for i in checker.items if i.id == "geo_country_city")

    for city in ("Nowy Sacz", "Zielona Gora", "Gorzów Wielkopolski"):
        page = f"Check Geo API\n\nPoland / {city}\n"

        # The BUG: it still READS, which is why it is quiet — but the captured
        # value has silently lost everything after the first space.
        broken = extract_text_item(checker, naive, page)
        assert broken.state == READ
        assert broken.value == f"Poland / {city.split()[0]}"
        assert broken.value != f"Poland / {city}", (
            "this test must demonstrate the truncation, not agree with the fix"
        )

        # ...while the corrected item captures the city WHOLE.
        assert extract_text_item(checker, fixed, page).value == f"Poland / {city}"


def test_creepjs_best_possible_ratings_are_not_recorded_as_adverse():
    """DEFECT 2. The three rating items are CAPTURE items: matching means "the
    rating was PUBLISHED", not "the rating is bad". Tagged adverse=True, the
    clean measured page — 0% headless, 0% stealth, 6% like headless, the BEST
    readings CreepJS gives — recorded three ADVERSE MATCHES, so the run that
    proved the engine looks right reported it as three red flags."""
    for item_id, expected in (("headless_rating", "0"),
                              ("stealth_rating", "0"),
                              ("like_headless_rating", "6")):
        reading = reading_for("creepjs", item_id)
        assert reading.state == READ
        assert reading.value == expected
        assert reading.adverse is False, (
            f"{item_id} is a captured NUMBER — its polarity lives in the "
            f"value, and an adverse flag makes the best possible reading look "
            f"like a defect"
        )


def test_no_adverse_row_matched_on_the_clean_captured_pages():
    """The whole-catalogue statement of both defects above: reading the four
    REAL clean pages must produce NO adverse match at all. Either defect alone
    broke this, and neither showed up as a test failure."""
    readings = readings_from_texts({cid: {"text": page(cid)} for cid in PAGES})
    flagged = [(r.checker, r.item, r.value)
               for r in readings if r.adverse and r.state == READ]
    assert flagged == [], f"clean pages reported adverse matches: {flagged}"


# --- which credential channel the RECORD says was used (PS-145) -------------
#
# THESE TESTS EXIST BECAUSE THE FEATURE WAS SILENTLY REMOVABLE. The guard's
# two-channel behaviour was well covered, but the leg that carries the answer
# INTO THE ARTEFACT was covered by nothing: deleting `credential_detail=...`
# from the `_notes_for` call in `checker_cli` left the entire suite green, so
# the provenance could regress to stderr-only — the exact state PS-145 says is
# insufficient — without a single test objecting.
#
# ⚠️ THE HELPER BELOW DELIBERATELY DOES **NOT** STUB `prove_exit`, which is
# what every other CLI test in this file does. Stubbing it hands the record a
# hand-written `Credential` and pins the CALL SIGNATURE while proving nothing
# about which channel a real run would have chosen. Only the NETWORK is
# replaced here; `resolve_credential` runs for real against a real file and a
# real environment variable, so what is asserted is what an operator would
# actually read weeks later.


def _read_with_real_credential(
        tmp_path, monkeypatch, *, file_credential=None, env_credential=None,
        credential_path=None, expect_rc=3):
    """Drive the CLI `read` path with ONLY the network stubbed out.

    Returns ``(record, used)`` where ``used`` carries the proxy URL the run
    actually reached the network with — so a test can assert the record's
    stated channel against the credential that was genuinely used, rather than
    against the one the test hoped would win.
    """
    import src.services.verify.checker_cli as cli
    import src.services.verify.exit_guard as exit_guard
    from src.services.verify.exit_guard import Exit

    used = {}

    def fake_observe_exit(proxy_url, **kw):
        # The one seam. Everything above it — both channels, the precedence
        # between them, the socks5h rewrite — is the real code.
        used["proxy_url"] = proxy_url
        return Exit(ip="91.150.1.1", country="PL", city="Warsaw",
                    org="AS9141 P4", timezone="Europe/Warsaw")

    monkeypatch.setattr(exit_guard, "observe_exit", fake_observe_exit)
    monkeypatch.setattr(cli, "read_json_tier", lambda *a, **k: [])

    if file_credential is None:
        # A path that genuinely does not exist, not an empty file: "absent" and
        # "present but unusable" are different dispositions.
        path = credential_path or str(tmp_path / "no-such-test-proxy.txt")
    else:
        written = tmp_path / "test-proxy.txt"
        written.write_text(file_credential + "\n", encoding="utf-8")
        path = credential_path or str(written)

    # The ambient variable is REAL in this container, so every test states its
    # own environment rather than inheriting one — see the equivalent fixture
    # in test_verify_exit_guard.py for what happens when it does not.
    if env_credential is None:
        monkeypatch.delenv(exit_guard.ENVIRONMENT_CREDENTIAL_VAR, raising=False)
    else:
        monkeypatch.setenv(
            exit_guard.ENVIRONMENT_CREDENTIAL_VAR, env_credential
        )

    target = tmp_path / "reading.json"
    rc = cli.main(["read", "-o", str(target), "--skip-browser", "--skip-json",
                   "--credential", path])
    assert rc == expect_rc
    return json.loads(target.read_text()), used


def _credential_note(record):
    """The record's own statement of which channel it used, or None."""
    notes = [n for n in record["notes"] if n.startswith("CREDENTIAL SOURCE:")]
    assert len(notes) <= 1, f"more than one credential note: {notes}"
    return notes[0] if notes else None


def test_the_record_states_the_credential_came_from_the_file(
        tmp_path, monkeypatch):
    """The ordinary case: both channels hold the SAME credential, the file
    wins, and the record says so by name."""
    cred = "socks5://alice:s3cr3t@gate.example.com:10000"
    record, used = _read_with_real_credential(
        tmp_path, monkeypatch, file_credential=cred, env_credential=cred,
    )

    note = _credential_note(record)
    assert note is not None, (
        "the record carries NO credential-source note. The run chose a "
        "channel and the artefact does not say which — an operator reading "
        "this record later cannot account for its exit."
    )
    # The note must say the FILE was USED. "environment" legitimately appears
    # further along (it holds the same credential, and the note says so), so
    # the assertion is on which channel was USED rather than on which words
    # occur — a substring test for "file" alone would pass on a note that said
    # the environment won and merely mentioned the file.
    assert "used the file" in note.lower(), (
        f"the file won and the record does not say so: {note!r}"
    )
    assert "used the environment" not in note.lower()
    assert used["proxy_url"].startswith("socks5h://")


def test_the_record_states_the_credential_came_from_the_environment(
        tmp_path, monkeypatch):
    """THE CASE THE TICKET EXISTS FOR — no file at all, and the run still
    proves its exit. The record must name the ENVIRONMENT, not the file: this
    is the assertion that a file-vs-environment distinction is genuinely
    visible in the artefact rather than a constant string that happens to
    mention a channel."""
    cred = "socks5://alice:s3cr3t@gate.example.com:10000"
    record, used = _read_with_real_credential(
        tmp_path, monkeypatch, file_credential=None, env_credential=cred,
    )

    note = _credential_note(record)
    assert note is not None, "the record does not say which channel was used"
    assert "environment" in note.lower(), (
        f"the run used the environment variable and the record does not say "
        f"so: {note!r}"
    )
    assert "PERSONA_TEST_PROXY" in note
    assert used["proxy_url"].startswith("socks5h://"), (
        "the environment channel must feed the same socks5h rewrite as the "
        "file — plain socks5 leaks a DNS query naming the checker"
    )


def test_the_record_names_the_channel_that_won_when_the_two_disagree(
        tmp_path, monkeypatch):
    """The case silent precedence would hide. Two DIFFERENT live credentials:
    the record must state which one the reading was actually taken through,
    because an operator who rotated one of them and is debugging the result
    has no other way to find out."""
    record, used = _read_with_real_credential(
        tmp_path, monkeypatch,
        file_credential="socks5://alice:s3cr3t@gate.example.com:10000",
        env_credential="socks5://bob:0th3r@relay.example.net:20000",
    )

    note = _credential_note(record)
    assert note is not None, (
        "two channels disagreed and the record is silent about which one was "
        "used — the reading cannot be accounted for"
    )
    assert "DISAGREE" in note, f"the divergence is not reported: {note!r}"
    # The run reached the network through the FILE, and the record has to
    # agree with that rather than merely mention both channels.
    assert "gate.example.com" in used["proxy_url"]
    # Case-insensitive: the divergence sentence capitalises "Used the file"
    # mid-paragraph while the ordinary one does not. The claim under test is
    # WHICH CHANNEL IS NAMED, not how the sentence is punctuated.
    assert "used the file" in note.lower()

    # And the value of NEITHER channel may appear in the artefact.
    blob = json.dumps(record)
    for secret in ("s3cr3t", "0th3r", "alice", "bob"):
        assert secret not in blob, (
            f"{secret!r} reached the written record — the provenance note is "
            f"the one path that must not bypass redact()"
        )


def test_a_record_taken_through_one_channel_does_not_claim_the_other(
        tmp_path, monkeypatch):
    """Guard the guard. If the note were a fixed string naming both channels,
    every test above would pass while telling a reader nothing. The
    environment-only run must NOT claim a file was read."""
    record, _ = _read_with_real_credential(
        tmp_path, monkeypatch, file_credential=None,
        env_credential="socks5://alice:s3cr3t@gate.example.com:10000",
    )

    note = _credential_note(record)
    assert "used the environment" in note, (
        f"expected the note to name the channel actually used: {note!r}"
    )
    assert "used the file" not in note
