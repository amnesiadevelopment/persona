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
from .device_ext import build_device_extension
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
        # Names the COUNTRY, and says the remedy is a code change rather than a
        # re-check — the same two things the TimezoneUnderivableError arm below
        # says, and for the same reason: the proxy's check may have passed
        # moments ago and will keep passing, because what is missing is a table
        # row. Sending this operator to "check the proxy" wastes their time.
        #
        # It names BOTH tables. Adding one row alone is precisely how this class
        # of defect is reintroduced, and the correspondence suite fails it in
        # either direction, so the message asks for the pair.
        raise LocaleUnderivableError(
            f"Profile {profile.name!r} has proxy {profile.proxy!r} assigned and its "
            f"exit country is known ({code.upper()}), "
            "but no locale is known for that country. Refusing to launch: falling "
            "back to en-US would declare an American-English browser beside the "
            "exit's own non-US clock — the 'spoofed location' tell this product "
            "exists to avoid. Re-checking will NOT help; add a row for that "
            "country to _COUNTRY_LOCALE *and* the matching _COUNTRY_TZ row "
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
    proxy = store.get(profile.proxy) if profile.proxy else None
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
    proxy = store.get(profile.proxy) if profile.proxy else None
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
        extensions.append(
            build_measuretext_extension(
                os.path.join(profile_dir, ".persona-measuretext-ext")
            )
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
            extensions.append(
                build_device_extension(
                    profile.fingerprint_seed,
                    os.path.join(profile_dir, ".persona-device-ext"),
                    profile.hardware_generation,
                    resolution=parse_resolution(getattr(profile, "resolution", "auto")),
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
            "--fingerprint-brand=Chrome",
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

        # Render scale is decoupled from the fingerprint: the device/mobile
        # extension pins the JS-visible screen.*, devicePixelRatio and the
        # matchMedia dppx answers, while --force-device-scale-factor only sets how
        # many physical pixels draw one CSS px. Without it a dpr-1 profile paints
        # 1:1 physical on a 150%/200% display, so a 2560x1440 profile renders
        # unreadably small even though scanners see the correct 2K/dpr-1 screen.
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
