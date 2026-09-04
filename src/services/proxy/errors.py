"""Errors shared across the consumers that route through a profile's proxy."""

from __future__ import annotations


class ProxyUnresolvedError(RuntimeError):
    """A profile has a proxy ASSIGNED but it could not be resolved to a usable
    URL (deleted/renamed proxy, or a stored value that lost its scheme/port).

    Every path that routes through a profile's exit IP — the browser launch and
    the SSH/SFTP/tmux session — must fail CLOSED on this: connecting anyway
    would go DIRECT from the operator's real IP, a de-anonymization.
    """


class GeographyUnknownError(RuntimeError):
    """A profile's proxy RESOLVES but carries no geography (never successfully
    checked), so there is no honest answer to "where does this persona claim to
    be?".

    The launch must fail CLOSED. The old behaviour derived the answer from the
    HOST — declaring the operator's real timezone inside the tunnel, a
    real-location disclosure on the very vector the proxy exists to close.

    When no geography is available the answer is STOP: not a host-derived value,
    not a coarser value, not a quieter value. A persona that will not launch has
    disclosed nothing. The remedy is one click — check the proxy, which writes
    country_code + timezone (ProxyStore.mark_checked) — and the profile then
    launches declaring the EXIT's zone.

    Deliberately an exception rather than a sentinel string: the unknown must be
    UNREPRESENTABLE as a zone, so no caller can accidentally ship it to an
    engine as if it were a real timezone.
    """


class TimezoneUnderivableError(GeographyUnknownError):
    """A profile's proxy carries a COUNTRY, but no timezone can be derived for
    it — the check recorded no usable zone and the country has no row in
    ``_COUNTRY_TZ``.

    Deliberately no row COUNT here. The table grows, and a number written into
    prose is wrong the first time someone adds a row — which is precisely what
    happened to the sentence this replaces. ``len(_COUNTRY_TZ)`` is one call
    away and is always right; a figure quoted here is only ever a snapshot.

    Distinct from both siblings in CAUSE, not in consequence. The parent means
    "we never learned where this exits"; ``GeographyDisprovenError`` means "we
    looked, and what we stored is contradicted"; this means **"we know the
    country and cannot say what time it is there."**

    What it replaces is the reason it exists. ``_timezone_for`` used to answer
    this case with ``"UTC"`` — silently, with no warning and no refusal. That is
    not a degraded profile, it is a CONTRADICTORY one: the locale table declares
    the country (``zh-TW`` for Taiwan) while the zone declares UTC, so a checker
    reading both sees a machine claiming one place whose clock says another.
    ``launch_policy``'s own comment names the cost: *"otherwise a direct profile
    shows UTC and scanners flag a 'spoofed location'"*. ``UTC`` is not a
    legitimate value for ANY key in ``_COUNTRY_TZ`` — nothing in the table maps
    to it — so it was never an approximation, only a sentinel that silently
    announced "unknown" in a field an engine consumes as fact.

    Fail CLOSED, for the same reason the siblings do. A profile that will not
    launch has disclosed nothing; a profile that launches declaring a location
    its own locale contradicts has disclosed that it is spoofed.

    An exception rather than a sentinel string, on the parent's stated rule: the
    unknown must be UNREPRESENTABLE as a zone, so no caller can ship it to an
    engine as though it were a real one. A returned ``"UTC"`` is exactly the
    accident that rule exists to prevent, and it was reachable for years.

    The remedy is NOT a re-check — unlike both siblings. The check may already
    have passed; the geo response simply carried no ``/``-form zone (an
    abbreviation like ``"CST"`` fails ``_validate_geo``'s substring test, as
    does an absent field). The remedy is to add the country's row to
    ``_COUNTRY_TZ``, which is why the message names the country: an operator who
    cannot launch must be told WHICH country and WHY, not handed a generic
    error. A refusal nobody can diagnose is a worse product than a wrong zone.
    """


class LocaleUnderivableError(GeographyUnknownError):
    """A profile's proxy carries a COUNTRY, but no locale can be derived for it
    — the country has no row in ``_COUNTRY_LOCALE``.

    The exact mirror of ``TimezoneUnderivableError``, and it exists because its
    absence was the defect. ``_locale_for`` used to answer this case with
    ``"en-US"`` while ``_timezone_for`` RAISED for the identical input, so the
    two halves of one derivation took opposite positions on the same country.

    ⚠️ AND THE REFUSAL DID NOT SAVE IT, which is the part that is easy to get
    wrong. ``_proxy_timezone``'s FIRST branch returns the zone the check
    recorded and never reaches ``_timezone_for``, so an ordinary PASSING check
    against a Bulgarian exit shipped ``en-US`` beside ``Europe/Sofia`` — an
    American-English browser whose clock is in Sofia. The zone half being strict
    did not stop it; it only guaranteed that when the two halves disagreed, the
    disagreement SHIPPED.

    Why ``en-US`` was worse than the ``UTC`` it mirrors. ``UTC`` was reachable
    for NO key in ``_COUNTRY_TZ``, so it could only ever mean "unknown" —
    detectable, at least in principle, as a sentinel. ``en-US`` is a legitimate
    row in ``_COUNTRY_LOCALE`` (``US`` maps to it), so an invented answer here
    is byte-identical to a genuine American one and nothing downstream can tell
    "this profile is American" from "we do not know what this profile is".

    ⚠️ NOT RAISED FOR THE NO-COUNTRY CASE, and that distinction is the whole
    point of this class rather than a caveat on it. ``_locale_for("")`` still
    returns ``"en-US"``, because an empty country is not an unanswerable country
    — it is the DIRECT (no-proxy) path, where persona forces ``en-US``
    deliberately so it never leaks the host locale, and pins a US zone so the
    pair agrees. Raising there would break a coherent, intentional identity in
    the name of fixing an incoherent, accidental one.

    Fail CLOSED, for the reason every sibling here does. A profile that will not
    launch has disclosed nothing; a profile that launches declaring a language
    its own clock contradicts has disclosed that it is spoofed.

    An exception rather than a sentinel string, on the parent's stated rule: the
    unknown must be UNREPRESENTABLE as a locale, so no caller can ship it to an
    engine as though it were a real one. Both engines consume this value as
    fact — Firefox as ``"locale"``, Chromium as ``--lang`` / ``--accept-lang``
    plus the locale and voice extensions — so a returned ``"en-US"`` reached
    four separate surfaces at once.

    The remedy is NOT a re-check — like ``TimezoneUnderivableError`` and unlike
    the other two siblings. The check may already have passed and will keep
    passing; what is missing is a table row, not a check result. So the message
    names the COUNTRY: an operator who cannot launch must be told WHICH country
    and WHY. Adding the row means adding it to BOTH tables — the correspondence
    suite fails a one-sided row in either direction, which is the mechanism that
    stops this defect being reintroduced one table at a time.
    """


class ExitCountryUnknownError(GeographyUnknownError):
    """A profile's proxy carries a TIMEZONE but no COUNTRY, so the locale half of
    the derivation has nothing to derive FROM.

    ⚠️ NOT the same state as ``LocaleUnderivableError``, and conflating the two
    is what let this case ship. That one means *"we know the country and have no
    row for it"* — the remedy is a code change. This one means **"we do not know
    which country this exits in at all"** — the remedy is a re-check, and there
    is no row to add. Telling an operator to add a ``_COUNTRY_LOCALE`` row for a
    country nobody can name is an instruction they cannot follow.

    ⚠️ NOR is it the parent, and the difference is the same one
    ``GeographyDisprovenError`` was split out for. The parent says *"the proxy
    has never been checked successfully"*. Here the check very likely PASSED —
    it simply answered without a country — so the parent's sentence and its
    ``"proxy never checked"`` label would both be false on the card, sending the
    operator to look for a failure that did not happen.

    Two shipped paths produce this record deliberately, which is why it needed a
    name rather than a guard:

    * ``proxy_checker._resolve_geo`` REMEMBERS a partial — a 200 that carried a
      usable timezone but no country is kept rather than discarded, on the
      stated reasoning that condemning a healthy exit is worse than a partial
      answer. ``ProxyStore.mark_checked`` then stores ``country_code=""`` beside
      a real zone and ``last_check_ok=True``.
    * ``proxy_checker._validate_geo`` DROPS a country code that is not two
      alphabetic characters, while keeping any timezone containing ``/``. A
      lying or malformed endpoint therefore yields the same shape, and
      ``test_proxy_checker_socks`` pins that dropping as correct.

    Why it must refuse rather than fall back to ``en-US``. The zone half answers
    for this record — ``_proxy_timezone``'s FIRST branch returns the recorded
    zone without consulting any country — so a fallback ships an
    American-English browser beside a Sofia clock, which is the exact
    contradiction the locale refusal exists to stop. The country being unknown
    makes the invented locale MORE wrong, not less: there is not even a country
    row that could have justified it.

    ⚠️ A REAL BEHAVIOURAL CONSEQUENCE, stated rather than discovered later: a
    proxy on the partial path was launchable before this class existed and is
    not now, until a check records a country. That is the correct answer under
    the fail-closed rule every sibling here follows — a coherent refusal beats
    an incoherent launch — and it is strictly narrower than what the zone half
    already does to a proxy carrying no geography at all. It is a real cost on a
    path another module built on purpose, so it is named here.

    Also covers a code ISO 3166-1 reserves to mean "not a country" (``ZZ``): a
    proxy carrying one is in this state, not in ``LocaleUnderivableError``'s.
    ``_locale_for("ZZ")`` still answers ``en-US`` untouched — that function
    cannot see whether a proxy exists, and for the DIRECT path ``en-US`` is the
    deliberate policy answer (#218). Only the CALLER knows a proxy is present,
    so only the caller can tell "no country supplied" from "no country known".
    """


class GeographyDisprovenError(GeographyUnknownError):
    """A profile's proxy carries geography, but the most recent check FAILED —
    so the product's own latest evidence says that geography is untrue.

    Distinct from its parent in CAUSE, not in consequence. The parent means "we
    never learned where this exits"; this means "we looked, and what we stored
    is contradicted". Both refuse the launch, because a profile that declares a
    location its owner's own evidence disproves is incoherent: the zone shipped
    is the exit's LAST-RECORDED zone, which the failed check gives us no reason
    to believe is still the exit.

    A SUBCLASS on purpose. Every existing handler and test says
    `except GeographyUnknownError` / `pytest.raises(GeographyUnknownError)` —
    the launcher's report path, process.py's re-raise, PS-31's refusal suite —
    and all of them must keep catching this without being touched. Subclassing
    buys the distinction where it matters (the operator-facing message can say
    "the check failed" instead of "never checked") while the fail-closed
    plumbing stays exactly as PS-31 left it.

    The remedy is the same one click: re-check the proxy. A passing check writes
    fresh geo and `last_check_ok=True` (ProxyStore.mark_checked) and the profile
    launches again, declaring the exit's zone.
    """
