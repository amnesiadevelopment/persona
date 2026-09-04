"""How an exit country is NAMED on the operator's surfaces.

ONE implementation, because there are two surfaces that must agree about the
same record: the network page's meta line and the Activity Log's
``proxy_ok_message``. Both used to gate the country segment on the country
NAME alone, and PS-268 introduced a record shape in which the name is
legitimately empty beside a populated code — ``ipinfo.io`` has no country-name
field at all (see ``proxy_checker._geo_fields_from_payload``). So a verified
Polish exit stored ``country_code='PL'``, ``country_name=''`` and then rendered
no country at all on both surfaces, while the row's flag — which keys on the
CODE — still painted the Polish flag beside it.

The rule here is the whole fix, stated once:

    the ``[XX]`` marker appears exactly when there IS a code, and the name
    appears exactly when there IS a name.

That covers all four shapes without a special case per surface:

    code   name       segment
    'PL'   'Poland'   '[PL] Poland'   ipwho.is — byte-identical to before
    'PL'   ''         '[PL]'          ipinfo.io fallback — the defect
    ''     'Poland'   'Poland'        the degraded/partial body (see below)
    ''     ''         ''              nothing known — say nothing

NO CODE->NAME TABLE. ``_geo_fields_from_payload``'s docstring argues against
one explicitly ("a second source of truth for something no shipped behaviour
depends on"), and this module agrees: ``[PL]`` is rendered, the word "Poland"
is never invented from it. A row reading ``[PL]`` is honest; a row saying
nothing about a country the product just measured and stored is not.

The third shape is worth naming because its output CHANGES here. A body
carrying a name and no code reaches the UI through ``_resolve_geo``'s partial
fallback, and the log's old expression printed it as ``[] Poland`` — an empty
marker asserting a code that does not exist. That is the same defect from the
other side (a field-driven format ignoring its own emptiness), and it falls out
of the same one-line rule rather than being a second change.

This lives in ``utils`` rather than in either caller so that neither caller
owns it: a UI module importing a formatter out of ``proxy_checker`` (or
``proxy_checker`` importing one out of ``src.ui``) would be the layering
inversion, and two private copies would be the drift.
"""

from __future__ import annotations


def country_label(code: str, name: str) -> str:
    """The country segment for a stored proxy record, or ``""`` if nothing is
    known about its exit country.

    ``code`` is rendered as a bracketed marker; ``name`` is rendered verbatim.
    Neither is derived from the other. Whitespace-only inputs count as absent,
    so a stored ``" "`` cannot produce a lone bracket pair or a dangling space.
    """
    code = (code or "").strip()
    name = (name or "").strip()
    marker = f"[{code}]" if code else ""
    return " ".join(part for part in (marker, name) if part)
