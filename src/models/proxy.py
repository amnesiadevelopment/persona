from dataclasses import asdict, dataclass


@dataclass
class Proxy:
    name: str
    url: str
    rotate_url: str = ""
    country_code: str = ""
    country_name: str = ""
    last_ip: str = ""
    timezone: str = ""
    lat: float | None = None
    lon: float | None = None
    checked_at: float = 0.0
    last_check_ok: bool | None = None
    #: The zone the OPERATOR declared for this exit, and the country they
    #: declared it FOR. Separate from ``timezone`` on purpose: that field is
    #: MEASURED (``mark_checked`` writes what the geo provider reported) and
    #: this one is DECLARED. Keeping them apart is what lets the launch path
    #: give measured evidence strict precedence, and it is why ``mark_checked``
    #: needs no change at all — it writes only the six geo fields, so a check
    #: that reports no zone can no longer destroy the operator's declaration.
    #:
    #: ``manual_timezone_country`` is the disarming term. A backconnect exit
    #: moves; a zone declared for an RO exit says nothing about a CZ one. The
    #: declaration is consulted only while the stored ``country_code`` still
    #: matches the country it was made for, so a country move silently retires
    #: it (and the launch refuses again) instead of asserting a stale clock —
    #: the country/clock contradiction ``TimezoneUnderivableError`` exists to
    #: make unrepresentable.
    manual_timezone: str = ""
    manual_timezone_country: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
