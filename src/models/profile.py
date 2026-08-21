import zlib
from dataclasses import asdict, dataclass, field


def mint_fingerprint_seed(name: str) -> int:
    """Mint the seed a NEW profile freezes into ``fingerprint_seed_value``.

    Deliberately the SAME crc32(name) the old property derived on every read,
    so a freshly created profile presents byte-identically to what it would
    have presented before the seed was persisted — this change freezes the
    seed, it does not re-roll it. Making the minted value secret/unguessable is
    a separate decision (it would move existing profiles); keeping the two
    apart is why this is a named function and not an inlined literal.
    """
    return zlib.crc32(name.encode("utf-8"))


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
        """
        if self.fingerprint_seed_value is not None:
            return self.fingerprint_seed_value
        return zlib.crc32(self.name.encode("utf-8"))

    def to_dict(self) -> dict:
        return asdict(self)
