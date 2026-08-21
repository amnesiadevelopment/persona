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
