import zlib
from collections.abc import Container
from dataclasses import asdict, dataclass, field

from .hardware_generation import normalize_generation


def mint_fingerprint_seed(name: str, taken: Container[int] | None = None) -> int:
    """Mint the seed a NEW profile freezes into ``fingerprint_seed_value``.

    The seed carries TWO properties, and this function has to hold both:

    1. STABILITY — a profile's presented machine must not move under its own
       live cookie jar. That is what persisting the seed buys, and why the
       common case here is deliberately the SAME crc32(name) the old property
       derived on every read: a freshly created profile presents
       byte-identically to what it would have presented before the seed became
       a field. This freezes the seed, it does not re-roll it.
    2. ISOLATION — two LIVE profiles must not present the same machine. Once
       the seed is frozen at creation, crc32(name) alone no longer delivers
       this, because a name is REUSABLE: rename 'acme-bank' to 'acme-bank-old'
       and create 'acme-bank' again and both hold crc32('acme-bank'), i.e. one
       resolution, one device preset, one --fingerprint=. That is a
       cross-profile linkage, and it is the ordinary "archive last quarter's
       account, start a fresh one under the same label" workflow — not a
       corner case.

    So: mint crc32(name); if that value is already held by a live profile,
    walk crc32(name + ':' + n) from n=1 for the first free one. Deterministic
    (no RNG, reproducible from the inputs), and it only diverges from plain
    crc32(name) when it must — every non-colliding create still mints exactly
    the value it always would have.

    ``taken`` is the set of seeds held by live profiles. Pass it under the same
    lock that guards the check-then-insert, or two concurrent creates can both
    read a stale set and mint the same seed. None = don't check (the caller has
    no registry in hand).

    Making the minted value SECRET/unguessable is a separate decision — it
    would move existing profiles' fingerprints — and is deliberately not
    attempted here. Freezing and hiding are different problems; keeping them
    apart is why this is a named function and not an inlined literal.
    """
    seed = zlib.crc32(name.encode("utf-8"))
    if taken is None or seed not in taken:
        return seed
    n = 1
    while True:
        seed = zlib.crc32(f"{name}:{n}".encode("utf-8"))
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

    def to_dict(self) -> dict:
        return asdict(self)
