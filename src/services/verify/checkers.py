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
    Driven by the machine the engine ran on — installed fonts, text metrics.
    Constant here, and DIFFERENT on another machine. Recorded with its reason,
    never counted as a pass, never deleted to make a report look clean.

    **The GPU is NOT in this sort, and that is an owner decision rather than a
    classification opinion** (2026-08-22, recorded in PS-10 under "a GPU-less
    environment is not an exemption"). The renderer rows used to sit here and
    were written off as "the runner has no GPU". They are now FINGERPRINT:
    there will be no dev-VM and no GPU machine in the loop, and *the engine is
    expected to present a plausible GPU wherever it runs, including on a host
    that has none*. That makes an implausible renderer persona's defect, not
    the container's — see ``GPU_CLAIMED`` / ``GPU_RENDERED`` below.

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

# --- the two GPU vectors ----------------------------------------------------
#
# ORTHOGONAL to the sorts above: a sort says WHAT A READING DEPENDS ON, a
# vector says WHICH OF TWO DIFFERENT QUESTIONS ABOUT THE GPU a reading answers.
# Both GPU vectors are FINGERPRINT-sorted; they are told apart by this.
#
# The owner ruled (2026-08-22, PS-10) that the bar wants both — *"планка
# требует идеала и там и там"* — and verification's obligation follows from it:
# report WHICH of the two a red came from rather than collapsing them into "GPU
# red". They have completely different fixes, so a merged row cannot be acted
# on by whoever picks up the masking ticket.
#
# ``GPU_CLAIMED``
#     What the renderer SAYS IT IS: the ``WEBGL_debug_renderer_info`` strings,
#     the vendor, the reported capabilities. Fixed by changing what the spoofer
#     declares. A checker reading `SwiftShader` or `llvmpipe` here is reading a
#     string persona chose to present.
#
# ``GPU_RENDERED``
#     What the checker's OWN RENDERING ACTUALLY PRODUCED: the canvas and WebGL
#     hashes it computes FROM PIXELS. persona does not choose these — they fall
#     out of whatever rasteriser really drew the frame. Fixed, if at all, at
#     the rendering layer, and NOT by editing a declared string.
#
# The pairing is the point. A plausible claimed string beside a hash that came
# out of a software rasteriser is the "the string is right but the render gives
# us away" case the owner explicitly called a defect rather than an accepted
# limit. Neither row alone can show it.
GPU_CLAIMED = "gpu_claimed"
GPU_RENDERED = "gpu_rendered"

GPU_VECTORS = (GPU_CLAIMED, GPU_RENDERED)

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
    # Which GPU question this row answers, when it answers one at all:
    # GPU_CLAIMED (the declared strings) or GPU_RENDERED (hashes computed from
    # actual pixels). Empty on every row that is not about the GPU. Carried
    # into the record so a report can say WHICH vector a red came from — the
    # two have different fixes and a merged "GPU red" cannot be acted on.
    vector: str = ""


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

    So an adverse pattern that has a "No ..." form on the real page declares its
    negator in ``negated_by``, and ``tests/test_verify_checkers.py`` asserts
    against the MEASURED page text that the negated form does not match.

    ``negated_by`` REPLACED A NEGATIVE LOOKBEHIND — a measured fix, not a
    refactor (PS-119)
    ------------------------------------------------------------------
    These patterns used to spell the guard inline as ``(?<!no )``. Python's
    lookbehind is FIXED-WIDTH, so that construction can express exactly ONE
    separator — a single space — and the clean page is only safe if the page
    happens to render it that way. Measured against the shipped patterns:

        'No masking detected'     -> absent  (correct)
        'Masking detected'        -> MATCH   (correct)
        'No\\nmasking detected'    -> MATCH   <- FALSE FIRE
        'No  masking detected'    -> MATCH   <- FALSE FIRE
        'No\\tmasking detected'    -> MATCH   <- FALSE FIRE

    All FOUR pixelscan verdicts carried it. The whitespace is not hypothetical:
    the browser tier reads pages with ``inner_text``, and pixelscan renders each
    verdict in a component tree, so "No" and "masking detected" landing in
    different elements is precisely what puts a NEWLINE between them. The
    committed fixture carries the single-space form, which is why the existing
    tests pass and could never have caught this.

    The failure mode is the worst this subsystem has: a CLEAN page read as
    ``masking_detected`` — the one verdict in the catalogue that says "this
    browser is running an antidetect tool" — reported with a real match and a
    real quote behind it.

    So the negation is declared as DATA and enforced in
    :func:`~.matrix.extract_text_item`, which walks every occurrence and skips
    the ones a negator introduces, whatever whitespace separates them. The
    haystack is still never normalised (see that function): the GPU patterns are
    newline-anchored, and collapsing whitespace globally would break them.
    """

    id: str
    pattern: str
    sort: str
    adverse: bool = True
    capture: bool = False
    # Record EVERY non-negated match's group 1, deduplicated and sorted, joined
    # with commas — rather than the first match alone. Requires ``capture``.
    #
    # WHY THIS IS NOT A CONVENIENCE (PS-121). ``extract_text_item`` takes the
    # FIRST non-negated match, and ``matrix_diff._verdict`` compares
    # ``(state, value)`` only. So on a page that renders one row per test, an
    # item that captures the first match records the SAME value whether one row
    # or five are adverse — and a browser going from 1 detection to 3 compares
    # equal and returns None, the branch documented as "read on both sides and
    # agreed". The silent pass. A detection count that can triple invisibly is
    # a regression the matrix exists to catch and cannot see.
    #
    # Sorted so the value depends on WHICH tests fired, never on the order the
    # page happened to render them in — otherwise a reshuffled table reads as a
    # changed verdict. Deduplicated because a name repeated on the page is
    # still one test that caught us.
    #
    # Opt-in, and deliberately not the default: the capturing items that read a
    # SCORE or a COUNTRY ("0% headless", "Poland / Nowy Sacz") publish one
    # answer per page, and joining their incidental repeats would corrupt the
    # single value a comparator is meant to read.
    capture_all: bool = False
    note: str = ""
    # The word that, sitting immediately before a match, INVERTS it — "no" for
    # pixelscan's "No masking detected". Whitespace-insensitive and anchored on
    # a word boundary, so "casino masking detected" is not read as a negation.
    # Empty for every pattern that has no negated form on the real page.
    negated_by: str = ""
    # Which GPU question this row answers, when it answers one at all:
    # GPU_CLAIMED (the declared strings) or GPU_RENDERED (hashes computed from
    # actual pixels). Empty on every row that is not about the GPU. Carried
    # into the record so a report can say WHICH vector a red came from — the
    # two have different fixes and a merged "GPU red" cannot be acted on.
    vector: str = ""


@dataclass(frozen=True)
class Checker:
    """One checker in the matrix."""

    id: str
    url: str
    tier: str
    items: "tuple[JsonItem | TextItem, ...]" = ()
    # REDUNDANT PROVIDERS OF THE SAME ANSWER, tried in order until one answers.
    # Empty for almost every checker in the matrix, and deliberately so: two
    # checkers are two opinions and are both recorded, never substituted for
    # one another. This exists for the EXIT OBSERVATION alone, where the row is
    # a PRECONDITION rather than a verdict — one unreachable oracle there marks
    # every other row in the tier unobtainable (PS-128), which is a fault of
    # the instrument and not a reading of the product.
    urls: "tuple[str, ...]" = ()
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
# The providers the ENGINE may observe its own exit through, tried in order
# until one answers — the engine-side twin of ``exit_guard.EXIT_OBSERVATION_URLS``.
#
# PS-128 measured a single oracle failing closed on a HEALTHY exit. ipinfo.io
# answered `HTTP 429 Rate limit hit` through the mobile exit (the limit
# attaches to the EXIT's shared address, not to us), and because this row is
# the tier's PRECONDITION, all 37 browser rows in all 4 configurations were
# marked unobtainable — the run refused itself over a provably Polish exit.
#
# Redundant for REACHABILITY only. A provider that ANSWERS is authoritative:
# the wrong country still ends the run, and is never retried against a
# friendlier provider. Only a page that could not be loaded, or that carried
# no country at all, advances to the next.
ENGINE_EXIT_URLS = (
    "https://ipinfo.io/json",
    "https://ipwho.is/",
)

ENGINE_EXIT_CHECKER = Checker(
    id="engine-exit",
    # `url` stays the FIRST provider so every existing reader of it (the
    # record, the catalogue tests) keeps its meaning; `urls` is what the
    # observation loop walks.
    url=ENGINE_EXIT_URLS[0],
    urls=ENGINE_EXIT_URLS,
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
        # TWO DIALECTS, ONE ROW. ipinfo answers `"country": "PL"`; ipwho.is
        # answers `"country": "Poland"` and puts the code in `"country_code"`.
        # This row is compared against "PL", so reading ipwho.is with the
        # naive `"country"` pattern captures "Poland" and REFUSES the run for
        # being in the wrong country — a worse failure than the 429 the
        # fallback exists to survive, because the message would be actively
        # false. Anchoring on a TWO-LETTER value picks the code out of either
        # dialect: "Poland" cannot match, so on ipwho.is the pattern skips
        # past it to `country_code`. `continent_code` cannot match either,
        # since the key must literally be `country`/`country_code`.
        TextItem("country", r'"country(?:_code)?"\s*:\s*"([A-Za-z]{2})"', EXIT,
                 adverse=False, capture=True),
        TextItem("city", r'"city"\s*:\s*"([^"]+)"', EXIT,
                 adverse=False, capture=True),
        # ipinfo has `org` flat; ipwho.is nests it under `connection`. The
        # pattern is unanchored, so it finds either.
        TextItem("org", r'"(?:org|isp)"\s*:\s*"([^"]+)"', EXIT,
                 adverse=False, capture=True),
        # ipinfo: `"timezone": "Europe/Warsaw"`. ipwho.is: `"timezone": {"id":
        # "Europe/Warsaw", ...}` — an OBJECT, which the quoted-value form
        # cannot match, so the zone is reached through the nested `id`.
        # ONE capture group, not two alternatives each with their own: `capture`
        # records GROUP 1, so an alternation of the form `(?:"(A)"|{"id":"(B)")`
        # reads ipinfo fine and returns None on ipwho.is, where the value lands
        # in group 2. The alternation is therefore over the PREFIX only, and the
        # single group follows it — measured, this was returning None.
        TextItem("timezone",
                 r'"timezone"\s*:\s*(?:"|\{\s*"id"\s*:\s*")([^"]+)"',
                 EXIT,
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
            # THIS CHECKER PUBLISHES NO ADVERSE PROSE — read its MARKER (PS-121).
            #
            # The pattern here was `\bdetected\b`, and measuring it against both
            # captured pages showed it was not merely under-guarded, it was
            # aimed at a token that carries NO VERDICT AT ALL. The word
            # "detected" appears twice, in the same two places, on a CLEAN page
            # and on a CAUGHT one — and both occurrences are GREEN rows:
            #
            #     🟢 runtimeEnableLeak   No leak detected.
            #     🟢 pwInitScripts       No window.__pwInitScripts detected.
            #
            # while every RED row is prose that never uses the word:
            #
            #     🔴 navigatorWebdriver  navigator.webdriver = true indicates…
            #     🔴 exposeFunctionLeak  You're using unpatched Playwright…
            #     🔴 mainWorldExecution  You've called …ByClassName() in the…
            #
            # So the old reader returned READ on every page it ever saw, with
            # `matched_text` byte-identical in both directions. It could not
            # have reported a real detection and could not have reported a
            # clean one: the row was decoration with an adverse label on it.
            #
            # A negative lookbehind cannot rescue that — there is no negator to
            # anchor against ("No leak detected" puts it two words away, and the
            # adverse rows lack the token entirely), which is why this is the
            # one item in the catalogue that does NOT follow the sibling shape.
            #
            # What IS discriminating is the site's own per-row verdict icon,
            # which its renderer assigns from the numeric rating: 🔴 for
            # rating >= 1 (a real detection), 🟢 for rating < 0 (clean), 🟡 for
            # 0.5 (inconclusive) and ⚪️ for 0 (the test was never triggered).
            # Reading the red marker asks the checker for its VERDICT instead of
            # guessing at its wording.
            #
            # ANCHORED TO A VERDICT ROW, not to the character. The first
            # spelling of this was `\uFE0F?\s*(\w+)`, and `\s*` crosses
            # NEWLINES — so a 🔴 anywhere on the page read as a detection:
            #
            #     'Legend: 🔴 means the test caught you.' -> READ 'means'
            #     'Icons:\n🔴\nfailed\n'                  -> READ 'failed'
            #
            # An adverse FINGERPRINT row, READ, with a real match and a real
            # quote behind it — the exact failure this file's docstring says
            # the subsystem exists to prevent, reintroduced by the fix for it.
            # Today's captures carry no 🔴 outside the table, so it was latent
            # rather than live; "the current capture happens not to contain
            # the character" is the same reasoning that left `\bdetected\b` in
            # place, and it is not a reason to leave a reader able to invent a
            # verdict from wording it was not written for.
            #
            # The row shape is `<marker> <name>\t<time>\t<note>`, so the
            # trailing `\t` is what makes this a ROW rather than a sighting.
            # `[ \t]` and NOT `\s` for the intra-row separators, so a match can
            # never span two rows.
            TextItem("detected",
                     "\U0001F534" + r"\uFE0F?[ \t]*(\w+)[ \t]*\t",
                     FINGERPRINT, adverse=True, capture=True, capture_all=True,
                     note="Captures the NAMES of EVERY red-flagged test "
                          "(e.g. 'exposeFunctionLeak,mainWorldExecution'), "
                          "because 'which tests caught us' is the actionable "
                          "half — a bare True would send someone back to the "
                          "page to find out. ALL of them and not the first: "
                          "the comparator reads (state, value), so a first-"
                          "match value would report a browser going from one "
                          "detection to three as no change at all. Covers the "
                          "modern CDP leaks (runtime enable, source url, "
                          "exposed function)."),
            # The liveness half, and it is load-bearing rather than tidy.
            #
            # Reading a MARKER makes ABSENCE the clean verdict, so a page whose
            # JavaScript never populated the table would read as a perfect
            # browser. The static shell really does survive the tier's
            # settle guard — it carries the title, the intro and the table
            # HEADER, so `text.strip()` is non-empty and the row is not
            # recorded unobtainable.
            #
            # This item matches a rendered verdict row of ANY colour, so it is
            # READ exactly when the table exists. It is the "the instrument was
            # working" arm: an adverse ABSENT beside this one ABSENT is not a
            # clean page, it is a page that never rendered.
            #
            # Row-anchored with `[ \t]` for the same reason the adverse arm is
            # (PS-121): this was first written with `\s*`, which crosses
            # NEWLINES, so a legend rendered as a LIST satisfied it —
            # `'Legend:\n🟢\npassed\n\t'` read as a rendered verdict table.
            # That direction is quieter than the adverse one and worse to
            # trust: a shell page claiming its table rendered, sitting beside
            # an adverse ABSENT, is read as a CLEAN browser. This arm's whole
            # job is to be the reading you can believe when the other one is
            # silent, so it may not be the looser of the two.
            TextItem("verdicts_rendered",
                     "[\U0001F534\U0001F7E1\U0001F7E2\u26AA]"
                     + r"\uFE0F?[ \t]*\w+[ \t]*\t",
                     FINGERPRINT, adverse=False,
                     note="Proves the verdict table rendered at all. Without "
                          "it an unsettled page reads as a clean one, because "
                          "for a marker-based item ABSENT is the good news."),
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
            # The four "No X detected" verdicts. Each adverse pattern declares
            # its negator, because the CLEAN page contains the adverse phrase
            # verbatim, prefixed by "No". Declared as DATA rather than spelled
            # inline as `(?<!no )`: the lookbehind is fixed-width and so could
            # only ever match a SINGLE SPACE, which made all four of these fire
            # on a clean page whose "No" was split off by a newline — exactly
            # what inner_text produces from pixelscan's component tree. See the
            # negation trap on TextItem for the measurement (PS-119).
            TextItem("proxy_detected", r"proxy detected", EXIT,
                     negated_by="no"),
            TextItem("masking_detected", r"masking detected",
                     FINGERPRINT, negated_by="no"),
            TextItem("automation_detected",
                     r"automated behaviou?r detected", FINGERPRINT,
                     negated_by="no"),
            TextItem("timezone_spoofed", r"timezone spoofed", EXIT,
                     negated_by="no",
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
            # --- the GPU, as TWO rows -------------------------------------
            #
            # These used to be one HOST-sorted row written off as "no GPU on
            # this runner". That exemption is WITHDRAWN (owner, 2026-08-22,
            # PS-10): the engine is expected to present a plausible GPU
            # wherever it runs, so both rows are FINGERPRINT and a red on
            # either is a PRODUCT finding filed against undetectable-masking.
            #
            # Split because the fixes are different. Measured on the real page
            # (tests/fixtures/checker-pages/pixelscan.txt:209-218), pixelscan
            # publishes BOTH under "Hardware": the strings it was TOLD, and
            # hashes it computed from PIXELS IT DREW ITSELF.
            TextItem("webgl_vendor", r"webgl vendor\s+([^\n]+)", FINGERPRINT,
                     adverse=False, capture=True, vector=GPU_CLAIMED,
                     note="What the renderer CLAIMS TO BE — a string persona "
                          "chose to present. Measured `Google Inc. (NVIDIA)`."),
            TextItem("webgl_renderer", r"webgl renderer\s+([^\n]+)",
                     FINGERPRINT, adverse=False, capture=True,
                     vector=GPU_CLAIMED,
                     note="The claimed WEBGL_debug_renderer_info string. "
                          "Captured as a VALUE, not matched against a "
                          "software-rasteriser pattern: `SwiftShader` or "
                          "`llvmpipe` here is a real finding, but so is a "
                          "plausible card that disagrees with the DECLARED "
                          "MACHINE in the record header, and only a value can "
                          "show the second. `[^\\n]+` to the line end — the "
                          "measured string contains `Intel(R)`, and a `[^)]+` "
                          "form truncates inside it."),
            # THE OTHER VECTOR. persona does not choose these: they fall out of
            # whatever rasteriser really drew the frame. A believable renderer
            # string beside a hash produced by software rendering is the "the
            # string is right but the render gives us away" case the owner
            # called a defect rather than an accepted limit — and NEITHER ROW
            # ALONE CAN SHOW IT, which is the whole reason they are separate.
            TextItem("webgl_hash", r"webgl hash\s+([0-9a-f]{8,})", FINGERPRINT,
                     adverse=False, capture=True, vector=GPU_RENDERED,
                     note="Computed by pixelscan FROM PIXELS. Not a string "
                          "persona can edit — this is what the GPU actually "
                          "produced."),
            TextItem("canvas_hash", r"canvas hash\s+([0-9a-f]{8,})",
                     FINGERPRINT, adverse=False, capture=True,
                     vector=GPU_RENDERED,
                     note="Canvas rendering, same vector as webgl_hash: drawn "
                          "by the real rasteriser, not declared."),
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
            # --- the GPU, as TWO rows -------------------------------------
            #
            # WAS one HOST-sorted row carrying a long note that called this "a
            # KNOWN-ENVIRONMENTAL red ... never raised as a new defect". THAT
            # NOTE IS WITHDRAWN, by owner decision of 2026-08-22 recorded in
            # PS-10: there will be no dev-VM and no GPU machine, and the engine
            # is expected to present a plausible GPU wherever it runs. So both
            # rows are FINGERPRINT and a red on either is a PRODUCT finding,
            # filed against undetectable-masking with the reading attached.
            #
            # The old note's most useful observation survives the retag and is
            # worth keeping in front of whoever reads this: NO CHECKER IN THIS
            # MATRIX FLAGGED the renderer. The claimed string alone reads as
            # plausible. That is exactly why the claim is recorded as a value
            # to compare rather than as a verdict to trust — and exactly why
            # the rendered hashes below are read as their own vector, since a
            # checker that believes the string may still be fingerprinting the
            # pixels.
            # `(?<![a-z])` IS LOAD-BEARING — measured, not defensive style.
            # CreepJS also prints `webgpu: unsupported` (creepjs.txt:207), and
            # a bare `gpu:` matches INSIDE it. Today the real `gpu:` block
            # renders first so first-match-wins hides the bug; on a page where
            # the gpu block does NOT populate — which CreepJS does, its blocks
            # are notoriously partial — the pattern falls through to `webgpu:`
            # and records `unsupported` AS THE GPU VENDOR. That is the quiet
            # failure direction: a missing verdict recorded as a real value,
            # rather than the loud ABSENT it should be.
            TextItem("gpu_vendor",
                     r"(?<![a-z])gpu:\s*(?:confidence:[^\n]*\n)?([^\n]+)",
                     FINGERPRINT, adverse=False, capture=True,
                     vector=GPU_CLAIMED,
                     note="The claimed vendor, measured `Google Inc. "
                          "(NVIDIA)`. CreepJS prints `gpu:` then an optional "
                          "`confidence:` line before the value, so the "
                          "confidence line is skipped rather than captured as "
                          "the vendor. Anchored so `webgpu:` cannot match."),
            TextItem("gpu_renderer", r"(angle \([^\n]+\))", FINGERPRINT,
                     adverse=False, capture=True, vector=GPU_CLAIMED,
                     note="What the renderer CLAIMS TO BE. Captured as a value "
                          "rather than matched against a software-rasteriser "
                          "pattern, because a PLAUSIBLE card that disagrees "
                          "with the declared machine in the header is just as "
                          "much a finding as `SwiftShader`, and only a value "
                          "shows the second. The capture takes the WHOLE "
                          "ANGLE(...) string to the line end: a `[^)]+` form "
                          "stopped at the first ')' — which falls INSIDE "
                          "'Intel(R)' — and recorded the truncated 'Intel, "
                          "Intel(R'. A truncated renderer silently defeats "
                          "the comparison this row exists for."),
            # THE OTHER VECTOR — hashes CreepJS computed from pixels it drew.
            # persona cannot edit these: they are what the rasteriser really
            # produced. Measured at creepjs.txt:92-96 and :119-120.
            TextItem("webgl_image_hash", r"images:\s*([0-9a-f]{8,})",
                     FINGERPRINT, adverse=False, capture=True,
                     vector=GPU_RENDERED,
                     note="WebGL imagery as actually rendered."),
            TextItem("webgl_pixel_hash", r"pixels:\s*([0-9a-f]{8,})",
                     FINGERPRINT, adverse=False, capture=True,
                     vector=GPU_RENDERED,
                     note="The sharpest of the rendered rows: a hash straight "
                          "off the drawn pixels. A believable claimed string "
                          "beside a pixel hash produced by software rendering "
                          "is the 'the string is right but the render gives us "
                          "away' defect, and neither row alone can show it."),
            # ANCHORED TO THE CANVAS BLOCK — measured, not defensive style.
            # A bare `data:` matches TWICE on the real page: Canvas's
            # `data: 8d1ce292` (creepjs.txt:120) and AUDIO's `data:2dcdf6c2`
            # (creepjs.txt:157). It reads correctly today only because Canvas
            # happens to render FIRST and first-match-wins — i.e. by luck of
            # ordering, not by construction. If CreepJS reorders its blocks, or
            # the Canvas block fails to populate while Audio does, this row
            # silently records THE AUDIO HASH as the canvas rendering. That is
            # the quiet failure direction: a READ reading carrying a corrupted
            # value, on a row whose entire purpose is being compared run to run.
            TextItem("canvas_data_hash",
                     r"canvas\s*[0-9a-f]{8,}\s*data:\s*([0-9a-f]{8,})",
                     FINGERPRINT, adverse=False, capture=True,
                     vector=GPU_RENDERED,
                     note="Canvas as actually rendered (CreepJS's `Canvas` "
                          "block). Same vector as the pixel hash. Anchored to "
                          "the `Canvas <hash>` heading so the AUDIO block's "
                          "own `data:` cannot be captured as a canvas render."),
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
