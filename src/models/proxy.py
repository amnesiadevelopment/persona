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
    #: The LANGUAGE the operator declared for this exit, and the country they
    #: declared it for — the locale twin of the two fields above (PS-332), and
    #: deliberately the same shape so the two declarations behave identically.
    #:
    #: ⚠️ A LANGUAGE SUBTAG, NOT A LOCALE STRING, and that is the design
    #: decision rather than an abbreviation of one. Every one of
    #: ``_COUNTRY_LOCALE``'s 241 rows is exactly ``lang-REGION`` with the
    #: REGION equal to the table key, so the region half of a declared locale
    #: is not the operator's to choose: it is the exit country already on file.
    #: Storing the language alone and composing ``<lang>-<country_code>`` at
    #: the read makes an ``en-GB`` declared against a Nigerian exit
    #: UNREPRESENTABLE rather than merely refused — the country/clock
    #: contradiction ``_locale_for``'s docstring exists to prevent, in its
    #: locale half. It also means a declaration cannot fall out of agreement
    #: with the country gate: the two halves come from one field pair.
    #:
    #: ``manual_locale_country`` is the disarming term, exactly as its
    #: timezone counterpart is. A language declared for an NG exit says nothing
    #: about a CZ one, so the declaration is consulted only while the stored
    #: ``country_code`` still matches, and a country move silently retires it
    #: (the launch refuses again) instead of composing a locale for a country
    #: the exit has left.
    manual_locale_language: str = ""
    manual_locale_country: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
