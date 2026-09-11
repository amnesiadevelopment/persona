import os
import pathlib
import subprocess
import sys
from collections.abc import Callable
from urllib.parse import urlparse

from ...core.config import DATA_DIR
from ...core.logging import get_logger
from ...models.profile import Profile
from ...utils.proxy_parser import parse_proxy_server
from ..bookmark.store import BookmarkStore
from ..cert.store import CertStore
from ..proxy.bridge import ProxyBridge
from ..proxy.errors import (
    ExitCountryUnknownError,
    GeographyDisprovenError,
    GeographyUnknownError,
    LocaleUnderivableError,
    ProxyUnresolvedError,
    TimezoneUnderivableError,
)
from ..proxy.store import ProxyStore
from .bookmarks_seed import seed_bookmarks
from .process_group import popen_in_new_session, reap_process_group
from .audio_ext import build_audio_extension
from .device_ext import (
    build_device_extension,
    device_memory_for,
    hardware_concurrency_for,
    spec_device_memory,
)
from .env_policy import (
    browser_child_cwd,
    pin_child_tmpdir,
    scrub_inherited_environment,
)
from .resolution import parse_resolution, resolve_resolution
from .device_presets import (
    is_mobile_os,
    is_mobile_profile,
    pick_preset,
    pick_touch_points,
)
from .engine_platform import engine_platform_for
from .engine_version import (
    ChromiumVersion,
    EngineVersionUnreadableError,
    installed_chromium_version,
    measuretext_repair_required,
)
from .gpu_ext import build_gpu_extension
from .canvas_ctx_ext import build_canvas_ctx_extension
from .measuretext_ext import build_measuretext_extension
from .mobile_ext import build_mobile_extension
from .webgl_ext import build_webgl_extension
from .geo_ext import build_geo_extension
from .locale_ext import build_locale_extension
from .voice_ext import build_voice_extension
from .native_ext import build_native_extension
from .stealth_ext import build_stealth_extension
from .profile_seed import seed_profile_prefs
from .search_ext import build_search_extension
from .window_entry import app_id_for, write_window_entry

# The locale / timezone / display-scale policy lives in launch_policy.py so both
# engines can share it. Re-exported as bare globals: this module's own call sites
# resolve them here, and tests import (and monkeypatch) them from here.
from .launch_policy import (  # noqa: F401
    _COUNTRY_LOCALE,
    _COUNTRY_TZ,
    _NO_COUNTRY_CODES,
    _WINDOWS_TZ_TO_IANA,
    _host_display_scale,
    _host_timezone,
    _locale_for,
    _offset_zone,
    _proxy_timezone,
    _timezone_for,
    _windows_timezone_key,
    declared_locale,
    host_workarea_dip,
)

logger = get_logger("browser.process")


from ...core.config import ENGINE_DIR
from ...core import platform as _platform

FINGERPRINT_CHROMIUM = os.path.join(
    ENGINE_DIR, _platform.fingerprint_chromium_filename()
)


def _cert_session_for(profile: Profile, profile_dir: str, upstream: str | None):
    """Start an mTLS terminator for the profile's assigned certificate, or None.
    The terminator's own upstream is the profile's real proxy so the exit IP is
    unchanged. Its work dir is under the profile so leaf/PEM material is cleaned
    with the profile.

    Deciding there is NO mTLS session is also a decision about key material.
    ``sweep_key_material`` used to be reachable only from inside
    ``start_cert_session``, so a profile whose certificate was unassigned (an
    ordinary, supported edit — ``models/profile.py``: ``certificate: str | None``)
    stranded a previous session's decrypted client key in the profile's own data
    dir with nothing left in the tree that would ever remove it. Every path out
    of here now sweeps, so the key's lifetime belongs to the directory rather
    than to the one path that happens to start a session.
    """
    from ..cert.terminator import sweep_key_material

    work = os.path.join(profile_dir, ".persona-mtls")
    cert_name = getattr(profile, "certificate", None)
    if not cert_name:
        # No certificate assigned: nothing will start a session for this
        # profile, so this is the last chance to clear an earlier one's residue.
        sweep_key_material(work)
        return None
    from ..cert.manager import start_cert_session

    cert = CertStore().get(cert_name)
    if cert is None:
        # The profile still references a certificate record the operator has
        # deleted. Same reasoning: no session will start, so sweep here.
        sweep_key_material(work)
        # SAY IT ONCE. Until this line, the ONE path that drops the operator's
        # client certificate was the only path here that said nothing: the
        # successful mTLS path logs, and every failure inside
        # start_cert_session logs at error. The launch then succeeds — the
        # browser opens, reaches the site the certificate was meant to
        # authenticate it to, and is simply not recognised. Nothing in that
        # sequence points at a certificate record that quietly stopped
        # resolving.
        #
        # The record does not have to be DELETED to land here: it is also this
        # branch when one malformed record was skipped on load, or the whole
        # certificates.json was quarantined. Both are protections firing
        # correctly, which is exactly why the outcome must be announced rather
        # than inferred.
        #
        # WARNING, not INFO: the successful path is info and the in-session
        # failures are error, and a configured protection silently not applying
        # belongs between them. It also clears the console handler's WARNING
        # floor, so the one place a launch drops a certificate is not the one
        # place that is quiet.
        #
        # THE ADMIN HOST IS NOT NAMED, deliberately — the reason recorded at the
        # successful chromium path below (an internal hostname identifying the
        # operator's infra, landing one line per cert-profile launch in the
        # persistent log + Activity Log) binds here too. There is no session to
        # name one from in any case; the profile and the certificate NAME are
        # what the operator needs to act, and neither is infrastructure.
        logger.warning(
            "mTLS: certificate %r assigned to profile %r was not found — "
            "launching WITHOUT a client certificate",
            cert_name,
            profile.name,
        )
        return None
    return start_cert_session(cert, upstream or None, work)


def _proxy_arg(proxy_url: str | None) -> tuple[str | None, ProxyBridge | None]:
    """Resolve the --proxy-server value, starting a local bridge when the
    upstream proxy needs username/password auth (Chromium can't pass creds).

    Chromium's SOCKS5 client already resolves the destination at the proxy
    (remote DNS) — socks5h is a curl-ism Chromium rejects with
    ERR_NO_SUPPORTED_PROXIES — so the scheme stays socks5.
    """
    if not proxy_url:
        return None, None
    parsed = urlparse(proxy_url if "://" in proxy_url else "socks5://" + proxy_url)
    if parsed.username:
        bridge = ProxyBridge(proxy_url)
        port = bridge.start()
        # Log only the local bridge port. The upstream hostname identifies the
        # proxy provider (and often carries session/geo labels) and would land,
        # one line per launch, in the persistent log + Activity Log.
        logger.info("Proxy bridge started on 127.0.0.1:%s", port)
        return f"socks5://127.0.0.1:{port}", bridge
    return parse_proxy_server(proxy_url), None


def _require_proxy_resolved(profile: Profile, proxy_url: str | None) -> None:
    """Fail CLOSED when a proxy is assigned but unresolvable.

    The entire anti-leak block (--proxy-server, DoH off, WebRTC/QUIC/DNS guards)
    is gated on a resolved proxy_url. If a profile has profile.proxy set but
    resolve() yielded nothing, launching anyway opens a DIRECT clearnet window
    with the real IP/DNS/WebRTC — a total de-anonymization with no error shown.
    A security tool must fail closed: refuse the launch instead.
    """
    if getattr(profile, "proxy", None) and not proxy_url:
        raise ProxyUnresolvedError(
            f"Profile {profile.name!r} has proxy {profile.proxy!r} assigned but it "
            "could not be resolved (deleted/renamed?). Refusing to launch DIRECT."
        )


def _mobile_chromium_version(profile: Profile, preset) -> "ChromiumVersion | None":
    """The Chromium version an Android profile will advertise — or a refusal.

    Fails CLOSED, in the same spirit as ``_require_proxy_resolved`` above. An
    Android profile advertises a Chromium version in three places (the UA, the
    Client Hints brands, the full-version list), and every one of them must be
    the version of the engine ACTUALLY INSTALLED. When that version cannot be
    read, the alternative to refusing is advertising a guessed one — which is
    precisely the engine/masking-layer mismatch a checker notices, shipped
    silently. A refused launch says so and is fixable by an engine check; a
    launched profile with a wrong version is not fixable after the fact,
    because the pages that saw it already saw it.

    iOS profiles return None: real Safari ships no UA-CH and its UA carries no
    Chromium version, so there is nothing to derive and nothing to refuse.

    ⚠️ THIS IS THE MOBILE-UA HELPER AND ITS FAIL-CLOSED SCOPE IS DELIBERATELY
    UNCHANGED. The Client-Hints version every Chromium profile advertises —
    desktop included — is resolved by ``_chromium_brand_version`` below, which
    deliberately SKIPS rather than refusing. ⛔ That difference is intentional
    and must not be "tidied" into consistency: skipping on THIS arm would make
    the layer type a version the engine does not match (a contradiction), while
    skipping on the desktop arm leaves the engine answering all three shapes
    from its own default (coherent, merely less current). Different failure
    modes, different correct answers — see that helper's docstring for the full
    table. The two are also separate because they answer different questions:
    this one decides what goes in ``--user-agent`` (mobile only; desktop passes
    none), that one decides what goes in ``--fingerprint-brand-version`` (every
    Chromium launch).
    """
    if preset is None or preset.os_type == "ios":
        return None
    try:
        return installed_chromium_version()
    except EngineVersionUnreadableError as e:
        raise EngineVersionUnreadableError(
            f"Profile {profile.name!r} is an Android profile, which must advertise "
            f"the installed engine's Chromium version, but that version could not "
            f"be read ({e}). Refusing to launch rather than advertise a guessed "
            f"version the engine underneath does not match — run an engine check "
            f"to record it."
        ) from e


def _chromium_brand_version(profile: Profile) -> "ChromiumVersion | None":
    """The version `--fingerprint-brand-version` advertises, or ``None`` to skip.

    WHAT THE SWITCH BUYS
    --------------------
    Without it the engine does NOT report the version it actually is.
    `002-user-agent-fingerprint.patch`'s ``GetChromiumVersion()`` falls through
    to a HARDCODED table when the switch is absent::

        constexpr const char* kChromiumVersions[] = {
            "144.0.7559.132", "144.0.7559.109", "144.0.7559.96", "144.0.7559.59" };
        ...
        return kChromiumVersions[seed % std::size(kChromiumVersions)];

    So a profile on the 152 engine advertised Client Hints saying **144** while
    its reduced user agent said **152** — pixelscan read that as *"masking
    detected"*, browserscan as *"different browser version"*, creepjs as a
    version lie. That is not drift; it is the engine's documented default
    firing because nothing overrode it.

    ⭐ WHY THIS SKIPS WHERE ITS MOBILE SIBLING REFUSES — A DELIBERATE ASYMMETRY
    --------------------------------------------------------------------------
    ⛔ DO NOT "FIX" THE INCONSISTENCY WITH ``_mobile_chromium_version``. The two
    arms have DIFFERENT FAILURE MODES, so they have different correct answers,
    and this was decided on the owner's ruling of 2026-09-06 after the opposite
    design was tried and rejected.

    An unreadable version is a REACHABLE TRANSIENT STATE, not a broken install —
    ``EngineVersionUnreadableError``'s own docstring says ``version.txt`` may be
    *"absent (a reachable state: the install completeness gate accepts a marker
    OR a version file)"*, which is what a profile launched mid-update sees. So:

    ================  ==========================  ============================
    ..                Android (``--user-agent``)  Desktop (Client Hints)
    ================  ==========================  ============================
    population        a minority of profiles      ~99% of launches
    if we skip        the layer would TYPE a      the engine answers from its
                      version the engine does     OWN built-in default
                      not match
    resulting state   a CONTRADICTION between     UA, brands and full-version
                      the layer and the engine    still agree WITH EACH OTHER
    cost of refusing  one profile does not run    NO CHROMIUM PROFILE RUNS
    ================  ==========================  ============================

    The last two rows decide it. The tell this ticket exists to close is the
    DISAGREEMENT between the shapes; skipping does not reintroduce it, because
    the engine then answers all three shapes from one source of its own. What
    is lost is CURRENCY (the advertised version is the engine's built-in
    default rather than the installed build) — a weaker claim, not an incoherent
    one. Refusing instead would convert a transient, self-healing condition into
    "nothing launches at all" for the overwhelming majority of users.

    On mobile the same skip WOULD be incoherent, so ``_mobile_chromium_version``
    keeps failing closed. Its rationale is unchanged and still correct on its
    own arm.

    ⛔ THERE IS NO FALLBACK CONSTANT HERE, AND THERE MUST NEVER BE ONE.
    "Skip" means PASS NO FLAG and let the engine answer. It does NOT mean
    substituting ``"152.0.7977.75"`` or any other literal — that would
    re-create, in a new place, the hand-written duplication
    ``engine_version.py`` exists to remove, and would go stale INVISIBLY the
    moment the engine moves. The only two outcomes are the READ value or no
    flag at all.

    ⚠️ THE SKIP IS LOGGED, BECAUSE IT IS A DEGRADED STATE.
    A profile launching without this flag advertises the engine's built-in
    version rather than its real one, and an operator wondering why their Client
    Hints look old needs something to find. The log carries the REASON (the
    underlying read failure) and the REMEDY, not merely the fact of a skip.

    ⚠️ THE VALUE IS ``.full``, ESTABLISHED FROM THE PATCH — NOT ASSUMED.
    The engine takes ONE input and fans it out into all three shapes itself::

        chromium_version = GetChromiumVersion();                 # this value
        chromium_major   = GetMajorVersion(chromium_version);    # engine derives
        brand_version_list      <- chromium_major                # bare major '152'
        brand_full_version_list <- chromium_version              # '152.0.7977.75'
        metadata->full_version  <- chromium_version              # uaFullVersion

    So passing ``.reduced`` (``152.0.0.0``) would land verbatim in
    ``uaFullVersion`` — the exact tell ``engine_version.parse()`` refuses via
    its ``full != reduced`` guard (*"a real Chrome never reports a .0.0 full
    version"*). ``.major`` would truncate the full-version list. ``.full`` is
    the only value that yields all three correct shapes.
    """
    try:
        return installed_chromium_version()
    except EngineVersionUnreadableError as e:
        logger.warning(
            "Profile %r launches Chromium WITHOUT --fingerprint-brand-version: "
            "the installed engine's version could not be read (%s). The profile "
            "still launches, and its user agent, Client Hints brands and "
            "uaFullVersion still agree with each other — but they will report "
            "the engine's own built-in version rather than the installed build, "
            "so they may look out of date. This is usually transient (version.txt "
            "is absent mid-update); run an engine check to record the version.",
            profile.name,
            e,
        )
        return None


def _profile_locale(profile: Profile, proxy) -> str:
    """The locale a profile declares — or a refusal, failing CLOSED exactly like
    ``_profile_timezone`` below.

    THE TWO MUST AGREE ABOUT WHETHER THE COUNTRY IS KNOWN. That is the whole
    property this helper exists to hold: either both halves of one derivation
    answer, or both refuse. Before it, the locale half answered ``en-US`` for a
    country the zone half would have refused — and because ``_proxy_timezone``'s
    first branch returns the checked zone without ever reaching that refusal,
    the two answers SHIPPED TOGETHER: an American-English browser whose clock
    was in Sofia.

    No proxy: ``en-US``, unchanged and deliberate. persona forces it so it never
    leaks the host locale (#218), and ``_profile_timezone`` pins a US zone so
    the pair is coherent by construction. That path never consults the table and
    can never refuse.

    A proxy: the locale of its EXIT country, and this is where the caller does
    work ``_locale_for`` structurally cannot.

    ⚠️ AN EMPTY ``country_code`` MEANS DIFFERENT THINGS ON THE TWO PATHS, and
    only THIS function can tell them apart. ``_locale_for`` is pure — handed
    ``""`` it cannot see whether a proxy exists, so it answers ``en-US``, which
    is RIGHT for the direct path and wrong here. With a proxy present, ``""`` is
    not "no country", it is **"we do not know this proxy's country"** — and the
    zone half still ANSWERS for that record, from ``_proxy_timezone``'s first
    branch, so falling through to ``en-US`` reproduces the exact contradiction
    this ticket exists to remove: ``en-US`` beside ``Europe/Sofia``, on both
    engines. It is not a hypothetical shape; ``proxy_checker`` builds it two
    ways on purpose (``_resolve_geo`` remembers a zone-carrying partial, and
    ``_validate_geo`` drops a malformed code while keeping the zone). So the
    gate is here, ahead of the lookup. See ``ExitCountryUnknownError`` for the
    behavioural consequence, which is real and deliberate.

    Both refusals are re-raised here rather than in launch_policy so the
    operator gets the profile and proxy BY NAME, matching what the zone half
    already does — a refusal an operator cannot diagnose is a worse product than
    a wrong locale.
    """
    if proxy is None:
        return "en-US"
    code = (getattr(proxy, "country_code", "") or "").strip()
    if not code or code.upper() in _NO_COUNTRY_CODES:
        # A proxy IS present and we cannot name its country. The REMEDY here is
        # the opposite of the one below — a check that answers with a country
        # fixes this, and no table row can — so it gets its own error class and
        # its own sentence rather than being folded into either neighbour.
        raise ExitCountryUnknownError(
            f"Profile {profile.name!r} has proxy {profile.proxy!r} assigned, but that "
            "proxy's EXIT COUNTRY is not known (its check answered without one). "
            "Refusing to launch: the recorded timezone still declares a location, so "
            "falling back to en-US would declare an American-English browser beside a "
            "non-US clock — the 'spoofed location' tell this product exists to avoid. "
            "Re-check the proxy to resolve it."
        )
    try:
        return _locale_for(code)
    except LocaleUnderivableError as e:
        # SECOND, NEVER FIRST — the same precedence rule the zone half states at
        # length in ``_proxy_timezone`` and says not to reorder, applied here.
        # The table is the product's own derivation and always wins; the
        # declaration exists for the countries the table cannot answer for, not
        # because an operator's typing outranks a shipped row. Because it is
        # consulted only inside this refusal arm, NO currently-launching profile
        # changes behaviour at all.
        #
        # Gated on the country it was declared for (``declared_locale``), which
        # also supplies the region half — so a declaration retires itself when a
        # backconnect exit moves and the launch refuses again, exactly as the
        # zone half does.
        declared = declared_locale(proxy)
        if declared:
            return declared
        # Names the COUNTRY, and says the remedy is NOT a re-check — the proxy's
        # check may have passed moments ago and will keep passing, because what
        # is missing is a table row. It names BOTH tables (adding one row alone
        # is precisely how this class of defect is reintroduced, and the
        # correspondence suite fails it in either direction) and, since PS-332,
        # the DECLARATION first — because that is the remedy the operator can
        # reach without shipping a build.
        raise LocaleUnderivableError(
            f"Profile {profile.name!r} has proxy {profile.proxy!r} assigned and its "
            f"exit country is known ({code.upper()}), "
            "but no locale is known for that country. Refusing to launch: falling "
            "back to en-US would declare an American-English browser beside the "
            "exit's own non-US clock — the 'spoofed location' tell this product "
            "exists to avoid. Re-checking will NOT help; declare the exit's "
            "language in the proxy editor, or add a row for that country to "
            "_COUNTRY_LOCALE *and* the matching _COUNTRY_TZ row "
            "(launch_policy.py) to resolve it."
        ) from e


def _profile_timezone(profile: Profile, proxy) -> str:
    """The zone a profile declares — or a refusal, failing CLOSED like the guard
    above.

    No proxy: a US zone, which must AGREE with the forced en-US language (see
    the call sites) rather than leak the host zone.

    A proxy: the zone of its EXIT. If that proxy carries no geography (it has
    never been checked successfully), there is no honest answer — the old
    fallback declared the OPERATOR'S REAL TIMEZONE inside the tunnel, a
    real-location disclosure on the very vector the proxy exists to close. A
    security tool must fail closed: refuse the launch instead.

    Re-raised here rather than in launch_policy so the operator gets the profile
    and proxy by name, and is told what resolves it — one proxy check writes the
    geo and the same profile then launches declaring the exit's zone.
    """
    if proxy is None:
        return _timezone_for("US")
    try:
        return _proxy_timezone(proxy)
    except GeographyDisprovenError as e:
        # Named apart from the branch below on purpose (AC4): telling an
        # operator the proxy was "never checked" when it WAS checked and the
        # check FAILED sends them looking for the wrong thing. Same remedy, but
        # the cause has to be stated truthfully.
        raise GeographyDisprovenError(
            f"Profile {profile.name!r} has proxy {profile.proxy!r} assigned, but that "
            "proxy's LAST CHECK FAILED — the geography still on file is disproven by "
            "the most recent evidence. Refusing to launch: declaring a location the "
            "product's own latest check contradicts would be incoherent, and the "
            "stored zone is no longer known to describe the exit. "
            "Re-check the proxy to resolve it."
        ) from e
    except TimezoneUnderivableError as e:
        # BEFORE the parent, like the branch above and for the same reason: this
        # is a THIRD cause, not a variant of "never checked". The proxy may have
        # been checked successfully moments ago — the geo response simply
        # carried no usable zone — so telling this operator to "check the proxy"
        # sends them to re-run a check that already passed and will keep
        # passing. The remedy here is a code change (a _COUNTRY_TZ row), which
        # is a different action by a different person, so it has to be said.
        #
        # Names the COUNTRY. A refusal an operator cannot diagnose is a worse
        # product than a wrong timezone, and "which country" is the whole
        # diagnosis: it turns an unlaunchable profile into a one-line fix.
        raise TimezoneUnderivableError(
            f"Profile {profile.name!r} has proxy {profile.proxy!r} assigned and its "
            f"exit country is known ({(getattr(proxy, 'country_code', '') or '?').upper()}), "
            "but no timezone is known for that country and its last check recorded "
            "none. Refusing to launch: falling back to UTC would declare a clock "
            "that contradicts the exit's own country — the 'spoofed location' tell "
            "this product exists to avoid. Re-checking will NOT help; add a row for "
            "that country to _COUNTRY_TZ (launch_policy.py) to resolve it."
        ) from e
    except GeographyUnknownError as e:
        raise GeographyUnknownError(
            f"Profile {profile.name!r} has proxy {profile.proxy!r} assigned but its "
            "geography could not be established (the proxy has never been checked "
            "successfully). Refusing to launch: deriving the timezone from the host "
            "would declare the operator's real location inside the tunnel. "
            "Check the proxy to resolve it."
        ) from e


def _launch_geo(store, ref: str | None):
    """The geography-bearing proxy record a launch must reason about.

    ⭐ WHY THIS INDIRECTION EXISTS, since a bare ``store.geo_for_launch(ref)``
    would read better. The launch path is driven by ~20 test files that install
    their own minimal store DOUBLE — a class with ``resolve`` and ``get`` and
    nothing else. Those doubles are how the QUIC, DoH, VA-API, env-scrub, cwd
    and cert suites reach the launch at all, and almost none of them are about
    proxies: they need *a* proxy to exist and assert something else entirely.

    Calling a NEW store method directly makes every one of them raise
    ``AttributeError`` — measured, 51 failures across five files — which is not a
    real finding about the product. It is a doubles-out-of-date finding, and
    "make 51 unrelated tests pass again" is exactly the pressure that produces a
    rushed edit to a suite nobody re-reads. So the new capability is asked for
    politely and the old question is the fallback.

    ⚠️ THE FALLBACK IS FOR TEST DOUBLES, NOT FOR PRODUCTION. The real
    :class:`ProxyStore` always implements ``geo_for_launch``, so the production
    path ALWAYS takes the first branch — a double that lacks the method gets the
    pre-PS-358 behaviour, which is correct for a double that hardcodes a stored
    proxy anyway (an inline ref never reaches it). A test that wants the inline
    behaviour must provide a store that implements it, which is what the PS-358
    tests do.
    """
    geo = getattr(store, "geo_for_launch", None)
    if callable(geo):
        return geo(ref)
    return store.get(ref) if ref else None


def _spawn_invisible(profile: Profile, profile_dir: str, *, in_process: bool = False):
    """Launch the invisible_playwright (patched Firefox 150) engine. SOCKS5
    proxy auth is handled natively (no bridge). Returns a Popen-compatible
    handle.

    ``in_process`` runs the session in a THREAD of this process instead of a
    forked child, so the caller can reach the session's eval hook (which is
    registered in a per-process dict). See :class:`InvisibleProcess`.
    """
    from .invisible_launch import is_invisible_installed, spawn

    store = ProxyStore()
    _resolved = store.resolve(profile.proxy)
    # Fail CLOSED: never launch FF DIRECT for a profile that HAS a proxy assigned.
    _require_proxy_resolved(profile, _resolved)
    proxy_url = _resolved or ""
    # `geo_for_launch`, NOT `get`: a plain name lookup answers None for an INLINE
    # proxy, and None is the no-proxy sentinel the two helpers below read as
    # "this profile is DIRECT" — so an inline socks5 exiting in Warsaw used to
    # launch with a US zone and en-US, a language contradicting its own IP
    # (PS-358). This resolves the exit instead, and where it cannot it returns a
    # record carrying no geography so the gates below REFUSE rather than fall
    # back. The named path is unaffected: a stored proxy is returned untouched
    # and pays no launch-time probe.
    proxy = _launch_geo(store, profile.proxy)
    # Locale + timezone follow the proxy's geo so they match the exit IP. Always
    # resolve to a CONCRETE zone — never leave it empty: invisible treats an
    # empty timezone as "auto" and blocks the launch ~40s on an egress-IP lookup
    # (direct, over Tor).
    #
    # With NO proxy the language is forced to en-US (persona never leaks the host
    # locale, e.g. uk-UA — #218), so the timezone must AGREE with en-US, not the
    # host zone: a fresh CreepJS run on a Kyiv host showed language=en-US paired
    # with timezone=Europe/Kyiv, and language⊥timezone is a classic inconsistency
    # a detector flags. Pin a US zone so the direct identity reads as one coherent
    # US-English user AND the host location stays hidden.
    #
    # BOTH halves now go through the fail-closed helpers, and they AGREE about
    # whether the country is known: either both answer or both refuse. The bare
    # `_locale_for(proxy.country_code)` that used to sit here answered `en-US`
    # for a country `_profile_timezone` would have refused for — and because
    # `_proxy_timezone`'s first branch returns the CHECKED zone without ever
    # reaching that refusal, the two answers shipped together: en-US beside the
    # exit's real zone.
    #
    # TIMEZONE GATE FIRST, then the locale gate — the same order the chromium
    # arm asks them in, so a profile both refusals could apply to is reported
    # identically whichever engine it launches on. Both are asked before any
    # launch work (PS-283).
    tz = _profile_timezone(profile, proxy)
    lang = _profile_locale(profile, proxy)

    if _platform.supports_linux_desktop_integration():
        write_window_entry(profile.name, icon="firefox")

    chosen = BookmarkStore().resolve_selection(
        profile.bookmark_pool, profile.bookmarks
    )

    # An assigned mTLS certificate starts a terminator (in this parent) that the
    # engine talks to as its proxy; the engine trusts the terminator's leaf by
    # importing its CA into the profile's cert9.db. The certificate is presented
    # to the admin host only and never enters the browser.
    cert_session = None
    try:
        cert_session = _cert_session_for(profile, profile_dir, proxy_url)
        if cert_session is not None:
            proxy_url = cert_session.proxy_url

        width, height = resolve_resolution(
            getattr(profile, "resolution", "auto"),
            profile.fingerprint_seed,
            profile.hardware_generation,
        )

        # WHY `lat`/`lon` ARE NOT IN THE cfg BELOW, though `locale` and
        # `timezone` are and `proxy.lat`/`proxy.lon` are in scope right here.
        # This is a RECORDED DECISION, not an oversight — PS-312 established it
        # by measurement, and the matrix cell it answers is re-read by
        # `tests/test_engine_masking_matrix.py`.
        #
        # The Chromium arm installs `build_geo_extension` for every proxied
        # profile because `getCurrentPosition` could otherwise fall through to
        # the REAL host coordinates while locale and timezone already name the
        # exit country. That premise was tested on THIS engine rather than
        # assumed, on a real proxied headful launch reading the value a page
        # actually receives, and it does not hold here:
        #
        # getCurrentPosition is answered LOCALLY and yields no coordinates
        #
        # so there is no host position for a spoof to displace.
        #
        # Measured (invisible_core 20.14.0, firefox-20 / FF 151.0), reading the
        # success/error callback from a page on a real https origin:
        #
        #   permission "prompt"  (the shipped default) -> neither callback ever
        #                        fires; the call simply hangs on the doorhanger
        #   permission "granted" -> error code 2 (POSITION_UNAVAILABLE) in
        #                        ~12ms, proxied AND direct alike
        #
        # ~12ms is far too fast for a network round trip: the refusal is local,
        # and it is CAUSED by the engine's own `geo.provider.network.url: ""`.
        # That was isolated by A/B — the same launch with that one pref pointed
        # at a reachable provider returns a position in ~18ms, which is also the
        # positive control proving the reading is a real null and not a null
        # instrument.
        #
        # So persona ships no Firefox geolocation spoof, and adding one would be
        # a NET LOSS under Invariant #0: it would replace a native refusal that
        # reads clean (`Function.prototype.toString` on getCurrentPosition still
        # renders "[native code]") with a JS override a detector can see, to
        # close a hole the measurement shows is not open. The engine's two geo
        # decisions are load-bearing and must not be reversed — `geo.enabled:
        # True` (an absent `Navigator.prototype.geolocation` is itself a tell)
        # and `permissions.default.geo` left unset (a `denied` where stock says
        # `prompt` was measured as a divergence).
        #
        # ⚠️ This rests on the engine, so it is only true while the engine
        # behaves this way. Re-measure on an engine bump before trusting it.

        cfg = {
            "os_type": profile.os_type,
            "proxy_url": proxy_url,
            "mtls_ca_path": cert_session.ca_path if cert_session else None,
            "profile_name": profile.name,
            # The stable crc32 fingerprint seed — the child must NOT derive it via
            # hash(), which is salted per-process and changes every app restart.
            "seed": profile.fingerprint_seed,
            "search_engine": profile.search_engine,
            "locale": lang,
            "timezone": tz,
            "bookmarks": [{"name": b.name, "url": b.url} for b in chosen],
            "resolution": [width, height],
            "profile_dir": os.path.join(profile_dir, ".invisible-profile"),
            # The PROFILE DATA DIR, distinct from "profile_dir" just above —
            # which is the engine's own inner profile, one level down. The
            # child pins its scratch directory off THIS one so both engines
            # agree on the same path: chromium's --user-data-dir IS the data
            # dir, so deriving the firefox value from .invisible-profile would
            # put the two seams one level apart. Both would still be inside the
            # perimeter, but "both seams agree" is the property under test.
            "profile_data_dir": profile_dir,
            # A pure presence check: ensure_invisible_installed would DOWNLOAD the
            # ~118MB engine here and block the launch for minutes over Tor.
            "_needs_fetch": not is_invisible_installed(),
        }
        proc = spawn(cfg, in_process=in_process)
        # Claim the terminator for this browser now that it exists (it had to
        # bind first — the engine gets its port in the proxy config). The handle
        # reports pid 0 on the non-fork path, where the engine runs on a thread
        # of THIS process rather than as a child; the gate treats that as "our
        # own tree", which is exactly right there.
        if cert_session is not None:
            cert_session.bind_to_process(getattr(proc, "pid", 0))
        # Stop the terminator when the FF session ends (same hook the chromium path
        # uses; None when no certificate is assigned).
        proc._cert_session = cert_session  # type: ignore[attr-defined]
        return proc
    except BaseException:
        # spawn() (os.pipe/fork inside InvisibleProcess.__init__) can raise after
        # the terminator started; without this the terminator + .persona-mtls key
        # material would be orphaned (repeated fails exhaust ephemeral ports).
        if cert_session is not None:
            with _suppress():
                cert_session.stop()
        raise


def effective_engine(profile: Profile) -> str:
    """The engine actually launched for a profile — readiness monitoring and
    install checks must follow this, not the stored engine."""
    # Delegates to the model's coherence rules (services/profile/coherence.py),
    # which every door that WRITES a profile also crosses, so the launch and the
    # record cannot answer the same question differently.
    #
    # A profile stored before those rules existed (or through the once-unguarded
    # REST lane) can still carry an impossible pair. Both are reconciled toward
    # chromium: it has the device presets a mobile profile needs, and — unlike
    # stealth-Firefox, which reports Windows regardless (#211) — it HONORS
    # os_type, so a stored macos/linux profile actually presents the OS its
    # record claims instead of contradicting it. "camoufox", the retired Firefox
    # engine name, is mapped forward so an old profile keeps launching.
    #
    # Imported inside the function on purpose: services.profile imports
    # browser.device_presets, and reaching it runs browser/__init__ →
    # launcher → process, so a module-level import here closes a cycle that
    # fails at import time. Every other consumer of effective_engine imports it
    # function-locally for the same reason.
    from ..profile.coherence import coherent_engine

    return coherent_engine(
        profile.os_type, getattr(profile, "engine", "chromium")
    )


def spawn_browser(profile: Profile, *, in_process: bool = False) -> subprocess.Popen:
    """Launch a persona browser (fingerprint-chromium or the patched Firefox)
    for the given profile.

    ``in_process`` is honoured by the FIREFOX path only: it runs the session in
    a thread of this process so the caller can reach its eval hook. Chromium
    needs no such flag — it is reachable over CDP from any process.
    """
    profile_dir = os.path.join(DATA_DIR, profile.name)
    os.makedirs(profile_dir, exist_ok=True)

    engine = effective_engine(profile)
    if engine == "firefox":
        proc = _spawn_invisible(profile, profile_dir, in_process=in_process)
        proc._proxy_bridge = None  # type: ignore[attr-defined]
        return proc

    store = ProxyStore()
    # `geo_for_launch`, NOT `get` — the chromium arm carried the SAME defect as
    # the firefox arm above and must be fixed with it, or the mismatch simply
    # moves to whichever engine was left behind (PS-358).
    proxy = _launch_geo(store, profile.proxy)
    proxy_url = store.resolve(profile.proxy)
    # Fail CLOSED: never open a DIRECT window for a profile that HAS a proxy.
    _require_proxy_resolved(profile, proxy_url)
    # Both fail-closed gates run BEFORE any launch work, matching the Firefox arm
    # (_spawn_invisible: _require_proxy_resolved then _profile_timezone, both ahead
    # of the desktop entry, bookmarks and the cert session). The timezone gate used
    # to be asked 320 lines later, next to the flag that consumes it, so a launch
    # this gate REFUSES still wrote the whole profile (11 extension dirs, prefs,
    # bookmarks — 433 KB), a host desktop entry OUTSIDE the profile perimeter, and
    # started the mTLS terminator (decrypted client key on disk + a bound port).
    # A launch that will be refused must do no launch work. Computed once, here;
    # the arg builder below consumes _tz rather than re-asking.
    _tz = _profile_timezone(profile, proxy)
    # The locale gate sits HERE, beside the timezone gate, not 26 lines down at
    # the flag that consumes it — for PS-283's reason: a launch that will be
    # refused must do no launch work. Both halves of the geo derivation are now
    # asked before the profile dir, the desktop entry and the mTLS terminator.
    _lang = _profile_locale(profile, proxy)

    # The version EVERY Chromium profile advertises in its Client Hints — desktop
    # included, unlike the mobile-only UA version resolved further down.
    #
    # Resolved HERE, beside the three fail-closed gates above, even though this
    # one does NOT refuse. Two reasons, and the second is the durable one:
    #   * it reads the engine record, which is launch-independent work that has
    #     no business happening after the profile dir, the host desktop entry and
    #     the mTLS terminator (PS-283's ordering property);
    #   * ⭐ if this gate is ever made fail-closed again, it is ALREADY in the
    #     position that keeps PS-283's invariant. Putting it downstream would
    #     leave a trap that only fires the day someone changes the policy.
    # It takes only `profile` — nothing here depends on `preset`, which is why it
    # can sit this early while `_mobile_chromium_version` cannot.
    brand_version = _chromium_brand_version(profile)

    # ⭐ WHY THERE IS NO PROFILE MIGRATION ON THIS ARM, THOUGH THE FIREFOX ARM
    # RUNS A FOUR-PART ONE ON EVERY LAUNCH. A RECORDED POSITION, ESTABLISHED BY
    # MEASUREMENT (PS-341) — not an oversight, and not an untested assumption.
    #
    # The asymmetry is real and it is deliberate. `invisible_launch.py` calls
    # `_migrate_profile_for_engine_build` on every Firefox launch because that
    # engine genuinely misbehaves: its own docstring records that a profile
    # seeded on firefox-18 makes firefox-19 SIGSEGV before the window paints, so
    # `prefs.js` is dropped, `compatibility.ini` (the downgrade guard) is
    # removed, and the addon startup cache is invalidated on a revert.
    #
    # ⛔ THE PARITY QUESTION IS NOT "WHY IS CHROMIUM MISSING FIREFOX'S GUARD".
    # It is "does Chromium EXHIBIT THE BEHAVIOUR that guard defends against?"
    # Only the second is a defect, and it was asked of a REAL ENGINE rather than
    # reasoned about:
    #
    #   Launch a profile on personium-152.0.7977.75, move the engine BACKWARDS
    #   to 148.0.7778.215 through the shipping operator gesture
    #   (`updater.revert_to_previous_build`, what ui/app.py's rollback button
    #   calls — not a hand-swap), relaunch THE SAME profile dir, and read both
    #   what a RUNNING browser reports and what is left on disk — the two are
    #   different strengths of evidence and the readings below say which is
    #   which. Positive control on three independent axes: the version record
    #   moved, the binary sha256 moved, and the page's own `navigator.userAgent`
    #   major moved 152 -> 148.
    #
    #   * IT OPENS. No refusal, no crash, no SIGSEGV — the Firefox analogue does
    #     not occur. Forward again (148 -> 152) opens too.
    #   * CHROMIUM'S OWN DOWNGRADE HANDLING DOES NOT FIRE. `Last Version` is
    #     WRITTEN and silently OVERWRITTEN (152 -> 148 -> 152); the engine
    #     treats it as a record, not as a gate. `Default/` is neither renamed
    #     nor recreated and no backup/reset directory appears.
    #   * DERIVED STATE SURVIVES. `seed_profile_prefs`'s once-only guard is
    #     never re-triggered, because nothing removes `Default/Preferences`.
    #     ⚠️ THE EVIDENCE IS OF TWO DIFFERENT STRENGTHS AND THEY ARE NOT
    #     INTERCHANGEABLE — a file that survives but is IGNORED is the same
    #     outcome for the operator as one that was deleted, so only a LIVE read
    #     settles that, and only three of these five were read live:
    #       - LIVE, from the running browser on the OLDER build: the profile's
    #         chosen search engine (`Brave (Default)` on
    #         chrome://settings/searchEngines), the seeded bookmarks (present in
    #         chrome://bookmarks), and the cookie jar (the sentinel written on
    #         build N is served on build N−1).
    #       - ON DISK ONLY: the Classic theme and dark mode
    #         (`color_scheme2: 2`). Both are byte-identical across the change
    #         and nothing renames, resets or removes the file holding them.
    #         ⭐ That is enough for the question THIS arm actually asks —
    #         `seed_profile_prefs` keys purely on `Default/Preferences`
    #         EXISTING, so a surviving file is exactly what stops the operator's
    #         choice being silently dropped. It is NOT the stronger claim that
    #         the engine still honours those two values, which was not measured
    #         here. Do not upgrade it to one without taking the reading.
    #
    # So NO MIGRATION IS OWED HERE, and adding one would be a fix for a state
    # this engine does not enter. That is the whole position; the evidence is in
    # `readings/ps341-2026-09-07/` and is re-read live by
    # `tests/test_ps341_engine_continuity_live.py`.
    #
    # ⚠️ ONE CAPTURED NUMBER IS NOT EXPLAINED, AND IT IS LEFT OPEN ON PURPOSE.
    # The legs read `matchMedia('(prefers-color-scheme: dark)').matches` as
    # FALSE on BOTH builds, though the profile is seeded `color_scheme2: 2` and
    # launched with `--force-dark-mode` (further down this same arg list). The
    # obvious explanation — "`--force-dark-mode` is UI-level and does not drive
    # `prefers-color-scheme`" — was CHECKED AND IS FALSE: on stock chromium
    # 152, headless, each of the flag alone, the seeded pref alone, and both
    # together give `dark=true`, against a fresh-profile negative control that
    # correctly gives `false` (`scripts/ps341_dark_control.py`,
    # `readings/ps341-2026-09-07/control-dark-mode.json`). Two variables move
    # between that control and the legs — a STOCK engine vs the packaged
    # fingerprint build, and headless vs headful-under-Xvfb — and one control
    # cannot separate them, so the cause is genuinely NOT KNOWN.
    # ⛔ NOTHING IN THIS POSITION TURNS ON IT: the value is identical on both
    # builds, so it does not move across a build change and is not a continuity
    # fact. It is written down rather than left bare in the reading so the next
    # reader inherits the open question and the control that already ruled out
    # its most plausible answer, instead of re-deriving both.
    #
    # ⚠️ ONE THING DOES MOVE, AND IT IS NOT A MIGRATION PROBLEM — SEE THAT TEST
    # AND `gpu_ext.py`. The WebGL vendor/renderer pair a page reads CHANGES
    # across a build change on the WINDOWS arm, which is the only arm where the
    # ENGINE authors it (`ENGINE_AUTHORED_IDENTITY_ARMS` is
    # `frozenset({"windows"})`, and that is where persona's own GPU layer
    # deliberately stands down). Measured across 8 seeds, headful under CDP:
    # 8/8 moved, and the two builds' card pools do not intersect AT ALL (148
    # answers Intel integrated parts, 152 answers NVIDIA RTX parts). It is
    # STABLE within a build — two launches of one build at one seed agree — so
    # the move is attributable to the build change and to nothing else.
    #
    # ⛔ MACOS IS THE CONTRAST, NOT A SECOND INSTANCE OF THIS, and the
    # difference follows from the mechanism below rather than being an
    # exception to it. `engine_authors_identity_for_engine_platform("macos")`
    # is `False`, so `gpu_ext.py` renders `ENGINE_AUTHORS_IDENTITY` false into
    # the content script there and persona writes the pair ITSELF from its own
    # `MAC_GPUS` table (gpu_ext.py:969/:992). A table in persona's Python is
    # not a table in the engine binary, so on macos this pair should be STABLE
    # across a build change — for precisely the reason it is unstable on
    # windows. ⚠️ THAT IS AN ARGUMENT, NOT A READING: the macos arm was NOT
    # measured here (`scripts/ps341_gpu_seeds.py:60` defaults to
    # `platform="windows"` and both call sites take the default, so all 8 seeds
    # are windows). Do not restate it as measured without taking it.
    #
    # That is a LEVEL-2 (bit-stability across engine updates) continuity fact
    # about an ENGINE-AUTHORED vector, and NOT something a profile migration
    # could repair: the value is produced by a table compiled into the engine
    # binary, so no amount of rewriting the profile directory changes it. It is
    # recorded rather than fixed here on purpose — the fix, if one is wanted, is
    # a decision about WHO AUTHORS that pair on those arms, which is
    # `gpu_ext.py`'s question and not this launch path's.
    seed_profile_prefs(profile_dir, profile.search_engine)

    chosen = BookmarkStore().resolve_selection(
        profile.bookmark_pool, profile.bookmarks
    )
    seed_bookmarks(profile_dir, chosen)
    if _platform.supports_linux_desktop_integration():
        write_window_entry(profile.name)

    # An assigned mTLS certificate starts a local terminator that presents the
    # client cert to the admin host only; its own upstream is the profile's real
    # proxy, so the exit IP is unchanged. Unlike Firefox (which points its whole
    # proxy at the terminator and trusts the leaf via cert9.db), chromium keeps
    # its REAL proxy and reaches the terminator DIRECTLY for the admin host only
    # (--proxy-bypass-list + --host-resolver-rules below): its spki-list trust
    # only covers a leaf seen on a direct connection, not one behind a CONNECT
    # proxy. So proxy_url stays the real proxy here.
    bridge = None
    cert_session = None
    try:
        cert_session = _cert_session_for(profile, profile_dir, proxy_url)

        # Locale + timezone follow the proxy's geo so they match the exit IP.
        # Computed once, at the gate above, alongside _tz — this consumes it
        # rather than re-asking, so the two cannot drift and a refusal cannot
        # arrive after the launch work has already been done.
        lang = _lang

        # Imported function-locally for the same reason effective_engine above
        # does: services.profile imports browser.device_presets, and reaching it
        # runs browser/__init__ → launcher → process, so a module-level import
        # here closes a cycle that fails at import time.
        from ..profile.coherence import coherent_device_type

        # The device_type this profile actually LAUNCHES as, reconciled against
        # os_type ONCE, here, before either consumer reads it — the Rule 3
        # counterpart of effective_engine/coherent_engine above, and owned by the
        # same module so the RULE has one author.
        #
        # An already-stored `windows` + `mobile` record is reachable (import,
        # restore, legacy records, the unguarded REST lane — the authoring doors
        # refuse the pair, the recovery doors accept and RECORD it, PS-188). Only
        # TWO of the four vectors a launch computes read device_type — the device
        # preset below and --fingerprint-platform; the GPU pool arm and the voice
        # roster read os_type alone and cannot be moved by it. So unreconciled,
        # that record launches an Android Pixel-class UA and screen over a WINDOWS
        # Direct3D11 GPU pool and Microsoft SAPI voices, told
        # --fingerprint-platform=linux: one machine, three answers, and any pair of
        # them is a contradiction a checker reads directly. Reconciling here brings
        # the two vectors that read the field into line with the two that do not.
        #
        # ⚠️ LOCAL VALUE ONLY — never assigned back to profile.device_type. The
        # record is not rewritten (a pair rule has no safe repair at rest: nothing
        # says WHICH of the two fields is the lie), so
        # Profile.device_type_incoherence keeps reporting it after this launch,
        # which is the whole point of the accept-and-record decision.
        device_type = coherent_device_type(profile.os_type, profile.device_type)
        # Mobile profiles are assembled at this layer (the engine has no Android/iOS
        # mode): a real device preset drives the UA, window size, screen and the
        # touch/Client-Hints extension. A profile is mobile when its OS is a mobile
        # family (android/ios) OR device_type says so — the predicate is owned by
        # device_presets.is_mobile_profile and shared with engine_platform, so the
        # launch gate and the platform the engine is told cannot drift apart.
        is_mobile = is_mobile_profile(profile.os_type, device_type)
        # The one string the engine is told, computed ONCE, here, BEFORE the
        # extensions are built — because build_gpu_extension takes it and
        # resolves WHO AUTHORS the WebGL identity pair from it. It used to be
        # computed further down, next to the flag that consumes it, and
        # gpu_ext re-derived its own answer from os_type; the two disagreed on
        # windows+mobile (engine told `linux`, our layer standing down for a
        # `windows` identity nobody wrote) and the host's SwiftShader reached
        # the page. One value, both consumers, no second computation to drift.
        engine_platform = engine_platform_for(profile.os_type, device_type)
        # the mobile OS family for preset selection (android unless explicitly ios)
        mobile_os = profile.os_type if is_mobile_os(profile.os_type) else "android"
        preset = (
            pick_preset(
                profile.fingerprint_seed, mobile_os, profile.hardware_generation
            )
            if is_mobile
            else None
        )
        # The Chromium version this profile advertises, READ from the installed
        # engine rather than stored as a constant, so a routine engine bump
        # cannot leave the profile claiming a version the engine is not. None
        # for desktop (no --user-agent is passed at all, so the engine's own
        # reported version is what the page sees) and for iOS (no UA-CH).
        chromium_version = _mobile_chromium_version(profile, preset)

        extensions = []
        # native_ext patches Function.prototype.toString so persona's wrapped
        # built-ins (Intl/matchMedia/getVoices/Worker/…) stringify as native code,
        # hiding the JS-override tell a masking detector reports.
        extensions.append(
            build_native_extension(
                os.path.join(profile_dir, ".persona-native-ext")
            )
        )
        extensions.append(
            build_locale_extension(
                lang, os.path.join(profile_dir, ".persona-locale-ext")
            )
        )
        # fingerprint-chromium leaks the host OS speech-voice list (~180 macOS voices
        # led by the host locale); replace it with a Windows-plausible set matching
        # `lang`, at parity with the Firefox engine.
        extensions.append(
            build_voice_extension(
                lang, os.path.join(profile_dir, ".persona-voice-ext"),
                os_type=profile.os_type,
            )
        )
        extensions.append(
            build_stealth_extension(
                os.path.join(profile_dir, ".persona-stealth-ext")
            )
        )
        # ⭐ THE MEASURETEXT REPAIR IS GATED ON THE INSTALLED ENGINE (PS-409).
        #
        # `measuretext_ext` exists ONLY to repair the ~1e-6 multiplicative scale
        # the UNFIXED engine applies to every Canvas `measureText` metric — its
        # own header names what breaks without it (Google Sheets' canvas grid
        # laying glyphs against a width of ~0; the date-cell popover collapsing
        # off-screen). PS-345 fixed that IN THE ENGINE, and the repair's guard
        # (`var corrupt = hasText && !(Math.abs(m.width) >= 1)`) only fires on
        # absurdly small widths — so on a fixed engine, where real widths are
        # ~200, it CAN NEVER FIRE.
        #
        # ⭐ MEASURED ON BOTH ARMS, readings/ps409-2026-09-11/: the same FIXED
        # binary launched with the repair installed and with it omitted reports
        # BYTE-IDENTICAL widths (15.82 / 56.44 / 298.41, ratio 1.0000 against an
        # un-noised DOM reference). The wrapper is a structural no-op there. On
        # the SHIPPED engine with the repair omitted the same probe reads
        # 0.0000366 / 0.00013 / 0.00069 — ratio 2.3e-6, which is the Sheets
        # failure above, measured.
        #
        # ⚠️ AND THE READING CORRECTED THIS TICKET'S OBSERVABILITY PREMISE — do
        # not restate the old one. PS-409 argued the leftover wrapper is a TELL
        # because it stringifies as `m() { return inner.apply(...) }` instead of
        # `[native code]`. That is PR #327's Arm N, which is FIREFOX. On Chromium
        # PS-368 gave every leaf its own toString cloak, so the wrapper reads
        # `function measureText() { [native code] }` with exactly
        # ["length","name"] — identical to native, on every arm. What a page CAN
        # still see is that the repair returns a Proxy when it fires (a native
        # accessor invoked with it as receiver throws TypeError), which is a
        # SHARPER tell but only reachable where the repair actually fires. So the
        # measured cost of leaving the extension on a fixed engine is "a wrapper
        # no probe in that reading could see" rather than "an observable tell" —
        # a weaker claim than the ticket makes, and the gate still earns its place
        # on the narrower ground that a no-benefit extension should not be loaded.
        #
        # ⛔ OMITTED, NOT NEUTERED. A wrapper that installs and returns early is
        # still an installed extension — a file on disk and a content script in
        # every frame — so the extension is left off the command line entirely
        # rather than built with its repair disabled. (The neutered variant was
        # run: readings/ps409-2026-09-11/falsification-neutered.txt.)
        #
        # ⛔ AND IT FAILS OPEN, WHICH IS THE WHOLE RISK ORDERING. The engine
        # update is offered on a strict version compare, so every user who does
        # not take it stays on the old engine indefinitely, and that population
        # is large. Getting this wrong in the PERMISSIVE direction leaves a tell;
        # getting it wrong in the STRICT direction hands those users ~1e-6
        # geometry with nothing repairing it — a working product broken to remove
        # a tell. So `measuretext_repair_required()` answers True on every
        # uncertainty: no threshold committed, an unreadable `version.txt`, an
        # unparseable tag. See `engine_version.carries_measuretext_fix` for why
        # it compares the RAW TAG rather than `ChromiumVersion.full`.
        if measuretext_repair_required():
            extensions.append(
                build_measuretext_extension(
                    os.path.join(profile_dir, ".persona-measuretext-ext")
                )
            )
        else:
            # ⚠️ THE OMISSION IS LOGGED, BECAUSE IT CHANGES WHAT THE BROWSER
            # CARRIES — exactly as the `--fingerprint-brand-version` skip above
            # is logged. An operator whose Sheets geometry breaks after an engine
            # change needs one searchable line naming the decision and the engine
            # version it was taken on, rather than having to diff a command line.
            #
            # Imported function-locally like every other browser→engine
            # reference in this package (a module-level one closes a cycle
            # through `browser/__init__`).
            from ..engine import policy as _engine_policy

            logger.info(
                "Profile %r launches WITHOUT the measureText repair extension: "
                "the installed engine carries the PS-345 fix (policy threshold "
                "%s), so the repair's guard could never fire and the wrapper "
                "would be an observable tell with no benefit. If canvas text "
                "geometry misbehaves on this engine, clear "
                "measuretext_fix_min_version in your engine policy file to "
                "restore it.",
                profile.name,
                _engine_policy.measuretext_fix_min_version(),
            )
        # On Windows/macOS the seeded default_search_provider_data pref is reset by
        # tracked-preference (default-search) enforcement, so a settings-override
        # extension is the per-profile mechanism that actually applies the chosen
        # engine. On Linux enforcement is off and the plaintext seed already sticks,
        # so skip the extension there (it would raise the "an extension changed your
        # search settings" bubble on a path that already works silently).
        if not _platform.IS_LINUX:
            extensions.append(
                build_search_extension(
                    profile.search_engine,
                    os.path.join(profile_dir, ".persona-search-ext"),
                )
            )
        extensions.append(
            build_audio_extension(
                profile.fingerprint_seed,
                os.path.join(profile_dir, ".persona-audio-ext"),
            )
        )
        # The operator's chosen desktop resolution, parsed ONCE here because two
        # places need it: the device extension (which pins screen.* to it) and
        # the launch args below (which size the WINDOW to it). `None` on AUTO
        # and on every mobile profile, which is what keeps both of those paths
        # byte-identical to before — see the window-size block for why the
        # window must be capped rather than the reported outer size clamped.
        desktop_resolution = None
        if is_mobile and preset is not None:
            # iOS always reports 5 touch points; Android varies by device (commonly 5
            # or 10). A constant 5 on every Android profile is a weak cluster tell, so
            # pick it deterministically from the profile seed — stable per profile,
            # spread across profiles. The Android pick is generation-filtered (see
            # hardware_generation.py): it was `(5, 10)[seed % 2]`, so appending a
            # third value would have re-indexed half of all Android profiles onto a
            # different maxTouchPoints. iOS is a constant, so it has nothing to move.
            if preset.os_type == "ios":
                touch_points = 5
            else:
                touch_points = pick_touch_points(
                    profile.fingerprint_seed, profile.hardware_generation
                )
            extensions.append(
                build_mobile_extension(
                    os.path.join(profile_dir, ".persona-mobile-ext"),
                    is_ios=(preset.os_type == "ios"),
                    platform=preset.platform,
                    model=preset.model,
                    chromium_version=chromium_version,
                    css_width=preset.width,
                    css_height=preset.height,
                    dpr=preset.dpr,
                    device_memory=preset.device_memory,
                    hardware_concurrency=preset.hardware_concurrency,
                    touch_points=touch_points,
                )
            )
        else:
            desktop_resolution = parse_resolution(
                getattr(profile, "resolution", "auto")
            )
            extensions.append(
                build_device_extension(
                    profile.fingerprint_seed,
                    os.path.join(profile_dir, ".persona-device-ext"),
                    profile.hardware_generation,
                    resolution=desktop_resolution,
                    os_type=profile.os_type,
                )
            )
        extensions.append(
            build_webgl_extension(
                profile.fingerprint_seed,
                os.path.join(profile_dir, ".persona-webgl-ext"),
            )
        )
        extensions.append(
            build_gpu_extension(
                profile.fingerprint_seed,
                profile.os_type,
                os.path.join(profile_dir, ".persona-gpu-ext"),
                profile.hardware_generation,
                # The SAME string emitted as --fingerprint-platform below. Not
                # os_type: authorship is resolved from what the ENGINE is told.
                engine_platform=engine_platform,
            )
        )
        # Safari's legacy webkit-3d context alias. iOS-only, and the extension
        # enforces that itself from the baked OS — a non-iOS profile's copy
        # returns before touching getContext, so it is built unconditionally
        # like the others rather than gated here.
        extensions.append(
            build_canvas_ctx_extension(
                profile.os_type,
                os.path.join(profile_dir, ".persona-canvas-ctx-ext"),
            )
        )
        if proxy:
            # A proxy with coords → pin them. A proxy WITHOUT usable coords (many
            # geo endpoints return a valid country + timezone but null/malformed
            # lat/lon) → build the extension in DENY mode so getCurrentPosition
            # can't fall through to the REAL host coordinates while the locale and
            # timezone already say the exit country (audit7 #5). Only a proxy-less
            # profile leaves geolocation untouched.
            has_coords = proxy.lat is not None and proxy.lon is not None
            extensions.append(
                build_geo_extension(
                    proxy.lat if has_coords else None,
                    proxy.lon if has_coords else None,
                    os.path.join(profile_dir, ".persona-geo-ext"),
                )
            )

        args = [FINGERPRINT_CHROMIUM]
        # Chromium honors only the LAST --disable-features switch on the command
        # line — repeated switches replace, not merge. Collect every disabled
        # feature here and emit a single merged flag below.
        disabled_features = []
        # --appimage-extract-and-run only applies to the Linux AppImage engine; the
        # Windows .exe / macOS .app are launched directly.
        if _platform.IS_LINUX:
            args.append("--appimage-extract-and-run")
        args += [
            f"--user-data-dir={profile_dir}",
            f"--fingerprint={profile.fingerprint_seed}",
            f"--fingerprint-platform={engine_platform}",
            # ⚠️ THE BRAND LINE IS THE GATE FOR THE VERSION FLAG BELOW — DO NOT
            # REMOVE OR VARY IT.
            #
            # `--fingerprint-brand-version` is read by the engine ONLY inside
            # `if (brand == "chrome")`, after a ToLowerASCII, in
            # 002-user-agent-fingerprint.patch's GetChromiumVersion(). So this
            # line gates the one appended just after this list.
            # Removing it, or offering an Edge/Opera/Vivaldi brand option that
            # varies it, does not merely change the brand: it SILENTLY disables
            # the version flag — no error, no log — and the engine reverts to
            # its hardcoded 144.x table while the reduced UA still says 152.
            # That mismatch is the whole defect (pixelscan: "masking detected").
            # ⛔ It is passed UNCONDITIONALLY, including when the version flag is
            # skipped: the brand claim ("presents as Chrome and nothing else") is
            # not contingent on the version being readable.
            # Pinned by tests/test_ps356_brand_version.py.
            "--fingerprint-brand=Chrome",
            # ⭐ THE VERSION FLAG, CONDITIONAL — THIS IS THE SKIP.
            #
            # The value the engine advertises in sec-ch-ua / uaFullVersion, READ
            # from the installed engine. `.full` is correct for all three shapes
            # because the engine derives the bare major itself — see
            # _chromium_brand_version for the fan-out and why `.reduced` is a tell.
            #
            # `brand_version is None` means the version could not be read (a
            # reachable transient state mid-update). We then emit NO FLAG and let
            # the engine answer from its own built-in default, which keeps the
            # three shapes agreeing WITH EACH OTHER — less current, not incoherent.
            # ⛔ There is deliberately no `else` substituting a literal: a fallback
            # constant is the exact duplication engine_version.py exists to remove
            # and would go stale invisibly. The skip is logged as a degraded state.
            #
            # Spliced in place rather than appended at the end so it stays
            # ADJACENT TO ITS GATE above — the pair reads as the unit it is, and
            # tests/test_ps283_refused_launch_does_no_work.py pins argv order.
            *(
                [f"--fingerprint-brand-version={brand_version.full}"]
                if brand_version is not None
                else []
            ),
            # THE SERVICE WORKER'S ONLY AUTHOR (PS-354).
            #
            # `applyHwPatch` carries hardwareConcurrency into Web and Shared
            # Workers, but a ServiceWorkerGlobalScope is reached by NEITHER of
            # persona's identity authors: it is never CONSTRUCTED by the page,
            # so `worker_wrap`'s chaining has no constructor to intercept, and
            # an MV3 content script does not run there. The realm therefore
            # fell through to the engine's own seed fallback, or on arms the
            # engine does not spoof, to the HOST. PS-189 measured that directly
            # -- a linux service worker reported the host's SwiftShader while
            # ELEVEN sibling realms in the same launch reported the profile's
            # card.
            #
            # The engine authors this before any of our code runs, so it covers
            # every realm INCLUDING the service worker natively -- no wrapper,
            # no descriptor, and no observable surface added to a realm we
            # otherwise never touch (which a JS shim would have done).
            #
            # ⛔ THE VALUE IS THE PAGE REALM'S OWN PICK, NOT A CONSTANT. It is
            # resolved through the same generation-filtered CORES_MEMORY pool,
            # the same hash and the same salt the emitted device.js uses, so
            # page and engine agree BY CONSTRUCTION. A hardcoded number would
            # pass a spot check on whichever profile happens to match it and
            # would replace a page/worker mismatch with a page/engine mismatch
            # on every other profile -- the same tell, relocated. Measured
            # live: profiles resolve to 4, 6, 8 and 16 across the pool, so
            # "it's 8" is false for most of them.
            #
            # ⛔ MOBILE TAKES ITS OWN VALUE, and this branch is load-bearing —
            # the exact twin of the deviceMemory branch below, and it was
            # MISSING here until PS-394. This list is built OUTSIDE the
            # mobile/desktop if-else above, so a mobile profile reaches it too,
            # and mobile's core count comes from its DEVICE PRESET (an iPhone
            # declares 6, every Android preset 8), not from the desktop
            # CORES_MEMORY pool. mobile_ext.py's JS authors that same preset
            # value in every realm a content script can reach, so passing the
            # desktop pick put a DIFFERENT number in the realms JS cannot reach
            # — the ServiceWorker above all. Measured on the release engine
            # before the fix: five of six mobile profiles disagreed with
            # themselves (iphone-15 page=6 sw=4/12/6; galaxy-s23 page=8 sw=12;
            # xiaomi-13 page=8 sw=4/6), and the one that agreed did so by
            # coincidence when the desktop draw happened to equal the preset.
            # A page that spawns a ServiceWorker and compares
            # navigator.hardwareConcurrency identifies the profile in two lines
            # with no knowledge of the host — the same class of tell the
            # desktop arm of PS-354 removed, left live on the mobile arm.
            # ⚠️ A DEDICATED Worker cannot see it: applyHwPatch reaches those.
            # Pinned by tests/test_ps394_mobile_hardware_concurrency.py.
            f"--fingerprint-hardware-concurrency="
            f"{preset.hardware_concurrency if (is_mobile and preset is not None) else hardware_concurrency_for(profile.fingerprint_seed, profile.hardware_generation)}",
            # navigator.deviceMemory, authored NATIVELY rather than by a JS
            # descriptor (pixelscan port, slice 2). Before this the engine
            # returned a HARDCODED 8 for every profile
            # (005-hardware-concurrency-fingerprint.patch: `return 8;`) while
            # device_ext.py re-declared the same property in two JS realms — so
            # the value was both unauthored by the seed AND carried by exactly
            # the detectable descriptor this port exists to remove. Both JS
            # sites are deleted in the same change; this flag is what replaces
            # them, and it reaches the ServiceWorker realm neither of them could.
            #
            # ⭐ WHY THIS IS NOT SEED-VARYING, AND WHY THAT IS CORRECT. The
            # Device Memory API reports RAM rounded DOWN to a power of two and
            # CLAMPED AT 8, so an 8 GB and a 16 GB machine both report 8 — the
            # spec discards the difference. persona's pool has RAM {8, 16}, so
            # every profile legitimately reports 8. That is the real browser's
            # behaviour, not a lost opportunity: a per-seed value here would
            # contradict the profile's own claimed RAM and publish a figure no
            # capped browser produces. `spec_device_memory` is applied at this
            # boundary so the ENGINE is handed an already-legal value, and the
            # patch clamps again defensively so no path can publish an illegal
            # one.
            #
            # ⛔ MOBILE TAKES ITS OWN VALUE, and this branch is load-bearing.
            # This list is built OUTSIDE the mobile/desktop if-else above, so a
            # mobile profile reaches it too — and mobile's deviceMemory comes
            # from its DEVICE PRESET (an iPhone reports 4), not from the desktop
            # CORES_MEMORY pool. Passing the desktop pick to an iPhone profile
            # would put desktop RAM in the ServiceWorker realm while
            # mobile_ext.py's JS says 4 everywhere else — a realm disagreement
            # inside one launch, which is the exact tell this slice removes on
            # the desktop arm.
            f"--fingerprint-device-memory="
            f"{spec_device_memory(preset.device_memory) if (is_mobile and preset is not None) else device_memory_for(profile.fingerprint_seed, profile.hardware_generation)}",
            # ⛔ THE SWITCH THAT MUST NEVER APPEAR IN THIS LIST: --disable-spoofing.
            #
            # A reader auditing the fingerprint switches will notice that patch
            # 000 declares thirteen and this launch passes seven, and the natural
            # next thought is "wire the rest". This one is the counter-example
            # that makes that instinct wrong, and the reason is recorded HERE —
            # beside the flags it sits among — rather than in a test, because
            # this is where the question gets asked.
            #
            # --disable-spoofing is the most consumed switch in the whole patch
            # set after --fingerprint itself: SEVEN patches read it — 003, 006,
            # 011, 012, 013, 014 and 016.
            #
            # ⚠️ THE MECHANISM, because getting it wrong points the warning at
            # the wrong form of the flag:
            # it is a value-keyed selective disable, not a boolean kill switch.
            # Every consumer tests the switch's VALUE for a token, never its
            # mere presence. The dominant shape (003, 006, 012, 013, 014, 016)
            # is:
            #
            #     if (HasSwitch(kFingerprint) &&
            #         (!HasSwitch(kDisableSpoofing) ||
            #          GetSwitchValueASCII(kDisableSpoofing).find("canvas")
            #              == std::string::npos)) { ...spoof... }
            #
            # Trace the BARE flag (empty value) through it: HasSwitch is true,
            # so the `!HasSwitch` arm is false; then "".find("canvas") returns
            # npos, so the `== npos` arm is TRUE, the `||` is true, and THE
            # SPOOFING STILL APPLIES. 011 is the only genuine `return ""`, and
            # it inverts the test (`find("gpu") != npos`), which an empty value
            # fails identically. Conclusion:
            # bare --disable-spoofing is inert across all seven patches.
            #
            # ⛔ THE FORM THAT DOES THE DAMAGE IS THE VALUED ONE:
            # --disable-spoofing=canvas,gpu,audio,font,clientrects
            #
            # The tokens are matched by SUBSTRING, one per masking family:
            # `audio` (003) switches off the AudioContext sample-rate noise,
            # `font` (006) the font masking, `gpu` (011) the GL
            # vendor/renderer spoof, `canvas` (012, 013, 016) getImageData /
            # toDataURL / measureText and WebGL readPixels, `clientrects`
            # (014) the client-rects offset. That comma list is upstream's
            # kill switch for the whole masking layer, present so a developer
            # can A/B the patched engine against stock.
            # The prohibition is on the VALUED form; the bare form is inert.
            #
            # (015 edits code INSIDE the guard 012 authored and adds no read of
            # its own — it carries the constant on CONTEXT lines only, which is
            # why the census counts seven consumers and not eight.)
            #
            # So its absence from this list is a DELIBERATE POSITION, not an
            # oversight, and it is the one row of the switch census where
            # "declared, consumed, and correctly never passed" is the finished
            # state. Pinned by tests/test_engine_switch_matrix.py, which
            # re-reads this paragraph so deleting it turns the suite red rather
            # than silently converting a decision into an unexplained gap.
            f"--lang={lang}",
            f"--accept-lang={lang},{lang.split('-')[0]}",
            f"--load-extension={','.join(extensions)}",
            "--no-first-run",
            "--no-default-browser-check",
            # Chromium 130+ shows a default-search choice screen (EEA) and, until the
            # choice is recorded, drives the default from the prepopulated set —
            # which overrides the profile's chosen engine. Suppress it so the seeded
            # engine / search-override extension is what takes effect.
            "--disable-search-engine-choice-screen",
            "--restore-last-session",
            "--hide-crash-restore-bubble",
            "--force-dark-mode",
            # Keep the page's Page-Visibility state "visible" and its rAF running
            # even when the window isn't the foreground/focused window. Chromium
            # otherwise marks a non-foreground or occluded window hidden and throttles
            # requestAnimationFrame to ~0fps. Google Sheets mounts its overlays — the
            # date-cell calendar picker and the custom-currency dialog — via
            # rAF-driven animations, so a throttled window never paints them: they
            # read as "dead"/never-open on every OS, while the grid (already drawn)
            # looks fine. Firefox drives those overlays off a path that isn't
            # visibility-throttled, which is why it was never affected. Measured on a
            # real-GPU host: a persona window reported visibilityState=hidden and rAF
            # fired 0 frames in 5s; with visibility forced, rAF ran full speed and the
            # overlays opened.
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-background-timer-throttling",
        ]
        # Windows computes native window occlusion and marks a covered window hidden;
        # that alone throttled rAF to zero even with the backgrounding flags above.
        disabled_features.append("CalculateNativeWinOcclusion")

        if _platform.IS_MACOS:
            # Keep the cookie-encryption key out of the login Keychain (no Keychain
            # prompt, no host-identity leak) — the password-store flags Linux also
            # uses. Separate from the Linux SwiftShader block (must NOT run on mac).
            args += ["--password-store=basic", "--use-mock-keychain"]

        if _platform.IS_LINUX:
            # Software GL (SwiftShader) keeps the GPU process alive so the
            # fingerprint WebGL spoofer populates a believable vendor/renderer;
            # --disable-gpu left a blank WebGL that flagged as fake. On Windows the
            # native D3D11 ANGLE backend renders correctly — forcing SwiftShader for
            # the whole GL stack there paints a BLACK, unrendered window, so keep
            # these Linux-only. The keychain flags are Linux/mac password-store
            # concepts and are meaningless (and unneeded) on Windows.
            args += [
                "--use-gl=angle",
                "--use-angle=swiftshader",
                "--enable-unsafe-swiftshader",
                "--password-store=basic",
                "--use-mock-keychain",
                # Under the software (SwiftShader) compositor the frame clock is
                # degenerate ("Frame latency is negative") and a browser-UI
                # animation can spin without ever reaching its end state — the log
                # shows "CompositorAnimationObserver is active for too long (180s)
                # location=Button". While it spins, the compositor never idles, so
                # the "Working…" throbber sticks and Google Sheets' overlays (the
                # date-cell calendar, the custom-currency menu) never get a frame
                # to paint into. Starve those animations at the source instead of
                # trusting the clock: --animation-duration-scale=0 completes every
                # gfx UI animation on its first tick (read in
                # ui/compositor/compositor.cc → ScopedAnimationDurationScaleMode →
                # LinearAnimation::GetDuration) and --wm-window-animations-disabled
                # drops window show/hide animations outright
                # (ui/wm/core/window_animations.cc). Both act on browser UI only —
                # web-content animations are Blink-side — so nothing is
                # page-visible (unlike prefers-reduced-motion, which is).
                # --disable-threaded-animation stays so the compositor thread holds
                # no animation state of its own to get stuck on.
                "--disable-threaded-animation",
                "--animation-duration-scale=0",
                "--wm-window-animations-disabled",
                # Under SwiftShader the frame clock is degenerate, and chromium's
                # vsync throttle paces the compositor off it: measured
                # requestAnimationFrame ran at ~6fps in Google Sheets. Detaching from
                # the broken vsync clock lifts it back to ~60fps. (Harmless, no GPU
                # readback stalls — unlike --disable-gpu-compositing, which forced a
                # CPU readback that spammed "GL_CLOSE_PATH_NV: GPU stall due to
                # ReadPixels" and did NOT fix the real Sheets-through-proxy hang.)
                "--disable-gpu-vsync",
            ]
            # The VM has no VA-API hardware, so chromium's attempt to init
            # hardware video decode logs a red "vaInitialize failed: unknown
            # libva error" (media/gpu/vaapi/vaapi_wrapper.cc). Harmless, but it
            # noises the log AND is a VM tell — real desktop Chrome on a GPU
            # inits VA-API fine, a hard failure says "no hardware". Don't try:
            # software decode is what a machine without video hardware uses.
            disabled_features += ["VaapiVideoDecoder", "VaapiVideoEncoder"]

        # Wayland app_id (taskbar label/icon per persona) is an X11/Wayland concept;
        # only pass it on Linux. Matches the .desktop StartupWMClass via app_id_for.
        if _platform.supports_linux_desktop_integration():
            args.append(f"--class={app_id_for(profile.name)}")

        if is_mobile and preset is not None:
            # Drive the real device's UA and a window sized to its CSS viewport, so
            # the browser presents the device's screen and layout. The mobile
            # extension fills the JS-visible touch/Client-Hints/screen signals.
            #
            # On Android the UA carries the INSTALLED engine's version (reduced
            # form) so it agrees with the Client Hints the extension emits; on
            # iOS the template has no version slot at all.
            args.append(f"--user-agent={preset.user_agent_for(chromium_version)}")
            args.append(f"--window-size={preset.width},{preset.height}")
        elif desktop_resolution is not None:
            # CAP THE WINDOW TO THE OPERATOR'S CHOSEN RESOLUTION (#327).
            #
            # WHY THE WINDOW AND NOT THE REPORTED SIZE. With the screen spoofed
            # SMALLER than the live window, three relations a real browser
            # always satisfies cannot all hold, and this is arithmetic rather
            # than a matter of taste — measured live at inner 1919 with a 1280
            # pick:
            #
            #   R1  outer >= inner    (a window contains its own content)
            #   R2  inner <= screen   (content fits the monitor)
            #   R3  outer <= screen   (the window fits the monitor)
            #
            #   R1 needs outer >= 1919; R3 needs outer <= 1280. EMPTY.
            #
            # So NOTHING the content script reports can satisfy both: pinning
            # `outer` down to the screen buys R3 by breaking R1, which is a
            # window smaller than its own content — negative chrome, and the
            # exact signature `_outer_size_probe` in invisible_launch.py
            # calibrates as "leaking". The root is R2: `inner` is the real
            # window's content box and is not spoofable at the reporting layer
            # without breaking layout on real pages.
            #
            # Capping the WINDOW fixes R2 at its source, and then all three
            # hold at once. Measured live under Xvfb, 1280x720 pick:
            #   inner [1280, 577]  outer [1280, 680]  screen [1280, 720]
            #   R1 1280>=1280 OK   R2 1280<=1280 OK   R3 1280<=1280 OK
            #
            # This is Firefox's own answer to the same question
            # (_seed_window_size, #216: "a window can't be wider than its
            # screen"), reached here through a launch flag because Chromium has
            # no persisted window-size seed.
            #
            # #167 IS NOT RE-OPENED: that leak was the FORCED branch falling
            # through to the auto-pick and reporting ~4K. This does not touch
            # how W/H are chosen — `screen.*` remains exactly the pick, and the
            # extension's FORCED branch is unchanged.
            #
            # AUTO IS UNTOUCHED: `parse_resolution("auto")` is None, so this
            # arm cannot fire and the AUTO branch keeps floor-picking a screen
            # that contains the real window, exactly as before.
            # ⭐ FIT TO THE HOST WORK AREA (PS-352). The cap above is the
            # operator's PICK, which can legitimately exceed the physical
            # monitor — a 2560x1440 pick on a 1920x1080 host overflows even at
            # scale 1.0, so this is a SEPARATE constraint from the DIP/scale
            # collision and neither one subsumes the other.
            #
            # `host_workarea_dip()` answers in DIP (the unit --window-size is
            # read in) and returns None when it cannot tell — non-Windows, or a
            # failed reading. ⛔ None must SKIP the fit, never become a zero:
            # min(pick, 0) is a zero-sized window, which is why the helper
            # refuses to express "unknown" as (0, 0).
            #
            # ⛔ screen.* IS NOT TOUCHED. The extension still reports the
            # operator's full pick; only the WINDOW shrinks. Shrinking the
            # reported screen to fit the host would change the identity of
            # every profile on every small-monitor machine — a masking
            # regression wearing the costume of a window fix (PS-167/PS-327).
            win_w, win_h = desktop_resolution[0], desktop_resolution[1]
            workarea = host_workarea_dip()
            if workarea is not None:
                win_w = min(win_w, workarea[0])
                win_h = min(win_h, workarea[1])
            args.append(f"--window-size={win_w},{win_h}")

        # RENDER SCALE — a THREE-WAY platform split, and the asymmetry is
        # deliberate. Do not "tidy" it into one branch (PS-352).
        #
        #   macOS          -> force the Retina 2x explicitly
        #   Windows/Linux  -> force NOTHING; Chromium uses native per-monitor DPI
        #
        # ⛔ WHY THE FLAG WAS REMOVED ON WINDOWS (overturning a deliberate
        # decision, so the reasoning is recorded rather than deleted). The old
        # comment here argued FOR forcing the host scale: "without it a dpr-1
        # profile paints 1:1 physical on a 150%/200% display, so a 2560x1440
        # profile renders unreadably small." That concern was real, but the
        # flag was not the only way to avoid it — and forcing it caused two
        # operator-visible defects on a 4K@150% host:
        #
        #   1. --window-size is interpreted in DIP, and this flag REDEFINES what
        #      a DIP is. A 2560x1440 pick under scale 1.5 asked for 3840x2160
        #      PHYSICAL — a window filling a 4K monitor edge to edge.
        #   2. ⭐ The forced-vs-native DPI mismatch MISLOCATED Chromium's own
        #      popups: on a dual-monitor host the three-dot menu opened on the
        #      ADJACENT MONITOR. A window-size cap alone could never have fixed
        #      this one, and it is why the first diagnosis was incomplete.
        #
        # WHAT SCALES THE UI NOW: Chromium's own per-monitor DPI awareness. It
        # applies the host's real scale itself, which is what the flag was
        # trying to force — verified live by the operator: content readable,
        # popups correctly positioned, window fits.
        #
        # macOS KEEPS the explicit 2x: it is single-scale (no per-monitor
        # mismatch, so no popup defect), and without the flag the UI paints
        # tiny. Mirrors the Firefox engine's macOS dpr fix.
        #
        # ⭐ THE FINGERPRINT IS UNAFFECTED, and this is the load-bearing check
        # on the removal. Render scale is DECOUPLED from the fingerprint: the
        # device/mobile extension pins the JS-visible screen.*,
        # devicePixelRatio and the matchMedia dppx answers, while
        # --force-device-scale-factor only sets how many physical pixels draw
        # one CSS px. The extension's DPR is `IS_MAC ? 2 : 1` — derived from
        # the PROFILE'S DECLARED OS, never from the host's scale — so what a
        # scanner reads is authored there and cannot move when this flag goes.
        # Pinned by tests/test_ps352_hidpi_window.py, which reads the values
        # from a page rather than from the argv.
        if _platform.IS_MACOS:
            scale = _host_display_scale()
            if scale != 1.0:
                args.append(f"--force-device-scale-factor={scale:g}")

        # Always pin a concrete timezone. With a proxy it follows the exit geo; with
        # NO proxy it must AGREE with the forced en-US language (lang above), not leak
        # the host zone — CreepJS on a Kyiv host showed en-US paired with Europe/Kyiv,
        # a language⊥timezone tell. A US zone keeps the direct identity coherent and
        # hides the host location (matches the Firefox path).
        # _tz was resolved by the fail-closed geo gate at the top of this function,
        # before any launch work; do not re-ask the helper here.
        args.append(f"--timezone={_tz}")

        if getattr(profile, "ai_control", False):
            # Port 0 makes the kernel assign an unpredictable ephemeral port instead
            # of a name-derived one a co-resident process could guess and drive (that
            # would bypass the MCP bearer token). Chromium writes the bound port to
            # <user-data-dir>/DevToolsActivePort; read_cdp_port resolves it there.
            # ai_control opens an UNAUTHENTICATED CDP channel any same-user process
            # can drive (the port is discoverable via a loopback scan / proc); see
            # the SECURITY NOTE in cdp.py. Only enable it on profiles that need it.
            args.append("--remote-debugging-port=0")
            # Chrome 132+ rejects a DevTools WebSocket whose Origin isn't allow-listed
            # (403 "Rejected an incoming WebSocket connection"). The client's Origin
            # includes the now-unknown ephemeral port, so it can't be pre-listed; the
            # unpredictable loopback port is the guard. A local attacker could forge
            # any Origin anyway, so an Origin allow-list adds nothing against the
            # co-resident threat this defends.
            args.append("--remote-allow-origins=*")

        proxy_server, bridge = _proxy_arg(proxy_url)
        # Defense in depth (audit7 #1): resolve() now gates on parseability, but
        # if a profile has a proxy assigned and we STILL ended up with no usable
        # proxy_server here, launching would silently skip the whole anti-leak
        # block and go DIRECT. Fail CLOSED rather than deanonymize.
        if getattr(profile, "proxy", None) and not proxy_server:
            raise ProxyUnresolvedError(
                f"Profile {profile.name!r} has proxy {profile.proxy!r} assigned but "
                "it did not yield a usable --proxy-server. Refusing to launch DIRECT."
            )
        if proxy_server:
            args.append(f"--proxy-server={proxy_server}")
            # Keep DNS and WebRTC from leaking past the proxy. Chrome's built-in
            # DNS-over-HTTPS resolves names directly to a DoH endpoint, bypassing
            # the SOCKS proxy entirely (so the DNS test shows a country unrelated
            # to the exit IP). Turn DoH off so name lookups go through the proxy,
            # and forbid WebRTC's non-proxied UDP which can reveal the real IP.
            disabled_features.append("DnsOverHttps")
            args.append("--dns-over-https-mode=off")
            args.append(
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"
            )
            args.append("--dns-prefetch-disable")
            # A SOCKS5 proxy tunnels only TCP; it has no UDP path. Google apps
            # (Sheets/Docs) prefer QUIC — HTTP/3 over UDP — for their realtime
            # collaboration channel, so behind the proxy that channel's UDP never
            # reaches Google, Chromium doesn't fall back cleanly, and the app hangs
            # on a permanent "Working" while the calendar / custom-currency overlays
            # that load through it never paint. Disable QUIC so every request uses
            # HTTP/2 over TCP, which the proxy carries. The webrtc flag above only
            # covers WebRTC's UDP, not QUIC's, so this is a separate switch. A Chrome
            # behind a UDP-blocking proxy runs without HTTP/3 too, so this reads as
            # normal, not as a spoof tell.
            args.append("--disable-quic")
            disabled_features.append("EnableQuic")

        if cert_session is not None:
            # Trust the terminator's leaf without touching any OS store — keyed to the
            # leaf's public-key hash, so only the terminator's MITM is trusted. This
            # only takes effect on a DIRECT connection, so route the admin host to the
            # terminator directly: resolve it to the terminator's loopback port and
            # bypass the proxy for it (its traffic still exits via the real proxy —
            # that's the terminator's own upstream). Everything else keeps the proxy.
            host = cert_session.admin_host
            args.append(
                f"--ignore-certificate-errors-spki-list={cert_session.spki_b64}"
            )
            args.append(
                f'--host-resolver-rules=MAP {host} 127.0.0.1:{cert_session.port}'
            )
            if proxy_server:
                args.append(f"--proxy-bypass-list={host}")
            # Log only the loopback terminator port. The admin host is an internal
            # hostname (e.g. admin.corp.example.com) that identifies the operator's
            # infra; it would land, one line per cert-profile launch, in the
            # persistent log + Activity Log (same class as the proxy-hostname fix).
            logger.info("chromium mTLS: terminator on 127.0.0.1:%s (direct, spki-pinned)",
                        cert_session.port)

        if disabled_features:
            args.append("--disable-features=" + ",".join(disabled_features))

        env = os.environ.copy()
        # The browser executes untrusted remote code, so it inherits none of the
        # operator's identity — above all SSH_AUTH_SOCK, which is a live handle
        # onto their ssh-agent rather than a passive label — and none of the
        # runtime paths persona's own process exported that no longer resolve
        # for the child (FONTCONFIG_*, whose full rationale lives beside the
        # list). `env` is a COPY, so this cannot touch persona's own
        # environment. See env_policy.py for every name on the lists, what is
        # deliberately left off them, and why. This is the SAME entry point the
        # firefox fork child reaches via scrub_current_process_environ: an
        # inline tuple here instead is exactly the divergence that left the
        # firefox child inheriting stale FONTCONFIG_* mount paths.
        scrub_inherited_environment(env)
        # The child's scratch directory goes INSIDE the profile, from the same
        # module and for the perimeter reason stated there: everything under the
        # profile's data dir is reached by delete_profile (which renames it into
        # the trash) and by wipe_all_profiles (which rmtrees it), and the host's
        # shared temp dir is reached by neither. Measured before this line
        # existed: a SIGKILLed session left an `org.chromium.Chromium.*`
        # directory — product-identifying by name — plus the engine's own
        # ~714MB AppImage extraction sitting in the host temp dir, both
        # outliving the profile entirely.
        #
        # NOT a scrub: TMPDIR is deliberately off both scrub lists, because
        # deleting it would send the child to /tmp — straight back outside the
        # perimeter. It has to be POINTED, and pin_child_tmpdir creates the
        # directory before the launch (an unwritable TMPDIR can stop the engine
        # starting). `env` is a COPY, so persona's own temp dir is untouched.
        # Pinned on ALL THREE platforms here, exactly like the scrub above,
        # because Popen(env=) sets the child's environment only.
        pin_child_tmpdir(env, profile_dir)
        if _platform.IS_LINUX:
            env.setdefault("DISPLAY", ":0")

        if getattr(profile, "ai_control", False):
            # Drop any DevToolsActivePort from a previous run so a reader can't
            # attach to a stale port; chromium rewrites it once it binds port 0.
            with _suppress():
                os.remove(os.path.join(profile_dir, "DevToolsActivePort"))

        proc = popen_in_new_session(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            # The browser child's working directory comes from env_policy, not
            # from a path written here. This line used to BE the pin — a bare
            # os.path.expanduser("~") with no comment, so a reader could not
            # tell deliberate isolation from an incidental default — while the
            # firefox seam pinned nothing and its child inherited persona's own
            # cwd. The VALUE is unchanged; what changed is that one place now
            # owns it, so the next person to move it cannot fix one engine and
            # forget the other. Popen(cwd=) sets the directory in the CHILD
            # only, so this seam is safe on every platform by construction.
            cwd=browser_child_cwd(),
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            # PS-192: the browser's own process group. persona's chromium is a
            # WRAPPER launch (fpchrome.AppImage) around a multi-process
            # browser, so the pid held here is two layers above the renderers.
            # Without a session of its own, `terminate(proc)` reaps the wrapper
            # and orphans the entire tree to init, where no handle can reach
            # it. Accepted on every platform: POSIX honours it, Windows's
            # _execute_child takes it as `unused_start_new_session`.
            #
            # ⚠️ VIA THE HELPER, NOT BY HAND. `popen_in_new_session` also
            # RECORDS the group on the handle, and both halves are required.
            # This site passed `start_new_session=True` by itself and got only
            # the first half: `wait_for_exit` (launcher.py:400) waits the
            # leader on EVERY launch, after which `getpgid` answers ESRCH, the
            # teardown re-resolves to None and degrades to a single-process
            # kill — the original leak, on the product path. Measured at 3/3
            # orphans surviving the real `terminate()`; 0/3 through the helper.
            **_platform.no_window_kwargs(),
        )
        # Claim both loopback listeners for THIS browser, now that it exists.
        # They had to bind first (their ports go on the command line above), so
        # until this point they serve nobody. Chromium does not connect from this
        # pid — its network service is a child — so the gate authorizes the whole
        # descendant tree, not a single process.
        if bridge is not None:
            bridge.bind_to_process(proc.pid)
        if cert_session is not None:
            cert_session.bind_to_process(proc.pid)
        proc._proxy_bridge = bridge  # type: ignore[attr-defined]
        proc._cert_session = cert_session  # type: ignore[attr-defined]
        return proc
    except BaseException:
        # Any failure between starting the terminator/bridge and returning the
        # live proc (the ~10 build_*_extension disk-I/O calls, _proxy_arg, etc.)
        # would orphan those loopback listeners; repeated fails exhaust ephemeral
        # ports. Stop whatever was started, then re-raise.
        if bridge is not None:
            with _suppress():
                bridge.stop()
        if cert_session is not None:
            with _suppress():
                cert_session.stop()
        raise


def _stop_bridge(proc: subprocess.Popen) -> None:
    bridge = getattr(proc, "_proxy_bridge", None)
    if bridge is not None:
        with _suppress():
            bridge.stop()
        proc._proxy_bridge = None  # type: ignore[attr-defined]
    session = getattr(proc, "_cert_session", None)
    if session is not None:
        with _suppress():
            session.stop()
        proc._cert_session = None  # type: ignore[attr-defined]


def terminate(proc: subprocess.Popen, name: str, timeout: int = 5) -> None:
    """Gracefully terminate a browser process TREE, force-kill on timeout.

    PS-192: the audience is the process GROUP, not the single pid held here.
    persona's engine is a wrapper (``fpchrome.AppImage``) around a
    multi-process browser, so signalling the handle alone reaps the wrapper and
    leaves the zygote, the GPU process and every renderer alive, reparented to
    init and unreachable from any handle we ever had. Measured at ~35 surviving
    processes per launch (PS-185); observed at 361% CPU for 12.5h on a user's
    workstation.

    ⚠️ AN ALREADY-EXITED PARENT STILL GETS THE GROUP SIGNAL. The early return
    that used to sit here — ``if proc.poll() is not None: return`` — is exactly
    the leak's favourite path: a wrapper that has already handed off and exited
    reads as "nothing to do" while its children are the whole problem. The
    parent's exit status is not evidence about its descendants.
    """
    try:
        # terminate -> wait -> kill escalation is preserved inside the reaper,
        # which falls back to single-process signalling when the child never
        # got its own session (and refuses to signal OUR group — see
        # process_group's self-kill guard).
        reap_process_group(proc, timeout=timeout)
        logger.info("Browser %s process group torn down", name)
    except Exception as e:
        logger.exception("Error terminating browser %s: %s", name, e)
    finally:
        _stop_bridge(proc)


def wait_for_exit(
    proc: subprocess.Popen,
    name: str,
    notify_stopped: Callable[[], None],
) -> None:
    """Block until the process exits, then fire the callback."""
    try:
        proc.wait()
    except Exception as e:
        logger.exception("Wait error for profile %s: %s", name, e)
    finally:
        _stop_bridge(proc)
        notify_stopped()


class _suppress:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> bool:
        return True
