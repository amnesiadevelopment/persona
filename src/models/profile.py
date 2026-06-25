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
    search_engine: str = "duckduckgo"
    bookmark_pool: str | None = None
    bookmarks: list[str] = field(default_factory=list)
    cookie_import_status: str | None = None
    tags: list[str] = field(default_factory=list)
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
