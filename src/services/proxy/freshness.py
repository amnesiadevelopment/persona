"""How fresh — and how TRUSTWORTHY — a proxy's recorded geography is.

This predicate used to live in ``src/ui/components/profile_card.py``, where it
answered one question for one caller: which glyph to draw next to a profile. But
the same question governs a second, heavier decision — whether a profile may
LAUNCH declaring that geography — and ``src/services/`` cannot import from
``src/ui/`` (it doesn't, anywhere: the render layer is a leaf). So the knowledge
was trapped in the renderer while the launcher stayed structurally blind to it.

It lives here now, beside the model it describes, with ONE definition consulted
by both the renderer and the launch policy. ``profile_card`` imports it back and
renders exactly as before; ``launch_policy`` consults it to refuse a launch on
geography the product's own most recent evidence has DISPROVEN.

Read the threshold note on PROXY_STALE_AFTER_S before reusing it: only the
failed/verified DISTINCTION crosses into the launch path. The age threshold
remains render-only, deliberately.
"""

from __future__ import annotations

from ...models.proxy import Proxy

# How long a successful proxy check stays good enough to be drawn as a flag.
# Past this, the indicator reports "last known, and it is old" instead of
# continuing to assert a country: the exit behind a rotating/backconnect URL
# moves without any event reaching us, so an ageless flag would keep growing in
# confidence while its evidence rots.
#
# RENDER-ONLY, and that is a deliberate boundary, not an oversight. Crossing
# this threshold never triggers a re-check, and it never refuses a launch. It
# was calibrated for a render ("should this flag look confident?"), which does
# NOT transfer to a refusal ("should this profile be forbidden to launch?"):
# rotating/backconnect proxies are the product's stated target configuration, so
# staleness is their steady state, and a launch-time age limit would lock
# operators out of their own profiles between checks. A "stale" proxy therefore
# still launches. See launch_policy._proxy_timezone.
PROXY_STALE_AFTER_S = 24 * 60 * 60


def proxy_indicator_state(proxy: Proxy, now: float) -> str:
    """Which indicator state a proxy is in, as a function of age AND outcome.

    Pure: it reads the stored `checked_at` / `last_check_ok` and nothing else —
    it never probes, so calling it cannot open a socket. That is what makes it
    safe to consult on the launch path, which must not perform live
    verification.

    - "failed"      -> the last check failed. A failure does not age into
                       something softer; it stays a failure at any age.
    - "unverified"  -> no successful check is on record (`checked_at == 0.0`),
                       whatever else is stored. A country code written to disk
                       at an unrecorded past moment is not evidence.
    - "stale"       -> verified, but longer than PROXY_STALE_AFTER_S ago.
    - "verified"    -> verified within the threshold.

    The two fields are read via getattr so the predicate also answers for the
    duck-typed proxy stand-ins the launch-path tests use (which model geography
    but not check bookkeeping). A real `Proxy` always carries both, so this is
    byte-identical for every rendered record — it only widens what may be asked.
    """
    last_check_ok = getattr(proxy, "last_check_ok", None)
    checked_at = getattr(proxy, "checked_at", 0.0)

    if last_check_ok is False:
        return "failed"
    # A stored country_code alone never produces a verified state: without a
    # timestamp there is no moment at which anything was confirmed.
    if not checked_at or not last_check_ok:
        return "unverified"
    if (now - checked_at) > PROXY_STALE_AFTER_S:
        return "stale"
    return "verified"
