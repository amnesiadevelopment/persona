"""Regenerate ``src/services/proxy/language_names.py`` from the IANA registry.

PS-332 needs a set of legal language subtags that is **byte-identical on
Windows, macOS and Linux**, for exactly the reason PS-274 vendored the IANA
zone names: the operator's declared value is validated before it is stored and
is then handed to a browser engine as fact, so "accepted on my machine, refused
on yours" is not an acceptable property for it.

There is no ``available_locales()`` to reject in the way ``zoneinfo`` was
rejected — the situation is worse. Python's stdlib exposes NO list of language
subtags at all; the nearest OS-provided oracles are ``locale -a`` (absent on
Windows entirely), ``/usr/share/i18n/locales`` (glibc only — absent on macOS
and Windows, and absent in a musl container), and ``babel``/``pycountry``
(new runtime dependencies, each carrying its own CLDR snapshot). Measured on
this project's own container while writing this script:

    /usr/share/i18n/locales                     -> ABSENT
    /usr/share/iso-codes/json                   -> ABSENT
    import pycountry / import babel             -> ModuleNotFoundError

So the accepted set is VENDORED IN-TREE from the IANA Language Subtag Registry
(the BCP 47 oracle, RFC 5646's own source of truth) and shipped as source. No
dependency is added and nothing reads an OS locale database at runtime, which
is also what keeps every predicate in the module PURE (no IO) so the render
layer can call it.

Run it when the vendored list should be refreshed:

    curl -o /tmp/lsr.txt \\
      https://www.iana.org/assignments/language-subtag-registry/language-subtag-registry
    python3 scripts/ps332_vendor_language_subtags.py /tmp/lsr.txt

It is a SCRIPT, not a test and not a build step: the generated module is
committed, so a build never needs the registry and a checkout is
self-contained. The script prints the File-Date it read; that date is what
lands in ``REGISTRY_FILE_DATE``.

WHAT IS KEPT, AND WHY IT IS THE TWO-LETTER SUBTAGS ONLY
------------------------------------------------------
The registry carries ``Type: language`` records with two-letter (ISO 639-1) and
three-letter (ISO 639-3) subtags. Only the TWO-LETTER ones are vendored, which
is not an arbitrary narrowing: every value in ``_COUNTRY_LOCALE``
(``launch_policy.py``, 241 rows) uses a two-letter language subtag, so the
shipped table is the shape the declaration must be interchangeable with. A
declared locale is consumed by the SAME engine argument as a table-derived one,
so it is held to the same shape — the same reasoning that narrows
``DECLARABLE_ZONE_NAMES`` to the ``Region/City`` form.

WHAT IS DROPPED: THE SUBTAGS AN ENGINE RENAMES
----------------------------------------------
The rule is **"the composed locale canonicalizes to itself"**, and it is not
the same rule as "the registry does not mark it ``Deprecated``". The first
version of this script tested the ``Deprecated`` FIELD, and its own docstring
then described the INTENT — that an engine must not report the declared value
back under another name. For three subtags those two disagree, and the field
test admitted all three:

    sh -> sr-Latn   Scope: macrolanguage, no Deprecated field at all
    tl -> fil       an ordinary live record, no Deprecated field at all
    tw -> ak        live; and ``tw`` ALONE canonicalizes to ``tw``

``tw`` is why the property has to be measured on the **composed** value rather
than on the bare subtag: ``Intl.getCanonicalLocales(['tw'])`` answers ``tw``,
while ``tw-NG`` answers ``ak-NG``. This product never ships a bare subtag —
``declared_locale`` composes ``<lang>-<COUNTRY>`` and hands THAT to the engine
— so a bare-form check would have passed ``tw`` and shipped the rename anyway.

The renames are CLDR/ICU alias facts, not IANA fields: no single registry field
predicts the nine. ``Macrolanguage`` is present on ``bs hr id nb nn sr`` which
are all fine, and absent from ``sh`` and ``tl``; ``Scope`` and ``Comments``
likewise cut across the set. So the exclusions are read from CLDR's own
``supplementalMetadata.xml`` ``<languageAlias>`` table — the data ICU
implements — and vendored as a literal below with the measurement that produced
it, rather than inferred from a proxy field that does not carry the fact.

Measured with node 24 / ICU 78.2 over the full cross-product of 190 two-letter
language subtags x 261 live region subtags (49,590 pairs): exactly nine
languages are renamed, each on 261/261 regions, and no rename is
region-dependent. ``DEPRECATED`` (6) is a strict subset of ``RENAMED`` (9).
"""

from __future__ import annotations

import hashlib
import pathlib
import sys

#: Two-letter language subtags a BCP 47 implementation RENAMES — the exclusion
#: set, and the reason it is a literal rather than a registry-field test.
#:
#: These are CLDR ``<languageAlias>`` entries (``common/supplemental/
#: supplementalMetadata.xml``), which is the data ICU implements and therefore
#: what a browser engine actually does to the value we hand it. NO IANA field
#: predicts this set: ``Deprecated`` covers only six of the nine, and
#: ``Macrolanguage`` / ``Scope`` / ``Comments`` each cut across it (``nb``,
#: ``sr``, ``bs``, ``hr`` carry those fields and are perfectly canonical).
#:
#: Each entry was MEASURED on the COMPOSED form the product actually ships,
#: ``<lang>-<COUNTRY>``, over 190 x 261 = 49,590 language/region pairs with
#: node 24 / ICU 78.2. All nine are renamed on 261/261 regions; none is
#: region-dependent. ``tw`` is the one that proves the composed form is the
#: right unit: ``tw`` alone canonicalizes to ``tw``, but ``tw-NG`` -> ``ak-NG``.
#:
#: CLDR also aliases ``nb`` -> ``no`` and ``sr`` -> ``sh``, and both are
#: DELIBERATELY absent here: ICU does not apply those directions (``nb-NO`` and
#: ``sr-RS`` canonicalize to themselves, measured), and both are LIVE values in
#: the shipped ``_COUNTRY_LOCALE`` table -- excluding them would make a locale
#: the product itself ships undeclarable.
RENAMED_BY_ENGINES = {
    "bh": "bho",      # macrolanguage; also Deprecated
    "in": "id",       # Deprecated 1989
    "iw": "he",       # Deprecated 1989
    "ji": "yi",       # Deprecated 1989
    "jw": "jv",       # Deprecated 2001
    "mo": "ro",       # Deprecated 2008
    "sh": "sr-Latn",  # legacy alias; NO Deprecated field in the registry
    "tl": "fil",      # legacy alias; NO Deprecated field in the registry
    "tw": "ak",       # macrolanguage; renamed only in the COMPOSED form
}

HEADER = '''"""Legal BCP 47 language subtags, VENDORED — the oracle the operator's declared
exit language is validated against.

GENERATED by ``scripts/ps332_vendor_language_subtags.py`` from the IANA
Language Subtag Registry. Do not hand-edit; re-run the script.

WHY THIS IS VENDORED RATHER THAN COMPUTED (PS-332, measured, not assumed):

    Python stdlib: no list of language subtags exists at all
    /usr/share/i18n/locales   (glibc only)      -> ABSENT on this container
    /usr/share/iso-codes/json (iso-codes pkg)   -> ABSENT on this container
    import babel / import pycountry             -> ModuleNotFoundError
    `locale -a`                                 -> absent on Windows entirely

There is therefore no OS-provided oracle that answers the same way on the three
platforms we ship — the situation PS-274 measured for timezones, one step
worse, because for languages the stdlib offers nothing to degrade from. The
operator's value feeds an engine as fact, so "accepted on my machine, refused
on yours" is not an acceptable property for it. Shipping the subtags as SOURCE
makes the accepted set identical on Windows, macOS and Linux by construction,
adds no dependency, and reads no OS locale database at runtime — which is also
what keeps every predicate in this module PURE (no IO), so the render layer can
call it.

This does NOT reintroduce a host-derived locale anywhere, and that prohibition
is stricter here than its timezone counterpart. ``#218`` forces ``en-US`` on
the direct path precisely so the host locale never leaks inside the tunnel;
this module answers only "is this string a real language subtag", never "what
language is this host in", and nothing on the launch path may ever ask the
second question.
"""

from __future__ import annotations

#: The IANA registry snapshot the list below was taken from (its own File-Date).
REGISTRY_FILE_DATE = "{file_date}"

#: sha256 of the newline-separated subtag text below, so a refresh can be shown
#: to be a faithful copy of what the script produced rather than a hand edit.
LANGUAGE_SUBTAGS_SHA256 = "{sha}"

#: {count} newline-separated ISO 639-1 subtags, registry order.
_SUBTAGS_TEXT = """\\
{body}"""

#: Two-letter language subtags a BCP 47 engine RENAMES, mapped to what it
#: answers instead. THE EXCLUSION SET: none of these is declarable, and the
#: test suite asserts that as a property rather than by name.
#:
#: WHY THIS IS DATA AND NOT A REGISTRY-FIELD TEST. The rule wanted is "the
#: composed locale canonicalizes to itself"; the first version of this module
#: implemented "the registry does not mark it ``Deprecated``" while its prose
#: described the first rule. Those two disagree on ``sh``, ``tl`` and ``tw``,
#: and the field test admitted all three — the module SAID ``sh`` was excluded
#: and it was not. No IANA field predicts this set: ``Deprecated`` covers six
#: of the nine, and ``Macrolanguage`` / ``Scope`` / ``Comments`` each cut
#: across it (``nb``, ``sr``, ``bs``, ``hr`` carry those fields and are
#: perfectly canonical). The renames are CLDR ``<languageAlias>`` facts — the
#: data ICU implements — so they are vendored as data, with the measurement.
#:
#: MEASURED ON THE COMPOSED FORM, which is the only form this product ships:
#: ``declared_locale`` composes ``<lang>-<COUNTRY>`` and hands THAT to the
#: engine. 190 two-letter languages x 261 live regions = 49,590 pairs, node 24
#: / ICU 78.2. All nine are renamed on 261/261 regions; none is
#: region-dependent. ``tw`` is why the bare subtag is the wrong unit:
#: ``getCanonicalLocales(['tw'])`` answers ``tw``, but ``tw-NG`` answers
#: ``ak-NG`` — a bare-form check passes ``tw`` and ships the rename anyway.
ENGINE_RENAMED_SUBTAGS: dict[str, str] = {{
{renamed}}}

#: The language subtags an operator may DECLARE for a proxy exit.
#:
#: TWO-LETTER (ISO 639-1) ONLY, and the narrowing is the shipped rule rather
#: than a new one: every value in ``_COUNTRY_LOCALE`` (``launch_policy.py``,
#: 241 rows) uses a two-letter language subtag, and a declared language is
#: consumed by the same engine argument as a table-derived one. Every subtag
#: in ``ENGINE_RENAMED_SUBTAGS`` above is excluded: an engine reports its
#: replacement back, so accepting one would let the declared value and the
#: observed value disagree — ``sh-NG`` is shipped as ``--lang=sh-NG`` and the
#: page reads ``sr-Latn``, which is exactly the tell PS-2 exists to close.
DECLARABLE_LANGUAGE_SUBTAGS: frozenset[str] = frozenset(_SUBTAGS_TEXT.split())


def is_declarable_language(subtag: str) -> bool:
    """Whether ``subtag`` is a language an operator may declare for an exit.

    Pure and total: a membership test against the vendored set above, and NOT
    a pattern test — which is what makes it safe. ``[a-z]{{2}}`` would accept
    ``xx``, ``qq`` and ``zz``, none of which is a language, and each of which
    would then be composed into a locale string and handed to a browser engine
    as fact. A plausible-looking non-language is simply absent from the set, so
    it needs no rule of its own. Case is normalised because BCP 47 subtags are
    case-insensitive and conventionally lowercase; nothing else is.

    FALSE for a REAL language an engine renames (``sh``, ``tl``, ``tw`` and the
    six deprecated tags — see ``ENGINE_RENAMED_SUBTAGS``). That is not the set
    being wrong about what a language is: ``sh`` IS Serbo-Croatian. It is the
    set answering the question this product actually asks, which is "may an
    operator declare this as the exit's language", and the answer is no for a
    value the engine will report back under another name. The operator-facing
    remedy is to declare the replacement (``sr`` for ``sh``) — a value that
    survives the round trip.
    """
    return (subtag or "").strip().lower() in DECLARABLE_LANGUAGE_SUBTAGS
'''


def _records(text: str) -> list[dict[str, list[str]]]:
    out = []
    for chunk in text.split("\n%%\n")[1:]:
        fields: dict[str, list[str]] = {}
        lines: list[str] = []
        for line in chunk.split("\n"):
            # The registry folds long values onto continuation lines indented
            # by two spaces; unfold before parsing so a folded Description
            # cannot be mistaken for a field of its own.
            if line.startswith("  ") and lines:
                lines[-1] += " " + line.strip()
            else:
                lines.append(line)
        for line in lines:
            if ": " in line:
                key, value = line.split(": ", 1)
                fields.setdefault(key, []).append(value)
        out.append(fields)
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    raw = pathlib.Path(argv[1]).read_text(encoding="utf-8")
    file_date = ""
    for line in raw.split("\n%%\n")[0].splitlines():
        if line.startswith("File-Date:"):
            file_date = line.split(":", 1)[1].strip()
    subtags = []
    for record in _records(raw):
        if record.get("Type", [None])[0] != "language":
            continue
        subtag = record["Subtag"][0]
        if len(subtag) != 2:
            continue
        # THE RULE IS "the composed locale canonicalizes to itself", not "the
        # registry does not mark it Deprecated". Those two disagree on sh, tl
        # and tw, and the field test admits all three. See RENAMED_BY_ENGINES.
        if subtag in RENAMED_BY_ENGINES:
            continue
        subtags.append(subtag)
    body = "\n".join(subtags) + "\n"
    sha = hashlib.sha256(body.encode()).hexdigest()
    renamed = "".join(
        f'    "{tag}": "{replacement}",\n'
        for tag, replacement in sorted(RENAMED_BY_ENGINES.items())
    )
    out = pathlib.Path(__file__).resolve().parents[1] / (
        "src/services/proxy/language_names.py"
    )
    out.write_text(
        HEADER.format(
            file_date=file_date,
            sha=sha,
            count=len(subtags),
            body=body,
            renamed=renamed,
        ),
        encoding="utf-8",
    )
    print(f"wrote {out} — {len(subtags)} subtags, File-Date {file_date}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
