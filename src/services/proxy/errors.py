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
    it — the check recorded no usable zone and the country is not one of the
    ~29 rows ``_COUNTRY_TZ`` knows.

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
