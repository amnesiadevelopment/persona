import hashlib
import hmac
import zlib
from collections.abc import Container
from dataclasses import asdict, dataclass, field

from .hardware_generation import normalize_generation
from .os_type import canonical_os_type


def _derive(label: str) -> int:
    """Derive ONE seed value from a label, salted with the install secret.

    The single owner of the salted formula, so the mint and its collision walk
    cannot drift apart — a salted mint with an unsalted walk would leak the
    guessable scheme straight back for exactly the profiles a name-reuse
    workflow creates (see mint_fingerprint_seed).

    HMAC-SHA256 truncated to 32 bits, rather than crc32(secret + label). crc32
    is a checksum, not a keyed hash: it is linear over its input, so an
    attacker holding one (name, seed) pair could recover enough structure to
    predict others under the same install. HMAC is the standard construction
    for "keyed digest of an attacker-influenced message" and costs nothing
    here — this runs once per profile creation, not per request.

    The output stays a 32-BIT INT because that is what every consumer already
    is: `--fingerprint=` formats it, touch_points does `seed % 2`, the preset
    tables index into it. Salting removes GUESSABILITY, not the birthday bound
    of a 32-bit space; the reserved-seed check is what handles collisions, and
    it is unchanged. Widening the space is a different decision.

    The secret is read here and never returned, logged or stored on the model.
    """
    from ..core.install_secret import install_secret

    digest = hmac.new(
        install_secret(), label.encode("utf-8"), hashlib.sha256
    ).digest()
    return int.from_bytes(digest[:4], "big")


def mint_fingerprint_seed(name: str, taken: Container[int] | None = None) -> int:
    """Mint the seed a NEW profile freezes into ``fingerprint_seed_value``.

    The seed carries THREE properties, and this function has to hold all of
    them:

    1. STABILITY — a profile's presented machine must not move under its own
       live cookie jar. That is what persisting the seed buys: this function
       runs ONCE, at creation, and the value it returns is frozen into
       ``fingerprint_seed_value`` and read back verbatim on every access
       afterwards. So a RENAME cannot move the presented machine, even though
       the name is what the seed was derived FROM — the derivation is never
       re-run. (Before the seed became a field the old property re-derived
       from the name on every read, so a rename DID move the machine; that is
       the bug persisting the seed closed.) This freezes the seed, it does not
       re-roll it.

       Note what this property does NOT claim: a freshly created profile does
       NOT present byte-identically to pre-field behaviour. Salting the
       derivation deliberately moves NEW profiles — that is the whole point of
       property 3. The compatibility guarantee is about profiles that ALREADY
       EXIST, and it is delivered by the read path, not by this function; see
       WHAT THIS DOES NOT TOUCH below.
    2. ISOLATION — two LIVE profiles must not present the same machine. Once
       the seed is frozen at creation, crc32(name) alone no longer delivers
       this, because a name is REUSABLE: rename 'acme-bank' to 'acme-bank-old'
       and create 'acme-bank' again and both hold crc32('acme-bank'), i.e. one
       resolution, one device preset, one --fingerprint=. That is a
       cross-profile linkage, and it is the ordinary "archive last quarter's
       account, start a fresh one under the same label" workflow — not a
       corner case.
    3. SECRECY — and this is the property that changed the formula. crc32(name)
       is a PURE, PUBLIC function of a string the operator typed: same name,
       same integer, on every install and every machine. The seed is not
       internal bookkeeping — it derives the PRESENTED machine — so an
       adversary who guessed a naming scheme ('acme-bank', 'shop1',
       'client-alpha') computed that profile's presented hardware OFFLINE, with
       no access to the install. Mixing in a per-install secret (see
       core.install_secret) is what turns that computation back into a guess.

    So: mint _derive(name) — the install-salted derivation, NOT crc32(name);
    if that value is already held by a live profile, walk _derive(name + ':' +
    n) from n=1 for the first free one. Deterministic (no RNG, reproducible
    from the inputs), and the walk is entered only when it must be — a
    non-colliding create returns the first derivation. Both branches go
    through _derive, so both are salted; see THE WALK IS SALTED TOO below.

    ``taken`` is the set of seeds held by live profiles. Pass it under the same
    lock that guards the check-then-insert, or two concurrent creates can both
    read a stale set and mint the same seed. None = don't check (the caller has
    no registry in hand).

    WHAT THIS DOES NOT TOUCH, and it is the bound that makes the change
    shippable: this function is on NEITHER read path. ``Profile.fingerprint_seed``
    returns ``fingerprint_seed_value`` when set, else computes ``crc32(name)``
    INLINE. So salting here moves NO existing profile — every stored seed keeps
    its value and every pre-field profile keeps its crc32(name) fallback. The
    honest bound is therefore that this narrows guessability for NEW profiles
    and does not make an existing one secret; closing that needs a migration
    that would move fingerprints, which is deliberately not attempted here.

    THE WALK IS SALTED TOO, and that is not decoration. A salted mint whose
    collision walk fell back to plain crc32(f"{name}:{n}") would leak the
    scheme straight back: the walk is exactly the branch a name-reuse workflow
    ("archive last quarter's account, start a fresh one") takes, so the
    guessable value would be handed to precisely the profiles the operator
    creates most deliberately. Both derivations go through _derive.

    STILL DETERMINISTIC, and still no RNG at call time. The entropy was spent
    ONCE, when the install secret file was created; given the same name, the
    same ``taken`` set and the same install, this returns the same seed on
    every call. That is what keeps a profile's presented machine stable under
    its own cookie jar — the entire point of property 1 above.
    """
    seed = _derive(name)
    if taken is None or seed not in taken:
        return seed
    n = 1
    while True:
        seed = _derive(f"{name}:{n}")
        if seed not in taken:
            return seed
        n += 1


@dataclass
class Profile:
    name: str
    proxy: str | None = None
    os_type: str = "windows"
    # "desktop" | "mobile". For mobile profiles os_type carries the mobile OS
    # family ("android" | "ios") and a real device preset drives UA/screen/etc.
    device_type: str = "desktop"
    # "chromium" (fingerprint-chromium + extensions) or "firefox" (patched
    # Firefox 150, C++-level spoofing, no CDP/webdriver tells).
    engine: str = "chromium"
    # "auto" (a stable per-profile pick) or an explicit "WIDTHxHEIGHT".
    resolution: str = "auto"
    search_engine: str = "duckduckgo"
    bookmark_pool: str | None = None
    # None = never configured (the profile gets the stock default bookmarks);
    # [] = explicitly cleared (opens with an empty toolbar); a list = that exact
    # selection. Distinguishing None from [] is what lets a user remove every
    # bookmark and have it stay removed instead of resurrecting the defaults.
    bookmarks: list[str] | None = None
    # name of an mTLS client certificate (from CertStore) presented to admin
    # sites this profile visits. None = no certificate assigned.
    certificate: str | None = None
    cookie_import_status: str | None = None
    # Outcome of the last attempt to trust the mTLS certificate's CA in this
    # profile (Firefox engine). The CA import soft-fails by design — the launch
    # proceeds untrusted — so without this the profile is indistinguishable from
    # one whose trust imported cleanly. None = never attempted (no certificate
    # assigned, or never launched since the field was added).
    cert_trust_status: str | None = None
    # The engine this profile was LAST LAUNCHED under, recorded at launch as
    # the pair (which engine, what that engine reports as its build).
    #
    # WHY A PAIR, AND WHY NOT NORMALISED. The two engines report builds in
    # genuinely different shapes — Firefox a `firefox-NN` tag
    # (services/engine/firefox.py current_version), Chromium a dotted version
    # tag (services/engine/updater.py current_version). Flattening them into
    # one format would lose which engine produced the string, and a build
    # identifier that cannot say which engine it came from is not provenance:
    # `151.0.8000.10` and `firefox-18` are not points on one scale. So each
    # engine's own reported string is stored VERBATIM and labelled.
    #
    # WHY THE ENGINE CANNOT BE RE-DERIVED LATER. `engine` above is the STORED
    # engine, and it is neither what necessarily launched nor immutable: a
    # mobile profile stored as "firefox" actually launches chromium
    # (browser/process.py effective_engine), and the stored value can be edited
    # after the fact. This records what actually ran.
    #
    # NOT A SECOND SOURCE OF TRUTH ABOUT THE INSTALLED BUILD — the updater owns
    # that. This answers a different question ("what did THIS profile run
    # under"), and it may legitimately disagree with what is installed now.
    # That disagreement is the entire point: without it, engine drift and a
    # genuine masking regression are indistinguishable.
    #
    # None = NOT KNOWN, never a guess. Every profile that predates this field
    # reads None, and there is deliberately no backfill: the build a past
    # launch used was not recorded and cannot be recovered, so inventing one
    # would make the comparison this field exists to enable return a false
    # answer. An absent stamp is honest; a wrong one is worse than none.
    last_launch_engine: str | None = None
    last_launch_build: str | None = None
    tags: list[str] = field(default_factory=list)
    notes: str = ""
    ai_control: bool = False
    # The frozen fingerprint seed, minted once when the profile is created
    # (ProfileManager.add_profile) and never rewritten. None = a profile that
    # predates this field, which keeps deriving its seed from its name — see
    # fingerprint_seed below for why that fallback is the whole migration.
    # NOT named `fingerprint_seed`: that name is the read accessor every
    # consumer already calls, and a field of the same name would shadow the
    # property and hand callers a raw None on every pre-this-field profile.
    fingerprint_seed_value: int | None = None
    # The hardware-list generation this profile was CREATED IN, frozen once at
    # creation and never rewritten — the same freeze, for the same reason, as
    # fingerprint_seed_value above. The seed pins WHICH INDEX a profile picks;
    # this pins WHAT THAT INDEX MEANS, i.e. the pool the index divides into.
    # Freezing only the seed left the mapping under it free to move: appending
    # one entry to a hardware list changed `len(pool)` and re-indexed a large
    # share of existing profiles onto different hardware, under their live
    # cookie jars.
    #
    # NOT named `hardware_generation`: that name is the read accessor consumers
    # call, and a field of the same name would shadow the property and hand
    # callers a raw None on every pre-this-field profile — the same trap
    # documented on the seed above.
    hardware_generation_value: int | None = None

    @property
    def fingerprint_seed(self) -> int:
        """This profile's fingerprint seed: the frozen one, else crc32(name).

        The whole presented machine (resolution, device preset, touch points,
        --fingerprint=) derives from this integer, so it must not move under a
        live cookie jar. It used to be crc32(name) unconditionally, which meant
        renaming a profile re-rolled its hardware while update_profile carried
        the SAME data dir across — cookies and sessions intact, machine
        different. That is exactly the linkage event restore_profile refuses in
        writing (manager.py: "restoring under a different name would hand back
        the cookie jar attached to a DIFFERENT fingerprint").

        A profile created since the seed became a field carries its own value
        and a rename cannot touch it. A profile that predates the field has no
        value to carry, so it falls back to crc32(name) and presents exactly
        what it has always presented — the fallback IS the migration, which is
        why this change moves no existing profile's fingerprint by a single bit
        and needs no backfill. Do not "tidy" the fallback into a backfill.

        The seed's SECOND property is unchanged and equally load-bearing:
        distinct live profiles yield distinct fingerprints, so each persona is
        isolated without the user having to pick seed numbers. Freezing the
        seed is what put that at risk — a name is reusable, so crc32(name)
        alone would hand a recreated name the seed its renamed predecessor is
        still holding. mint_fingerprint_seed() is where isolation is enforced
        (it skips a seed a live profile already holds); this property only
        READS. Both halves are mandatory: stability without isolation is two
        profiles presenting one machine, which is the linkage this whole area
        exists to prevent.
        """
        if self.fingerprint_seed_value is not None:
            return self.fingerprint_seed_value
        return zlib.crc32(self.name.encode("utf-8"))

    @property
    def hardware_generation(self) -> int:
        """The hardware-list generation whose pools this profile picks from.

        The frozen value, else 0. Zero is the generation whose visible pool is
        every entry that shipped before generations existed — i.e. the whole
        list, in its original order, with its original length and therefore its
        original divisor. So a profile that predates this field keeps presenting
        EXACTLY what it has always presented, and the fallback IS the migration,
        precisely as it is for fingerprint_seed above. Do not "tidy" it into a
        backfill, and do not default it to CURRENT_HARDWARE_GENERATION: that
        would hand every old profile the newest pool and re-roll the hardware of
        every profile on the machine — the exact event this field prevents.

        Read via normalize_generation so a malformed or negative stored value
        also lands on 0 rather than on an empty pool.
        """
        return normalize_generation(self.hardware_generation_value)

    @property
    def device_type_incoherence(self) -> str | None:
        """Rule 3's verdict on THIS STORED RECORD, or None if it is coherent.

        THE RESIDUAL PS-188 CLOSES. Rule 3 (``device_type == "mobile"`` requires
        a mobile ``os_type``) is REFUSED at the two authoring doors and is not
        evaluated at the three RECOVERY doors — ``import_profile`` normalises
        the pair only (Rule 3 has no engine remedy), ``restore_profile`` is
        intentionally exempt, and the legacy disk load predates the rule. Those
        three exemptions are deliberate and stay: a door that refuses turns a
        recoverable backup into an unimportable one. See
        ``services/profile/coherence.py``.

        So the decision for those doors is ACCEPT AND RECORD, and this property
        is the RECORD half. The incoherence was previously SILENT — the record
        looked exactly like a coherent one — which is the part that was actually
        wrong. It is now ASKABLE of any profile, from any door, at any time.

        WHY A DERIVED PROPERTY AND NOT A STORED FLAG, and why that is the same
        argument ``__setattr__`` below makes for Rule 4. A stored flag is a
        second copy of a fact that is already fully determined by
        ``(os_type, device_type)``, so it can go stale the moment either field
        moves — and it would need a backfill for every record already on disk,
        including the ones this exists to describe. Computed on read, it cannot
        drift, needs no migration, and is true of the door nobody has written
        yet. It is a property rather than a field for one more reason: ``asdict``
        skips properties, so this changes NOTHING about what is persisted.

        NOT A REFUSAL, deliberately, and not a repair either. Unlike Rule 4's
        ``os_type``, Rule 3 has no safe repair: coercing ``device_type`` to
        "desktop" or ``os_type`` to "android" both silently rewrite a field the
        operator did not ask to change, and which of the two is the lie is not
        knowable from the record. Reporting is the only honest option — and
        ``restore_profile``'s contract is to replay a record "exactly as it
        was", which a repair would break outright.

        The reason string is Rule 3's own message, not a second wording of it:
        ``coherence.device_type_error`` is the single owner, so the property and
        the refusing doors cannot come to disagree about what Rule 3 says.

        Imported function-locally on purpose: ``services.profile.coherence``
        cannot be reached without executing ``services/profile/__init__``, which
        imports ``manager``, which imports THIS module — a module-level import
        here closes that cycle at import time. ``process.effective_engine``
        documents the same constraint for the same reason.
        """
        from ..services.profile.coherence import device_type_error

        return device_type_error(self.os_type, self.device_type)

    def __setattr__(self, name: str, value: object) -> None:
        """Repair ``os_type`` onto the canonical vocabulary as it is STORED.

        THE CHOKE POINT (PS-187). The defect this closes is that a spelling the
        engine cannot honour (``win``, ``mac``, ``darwin``, ``iphone``,
        ``ipad``, ``ipados``) was STORABLE, and a stored non-canonical value
        reaches the launch path — where the masking layer stands down expecting
        the engine to author the identity, the engine does not, and the host's
        software rasteriser reaches the page. See ``models/os_type.py``.

        WHY ``__setattr__`` AND NOT ``__post_init__``. Both would cover
        construction, and construction is how five of the six doors write
        (``add_profile``, the disk/legacy load, ``restore_profile``,
        ``import_profile`` via ``transfer.py``, ``baseline_profile``). But
        ``update_profile`` does not construct — it assigns onto a live instance
        (``manager.py``: ``profile.os_type = new_os``), and a ``__post_init__``
        would never see it. Overriding assignment covers construction AND
        mutation with one rule, so the guarantee is a property of the field
        rather than a list of the doors that happened to be enumerated.

        THAT DISTINCTION IS THE WHOLE POINT. "Enumerating the doors you happened
        to think of is what left this open the first time" — a guard placed at
        each known door protects those doors; a rule the FIELD enforces also
        covers the door nobody has written yet. Both halves of PS-161's
        follow-up list (``import_profile``, ``restore_profile``, legacy records)
        are covered here without being named, because they cannot write this
        field without passing through this method.

        REPAIR, NOT REFUSAL, AT THIS LAYER — deliberately. Refusing here would
        make an archive or a trashed profile carrying ``win`` permanently
        unimportable / unrestorable: a door that refuses turns a recoverable
        backup into an unrecoverable one, and ``restore_profile`` documents its
        exemption from the coherence rules in exactly those terms ("guarding it
        would strand a trashed profile behind a conflict it did not create").
        So the record SELF-HEALS: it lands canonical and stays editable. The
        loud refusal an operator can act on belongs at create/update, where
        something new is being authored and the caller can be told — that lives
        in ``coherence.py`` and is layered ON TOP of this, not instead of it.

        Nothing is lost by repairing: the folded value is the same one every
        consumer already derived from this field. What changes is that the
        folded value is now what gets STORED, so the launch path can no longer
        be handed a spelling the engine will not honour.
        """
        if name == "os_type":
            value = canonical_os_type(value)
        object.__setattr__(self, name, value)

    def to_dict(self) -> dict:
        return asdict(self)
