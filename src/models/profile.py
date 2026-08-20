import zlib
from dataclasses import asdict, dataclass, field


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

    @property
    def fingerprint_seed(self) -> int:
        """Deterministic per-profile fingerprint seed derived from the name.

        Same profile name always yields the same fingerprint; distinct names
        yield distinct fingerprints, so each persona is isolated without the
        user having to pick seed numbers.
        """
        return zlib.crc32(self.name.encode("utf-8"))

    def to_dict(self) -> dict:
        return asdict(self)
