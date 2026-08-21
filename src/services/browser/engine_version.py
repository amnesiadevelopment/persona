"""The Chromium version a mobile profile advertises, READ from the engine that
is actually installed.

WHY THIS FILE EXISTS
--------------------
The Chromium major used to be written down TWICE: once by the engine that gets
installed, and once by hand in the masking layer (the mobile extension's Client
Hints brands, its full-version fallback, and every Android preset's
``Chrome/NNN.0.0.0`` user agent). Nothing could detect that the two had drifted,
so ``engine/policy.MAX_TESTED_MAJOR`` existed to stop the engine from ever
getting AHEAD of the constants — the operator was told to update persona rather
than being allowed a routine engine update.

That ceiling was a symptom. The cause was the duplication, and this module
removes it: the version is derived from ``ENGINE_DIR/version.txt`` — the record
the update machinery already writes and already reads — and every advertised
reading is a projection of that one value.

THE THREE SHAPES, AND WHY THEY DIFFER
-------------------------------------
A Chromium version reaches a page in three different shapes, and emitting the
same string in all three is itself a tell. Real Chrome:

* **user agent** — ``Chrome/149.0.0.0``. REDUCED: major, then a frozen ``0.0.0``.
  Chrome froze the UA's minor/build/patch, so a real device never shows its true
  build here.
* **Client Hints ``brands``** — ``version: '149'``. A BARE MAJOR, no dots.
* **``uaFullVersion`` / ``fullVersionList``** — ``149.0.8000.10``. The TRUE full
  version; this is the shape that is expressly not frozen, and it is what a
  checker cross-references against the reduced UA.

Deriving all three from one source is the point. Note the previous constants got
the third shape WRONG in a way this fixes for free: they advertised
``148.0.0.0`` as ``uaFullVersion``, i.e. the frozen UA form in the one field
that is supposed to carry the real build. A real Chrome never reports a
``.0.0`` full version.

WHEN THE VERSION CANNOT BE READ, THIS REFUSES
---------------------------------------------
``read()`` raises rather than substituting a default. There is deliberately no
fallback constant anywhere in this module, because a fallback is exactly the
defect being removed: a stale constant silently re-creates the mismatch the
moment the engine moves, and it does so INVISIBLY — the profile would claim a
version the engine underneath is not. Refusing is loud, recoverable (an engine
check rewrites ``version.txt``), and confined to Android profiles, which are the
only ones that advertise a Chromium version at all. See
``browser/process.py`` for the launch-time gate that acts on this.
"""

from dataclasses import dataclass


class EngineVersionUnreadableError(RuntimeError):
    """persona cannot tell which Chromium version the installed engine is.

    Raised when ``version.txt`` is absent (a reachable state: the install
    completeness gate accepts a marker OR a version file) or holds something
    with no leading numeric component. Callers that would otherwise ADVERTISE a
    version must fail closed on this rather than guess one.
    """


@dataclass(frozen=True)
class ChromiumVersion:
    """One engine version, in the three shapes the masking layer needs.

    ``full`` is normalised to four dotted numeric components so the shapes below
    are always well-formed; see ``parse``.
    """

    full: str

    @property
    def major(self) -> str:
        """The bare major, for the Client Hints ``brands`` list: ``'149'``."""
        return self.full.split(".", 1)[0]

    @property
    def reduced(self) -> str:
        """The frozen user-agent form: ``'149.0.0.0'``.

        This is what goes in ``Chrome/...`` — NOT ``full``. Real Chrome freezes
        the UA's trailing components, so emitting a true build there would be
        the anomaly, not the fidelity.
        """
        return f"{self.major}.0.0.0"


def parse(tag: str) -> ChromiumVersion:
    """Read a release tag ('148.0.7778.215', 'v149.0.8000.10') into a version.

    Raises ``EngineVersionUnreadableError`` when the tag does not carry a real
    BUILD — this function has no default to fall back to, on purpose.

    A TAG TOO SHORT TO YIELD A BUILD IS REFUSED, NOT PADDED. This is the one
    decision in this function worth stating, because the tempting answer is the
    wrong one. Padding '151' out to '151.0.0.0' looks harmless — the major is
    all the user agent and the brand list need — but ``full`` also feeds
    ``uaFullVersion``/``fullVersionList``, and the module docstring above names
    a ``.0.0`` full version as a TELL that no real Chrome emits. Padding would
    therefore reintroduce by a side door exactly the defect this module was
    written to remove, and it would do it INVISIBLY, which is the property that
    made the old hardcoded constants dangerous in the first place. Refusing is
    the same fail-closed answer an unreadable version already gets, for the same
    reason: persona does not advertise a version it cannot read.

    So at least three numeric components are required (major.minor.build). Only
    the fourth is padded, because a patch of 0 is a real value real builds ship
    ('151.0.8000.0' is an ordinary version, '151.0.0.0' is not). Every
    fingerprint-chromium tag observed to date is four-component, so this refuses
    nothing upstream currently publishes.

    Trailing non-numeric junk is dropped component-wise, which keeps a tag like
    '149.0.8000.10-beta' readable as the version it plainly states.
    """
    raw = (tag or "").strip().lstrip("vV")
    parts: list[str] = []
    for chunk in raw.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        if not digits:
            break
        parts.append(str(int(digits)))
    if len(parts) < 3:
        raise EngineVersionUnreadableError(
            f"cannot read a Chromium version from engine tag {tag!r}: a tag must "
            "carry major.minor.build to yield a real full version; persona will "
            "not pad one into a build it cannot read"
        )
    parts = (parts + ["0"])[:4]
    return ChromiumVersion(full=".".join(parts))


def installed_chromium_version() -> ChromiumVersion:
    """The version of the Chromium engine THIS persona has installed.

    Reads the same record the update machinery writes and reads
    (``updater.current_version()`` → ``ENGINE_DIR/version.txt``) rather than
    asking the binary a second way: one source of truth means the advertised
    version and the governed version cannot disagree.

    Imported function-locally to match every other browser→engine reference in
    this package (a module-level import closes a cycle through
    ``browser/__init__``).
    """
    from ..engine.updater import current_version

    return parse(current_version())
