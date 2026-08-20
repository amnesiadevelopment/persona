"""Profile coherence: the rules that decide whether a profile describes a
machine that could actually exist.

These two rules used to live in the profile dialog (``ui/dialogs/profile.py``),
which narrowed its dropdowns so an incoherent pair could not be picked. That is
good UX and it stays — but it left the rules in ONE caller, so every other door
into the model (REST create, REST update, the automation lane, an importer, a
migration script) re-posed the same question from scratch and answered it
differently. The REST lane answered it by not asking: it stored whatever it was
given, and a ``firefox`` + ``macos`` profile launched presenting Windows while
the record, the API response and the operator all said macOS.

So the rules live HERE, below every door, and the manager applies them on the
paths that write a profile. A guard added at a route would cover that route
only; a rule the model enforces covers the doors nobody has written yet.

The two rules
-------------

**Rule 1 — a mobile OS forces the Chromium engine.** The Firefox engine is
desktop-only (no mobile mode), and only chromium carries the device presets that
make an android/ios profile coherent.

**Rule 2 — the Firefox engine pins the OS to Windows.** stealth-Firefox reports
a Windows platform regardless of ``os_type`` (#211), so a macOS/Linux Firefox
profile is an inconsistent lie. Changing what the engine REPORTS is a
masking-direction question and deliberately not attempted here; this module
stops the product from CLAIMING otherwise.

Both reduce to one predicate — the Firefox engine requires ``os_type ==
"windows"`` — because chromium is the only other engine and it honors
``os_type``. They are kept as distinct messages because the REASONS differ and
the caller acts on the reason.

Already-stored incoherent records
---------------------------------

Records written before these rules (or through the unguarded REST lane) are NOT
stranded. Two deliberate choices:

* ``coherent_engine`` reconciles the pair at LAUNCH, and it is what
  ``services.browser.process.effective_engine`` delegates to — so a stored
  ``firefox`` + ``macos`` profile launches CHROMIUM, which honors ``os_type``
  and therefore actually presents the macOS the record claims. This resolves
  the lie in favour of the record rather than in favour of the engine, and it
  extends the answer ``effective_engine`` already gave for the mobile half.
* An edit may not INTRODUCE incoherence, but is never blocked by incoherence it
  did not introduce — see ``ProfileManager.update_profile``. Otherwise an
  ordinary edit to an unrelated field (a note, a tag) would refuse on a
  pre-existing pair, making an already-stored profile uneditable.
"""

from __future__ import annotations

from ..browser.device_presets import is_mobile_os

#: The engine every coherent non-Windows / mobile profile runs on.
DEFAULT_ENGINE = "chromium"

#: The only OS the Firefox engine can honestly claim (Rule 2).
FIREFOX_OS = "windows"


class IncoherentProfile(ValueError):
    """A profile whose os_type/engine pair describes a machine that cannot exist.

    Raised by the model, not by a route, so every door hits it.
    """


def normalize_engine(engine: str | None) -> str:
    """The engine name in current terms.

    ``"camoufox"`` is the retired name of the Firefox engine; an old record can
    still carry it, and it must be read as Firefox rather than as some unknown
    third engine that trivially satisfies every rule.
    """
    if not engine:
        return DEFAULT_ENGINE
    return "firefox" if engine == "camoufox" else engine


def coherence_error(os_type: str, engine: str | None) -> str | None:
    """The reason this os_type/engine pair cannot exist, or None if it can.

    The message is written for the caller to act on: it names both fields, the
    conflict, and which way to resolve it.
    """
    if normalize_engine(engine) != "firefox":
        # chromium honors os_type, mobile included — nothing to refuse.
        return None
    if is_mobile_os(os_type):
        # Rule 1.
        return (
            f"engine 'firefox' cannot be combined with the mobile os_type "
            f"{os_type!r}: the Firefox engine has no mobile mode, so a mobile "
            f"profile must use the '{DEFAULT_ENGINE}' engine (which carries the "
            f"device presets that make a mobile profile coherent)"
        )
    if os_type != FIREFOX_OS:
        # Rule 2.
        return (
            f"engine 'firefox' cannot be combined with os_type {os_type!r}: the "
            f"Firefox engine reports a '{FIREFOX_OS}' platform regardless of "
            f"os_type, so this profile would present Windows while claiming "
            f"{os_type!r}. Use os_type '{FIREFOX_OS}' with the Firefox engine, "
            f"or the '{DEFAULT_ENGINE}' engine with os_type {os_type!r}"
        )
    return None


def is_coherent(os_type: str, engine: str | None) -> bool:
    """True when this os_type/engine pair describes a machine that could exist."""
    return coherence_error(os_type, engine) is None


def assert_coherent(os_type: str, engine: str | None) -> None:
    """Raise IncoherentProfile (with the reason) if this pair cannot exist."""
    reason = coherence_error(os_type, engine)
    if reason is not None:
        raise IncoherentProfile(reason)


def coherent_engine(os_type: str, engine: str | None) -> str:
    """The engine an already-stored profile actually launches on.

    The reconciliation for records that predate the rules: an incoherent pair
    falls back to chromium, which honors ``os_type`` and so makes the launched
    machine match the record instead of contradicting it. A coherent pair is
    returned unchanged (with the legacy engine name mapped forward).
    """
    normalized = normalize_engine(engine)
    if not is_coherent(os_type, normalized):
        return DEFAULT_ENGINE
    return normalized
