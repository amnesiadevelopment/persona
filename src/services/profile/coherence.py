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
  this module's. See ``device_type_error``, and the Rule 3 section below — that
  "tolerated" outcome is now a RECORDED decision rather than an unexamined
  residual, and the record it lands is no longer silent.
* ``ProfileManager.restore_profile`` — intentionally EXEMPT. Restore replays a
  record that already existed, so it introduces nothing; guarding it would
  strand a trashed profile behind a conflict it did not create.
* ``ProfileManager._load_profiles_locked`` (the legacy disk load) — does not
  judge either rule family. It reads records written by older builds, so it is
  a RECOVERY door like the two above, not an authoring one.

RULE 3 AT THE THREE RECOVERY DOORS: ACCEPT AND RECORD (PS-188)
--------------------------------------------------------------

The three entries above are the doors that can bring a ``windows`` + ``mobile``
record to rest. PS-188 asked, per door, whether to REFUSE, NORMALISE, or ACCEPT
AND RECORD. The answer is the same for all three — **accept and record** — and
the reasoning is worth keeping, because "make it symmetric with create/update"
is the obvious wrong answer:

* **REFUSE is wrong at a recovery door.** Import, restore and the legacy load
  exist to give an operator back a profile they already have. A door that
  refuses turns a recoverable backup into an unimportable one, at the one
  moment the operator cannot edit the record into shape. This is the same
  argument ``restore_profile``'s exemption has always rested on, and PS-188
  re-checked that exemption rather than inheriting it: the recorded reason
  still holds, and restore is additionally incapable of INTRODUCING the fault
  (delete→restore is a round trip over a pair the product already accepted).
* **NORMALISE has no honest form for Rule 3.** ``coherent_engine`` can
  reconcile the PAIR because an engine exists that satisfies it. Rule 3 has no
  such remedy: coercing ``device_type`` to ``"desktop"`` or ``os_type`` to a
  mobile family each rewrite a field the operator never asked to change, and
  **which of the two is the lie is not knowable from the record.** For restore
  it is worse than merely unhelpful — that door's contract is to replay a
  record "exactly as it was", to the point of refusing even a rename.

So the behaviour at these doors is unchanged, and that was never the defect.
The defect was that the incoherence was **SILENT**: a recovered ``windows`` +
``mobile`` record was indistinguishable from a coherent one, on every surface.
The RECORD half is what PS-188 adds:

* ``models.profile.Profile.device_type_incoherence`` — Rule 3's verdict on a
  STORED record, derived on read via ``device_type_error`` so this module stays
  its single owner. Like ``__setattr__`` below it is a property of the FIELDS
  rather than a list of doors, so it is true of the door nobody has written yet;
  and being computed rather than stored, it needs no migration for the records
  that already exist and cannot go stale against the fields it describes.
* ``import_profile`` and ``restore_profile`` log the pair they let through.
* ``api.schemas.profiles.ProfileResponse.device_type_incoherence`` exposes it,
  so an operator can FIND the incoherent profiles they already hold — which no
  surface previously permitted.

WHAT SUCH A RECORD DOES AT LAUNCH — measured on both engines (PS-188), because
the blast radius is what makes recording it worthwhile. PS-161 round 4 closed
the GPU **authorship** leak (``engine_platform`` is one computation over both
fields, handed to both consumers), and it closed exactly that one vector:

* **chromium — HISTORY, closed by PS-236.** Until PS-236 reconciled the field
  at the launch path, ``is_mobile`` was true on such a record, so an Android
  device preset drove the UA, screen and touch, while the GPU **pool arm**
  (``gpu_ext._os_norm``) and the **voice roster** (``voice_ext``) were still
  selected from ``os_type`` alone: a Direct3D11 renderer and Microsoft desktop
  voices underneath an Android UA, told ``--fingerprint-platform=linux``. One
  machine, three answers. ``process.spawn_browser`` now calls
  ``coherent_device_type`` (below) ONCE before either consumer reads the field,
  so the two vectors that read it agree with the two that do not, and the
  launched chromium machine is the coherent ``windows`` + ``desktop`` one. The
  GPU pool arm and the voice roster still read ``os_type`` alone — that is
  unchanged and is precisely why reconciling the ONE field was the remedy.
* **firefox** — the launch path reads NEITHER field (``invisible_launch.py``
  has no ``device_type`` or ``is_mobile`` reference at all, #211), and the pair
  rules make ``windows`` the only OS Firefox may carry, so a ``windows`` +
  ``mobile`` + ``firefox`` record is pair-COHERENT, launches Firefox, and has
  its ``device_type`` dropped entirely: the record claims a phone and the
  browser presents a desktop Windows machine. **Still true** — PS-236 touched
  the chromium path only, because there is nothing on the Firefox arm to
  reconcile.

The RULE is owned here — ``coherent_device_type`` below, the counterpart of
``coherent_engine`` — and its CALL SITE is the launch path (``process.py``),
which is the only place that needs the reconciled answer. The stored record is
never rewritten by either; see ``device_type_error``.

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
So a stored ``windows`` + ``mobile`` profile USED to launch one machine that
answered "what OS am I?" on its four vectors with THREE different values: an
**android** device preset drove the UA and screen (a Pixel 7 / SM-S911B,
``platform: "Android"``), while the GPU extension was built for **windows** and
reported a Direct3D11 renderer, the voice roster was built for **windows** and
carried Microsoft desktop voices, and the engine was launched with
``--fingerprint-platform=linux`` — and any one of those pairs is a contradiction
a checker reads directly. **PS-236 closed that at the launch path**:
``process.spawn_browser`` now reconciles the field ONCE through
``coherent_device_type`` (below) before either consumer reads it, so such a
record launches as the coherent ``windows`` + ``desktop`` machine its ``os_type``
claims — one machine, ONE answer. The rule stated here is unchanged and still
refuses the pair at the authoring doors; what changed is what an ALREADY-STORED
one does at launch. See the chromium bullet above for the measured before/after.

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
``process.py`` itself already claims is true two lines above the code that now
HONOURS it (it calls ``coherent_device_type``, PS-236): *"the OS is the source
of truth so the UI only needs the OS dropdown."*

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

    Raised as ``IncoherentProfile`` on purpose: it is the refusal type the
    coherence rules already use, so a door that already translates a pair
    refusal translates this one with no new exception type to learn — 400 at
    the REST lane (``routes/profiles.py``), an inline message in the profile
    dialog (``ui/actions/profile.py``), a structured
    ``{"created": False, "error": "refused", "detail": ...}`` at the MCP lane
    (``api/mcp_server.py``), and the ``skipped`` channel in ``bulk_create``.

    ⚠️ THAT LIST WAS NOT FREE, AND THE ORIGINAL CLAIM HERE WAS FALSE. This
    docstring used to assert that "every door that writes a profile already
    catches it", which was true of the REST lane and the dialog and NOT of the
    MCP lane or ``bulk_create``: both were enumerated as callers on PS-187 and
    neither had its exception handling checked. The MCP lane measurably could
    not raise before this rule existed — it passes no ``engine`` and no
    ``device_type``, so the pair rules were structurally unreachable through it
    — so this rule was the first refusal ever to reach an off-machine
    automation client, and it escaped uncaught. Both lanes now handle it.

    So: do not read this list as a standing guarantee about doors not written
    yet. A NEW caller of ``add_profile`` / ``update_profile`` must decide how it
    reports a refusal, exactly as these four did. Saying "every door already
    catches it" is what told the previous engineer they need not look.
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
    chromium for a record it cannot repair. Rule 3's reconciliation is
    ``coherent_device_type`` immediately below (PS-236), a separate function
    with its own answer, called from the launch path (``process.py``).
    """
    normalized = normalize_engine(engine)
    if not is_coherent(os_type, normalized):
        return DEFAULT_ENGINE
    return normalized


def coherent_device_type(os_type: str, device_type: str | None) -> str:
    """The ``device_type`` an already-stored profile actually launches as.

    Rule 3's reconciliation, and the exact counterpart of ``coherent_engine``
    for the other rule family: the doors REFUSE an incoherent value at write
    time, and this resolves an incoherent one that is ALREADY STORED — reached
    by import, restore, a legacy record, or the unguarded REST lane. It is the
    residual PS-188 declined and handed on by name, and the direction it
    applies is the one this module's docstring already settled: **``os_type``
    WINS and ``device_type`` is reconciled to it**, in favour of the record
    rather than in favour of the field.

    WHY THIS IS ONE FUNCTION AND NOT A CONDITION AT EACH CALL SITE. A launched
    profile answers "what OS am I?" on four vectors, and only two of them read
    this field: the device preset (UA / screen / touch) and
    ``--fingerprint-platform``. The GPU pool arm and the voice roster read
    ``os_type`` alone and cannot be moved by it. So UNRECONCILED, a stored
    ``windows`` + ``mobile`` record launches an Android **Pixel-class UA and
    screen** over a **Windows** Direct3D11 GPU pool and **Microsoft SAPI**
    voices, told ``--fingerprint-platform=linux`` — one machine, three answers,
    and any pair of them is a contradiction a checker reads directly.
    Reconciling the field ONCE at the launch path brings the two vectors that
    read it into line with the two that do not; spelling the condition out at
    each of those two call sites would be the "two authors, each deciding from
    its own copy of the question" shape ``engine_platform``'s module docstring
    was written about, and that PS-161 spent two review rounds on. There is one
    owner, so there is no second copy to drift.

    ⚠️ ONE-DIRECTIONAL, and the asymmetry is Rule 3's own (see the module
    docstring). It coerces the literal ``"mobile"`` to ``"desktop"`` when
    ``os_type`` is NOT a mobile family, and is a no-op in every other case —
    including ``android`` + ``desktop``, which is what EVERY android profile the
    UI has ever created is stored as (``"desktop"`` is the model default and the
    profile dialog carries no ``device_type`` control). Coercing that pair the
    other way would rewrite the normal case. A value that is neither literal
    makes no competing claim at launch and is passed through untouched.

    ⚠️ THE STORED RECORD IS NOT REWRITTEN. This returns a value; it never
    assigns to a profile. ``Profile.device_type_incoherence`` is a derived
    property computed from the stored fields on every read, so it keeps
    reporting the incoherence after a launch — which is the point of PS-188's
    accept-and-record decision, and would be silenced by an in-place repair. A
    *pair* rule has no safe repair at rest anyway: nothing in the record says
    which of the two fields is the lie. This is the launch answering coherently,
    not the record being corrected.
    """
    if device_type is None:
        return DEFAULT_DEVICE_TYPE
    if is_device_type_coherent(os_type, device_type):
        return device_type
    return DEFAULT_DEVICE_TYPE
