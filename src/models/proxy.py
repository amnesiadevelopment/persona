from dataclasses import asdict, dataclass


@dataclass
class Proxy:
    name: str
    url: str
    country_code: str = ""
    country_name: str = ""
    last_ip: str = ""
    timezone: str = ""
    lat: float | None = None
    lon: float | None = None
    checked_at: float = 0.0
    last_check_ok: bool | None = None

    def to_dict(self) -> dict:
        return asdict(self)
