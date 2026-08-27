"""Drive persona's OWN engine through the exit and read the prose checkers.

The browser tier is the half of the matrix that needs a page to actually run.
Three things about it are settled by measurement, not by preference, and each
one is a way this module could have been quietly wrong.

1. It must be persona's engine, not stock Chromium
--------------------------------------------------
Stock Chromium ``151`` is present in this container and is NOT the product. A
reading taken under it describes stock Chromium and answers nothing about
persona. This module launches ``invisible_playwright`` — the patched Firefox
the app itself launches (``services/browser/invisible_launch.py``) — pinned in
``pyproject.toml``.

2. The engine authenticates to the SOCKS5 proxy ITSELF
-------------------------------------------------------
The knowledge article and the ticket both expect a local ``ProxyBridge`` here,
on the grounds that Chromium cannot carry SOCKS5 credentials on
``--proxy-server``. That is true OF CHROMIUM and it is why the bridge exists —
but this tier runs the FIREFOX engine, and ``invisible_launch._proxy_dict``
records the difference in one line: *"invisible_playwright does SOCKS5-with-auth
natively, so no local bridge is needed (unlike Camoufox)"*. Measured here: the
engine reached the Polish exit with a ``{server, username, password}`` proxy
dict and no relay in the picture.

So no bridge is started. That is not a shortcut around PS-25's hardened
listener — it is declining to stand up a *second* local listener that nothing
would connect to. The rule the article is really protecting ("do not write a
weaker relay beside the hardened one") is honoured by starting NO relay at all.
The credential still never touches a command line: it goes in the proxy dict.

3. ``page.evaluate`` is not available, so the page is read as TEXT
------------------------------------------------------------------
Measured: ``Page.evaluate: call to eval() blocked by CSP`` on a real checker
page under the engine's context, and ``bypass_csp`` in the context kwargs does
not lift it. Every prose checker in the catalogue is therefore read through
``inner_text``, which is also what the catalogue's patterns are written
against. This is a real constraint on what this tier can answer and it is
recorded rather than worked around.

And the settle time is load-bearing
------------------------------------
``--dump-dom``-style "load and read" returns before the verdict exists;
Pixelscan and CreepJS take 45-60 seconds to settle. Each checker carries its
own ``settle_seconds`` and this module waits it out. A page read too early
records an empty verdict as though it were the answer — the same class of
defect as a skipped test reporting green.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Callable

from ...utils.proxy_parser import ProxyUrlUnparseable, engine_proxy_dict
from .checkers import BROWSER_CHECKERS, Checker, ENGINE_EXIT_CHECKER
from .exit_guard import EXPECTED_COUNTRY
from .masking_layer import LayerReport
from .matrix import (
    READ,
    Reading,
    extract_text_item,
    readings_for_unread_checker,
)

# A page that has not loaded in this long is not going to. Generous because it
# is a mobile exit carrying a full page load, and because the alternative to
# waiting is recording an unobtainable reading — the wait is cheaper than the
# lost row.
NAVIGATION_TIMEOUT_MS = 90_000

# --- the two engines --------------------------------------------------------

FIREFOX = "firefox"
CHROMIUM = "chromium"

# Both engines persona ships, and the order the matrix runs them in. Firefox
# first because it is the engine every prior record was taken under, so a
# two-engine run's first half is directly comparable to the existing baseline.
ENGINES: "tuple[str, ...]" = (FIREFOX, CHROMIUM)

# The machines a run may declare. These are the values chromium's
# ``--fingerprint-platform`` accepts; they are the OS the profile PRESENTS, not
# the host it runs on.
DECLARED_MACHINES: "tuple[str, ...]" = ("windows", "macos", "linux")

DEFAULT_DECLARED_MACHINE = "windows"

# What the Firefox engine declares no matter what it is asked for. Not a
# default and not a preference: ``InvisiblePlaywright`` takes no OS/platform
# argument at all, and the engine presents Windows regardless — the behaviour
# ``services/browser/process.py`` records for the product as #211 ("unlike
# stealth-Firefox, which reports Windows regardless"). Measured here too: a
# Firefox session asked for nothing reported
# ``Windows NT 10.0; Win64; x64 ... Firefox/151.0``.
FIREFOX_DECLARES = "windows"


def declared_machine_for(engine: str, requested: str = "") -> str:
    """The machine an engine ACTUALLY declares, for the record header.

    This function exists so the record can never claim a machine that was not
    presented. The two engines differ and the difference is not symmetric:

    * **chromium** honours ``--fingerprint-platform``, so it declares what was
      requested.
    * **firefox** cannot be asked — there is no parameter — and always presents
      Windows. Asking it for macos and recording "macos" would be a fabricated
      row: the record would state a machine the engine never declared, and a
      later comparison would read the resulting difference as a coupling.

    So a firefox run reports ``windows`` whatever was requested, and the CLI
    says out loud that the request was not honoured rather than silently
    dropping it. Reporting the asymmetry is the point; smoothing it over is the
    failure this ticket names.
    """
    if engine == FIREFOX:
        return FIREFOX_DECLARES
    return requested or DEFAULT_DECLARED_MACHINE


def honours_declared_machine(engine: str) -> bool:
    """True when asking this engine for a machine actually changes what it
    presents. False for firefox — see :func:`declared_machine_for`."""
    return engine != FIREFOX


class EngineUnavailable(RuntimeError):
    """persona's engine could not be launched here.

    Its own class so a caller can tell "the browser tier did not run" from "a
    checker did not answer". The first makes every browser-tier row
    unobtainable for one shared reason; the second is per-checker.
    """


def _proxy_dict(proxy_url: str) -> dict:
    """Turn the credential into the engine's proxy dict.

    Delegates to ``utils.proxy_parser.engine_proxy_dict`` — the ONE owner,
    shared with ``services/browser/invisible_launch._proxy_dict``, which is the
    launch path the app itself uses. The ``socks5h`` scheme this project reads
    its credential in is normalised back to ``socks5`` for the engine, which
    takes remote resolution from the ``network.proxy.socks_remote_dns`` pref
    instead of from the scheme (set in :func:`_prefs`); ``socks5h`` in a
    ``server=`` value is a curl-ism the browser would reject outright.

    ⚠️ THE DOCSTRING THAT USED TO SIT HERE CLAIMED THIS "mirrors" THE LAUNCH
    PATH, AND IT DID NOT (PS-217). This copy's regex carried ``socks5h?`` and
    the launch path's did not, so the scheme this project actually reads its
    credential in worked HERE and silently lost its credentials THERE — the
    verify-side copy held the fix the shipped path lacked, which is the reverse
    of the usual drift and is exactly why a parity claim in prose is worth
    nothing. Both now call one function, so the claim is structural rather than
    asserted: they cannot disagree because there is only one of them.

    Both regexes were ALSO wrong on the two delimiter axes (a ``:`` in the
    username, an ``@`` in the password) — see the shared parser for the detail.
    Lifting this copy into the launch path would have fixed the scheme and left
    those two.

    Raises :class:`EngineUnavailable` — not the shared parser's own error — so
    the caller's contract is unchanged: an unusable credential fails the TIER
    here rather than launching an engine that quietly goes direct.
    """
    try:
        out = engine_proxy_dict(proxy_url)
    except ProxyUrlUnparseable as e:
        raise EngineUnavailable(
            f"the proxy credential is not in a form the engine can take ({e})"
        ) from e
    if out is None:
        # An empty credential. The caller only reaches this for a truthy
        # proxy_url, so this is unreachable there — but the tier must never
        # answer "no proxy" with a direct engine, so it is a refusal here too.
        raise EngineUnavailable("no proxy credential was supplied")
    return out


def _prefs() -> dict:
    """Prefs the reading depends on.

    ``socks_remote_dns`` is the browser-side counterpart of ``socks5h``: with
    it off, Firefox resolves the checker's hostname LOCALLY and a DNS query
    naming the checker leaves this machine on the operator's own resolver,
    while the page itself still loads through the exit. The reading would look
    perfect and would have leaked exactly what the product forbids. The app's
    own launch path sets the same pref (``invisible_launch.py``).

    ``failover_direct`` is the one that makes a *silent* wrong reading
    possible. With it TRUE, Firefox answers a dead or refusing SOCKS proxy by
    retrying the request DIRECTLY — the page then loads, the verdicts parse,
    every row lands as READ, and the record is a reading of the operator's real
    address. It is asserted here rather than assumed: measured on this build,
    the engine's own ``_BASELINE`` already sets it False and ``extra_prefs`` is
    applied LAST (``invisible_core/prefs.py``: ``translate_profile_to_prefs``
    starts from ``_BASELINE`` and ends with ``_apply_caller_overlay``), so this
    re-declaration cannot weaken it and pins it against an upstream flip on a
    rebase. Exactly the reasoning the engine itself records for
    ``socks5_remote_dns``: the safe behaviour being somebody else's default is
    why it survived unasserted.

    ``devtools.jsonview.enabled`` is off because the engine-exit proof reads
    RAW JSON. With the viewer on, Firefox renders a JSON body as a DOM tree
    with unquoted keys, the quoted pattern does not match, and the proof reads
    ABSENT. That direction is fail-SAFE (an unmatched proof refuses the tier
    rather than passing it), but the raw form is what the pattern is written
    against and the viewer is a UI convenience nothing here wants.
    """
    return {
        "network.proxy.socks_remote_dns": True,
        "network.proxy.failover_direct": False,
        "devtools.jsonview.enabled": False,
    }


class ExitNotProvenInEngine(RuntimeError):
    """The ENGINE's own exit could not be proven.

    Separate from :class:`EngineUnavailable` because the causes are different
    and so is what a reader should conclude: the engine started fine, it simply
    could not be shown leaving through the exit we are entitled to read from.
    Both make the whole tier unobtainable, and neither is ever recoverable by
    reading the pages anyway.
    """


def _observe_engine_exit(
    live,
    *,
    expected_country: str = EXPECTED_COUNTRY,
    checker: Checker = ENGINE_EXIT_CHECKER,
) -> "tuple[str, str]":
    """Make the ENGINE observe its own exit, and check the country.

    Returns ``(page_text, country)`` — the TEXT rather than readings, so the
    engine-exit rows are extracted by the same pure ``readings_from_texts``
    path as every other browser row. One extraction path, so the proof cannot
    drift from the thing it is proving.

    Raises :class:`ExitNotProvenInEngine` when the engine may not go on to read
    a checker page.

    This is the browser tier's half of the run's first outcome. The Python
    fetcher's proof (``exit_guard.prove_exit``) was made on a DIFFERENT SOCKET
    IN A DIFFERENT PROCESS and does not transfer: an engine whose proxy
    silently failed would render every page and parse every verdict just the
    same. "The pages rendered" is not evidence the engine used the proxy, for
    the identical reason "it returned 200" is not.
    """
    # MORE THAN ONE PROVIDER, tried in order until one ANSWERS WITH A COUNTRY.
    #
    # PS-128 measured the single-oracle version failing closed on a healthy
    # exit: ipinfo.io answered `HTTP 429 Rate limit hit` through the mobile
    # exit (the limit attaches to the exit's SHARED address, so it is neither
    # ours to clear nor retryable), and because this is the tier's
    # PRECONDITION, all 37 browser rows in all 4 configurations went
    # unobtainable. The run refused itself over a provably Polish exit.
    #
    # Redundant for REACHABILITY ONLY. A provider that answers WITH A COUNTRY
    # is authoritative and the loop stops there — including when that country
    # is wrong, which still ends the run below. Asking a second provider after
    # a wrong-country answer would be shopping for a friendlier oracle, which
    # is how a fallback turns into a way to launder a bad exit.
    urls = tuple(getattr(checker, "urls", None) or (checker.url,))
    text = ""
    country = ""
    failures = []
    # Whether ANY provider's page loaded at all. This is what separates "the
    # engine could not reach its exit" from "it reached one and got no country
    # out of it" in the refusal below.
    loaded_any = False
    for url in urls:
        page = live.new_page()
        try:
            page.goto(
                url,
                timeout=NAVIGATION_TIMEOUT_MS,
                wait_until="domcontentloaded",
            )
            candidate_text = page.inner_text("body")
        except Exception as exc:
            failures.append(f"{url}: {type(exc).__name__}: {exc}")
            continue
        finally:
            try:
                page.close()
            except Exception:
                pass

        loaded_any = True

        # Extracted with the SAME pure function that reads every other browser
        # row, so the proof and the recorded rows can never disagree about what
        # the page said. Only the country is consumed here; the readings
        # themselves are rebuilt downstream from the text.
        by_id = {
            item.id: extract_text_item(checker, item, candidate_text)
            for item in checker.items
        }
        country_reading = by_id.get("country")
        candidate_country = ""
        if country_reading is not None and country_reading.state == READ:
            candidate_country = str(country_reading.value or "").upper()

        if not candidate_country:
            # Loaded, but carried no country — a rate-limit body, an error
            # page, or a dialect this row cannot read. Not an answer, so try
            # the next provider rather than refusing the whole tier on it.
            failures.append(f"{url}: answered, but carried no country")
            continue

        text = candidate_text
        country = candidate_country
        break

    if not country:
        detail = f"{len(urls)} provider(s) tried: {'; '.join(failures)}"
        # TWO DIFFERENT CAUSES, AND THE MESSAGE SAYS WHICH. "Nothing would
        # load" and "it loaded and did not say" mean opposite things about
        # whether the exit is usable — the first points at the proxy or the
        # engine, the second at a rate-limit body or a dialect this row cannot
        # read. Collapsing them into one sentence is what makes an operator
        # chase the wrong half.
        if not loaded_any:
            raise ExitNotProvenInEngine(
                "the engine could not observe its own exit "
                f"({detail}). Refusing to read any checker through an engine "
                "that has not been shown leaving through the expected exit — "
                "the pages would render either way."
            )
        raise ExitNotProvenInEngine(
            "the engine's exit observation carried no country, so the engine's "
            "exit is unproven. Recording the checker pages anyway would record "
            f"an address nobody has established. {detail}"
        )
    if country != expected_country.upper():
        raise ExitNotProvenInEngine(
            f"the ENGINE left through {country}, expected "
            f"{expected_country.upper()} — even though the Python fetcher's "
            "exit was proven. That is two different clients on two different "
            "sockets, which is exactly why this is checked separately. A "
            "checker folds geography into its cross-checks, so a reading taken "
            "from the wrong country is not a worse reading, it is a "
            "meaningless one."
        )
    return text, country


def _read_open_session(
    live,
    *,
    checkers: "tuple[Checker, ...]",
    sleep: Callable[[float], None],
) -> "dict[str, dict]":
    """Prove the exit, then load and read every checker, on an OPEN session.

    Engine-agnostic on purpose. It touches ``live`` only through ``new_page()``
    and a page's ``goto`` / ``inner_text`` / ``close``, which is the whole
    contract both engines satisfy — so the precondition, the settle wait, the
    per-checker isolation and the "ask the exit checker once" rule are written
    ONCE and cannot come out different on one engine than the other.

    That sharing is the point rather than tidiness: a Chromium-specific copy of
    this loop is exactly how one engine would quietly acquire a reading path
    the other lacks, and a record built from two dialects is not comparable.

    A SESSION THAT DIES MID-RUN ENDS THE RUN, and says so once
    ----------------------------------------------------------
    This loop used to catch a failed ``new_page()`` per checker and ``continue``.
    Measured consequence (PS-110): ``pixelscan.net`` crashed the Chromium
    renderer, every subsequent ``new_page()`` raised ``TargetClosedError``, and
    the run recorded nine CreepJS rows — plus every other row after the crash —
    as ordinary unobtainables. Forty-five rows lost to ONE dead context are
    indistinguishable, in that record, from forty-five checkers that each
    independently declined, and a later comparison against a healthy record
    reads them as forty-five moved vectors.

    So a ``new_page()`` failure is treated as what it is: not this checker's
    answer, but the end of the session. Every remaining checker is marked
    ``never_asked`` with the SAME cause, and the loop stops rather than calling
    ``new_page()`` another forty times on a context that cannot make pages.

    The two failure kinds are kept apart deliberately, because only one of them
    is a reading about a checker:

    * ``goto`` / ``inner_text`` raising is THAT CHECKER's failure — it was
      asked and could not answer. The run continues; one checker refusing must
      not take the others down.
    * ``new_page()`` raising is THE SESSION's failure. Nothing after it was
      ever asked.

    Re-establishing the browser and carrying on was the other option the ticket
    left open. It is not taken here: a relaunch mid-run would silently change
    the thing being measured (a fresh context, possibly a fresh seed-derived
    identity) halfway through a record that claims to be one reading, and a
    record built from two browsers is the same class of artifact as one built
    from two engines' dialects. Stopping and saying so keeps the record honest
    about what it is.
    """
    out: "dict[str, dict]" = {}

    # THE TIER'S OWN PRECONDITION, and it runs before a single checker page is
    # loaded. Not after, and not alongside: a checker that has already been
    # asked cannot be un-asked, and the whole point is that the operator's real
    # address must never reach one.
    try:
        exit_text, _country = _observe_engine_exit(live)
    except ExitNotProvenInEngine as exc:
        # Every row in the tier becomes unobtainable with this reason,
        # including the engine-exit rows themselves. Deliberately NOT an
        # EngineUnavailable: the engine was fine, it was the exit that could
        # not be shown.
        return {checker.id: {"error": str(exc)} for checker in checkers}
    out[ENGINE_EXIT_CHECKER.id] = {"text": exit_text}

    for index, checker in enumerate(checkers):
        if checker.id == ENGINE_EXIT_CHECKER.id:
            # Already observed above — asking twice would record a second,
            # later address for the same run and make the record
            # self-contradicting on a rotating exit.
            continue
        try:
            page = live.new_page()
        except Exception as exc:
            # THE SESSION IS GONE, not this checker. See the docstring: the
            # remaining checkers are marked as never asked, sharing this one
            # cause, and the loop stops rather than raising the same error
            # against every catalogue entry in turn.
            cause = f"{type(exc).__name__}: {exc}"
            for remaining in checkers[index:]:
                if remaining.id == ENGINE_EXIT_CHECKER.id:
                    continue
                out[remaining.id] = {
                    "error": (
                        "the browser session ended mid-run and this checker "
                        f"was NEVER ASKED — {cause}"
                    ),
                    "never_asked": True,
                }
            return out
        try:
            page.goto(
                checker.url,
                timeout=NAVIGATION_TIMEOUT_MS,
                wait_until="domcontentloaded",
            )
            # The verdict does not exist at load time. Waiting is the whole
            # difference between a reading and an empty page recorded as one.
            sleep(checker.settle_seconds)
            out[checker.id] = {"text": page.inner_text("body")}
        except Exception as exc:
            # THIS CHECKER's failure — it was asked and could not answer. The
            # run continues: one checker refusing must not take the others
            # down. Note this is where a crash-on-read (the PS-110 pixelscan
            # case) is first seen; the SESSION death it causes is detected at
            # the next new_page(), which is the honest place for it, because
            # this checker really was asked.
            out[checker.id] = {"error": f"{type(exc).__name__}: {exc}"}
        finally:
            try:
                page.close()
            except Exception:
                pass
    return out


@contextmanager
def firefox_session(
    proxy_url: str,
    *,
    seed: int,
    install_layer: bool = True,
    layer_vectors: "tuple[str, ...] | None" = None,
    layer_sink: "Callable[[LayerReport], None] | None" = None,
):
    """persona's Firefox engine, layer installed, as a context manager.

    THE ONE COPY of the Firefox launch-and-install wiring. It is a shared
    function rather than two similar blocks because a second copy of this
    sequence is the exact hazard :mod:`masking_layer` names as load-bearing —
    *"a second copy of the spoof set, drifting from the one the product
    launches, would reproduce the very defect this module exists to close"* —
    and the INSTALL wiring can drift the same way one level up. Both callers
    reach the page through here:

    * :func:`_read_page_texts_firefox`, the real checker run, and
    * :mod:`layer_differential`, the local-page demonstration,

    so a step added here tomorrow is a step both of them take. If the
    differential had its own launch, it could keep passing while the harness's
    own path lost the layer — proving something about a path no real run has.

    Yields the live CONTEXT, never the Browser. That distinction is what makes
    the layer exist at all: ``InvisiblePlaywright.__enter__`` hands back a
    ``Browser`` here (no ``profile_dir`` is set), and a playwright Browser has
    NO ``add_init_script`` — registering the spoofs on it installs nothing,
    silently. Measured, by running the differential for real and reading
    ``installed: []`` off the report. A Browser's ``new_page()`` also opens a
    THROWAWAY context per call, so even a working registration would not
    survive to the page. :func:`context_for` takes the one explicit context the
    spoofs can live in.

    THE LAYER IS INSTALLED AFTER ``__enter__`` AND BEFORE THE FIRST PAGE LOAD,
    and both halves are load-bearing. After, because ``add_init_script`` needs
    the live context the engine only hands back on entry. Before, because the
    first thing the checker loop does is prove the exit by loading a page — a
    spoof registered after that has already missed a document the run reads.

    ``proxy_url`` empty means NO PROXY, and that is only ever right for a venue
    with no exit (the loopback differential). A checker run always passes its
    credential: a reading taken without the exit describes the operator's REAL
    address while every verdict parses.

    ``install_layer=False`` is the differential's OTHER arm and exists for no
    other purpose: it produces the un-widened reading (engine only) so a caller
    can show the SAME harness reading differently with and without persona's
    masking. It is never the default and the record always says which it was.

    ``layer_vectors`` narrows the layer to a SUBSET of persona's spoofs, and is
    the third arm: not "product" and not "packaged engine", but the product with
    one vector removed. It exists for PS-119 step 3 — when a live checker names
    the layer, the way to find WHICH spoof it saw is to subtract them one at a
    time and re-read, rather than to read the generated source for something
    that looks suspicious. ``None`` is the full product set and is the default,
    because a subset reading does not describe the product either.
    """
    from .masking_layer import (
        DEFAULT_LOCALE,
        absent_layer,
        context_for,
        install_firefox_layer,
    )

    try:
        from invisible_playwright import InvisiblePlaywright
    except Exception as exc:  # pragma: no cover - import shape varies by env
        raise EngineUnavailable(
            f"persona's engine (invisible_playwright) is not importable: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    # THE HARNESS MUST DECLARE THE LOCALE THE WAY THE PRODUCT DOES, and this
    # line is not cosmetic parity — its absence was a measured masking tell.
    #
    # `invisible_launch` sets `kwargs["locale"]` on every product launch
    # (defaulting an unset profile to "en-US"), and that value is what reaches
    # Firefox's `intl.accept_languages` — i.e. the ACCEPT-LANGUAGE HEADER. The
    # harness passed no locale at all, so the header carried the HOST OS locale
    # while the masking layer pinned navigator.language/Intl to DEFAULT_LOCALE.
    #
    # Header de-DE + JS en-US is precisely the "internal contradiction a scanner
    # flags as masking" that `_language_override_script`'s own docstring exists
    # to prevent — and PS-119 measured it: on a live pixelscan reading through a
    # proven exit, `masking_detected` fired with the layer installed and went
    # ABSENT when the locale vector alone was subtracted, while a 314-observable
    # integrity sweep found the patched JS surface structurally IDENTICAL to the
    # unpatched engine. The tell was never the spoof's shape; it was the harness
    # asking the layer to spoof against a header the harness had not set.
    #
    # So a reading taken without this is a reading of the HARNESS's
    # contradiction, not of the product's surface.
    kwargs: "dict[str, Any]" = {
        "headless": True,
        "humanize": False,
        "locale": DEFAULT_LOCALE,
    }
    if proxy_url:
        # Only when there IS an exit. `_proxy_dict` refuses a value it cannot
        # parse, so an unusable credential fails here rather than launching an
        # engine that quietly goes direct.
        kwargs["proxy"] = _proxy_dict(proxy_url)
        # The prefs are the browser-side half of the credential (remote DNS,
        # no direct failover). They belong with it: on a loopback page there is
        # no resolution to leak and nothing to fail over to.
        kwargs["extra_prefs"] = _prefs()
    if seed:
        kwargs["seed"] = seed
    try:
        engine = InvisiblePlaywright(**kwargs)
    except Exception as exc:
        raise EngineUnavailable(
            f"could not construct persona's engine: {type(exc).__name__}: {exc}"
        ) from exc

    try:
        with engine as live:
            live, _context_note = context_for(live)
            if install_layer:
                report = install_firefox_layer(
                    live, seed, locale=DEFAULT_LOCALE,
                    vectors=layer_vectors,
                )
            else:
                report = absent_layer(
                    "install_layer=False: this reading is of the PACKAGED "
                    "ENGINE ONLY, with none of persona's masking layer. It is "
                    "the control arm of a differential, not a reading of the "
                    "product."
                )
            if layer_sink is not None:
                layer_sink(report)
            yield live
    except EngineUnavailable:
        raise
    except Exception as exc:
        raise EngineUnavailable(
            f"persona's engine failed during the run: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


def _read_page_texts_firefox(
    proxy_url: str,
    *,
    checkers: "tuple[Checker, ...]",
    seed: int,
    sleep: Callable[[float], None],
    layer_sink: "Callable[[LayerReport], None] | None" = None,
    install_layer: bool = True,
    layer_vectors: "tuple[str, ...] | None" = None,
) -> "dict[str, dict]":
    """The Firefox half: construct the engine, install the layer, run the loop.

    The launch and the layer install live in :func:`firefox_session`, which the
    local-page differential drives too — see that function on why there is
    exactly one copy of that sequence.

    Note what is NOT here: a declared machine. ``InvisiblePlaywright`` takes no
    OS/platform argument at all, and the engine reports Windows regardless —
    the same behaviour ``services/browser/process.py`` records for the product
    ("unlike stealth-Firefox, which reports Windows regardless (#211)"). The
    caller states the machine this engine actually declares rather than passing
    one in and pretending it was honoured.
    """
    with firefox_session(
        proxy_url,
        seed=seed,
        install_layer=install_layer,
        layer_vectors=layer_vectors,
        layer_sink=layer_sink,
    ) as live:
        return _read_open_session(live, checkers=checkers, sleep=sleep)


def _read_page_texts_chromium(
    proxy_url: str,
    *,
    checkers: "tuple[Checker, ...]",
    seed: int,
    declared_machine: str,
    sleep: Callable[[float], None],
    timezone: str = "",
    allow_unsandboxed: bool = False,
    allow_small_dev_shm: bool = False,
    layer_sink: "Callable[[LayerReport], None] | None" = None,
    install_layer: bool = True,
    include_geo: bool = False,
) -> "dict[str, dict]":
    """The Chromium half: the same loop, behind a session that sets itself up.

    Everything that makes Chromium harder than a flag — it cannot authenticate
    to a SOCKS5 proxy so persona's hardened loopback relay carries the
    credential, it is reached over CDP on a port that must be opened, it needs
    a display — lives in :mod:`chromium_tier` and none of it reaches this loop.

    The masking layer is installed there too, and for a structural reason
    rather than a stylistic one: Chromium takes its layer as unpacked
    extensions on ``--load-extension``, so it must be built BEFORE the process
    starts. There is no post-launch equivalent of Firefox's
    ``add_init_script``. The session therefore owns the build and reports what
    it managed, which this function forwards.

    ``ChromiumUnavailable`` is re-raised as :class:`EngineUnavailable` so a
    caller keeps ONE way to say "the engine did not run, every row is
    unobtainable for one shared reason" regardless of which engine was asked
    for. The message is preserved, so the record still names the real cause.
    """
    from .chromium_tier import ChromiumSession, ChromiumUnavailable

    try:
        session = ChromiumSession(
            proxy_url,
            seed=seed,
            declared_machine=declared_machine,
            timezone=timezone,
            allow_unsandboxed=allow_unsandboxed,
            allow_small_dev_shm=allow_small_dev_shm,
            install_layer=install_layer,
            include_geo=include_geo,
        )
    except ChromiumUnavailable as exc:
        raise EngineUnavailable(str(exc)) from exc
    except Exception as exc:
        raise EngineUnavailable(
            f"could not construct persona's chromium engine: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    try:
        with session as live:
            if layer_sink is not None:
                layer_sink(session.layer_report)
            return _read_open_session(live, checkers=checkers, sleep=sleep)
    except EngineUnavailable:
        raise
    except ChromiumUnavailable as exc:
        raise EngineUnavailable(str(exc)) from exc
    except Exception as exc:
        raise EngineUnavailable(
            f"persona's chromium engine failed during the run: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


def read_page_texts(
    proxy_url: str,
    *,
    checkers: "tuple[Checker, ...]" = BROWSER_CHECKERS,
    seed: int = 0,
    engine: str = FIREFOX,
    declared_machine: str = "",
    timezone: str = "",
    allow_unsandboxed: bool = False,
    allow_small_dev_shm: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    layer_sink: "Callable[[LayerReport], None] | None" = None,
    install_layer: bool = True,
    layer_vectors: "tuple[str, ...] | None" = None,
    include_geo: bool = False,
) -> "dict[str, dict]":
    """Load every browser-tier checker once and return its visible text.

    Returns ``{checker_id: {"text": str} | {"error": str}}``. Never raises for
    ONE checker's failure — a checker that refuses the connection is a reading
    about that checker, and it must not take the other five down with it.

    Raises :class:`EngineUnavailable` only when the ENGINE itself could not be
    started, which is the one failure that really is shared by every row.

    ``engine`` selects which of persona's two engines reads the pages. Both
    reach the same shared loop; only the construction differs.

    ``declared_machine`` is the OS the profile presents. It is honoured on
    chromium and IGNORED on firefox, which cannot be asked — see
    :func:`declared_machine_for`, which is what the record states so the
    difference is recorded rather than implied.

    ``allow_unsandboxed`` is the chromium-only waiver for a host that forbids
    the user namespace its sandbox needs. Off by default and never inferred —
    see :func:`chromium_tier.sandbox_available`.

    ``install_layer`` puts persona's OWN masking layer on top of the packaged
    engine, and it defaults to True because a reading without it does not
    describe the product. Passing False produces the engine-only reading
    deliberately, as the control arm of a differential.

    ``layer_vectors`` narrows that layer to a named SUBSET (firefox only), for
    the subtraction arm that finds WHICH spoof a checker reacted to. ``None`` is
    the full product set. Ignored on chromium, which takes its layer as unpacked
    extensions built before the process starts — see :func:`firefox_session`.

    ``layer_sink`` receives the :class:`~.masking_layer.LayerReport` for the arm
    that ran. It is a callback rather than a second return value because this
    function's contract — a dict of page texts — is consumed in several places,
    and the report must reach the record header on the runs that want it
    without changing what every other caller unpacks.
    """
    if engine not in ENGINES:
        raise EngineUnavailable(
            f"unknown engine {engine!r}: persona ships {' and '.join(ENGINES)}"
        )
    if engine == CHROMIUM:
        return _read_page_texts_chromium(
            proxy_url,
            checkers=checkers,
            seed=seed,
            declared_machine=declared_machine or DEFAULT_DECLARED_MACHINE,
            timezone=timezone,
            allow_unsandboxed=allow_unsandboxed,
            allow_small_dev_shm=allow_small_dev_shm,
            sleep=sleep,
            layer_sink=layer_sink,
            install_layer=install_layer,
            include_geo=include_geo,
        )
    return _read_page_texts_firefox(
        proxy_url,
        checkers=checkers,
        seed=seed,
        sleep=sleep,
        layer_sink=layer_sink,
        install_layer=install_layer,
        layer_vectors=layer_vectors,
    )


def readings_from_texts(
    pages: "dict[str, dict]",
    *,
    checkers: "tuple[Checker, ...]" = BROWSER_CHECKERS,
) -> "list[Reading]":
    """Turn captured page texts into readings.

    Pure: no network, no browser. This is the half of the browser tier the test
    suite drives against the committed page fixtures, which is how a pattern is
    proven to read a REAL page the way the catalogue claims.

    A checker missing from ``pages`` is unobtainable with a reason, not
    skipped — the record keeps its full width whatever the run did.

    ``never_asked`` rides through from the page result onto every row it
    produced. It is carried rather than re-derived because only the loop knows
    WHY a checker has no text: from here, a checker the session never reached
    and one that was asked and refused look identical, and that is exactly the
    conflation PS-110 is about.
    """
    out: "list[Reading]" = []
    for checker in checkers:
        page = pages.get(checker.id)
        if page is None:
            out.extend(
                readings_for_unread_checker(
                    checker, "the run produced no result for this checker"
                )
            )
            continue
        if "error" in page or "text" not in page:
            reason = page.get("error", "the run captured no text")
            note = checker.note_unreachable
            if note:
                reason = f"{reason} — {note}"
            out.extend(
                readings_for_unread_checker(
                    checker,
                    reason,
                    never_asked=bool(page.get("never_asked", False)),
                )
            )
            continue
        text = page["text"]
        if not text.strip():
            out.extend(
                readings_for_unread_checker(
                    checker,
                    "the page rendered no visible text (it may not have "
                    "settled); recorded as unobtainable rather than as a page "
                    "that said nothing adverse",
                )
            )
            continue
        for item in checker.items:
            out.append(extract_text_item(checker, item, text))
    return out


def read_browser_tier(
    proxy_url: str,
    *,
    checkers: "tuple[Checker, ...]" = BROWSER_CHECKERS,
    seed: int = 0,
    engine: str = FIREFOX,
    declared_machine: str = "",
    timezone: str = "",
    allow_unsandboxed: bool = False,
    allow_small_dev_shm: bool = False,
    layer_sink: "Callable[[LayerReport], None] | None" = None,
    install_layer: bool = True,
    layer_vectors: "tuple[str, ...] | None" = None,
    include_geo: bool = False,
) -> "list[Reading]":
    """The whole browser tier: launch, INSTALL THE LAYER, load, settle, read.

    An engine that will not start makes every row unobtainable WITH THE REASON
    — the run continues and records that, rather than dying and recording
    nothing, because "we could not run a browser here" is itself a result.

    That guarantee is why ``engine`` is safe to vary: a Chromium that cannot be
    provisioned on some host produces a full-width record of unobtainable rows
    naming the missing engine, never a narrower record and never a silent pass.

    THE ENGINE-UNAVAILABLE PATH REPORTS AN ABSENT LAYER, and that is not
    bookkeeping. If it stayed silent, the record header would carry whatever
    the caller initialised it with — and a header claiming the product's layer
    over a run where no browser started is the exact class of wrong record this
    subsystem exists to prevent.

    ``timezone`` is the PROVEN EXIT'S zone, forwarded to chromium and ignored
    on firefox. Chromium pins nothing of its own, so without it the engine
    reports the HOST clock and the reading contradicts the exit the run just
    proved (PS-132). Firefox needs no such value: given none, its engine
    resolves the zone from the egress IP itself
    (``invisible_launch.py``: "with no timezone it discovers the egress IP"),
    which is why only one of the two engines ever showed this.
    """
    from .masking_layer import absent_layer

    try:
        pages = read_page_texts(
            proxy_url,
            checkers=checkers,
            seed=seed,
            engine=engine,
            declared_machine=declared_machine,
            timezone=timezone,
            allow_unsandboxed=allow_unsandboxed,
            allow_small_dev_shm=allow_small_dev_shm,
            layer_sink=layer_sink,
            install_layer=install_layer,
            layer_vectors=layer_vectors,
            include_geo=include_geo,
        )
    except EngineUnavailable as exc:
        if layer_sink is not None:
            layer_sink(
                absent_layer(
                    f"the engine never started, so no masking layer was "
                    f"installed: {exc}"
                )
            )
        out: "list[Reading]" = []
        for checker in checkers:
            out.extend(readings_for_unread_checker(checker, str(exc)))
        return out
    return readings_from_texts(pages, checkers=checkers)


__all__ = [
    "CHROMIUM",
    "DECLARED_MACHINES",
    "DEFAULT_DECLARED_MACHINE",
    "ENGINES",
    "EngineUnavailable",
    "ExitNotProvenInEngine",
    "FIREFOX",
    "FIREFOX_DECLARES",
    "NAVIGATION_TIMEOUT_MS",
    "declared_machine_for",
    "honours_declared_machine",
    "read_browser_tier",
    "read_page_texts",
    "readings_from_texts",
]
