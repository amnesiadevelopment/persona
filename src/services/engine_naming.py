"""The one seam through which OUR Chromium engine's display name reaches the
service layer — and the reason it is a seam rather than an import.

WHY THIS MODULE EXISTS AT ALL. PS-318 requires every operator-facing string
naming our Chromium engine to say ``Personium``, sourced from
``core.strings.CHROMIUM_ENGINE_NAME`` rather than retyped. Some of those strings
are built in ``services/engine/`` and ``services/browser/`` — the rollback and
resume log lines, the engine-policy refusals, the mTLS fallback note — because
that is where the outcome they report is decided.

BUT THOSE TWO PACKAGES MAY NOT IMPORT ``core.strings``. That is a PS-224 fence
with a test behind it
(``tests/test_ps224_engine_name.py::test_the_launch_layer_cannot_even_import_the_module_that_defines_the_name``,
parametrized over ``services/browser`` AND ``services/engine``), and it is not
incidental to PS-318 — it is the mechanism enforcing PS-318's own bound that the
product name must never reach anything a web page can read. A browser announcing
itself as "Personium" would be a UNIQUE MARKER identifying every one of our
users, which is the exact opposite of what this product exists to do.

``services/engine`` is genuinely on that path and not merely near it:
``updater.py`` writes ``version.txt``, and ``current_version()`` →
``browser/engine_version.parse()`` makes it THE source of the Chromium version an
Android profile advertises (see ``updater.version_from_tag``: "THE PREFIX MUST
NOT TRAVEL PAST THIS MODULE'S API BOUNDARY").

So the two requirements — "name the engine in these strings" and "do not make the
name importable there" — are jointly unsatisfiable by a direct import. This
module is the resolution: it lives OUTSIDE both fenced packages, so the fenced
code reaches the display name through a named, greppable seam instead of pulling
``core.strings`` into the layer that builds command lines.

⚠️ WHAT THIS DOES AND DOES NOT BUY, stated plainly rather than overclaimed.
It preserves the fence's INTENT — the launch layer's import graph still does not
contain the module that defines the name, and an edit that wants the name on the
wire cannot get it by reaching for the obvious import. It does NOT make the name
unreachable in principle; a determined edit inside the fenced packages can call
this function. The fence was always a TRIPWIRE rather than a wall.

THE REAL GUARANTEES ARE ELSEWHERE, AND THEY ARE UNCHANGED — the substantive
invariant is not "the name is unimportable" but "the name is absent from what a
page observes", and that is asserted directly, against a REAL launch, by
``test_the_name_is_absent_from_the_real_chromium_launch_argv`` and
``test_the_name_is_absent_from_every_extension_the_launch_injects``. Those two
are the load-bearing tests; this seam does not touch them.

THIS NAME IS FOR OPERATOR-FACING TEXT ONLY. Never put the value it returns into
an argv, a user agent, a brand list, a header, an extension payload, or any other
value a page or a server can observe.
"""


def engine_display_name() -> str:
    """OUR Chromium engine's name, as shown to the OPERATOR.

    Resolved through a function-local import so this module stays a leaf at
    import time and cannot introduce a cycle into either fenced package's module
    graph — the same reason ``browser/engine_version.installed_chromium_version``
    imports the updater function-locally.

    There is deliberately NO fallback value. An unresolvable name is a broken
    installation, and returning a placeholder would ship an operator-facing
    string with a hole in it rather than failing where the cause is visible.
    """
    from ..core.strings import CHROMIUM_ENGINE_NAME

    return CHROMIUM_ENGINE_NAME
