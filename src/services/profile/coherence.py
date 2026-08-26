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

How each writing path applies them — the list is exhaustive on purpose, because
a vague claim that "the model handles it" is what tells the next engineer they
need not look:

* ``ProfileManager.add_profile`` — REFUSES (``assert_coherent``). A create
  composes a new machine, so an impossible one is a caller error. This covers
  REST create, the automation/MCP lane and ``bulk_create``, all of which reach
  the model through it.
* ``ProfileManager.update_profile`` — REFUSES, judging the pair the edit RESULTS
  in (stored value + supplied value), but only when the edit INTRODUCES the
  incoherence. See below.
* ``ProfileManager.import_profile`` — NORMALISES via ``coherent_engine``, and
  logs it. An archive is closer to an already-stored legacy record than to a
  fresh request: it was written by an older build, and refusing would make it
  permanently unimportable at the one moment the operator cannot edit it into
  shape. The PAIR therefore lands coherent instead of being rejected. Rule 3 is
  NOT reconciled here: ``coherent_engine`` answers "which engine?", and Rule 3
  has no engine remedy (a ``windows`` + ``mobile`` profile is contradictory on
  chromium and on firefox alike), so an imported ``windows`` + ``mobile``
  archive lands as a tolerated already-stored record — editable, never
  stranded, exactly like a legacy record predating these rules. Reconciling it
  would mean rewriting a field at launch, which is ``process.py``'s job and not
  this module's. See ``device_type_error``.
* ``ProfileManager.restore_profile`` — intentionally EXEMPT. Restore replays a
  record that already existed, so it introduces nothing; guarding it would
  strand a trashed profile behind a conflict it did not create.

⚠️ RULE 4 IS NOT DISPOSED OF BY THAT LIST, and the difference matters. Rules 1-3
judge a PAIR of fields, so they can only be applied where both values are in
hand — which is why the list above is per-door. Rule 4
(``unstorable_os_type_error``) judges ONE FIELD'S VOCABULARY, so it is enforced
in two layers instead:

* ``models.profile.Profile.__setattr__`` REPAIRS every non-canonical spelling,
  on construction AND on assignment. That covers all six write doors — including
  ``import_profile``, ``restore_profile`` and the legacy disk load — without
  naming any of them, and covers the door nobody has written yet. It is
  deliberately a REPAIR and never a refusal: a door that refuses turns a
  recoverable backup into an unimportable one, and ``restore_profile``'s
  exemption above is documented in exactly those terms.
* ``assert_storable_os_type`` adds a LOUD REFUSAL at ``add_profile`` and
  ``update_profile`` only — the two doors where a caller is AUTHORING something
  new and can therefore be told, rather than having their input silently
  rewritten. ``update_profile`` fires it only when the edit SUPPLIES an
  ``os_type``, matching the "introduces it" policy the pair rules use.

So the storage guarantee does not rest on this list being complete. That is the
point: PS-187 exists because the previous fix enumerated the doors it could
think of, and the recovery doors were not among them.

The rules
---------

**Rule 1 — a mobile OS forces the Chromium engine.** The Firefox engine is
desktop-only (no mobile mode), and only chromium carries the device presets that
make an android/ios profile coherent.

**Rule 2 — the Firefox engine pins the OS to Windows.** stealth-Firefox reports
a Windows platform regardless of ``os_type`` (#211), so a macOS/Linux Firefox
profile is an inconsistent lie. Changing what the engine REPORTS is a
masking-direction question and deliberately not attempted here; this module
stops the product from CLAIMING otherwise.

Rules 1 and 2 reduce to one predicate — the Firefox engine requires ``os_type ==
"windows"`` — because chromium is the only other engine and it honors
``os_type``. They are kept as distinct messages because the REASONS differ and
the caller acts on the reason.

**Rule 3 — ``device_type == "mobile"`` requires a mobile ``os_type``.** The
launch path derives "is this a phone?" from BOTH fields
(``services.browser.process``: ``is_mobile_os(os_type) or device_type ==
"mobile"``) while every other half of the same launch reads ``os_type`` alone.
So a stored ``windows`` + ``mobile`` profile launches one machine that answers
"what OS am I?" four different ways: an **android** device preset drives the UA
and screen (a Pixel 7 / SM-S911B, ``platform: "Android"``), while the GPU
extension is built for **windows** and reports a Direct3D11 renderer, the voice
roster is built for **windows** and carries Microsoft desktop voices, and the
engine is launched with ``--fingerprint-platform=linux``. Any one of those pairs
is a contradiction a checker reads directly.

The rule is DELIBERATELY ONE-DIRECTIONAL: it refuses ``device_type == "mobile"``
beside a desktop ``os_type``, and says nothing about ``device_type ==
"desktop"`` beside a mobile ``os_type``. That asymmetry is not an oversight:

* ``"desktop"`` is the model's DEFAULT (``models.profile.Profile``), and the
  profile dialog carries no ``device_type`` control at all — so every android
  profile the UI has ever created is stored ``android`` + ``desktop``. Refusing
  that pair would refuse the normal case.
* It also is not a lie. ``os_type`` already flips ``is_mobile`` on its own, so
  an ``android`` + ``desktop`` record launches as the phone its ``os_type``
  claims; the defaulted field makes no competing claim for the launch to honor.
  Only an explicit ``"mobile"`` does.

Which way it reconciles: ``os_type`` WINS and ``device_type`` is reconciled to
it — the same principle ``coherent_engine`` already applies for the pair ("in
favour of the record rather than in favour of the engine"), and the one
``process.py`` itself already claims is true two lines above the code that
breaks it: *"the OS is the source of truth so the UI only needs the OS
dropdown."*

Note the SET this rule refuses is exactly the set that flips that launch
derivation — the literal string ``"mobile"``, matched as ``process.py`` matches
it. A value that would not flip ``is_mobile`` produces no contradiction and is
not this module's business to refuse.

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
from ...models.os_type import (
    CANONICAL_OS_TYPES,
    RECOGNISED_OS_TYPES,
    canonical_os_type,
)

#: The engine every coherent non-Windows / mobile profile runs on.
DEFAULT_ENGINE = "chromium"

#: The only OS the Firefox engine can honestly claim (Rule 2).
FIREFOX_OS = "windows"

#: The ``device_type`` the model stores when nobody says otherwise, and the only
#: value the profile dialog can produce (it carries no device_type control).
DEFAULT_DEVICE_TYPE = "desktop"

#: The ``device_type`` that flips the launch path onto the mobile preset arm.
#: Matched as ``services.browser.process`` matches it — an exact string compare
#: against the same literal — so this rule refuses exactly the set that causes
#: the contradiction, no wider.
MOBILE_DEVICE_TYPE = "mobile"


class IncoherentProfile(ValueError):
    """A profile whose fields describe a machine that cannot exist.

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


def device_type_error(os_type: str, device_type: str | None) -> str | None:
    """Rule 3's reason ALONE, or None — the pair rules are not consulted.

    See the module docstring for the asymmetry. ``None`` means the caller did
    not supply the field, which is NOT the same as supplying the default: a
    caller with nothing to say gets no verdict.

    Public, and separate from ``coherence_error``, because the two answer
    different questions. ``coherence_error`` asks "can this whole profile
    exist?", which is right for a CREATE — a create composes all three fields at
    once, so all three are the caller's doing. It is the WRONG question for an
    edit that touches one rule family, because it submits the fields the edit
    never touched to judgement as well: a ``device_type``-only patch would then
    be refused by Rule 2 for an ``os_type``/``engine`` pair that was already
    stored — the "never blocked by incoherence it did not introduce" invariant,
    broken by the rule that was supposed to sit beside it. Worse, on a record
    violating BOTH families (reachable via ``restore_profile``, which is exempt
    by design, and via ``import_profile``, which reconciles only the pair) the
    edit that REPAIRS Rule 3 would be refused by Rule 2 — stranding the record
    through the exact door the exemption keeps open.

    So each rule family gets its own predicate, and ``update_profile`` gates each
    one on whether THAT family's inputs changed. Rule 3 reads ``(os_type,
    device_type)``; the pair rules read ``(os_type, engine)``. They overlap on
    ``os_type``, which is why an ``os_type`` edit opens both gates.
    """
    if device_type is None:
        return None
    if device_type != MOBILE_DEVICE_TYPE:
        # Only the literal that flips `is_mobile` at launch can contradict
        # os_type. Anything else (including the "desktop" default beside a
        # mobile os_type) makes no competing claim.
        return None
    if is_mobile_os(os_type):
        # The two fields agree — this is a phone that says it is a phone.
        return None
    return (
        f"device_type '{MOBILE_DEVICE_TYPE}' cannot be combined with os_type "
        f"{os_type!r}: {os_type!r} is not a mobile OS, so this profile would "
        f"launch on a mobile device preset (an Android UA, screen and touch "
        f"support) while its GPU renderer, voice roster and engine platform are "
        f"all built from os_type {os_type!r} — the same machine answering "
        f"'what OS am I?' more than one way. Use a mobile os_type ('android' or "
        f"'ios') with device_type '{MOBILE_DEVICE_TYPE}', or device_type "
        f"'{DEFAULT_DEVICE_TYPE}' with os_type {os_type!r}"
    )


def coherence_error(
    os_type: str,
    engine: str | None,
    device_type: str | None = None,
) -> str | None:
    """The reason this profile cannot exist, or None if it can.

    The message is written for the caller to act on: it names both fields, the
    conflict, and which way to resolve it.

    ``device_type`` is optional and defaults to None = "not supplied, so do not
    judge Rule 3". Every pre-existing caller passes the pair and keeps its exact
    behaviour; a caller that has the third field in hand supplies it and gets the
    third rule as well. That default is what keeps this change additive across a
    signature shared by ``is_coherent``, ``assert_coherent`` and
    ``coherent_engine`` — and it is safe rather than merely convenient, because
    an omitted ``device_type`` is genuinely unknown here, not assumed innocent:
    the two callers that REFUSE (``add_profile``, ``update_profile``) both
    resolve the field first and always pass it.

    That is not the same as "every write path judges Rule 3", and the difference
    is stated rather than papered over: ``coherent_engine`` calls ``is_coherent``
    with the PAIR only (deliberately — see its docstring, Rule 3 has no engine
    remedy), so ``import_profile``, which reaches this module through it, has
    Rule 3 evaluated on none of its records. An imported ``windows`` + ``mobile``
    archive therefore lands as a tolerated already-stored record rather than
    being normalised. That residual is the import door's, not this default's.

    Rule 3 is checked BEFORE the engine rules because it is the only one that can
    fire on a chromium profile, and the early return below exits on chromium.
    """
    device_error = device_type_error(os_type, device_type)
    if device_error is not None:
        # Rule 3 — read first because the chromium early-return below would
        # otherwise skip it for exactly the engine this defect lives on.
        return device_error
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


def is_coherent(
    os_type: str,
    engine: str | None,
    device_type: str | None = None,
) -> bool:
    """True when these fields describe a machine that could exist."""
    return coherence_error(os_type, engine, device_type) is None


def assert_coherent(
    os_type: str,
    engine: str | None,
    device_type: str | None = None,
) -> None:
    """Raise IncoherentProfile (with the reason) if this profile cannot exist."""
    reason = coherence_error(os_type, engine, device_type)
    if reason is not None:
        raise IncoherentProfile(reason)


def unstorable_os_type_error(os_type: str) -> str | None:
    """Why this ``os_type`` spelling may not be AUTHORED, or None if it may.

    Rule 4, and it is about ONE FIELD's vocabulary rather than about a pair —
    which is why it has its own entry point instead of joining
    ``coherence_error``. A caller judging a pair it did not touch must not have
    this fired at it (the "introduces it" policy the update path documents at
    length), and conversely a spelling refusal must not be skipped just because
    the pair happened not to move.

    THE DEFECT (PS-187). ``win`` is a spelling OUR fold recognises and the
    ENGINE does not: it answers ``--fingerprint-platform=win`` with SwiftShader
    and the host's GL strings reach the page. Five more behave identically
    (``mac``, ``darwin``, ``iphone``, ``ipad``, ``ipados``). The read side was
    fixed on PS-161; this is the write side.

    ⚠️ THIS IS THE REFUSING HALF OF A TWO-LAYER RULE, AND IT IS THE NARROWER
    ONE. ``Profile.__setattr__`` REPAIRS every spelling on every door, so the
    stored value is canonical no matter which door it entered through — that is
    what makes the property hold for doors nobody has written yet. This layer
    adds a LOUD REFUSAL on the two doors where the caller is authoring
    something new (``add_profile``, ``update_profile``) and can therefore be
    TOLD, rather than having their input silently rewritten.

    It is deliberately NOT applied to import / restore / legacy load. A door
    that refuses turns a recoverable backup into an unimportable one, and
    ``restore_profile`` is documented as exempt from these rules for exactly
    that reason. Those doors repair instead, and the record self-heals.

    Refuses the ALIAS as well as the unknown value, even though both repair
    cleanly. Accepting ``win`` silently at create would leave the operator
    believing the product has a ``win`` platform, and would keep alive the very
    conflation — "recognised" read as "servable" — that produced the leak.
    """
    if not isinstance(os_type, str) or os_type not in CANONICAL_OS_TYPES:
        canonical = canonical_os_type(os_type)
        supported = ", ".join(repr(v) for v in sorted(CANONICAL_OS_TYPES))
        if isinstance(os_type, str) and os_type.lower().strip() in RECOGNISED_OS_TYPES:
            return (
                f"os_type {os_type!r} is an alias this codebase recognises but "
                f"the browser engine does NOT honour: it is passed through to "
                f"--fingerprint-platform unchanged and the engine answers it "
                f"with its own software renderer, so the host's real GPU "
                f"strings would reach the page. Use {canonical!r}. "
                f"Supported values: {supported}"
            )
        return (
            f"os_type {os_type!r} is not a value this product can serve. "
            f"Supported values: {supported}"
        )
    return None


def assert_storable_os_type(os_type: str) -> None:
    """Raise IncoherentProfile unless this spelling may be authored.

    Raised as ``IncoherentProfile`` on purpose: every door that writes a profile
    already catches it and translates it (400 at the REST lane, an inline
    message in the profile dialog), so the refusal reaches the operator with
    its reason through machinery that already exists rather than through a new
    exception type each caller would have to learn.
    """
    reason = unstorable_os_type_error(os_type)
    if reason is not None:
        raise IncoherentProfile(reason)


def is_device_type_coherent(os_type: str, device_type: str | None) -> bool:
    """True when Rule 3 alone has nothing to say about these two fields."""
    return device_type_error(os_type, device_type) is None


def assert_device_type_coherent(os_type: str, device_type: str | None) -> None:
    """Raise IncoherentProfile if Rule 3 alone refuses these two fields.

    The Rule-3-only counterpart of ``assert_coherent``, for a caller that must
    judge the device_type family WITHOUT re-firing the pair rules on fields it
    did not touch. See ``device_type_error``.
    """
    reason = device_type_error(os_type, device_type)
    if reason is not None:
        raise IncoherentProfile(reason)


def coherent_engine(os_type: str, engine: str | None) -> str:
    """The engine an already-stored profile actually launches on.

    The reconciliation for records that predate the rules: an incoherent pair
    falls back to chromium, which honors ``os_type`` and so makes the launched
    machine match the record instead of contradicting it. A coherent pair is
    returned unchanged (with the legacy engine name mapped forward).

    Deliberately still a PAIR question, and it takes no ``device_type``: it
    answers "which engine?", and Rule 3 has no engine remedy — a windows +
    mobile profile is contradictory on chromium and on firefox alike, so
    feeding the third field in here could only make this function return
    chromium for a record it cannot repair. Rule 3's reconciliation at launch
    belongs on the launch path (``process.py``), which is out of scope for this
    slice; see the PR.
    """
    normalized = normalize_engine(engine)
    if not is_coherent(os_type, normalized):
        return DEFAULT_ENGINE
    return normalized
