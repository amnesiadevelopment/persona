"""The checker matrix, as DATA — what to read, and what each reading depends on.

Level 3 of the project bar names four checkers by name and says the result may
not regress. This file is the inventory that makes that statement readable by a
machine: every checker, every ITEM worth reading from it, and — the load-bearing
part — what each item DEPENDS ON.

Adding a checker or an item MUST mean adding a record here and nothing else.

The three sorts
---------------
A rotating mobile exit was chosen deliberately (owner decision, recorded in the
"Level 3 — reading the checker matrix" knowledge article) so that readings vary
between runs and a hidden coupling has somewhere to show itself. That only pays
off if every reading is tagged by what it depends on; without the tag, a
rotating exit makes EVERY run look changed and the whole record collapses into
noise. So the tag is not decoration:

``EXIT``
    Driven by the address we came out of — proxy/VPN detection, geography,
    carrier, timezone-vs-IP. **Expected to move between runs. Not news.**

``HOST``
    Driven by the machine the engine ran on — the WebGL renderer under a
    software rasteriser, installed fonts, text metrics. Constant here, and
    DIFFERENT on another machine. A red here on a GPU-less runner is
    environmental by construction: recorded with its reason, never counted as a
    pass, never deleted to make a report look clean.

``FINGERPRINT``
    Driven by what persona presents — automation tells, patched-function shape,
    declared-machine coherence. **Must NOT move when only the address moved.**
    One that does is a coupling worth its own ticket, and that is the entire
    return on accepting a rotating exit.

Tiers
-----
``TIER_JSON``
    No browser needed: the checker publishes JSON over HTTPS. Read through the
    proxy with ``socks_fetch``. This is the TLS/network layer.

``TIER_BROWSER``
    A page that must actually run. Read by driving persona's own engine.

``TIER_UNREADABLE``
    Known hostile to automation, or dead. Carried here ON PURPOSE, with a
    reason, and recorded as unobtainable on every run. Listing them as data
    rather than omitting them is the difference between "we could not read
    this" and "this was never in the matrix" — and the ticket is explicit that
    recording the former is a RESULT, not a gap. Nothing here is fetched: the
    scope boundary forbids building anything to defeat a challenge.

What an item is
---------------
For a JSON checker, an item names a PATH into the response
(``("tls", "ja4")``) — machine-readable, so the reading is the value itself.

For a prose checker, an item names a PATTERN. The reading then records BOTH
whether it matched AND the pattern that was used, so a later run can tell "the
verdict changed" from "the checker reworded its page" — two facts that look
identical if only the match is kept.

JA3 is deliberately not read
----------------------------
``ja3_hash`` varies with TLS extension PERMUTATION, so it moves between runs
without anything having changed and would manufacture false drift. ``ja4`` and
``ja3n`` (n = normalised) are read instead. This is a correctness choice, not a
preference: a vector that cannot be made stable is worse than no vector,
because it makes a real difference unreadable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- what a reading depends on ---------------------------------------------

EXIT = "exit"
HOST = "host"
FINGERPRINT = "fingerprint"

# A fourth sort, and it is NOT a fourth kind of dependency — it is the honest
# label for a reading that describes THE INSTRUMENT rather than the product.
#
# It exists because the first live run produced one. The JSON tier is fetched
# by this repository's own Python client (``socks_fetch``), so the TLS shape
# those checkers report is PYTHON'S: the run recorded ``user_agent:
# curl/8.14.1`` and ``http_version: HTTP/1.1`` beside a JA4 of
# ``t13d1712_...``. Tagged FINGERPRINT, as they first were, those rows would
# have claimed to be persona's TLS fingerprint — and a later Python or OpenSSL
# upgrade would then read as PERSONA'S FINGERPRINT MOVING, which is precisely
# the alarm the FINGERPRINT sort exists to raise and precisely the false one it
# must never raise.
#
# So they are labelled for what they are. This does not overrule the owner's
# three sorts (a HARNESS row is not a reading about the identity at all); it
# keeps the fingerprint set clean of readings that were never about persona.
#
# Reading persona's REAL TLS fingerprint means having the ENGINE visit these
# endpoints rather than the Python client. That is a later slice: it is a
# different transport, not a different pattern, and this ticket's boundary is
# to produce the reading and record it honestly — including recording what it
# does not yet measure.
HARNESS = "harness"

ALL_SORTS = (EXIT, HOST, FINGERPRINT, HARNESS)

# --- how a verdict is obtained ----------------------------------------------

TIER_JSON = "json"
TIER_BROWSER = "browser"
TIER_UNREADABLE = "unreadable"


@dataclass(frozen=True)
class JsonItem:
    """One value read out of a JSON checker's response.

    ``path`` is a tuple of keys walked from the document root. A path that does
    not exist is an unobtainable reading, NOT a null value — a checker that
    stopped publishing a field has told us something, and folding that into
    "the value is None" would erase it.
    """

    id: str
    path: "tuple[str, ...]"
    sort: str
    note: str = ""


@dataclass(frozen=True)
class TextItem:
    """One verdict matched out of a prose checker's rendered page.

    ``pattern`` is a regular expression matched case-insensitively against the
    page's visible text. The reading records the pattern ALONGSIDE the result,
    so a later run can distinguish a changed verdict from changed wording.

    ``adverse`` says which way the match points: True when MATCHING is the bad
    news ("Proxy detected"), False when matching is the good news ("You are not
    a bot"). This is recorded rather than inferred because the polarity is not
    guessable from the string, and a comparator that guessed it would report
    improvements as regressions.

    ``capture`` records group 1 of the match as the reading's value, for the
    checkers that publish a NUMBER in prose (CreepJS's "0% headless"). Without
    it the reading would be a bare boolean and a score moving from 0% to 40%
    would read as "still matching", i.e. as no change at all.

    THE NEGATION TRAP — read before adding a pattern
    ------------------------------------------------
    Every pattern here is anchored against its own NEGATION, and that is not
    defensive style, it is a measured near-miss. Pixelscan renders its clean
    verdicts as ``No proxy detected`` / ``No masking detected`` / ``No
    automated behavior detected``. The obvious patterns (``proxy detected``,
    ``masking detected``) match all three of those HAPPILY — so a reader built
    on them reports a clean page as three red flags, and does it silently, with
    a real match and a real quote to back it up. A wrong reading that looks
    like data is the exact failure this subsystem exists to prevent.

    So an adverse pattern that has a "No ..." form on the real page carries a
    negative lookbehind, and ``tests/test_verify_checkers.py`` asserts against
    the MEASURED page text that the negated form does not match.
    """

    id: str
    pattern: str
    sort: str
    adverse: bool = True
    capture: bool = False
    note: str = ""


@dataclass(frozen=True)
class Checker:
    """One checker in the matrix."""

    id: str
    url: str
    tier: str
    items: "tuple[JsonItem | TextItem, ...]" = ()
    # Seconds to let a page settle before reading it. Pixelscan and CreepJS
    # take 45-60s to reach a verdict; reading either at load time records an
    # empty page as though it were the answer.
    settle_seconds: float = 0.0
    # Only for TIER_UNREADABLE: why, in a sentence, so the record explains
    # itself without a trip to the knowledge article.
    unreadable_reason: str = ""
    # For a READABLE checker that was nonetheless not reachable when the
    # matrix was last read. Distinct from ``unreadable_reason`` on purpose:
    # that one means "this checker is out of scope to read", this one means
    # "this checker is in scope and did not answer us". The row stays in the
    # matrix and is recorded as unobtainable per run — a checker that stops
    # answering is a reading, and dropping the row would quietly shrink the
    # matrix until nothing was left to fail.
    note_unreachable: str = ""


# --- the matrix -------------------------------------------------------------
#
# Established by loading each one (PS-10). Ordered by how cheaply a verdict can
# be consumed, which is also roughly the order of how much can go wrong.

JSON_CHECKERS: "tuple[Checker, ...]" = (
    # NOTE ON THE SORT USED THROUGHOUT THIS TIER — read before adding an item.
    #
    # These endpoints are fetched by THIS REPOSITORY'S OWN PYTHON CLIENT
    # (``socks_fetch``), not by persona's engine. So the TLS/HTTP shape they
    # report is the SHAPE OF THE INSTRUMENT: the first live run recorded
    # ``user_agent: curl/8.14.1`` and ``http_version: HTTP/1.1`` beside its
    # JA4. Those rows say nothing whatever about persona's fingerprint, and
    # tagging them FINGERPRINT (as they first were) would have made a future
    # Python or OpenSSL upgrade read as PERSONA'S FINGERPRINT MOVING — a false
    # alarm of exactly the kind the FINGERPRINT sort exists to make credible.
    #
    # They are therefore tagged HARNESS: a real, comparable reading about the
    # instrument. Reading persona's REAL TLS fingerprint means driving these
    # same endpoints from the ENGINE, which is a different transport and a
    # later slice. Recording what this run does NOT measure is part of the
    # result.
    #
    # ipleak.net is the exception below: its items are geography, which is a
    # property of the EXIT and is identical whichever client asked.
    Checker(
        id="tls.peet.ws",
        url="https://tls.peet.ws/api/all",
        tier=TIER_JSON,
        items=(
            JsonItem("ja4", ("tls", "ja4"), HARNESS,
                     "TLS client shape OF THE PYTHON FETCHER. Stable across "
                     "extension permutation, unlike ja3."),
            JsonItem("peetprint_hash", ("tls", "peetprint_hash"), HARNESS),
            JsonItem("akamai_fingerprint", ("http2", "akamai_fingerprint"),
                     HARNESS,
                     "HTTP/2 SETTINGS shape. ABSENT on the first live run, "
                     "and correctly so: the fetcher negotiated HTTP/1.1, so "
                     "there was no h2 frame to fingerprint. An absent reading "
                     "here is a fact about the instrument, not a checker that "
                     "went quiet."),
            JsonItem("http_version", ("http_version",), HARNESS),
            JsonItem("user_agent", ("user_agent",), HARNESS),
            # The observed source address, from the checker's own point of
            # view. Tagged EXIT: it is SUPPOSED to move between runs.
            JsonItem("observed_ip", ("ip",), EXIT),
        ),
    ),
    Checker(
        id="tls.browserleaks.com",
        url="https://tls.browserleaks.com/json",
        tier=TIER_JSON,
        items=(
            JsonItem("ja4", ("ja4",), HARNESS),
            JsonItem("ja3n_hash", ("ja3n_hash",), HARNESS,
                     "Normalised JA3 — ja3_hash itself is deliberately not "
                     "read, it moves with extension order."),
            JsonItem("akamai_hash", ("akamai_hash",), HARNESS),
            JsonItem("user_agent", ("user_agent",), HARNESS),
        ),
    ),
    Checker(
        id="ipleak.net",
        url="https://ipleak.net/json/",
        tier=TIER_JSON,
        items=(
            JsonItem("country_code", ("country_code",), EXIT),
            JsonItem("city_name", ("city_name",), EXIT),
            JsonItem("as_number", ("as_number",), EXIT),
            JsonItem("isp_name", ("isp_name",), EXIT),
            JsonItem("time_zone", ("time_zone",), EXIT,
                     "The zone the EXIT implies. persona derives the profile's "
                     "timezone from proxy geography, so this is the value the "
                     "browser-side timezone must agree with."),
            JsonItem("observed_ip", ("ip",), EXIT),
        ),
    ),
    Checker(
        id="tools.scrapfly.io",
        url="https://tools.scrapfly.io/api/fp/anything",
        tier=TIER_JSON,
        items=(
            JsonItem("ja4", ("tls", "ja4"), HARNESS),
            JsonItem("akamai_hash", ("http2", "akamai_fingerprint"), HARNESS),
        ),
        note_unreachable=(
            "Did not answer through the mobile exit on 2026-08-21 (the SOCKS5 "
            "connection could not be completed). Kept in the matrix and "
            "recorded as UNOBTAINABLE per run rather than dropped."
        ),
    ),
)


# The address the ENGINE was observed leaving through — the browser tier's own
# proof, and a row rather than a belief.
#
# WHY THIS EXISTS AS A CHECKER AND NOT AS A PRECONDITION SOMEWHERE ELSE.
# ``exit_guard.prove_exit`` proves the exit for the PYTHON FETCHER: it opens a
# ``socks_fetch`` socket and reads ipinfo through it. The browser tier is a
# DIFFERENT PROCESS ON A DIFFERENT SOCKET, so that proof says nothing about it.
# Without this row, an engine whose proxy silently failed would still render
# every page, parse every verdict and land every row as READ — a
# complete-looking reading of the OPERATOR'S REAL ADDRESS taken against
# Pixelscan, CreepJS, iphey and Sannysoft. That is the Invariant #0 failure the
# guard exists to prevent, prevented on one leg and not the other.
#
# It is READ THE SAME WAY as every other browser row (``inner_text`` + a
# pattern), so it needs no new extraction path — and it is recorded as EXIT,
# because it is supposed to move between runs.
#
# The pattern is written against RAW JSON, which is why ``_prefs`` turns
# Firefox's JSON viewer off: with the viewer on, the body renders as a DOM tree
# whose keys are unquoted, the quoted pattern does not match, and the reading
# is ABSENT. That is the fail-SAFE direction — an unmatched proof refuses the
# tier rather than passing it — but the raw form is what this is written for.
ENGINE_EXIT_CHECKER = Checker(
    id="engine-exit",
    url="https://ipinfo.io/json",
    tier=TIER_BROWSER,
    settle_seconds=0.0,
    items=(
        TextItem("observed_ip", r'"ip"\s*:\s*"([^"]+)"', EXIT,
                 adverse=False, capture=True,
                 note="The address the ENGINE came out of, observed through "
                      "the engine itself. Compare against the JSON tier's "
                      "observed_ip: the two are different clients and a "
                      "divergence is a fact worth having in the record rather "
                      "than in a comment."),
        TextItem("country", r'"country"\s*:\s*"([^"]+)"', EXIT,
                 adverse=False, capture=True),
        TextItem("city", r'"city"\s*:\s*"([^"]+)"', EXIT,
                 adverse=False, capture=True),
        TextItem("org", r'"org"\s*:\s*"([^"]+)"', EXIT,
                 adverse=False, capture=True),
        TextItem("timezone", r'"timezone"\s*:\s*"([^"]+)"', EXIT,
                 adverse=False, capture=True,
                 note="The zone the engine's OWN exit implies. persona derives "
                      "the profile timezone from proxy geography, so this is "
                      "the value the browser-side timezone must agree with."),
    ),
)


# Every pattern below was matched against the checker's REAL rendered text,
# captured through the mobile exit on 2026-08-21, and those captures are kept
# as fixtures under ``tests/fixtures/checker-pages/`` so the suite can prove
# each pattern reads that page the way this file claims. Four of the patterns
# originally written here were WRONG against the real pages — see the negation
# trap on :class:`TextItem` — and were corrected by measurement, not review.

BROWSER_CHECKERS: "tuple[Checker, ...]" = (
    # First, and deliberately: it is the tier's own proof of exit, and no
    # checker page is loaded until it holds.
    ENGINE_EXIT_CHECKER,
    Checker(
        id="deviceandbrowserinfo.com",
        url="https://deviceandbrowserinfo.com/are_you_a_bot",
        tier=TIER_BROWSER,
        settle_seconds=20.0,
        items=(
            TextItem("bot_verdict_positive",
                     r"(?<!not )(?:you are|you're) (?:a )?(?:bot|robot)",
                     FINGERPRINT, adverse=True),
            TextItem("bot_verdict_negative",
                     r"(?:you are|you're) not (?:a )?(?:bot|robot)",
                     FINGERPRINT, adverse=False),
        ),
        note_unreachable=(
            "Answered NS_ERROR_CONNECTION_REFUSED through the mobile exit on "
            "2026-08-21 — reachable on a datacenter address, refused from this "
            "carrier. Kept in the matrix and recorded as UNOBTAINABLE per run "
            "rather than dropped: 'this checker did not answer us' is a "
            "reading, and deleting the row would silently shrink the matrix."
        ),
    ),
    Checker(
        id="bot.sannysoft.com",
        url="https://bot.sannysoft.com",
        tier=TIER_BROWSER,
        settle_seconds=20.0,
        items=(
            # Measured page renders "missing (passed)" for a clean browser, so
            # the adverse form is the PRESENT one. The label and the result are
            # separated by TWO newlines and a tab — "WebDriver\n(New)\n\tmissing
            # (passed)" — which is why these span whitespace explicitly. A
            # tighter `[^\n]*` form was written first and read this very page as
            # ABSENT: the page said "passed" and the reader recorded "the page
            # did not say this", which is a false negative dressed as a clean
            # result. tests/test_verify_checkers.py pins it against the fixture.
            TextItem("webdriver_present",
                     r"webdriver\s*(?:\(new\)\s*)?\bpresent\b", FINGERPRINT,
                     adverse=True),
            TextItem("webdriver_missing_passed",
                     r"webdriver\s*(?:\(new\)\s*)?missing \(passed\)",
                     FINGERPRINT, adverse=False),
            TextItem("webdriver_advanced_passed",
                     r"webdriver advanced\s*passed", FINGERPRINT,
                     adverse=False),
            # \s+ and NOT \s*. The page carries a probe LABEL spelled
            # "phantomJS" with no space, and \s* matched it — so the FIRST
            # live run recorded a clean browser as a PhantomJS detection, an
            # adverse verdict manufactured entirely by the reader. The fixture
            # happens not to contain that label, so only the live read caught
            # it; test_phantom_probe_label_is_not_a_detection pins it now.
            TextItem("phantom_js", r"phantom\s+js\b", FINGERPRINT,
                     adverse=True,
                     note="The page names its probes PHANTOM_*/phantomJS "
                          "regardless of outcome, so this matches the SPACED "
                          "prose form only — a probe label must never read as "
                          "a detection."),
        ),
    ),
    Checker(
        id="bot-detector.rebrowser.net",
        url="https://bot-detector.rebrowser.net",
        tier=TIER_BROWSER,
        settle_seconds=30.0,
        items=(
            TextItem("detected", r"\bdetected\b", FINGERPRINT, adverse=True,
                     note="Covers the modern CDP leaks (runtime enable, "
                          "source url, exposed function)."),
        ),
        note_unreachable=(
            "Answered NS_ERROR_CONNECTION_REFUSED through the mobile exit on "
            "2026-08-21, same as deviceandbrowserinfo.com. Recorded as "
            "unobtainable per run, not dropped."
        ),
    ),
    Checker(
        id="iphey.com",
        url="https://iphey.com",
        tier=TIER_BROWSER,
        settle_seconds=45.0,
        items=(
            # Measured: "Your Digital Identity Looks \nTrustworthy" — the
            # newline between the two words is why this is \s+ and not a space.
            TextItem("trustworthy", r"looks\s+trustworthy", FINGERPRINT,
                     adverse=False,
                     note="The .hero-status verdict, read as TEXT: the "
                          "engine's context refuses page.evaluate (CSP), so a "
                          "class read is not available here."),
            TextItem("not_trustworthy",
                     r"looks\s+(?:not\s+trustworthy|suspicious)", FINGERPRINT,
                     adverse=True),
            TextItem("hardware_fine", r"hardware\s+everything is fine", HOST,
                     adverse=False),
            TextItem("software_fine", r"software\s+everything is fine",
                     FINGERPRINT, adverse=False),
        ),
    ),
    Checker(
        id="pixelscan.net",
        url="https://pixelscan.net/fingerprint-check",
        tier=TIER_BROWSER,
        settle_seconds=60.0,
        items=(
            TextItem("fingerprint_inconsistent",
                     r"your browser fingerprint is inconsistent", FINGERPRINT),
            TextItem("fingerprint_consistent",
                     r"your browser fingerprint is consistent", FINGERPRINT,
                     adverse=False),
            # The four "No X detected" verdicts. Each adverse pattern carries a
            # negative lookbehind because the CLEAN page contains the adverse
            # phrase verbatim, prefixed by "No".
            TextItem("proxy_detected", r"(?<!no )proxy detected", EXIT),
            TextItem("masking_detected", r"(?<!no )masking detected",
                     FINGERPRINT),
            TextItem("automation_detected",
                     r"(?<!no )automated behaviou?r detected", FINGERPRINT),
            TextItem("timezone_spoofed", r"(?<!no )timezone spoofed", EXIT,
                     note="A timezone-vs-IP COMPARISON, so it is exit-driven: "
                          "it moves when the exit moves."),
            # CAPTURED, not asserted — and the fix for a defect the first live
            # run through a ROTATING exit exposed. This item is EXIT-sorted,
            # which means the value is SUPPOSED to move between runs; the
            # pattern nonetheless hardcoded `poland / warsaw`, so the moment
            # the mobile exit rotated to Ursynów/Krakow (measured 2026-08-22)
            # a perfectly clean Polish page read ABSENT. An EXIT item that only
            # matches ONE of the values the exit legitimately produces is a
            # reader defect, and the failure direction is the bad one: it looks
            # like the checker stopped reporting Poland.
            #
            # So the COUNTRY is what is matched and the CITY is recorded as a
            # value to compare. A country that stops being Poland is then a
            # real change, and a city that moves is the design working.
            # `[^\n]+`, NOT `\S+`. The value sits alone on its own line
            # (measured: `Poland / Warsaw` at pixelscan.txt:107), and `\S+`
            # stops at the first space — so a TWO-WORD city truncated to its
            # first token: `Poland / Nowy Sacz` was captured as `Poland /
            # Nowy`. Poland is full of them (Nowy Sącz, Zielona Góra, Nowy
            # Targ, Gorzów Wielkopolski) and this exit rotates by design, so
            # the case is reachable rather than theoretical.
            #
            # This is the SAME defect class as the hardcoded city above, one
            # level down, and it fails in the QUIETER direction: the old bug
            # read ABSENT (loud), this one read READ with a silently corrupted
            # value. On a `capture=True` row in a record whose whole purpose is
            # telling "the verdict changed" from "the wording changed",
            # `Poland / Nowy` -> `Poland / Zielona` reads as a genuine geo
            # change when both are just multi-word cities.
            TextItem("geo_country_city", r"(poland\s*/\s*[^\n]+)", EXIT,
                     adverse=False, capture=True,
                     note="The checker's own geo verdict. Matched on the "
                          "COUNTRY and captured WHOLE to the end of the line: "
                          "the exit rotates within Poland by design, so pinning "
                          "a city here manufactures a false ABSENT on a clean "
                          "page, and `\\S+` would truncate a multi-word city."),
            TextItem("timezone_from_js", r"timezone from js\s+(\S+)", EXIT,
                     adverse=False, capture=True,
                     note="Must agree with the zone the EXIT implies — "
                          "persona derives the profile timezone from proxy "
                          "geography."),
            TextItem("webgl_renderer", r"webgl renderer\s+([^\n]+)", HOST,
                     adverse=False, capture=True,
                     note="Recorded as a VALUE rather than matched against a "
                          "software-rasteriser pattern: the engine spoofs the "
                          "renderer string, so the declared GPU is what is "
                          "worth recording. See the host-fact note on "
                          "creepjs.gpu_renderer."),
        ),
    ),
    Checker(
        id="creepjs",
        url="https://abrahamjuliot.github.io/creepjs",
        tier=TIER_BROWSER,
        settle_seconds=60.0,
        items=(
            # Measured form is "0% headless: 52defe05" — the number PRECEDES
            # the word. Captured as a value, not as a boolean: a rating moving
            # 0% -> 40% would otherwise read as "still matching", i.e. as no
            # change at all.
            #
            # adverse=False ON ALL THREE, and that is a correction the first
            # live run forced. These are CAPTURE items: matching them means
            # "the rating was PUBLISHED", not "the rating is bad". Left at the
            # default adverse=True, the clean measured page — `0% headless`,
            # `0% stealth`, `6% like headless`, i.e. the best readings CreepJS
            # can give — recorded three ADVERSE MATCHES, so the run that proved
            # the engine looks right reported it as three red flags.
            #
            # The polarity of a captured NUMBER lives in the number, and it
            # cannot be expressed by a boolean that only says whether the
            # pattern fired: judging 0% against 40% is a comparator's job, and
            # this row's job is to hand it a value it can trust. An `adverse`
            # flag here would make every successful reading look like a defect
            # and every UNREADABLE page look clean — the wrong way round twice.
            TextItem("headless_rating", r"(\d+(?:\.\d+)?)%\s*headless\b",
                     FINGERPRINT, adverse=False, capture=True,
                     note="The headless-resistance block. Slow to populate, "
                          "and the trust-score block does not always render "
                          "at all — which is why a miss is recorded as a MISS "
                          "and never as a zero. LOWER IS BETTER: 0% measured "
                          "on the clean run, so the VALUE carries the verdict "
                          "and the match only says it was published."),
            TextItem("like_headless_rating",
                     r"(\d+(?:\.\d+)?)%\s*like headless", FINGERPRINT,
                     adverse=False, capture=True),
            TextItem("stealth_rating", r"(\d+(?:\.\d+)?)%\s*stealth",
                     FINGERPRINT, adverse=False, capture=True),
            TextItem("chromium_claim", r"chromium:\s*(true|false)", FINGERPRINT,
                     adverse=False, capture=True),
            TextItem("gpu_renderer", r"(angle \([^\n]+\))", HOST, adverse=False,
                     capture=True,
                     note="HOST-driven, and the one class this container "
                          "genuinely cannot answer: there is no GPU here, so "
                          "WebGL renders in SOFTWARE while the engine declares "
                          "a card. That pair is impossible on real hardware "
                          "and is a KNOWN-ENVIRONMENTAL red — the masking "
                          "charter records it as a host-fact leak rather than "
                          "a configuration mistake. Recorded with its reason, "
                          "never counted as a pass, never raised as a new "
                          "defect. Note no checker in this matrix FLAGGED it: "
                          "the string alone reads as plausible, which is why "
                          "it is recorded as a value to compare rather than "
                          "as a verdict to trust. The capture takes the WHOLE "
                          "ANGLE(...) string to the line end: a `[^)]+` form "
                          "stopped at the first ')' — which falls INSIDE "
                          "'Intel(R)' — and recorded the truncated 'Intel, "
                          "Intel(R'. A truncated renderer silently defeats "
                          "the comparison this row exists for."),
        ),
    ),
)


UNREADABLE_CHECKERS: "tuple[Checker, ...]" = (
    Checker(id="whoer.net", url="https://whoer.net", tier=TIER_UNREADABLE,
            unreadable_reason="Cloudflare challenge. Defeating it is out of "
                              "scope by charter — that is a different product."),
    Checker(id="amiunique.org", url="https://amiunique.org", tier=TIER_UNREADABLE,
            unreadable_reason="Click-gated: the verdict is behind a user "
                              "gesture."),
    Checker(id="coveryourtracks.eff.org",
            url="https://coveryourtracks.eff.org", tier=TIER_UNREADABLE,
            unreadable_reason="Click-gated, and observed returning HTTP 500."),
    Checker(id="fv.pro", url="https://fv.pro", tier=TIER_UNREADABLE,
            unreadable_reason="The fields worth reading are paywalled."),
    Checker(id="browserscan.net", url="https://browserscan.net",
            tier=TIER_UNREADABLE,
            unreadable_reason="Text-only verdict with no stable ids, and its "
                              "internal API requires signed params (returns "
                              "401). Carried as unreadable rather than matched "
                              "loosely against unanchored prose."),
)


CHECKERS: "tuple[Checker, ...]" = (
    JSON_CHECKERS + BROWSER_CHECKERS + UNREADABLE_CHECKERS
)


def checkers_for_tier(tier: str) -> "tuple[Checker, ...]":
    return tuple(c for c in CHECKERS if c.tier == tier)


def checker_by_id(checker_id: str) -> "Checker | None":
    for checker in CHECKERS:
        if checker.id == checker_id:
            return checker
    return None


__all__ = [
    "ALL_SORTS",
    "BROWSER_CHECKERS",
    "CHECKERS",
    "Checker",
    "ENGINE_EXIT_CHECKER",
    "EXIT",
    "FINGERPRINT",
    "HARNESS",
    "HOST",
    "JSON_CHECKERS",
    "JsonItem",
    "TIER_BROWSER",
    "TIER_JSON",
    "TIER_UNREADABLE",
    "TextItem",
    "UNREADABLE_CHECKERS",
    "checker_by_id",
    "checkers_for_tier",
]
