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

THE SECOND READER, WHICH DOES NOT ADVERTISE ANYTHING (PS-409)
-------------------------------------------------------------
``measuretext_repair_required()`` / ``carries_measuretext_fix()`` ask a different
question of the same record: not "what version shall this profile CLAIM" but
"does the installed engine still need ``measuretext_ext``'s repair". Three
consequences follow from that difference, and each is a deliberate asymmetry with
everything above rather than an inconsistency:

* it compares the RAW TAG through ``updater.parse_version``, never
  ``ChromiumVersion.full``, because the four-component truncation that keeps an
  ADVERTISED version honest destroys the fifth component the fixed engine release
  is distinguished by (PS-406);
* it FAILS OPEN, where every advertising path fails closed. An unreadable version
  must leave the repair installed: refusing the launch, or omitting the repair,
  would break Google Sheets for a user mid-update;
* it holds no threshold of its own. The "which builds behave how" knowledge lives
  in ``engine/policy``, beside ``KNOWN_BAD_VERSIONS``.
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

    A TAG THAT WOULD ADVERTISE THE FROZEN UA FORM IS REFUSED. This is the one
    decision in this function worth stating, because the tempting answer is the
    wrong one. Emitting '151.0.0.0' as ``full`` looks harmless — the major is
    all the user agent and the brand list need — but ``full`` also feeds
    ``uaFullVersion``/``fullVersionList``, and the module docstring above names
    a ``.0.0`` full version as a TELL that no real Chrome emits. Worse, it is
    the tell a checker can actually SEE: it cross-references ``uaFullVersion``
    against the reduced UA, and on such a tag the two fields come back
    byte-identical. Refusing is the same fail-closed answer an unreadable
    version already gets, for the same reason: persona does not advertise a
    version it cannot state a real build for.

    TWO GUARDS, BECAUSE NEITHER SUBSUMES THE OTHER — and getting this wrong is
    how the first attempt at this rule shipped a hole:

    * **the value guard** (``full != reduced``, checked below against the very
      property the surfaces read) is the invariant proper. It is a statement
      about component VALUES, so it catches '151.0.0.0' — four components,
      nothing padded, straight through any count-based check.
    * **the count guard** (three stated components) catches what the value
      guard cannot: '151.5' normalises to '151.5.0.0', which is NOT equal to
      the frozen form and so passes the invariant, while still stating no build
      at all. A tag has to actually say major.minor.build.

    Only the fourth component is padded, because a patch of 0 is a real value
    real builds ship ('151.0.8000.0' is an ordinary version, '151.0.0.0' is
    not). Every fingerprint-chromium tag observed to date is four-component, so
    this refuses nothing upstream currently publishes.

    What this deliberately does NOT rule out: a tag stating an implausibly
    small build ('151.0.0.5' parses). That would be a claim about which build
    NUMBERS are realistic, which persona has no evidence for and no business
    guessing — whereas ``full == reduced`` is a defect persona can prove,
    because it is the equality a checker performs. The guard is positioned on
    the property that is observable, not on plausibility.

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
    version = ChromiumVersion(full=".".join(parts))
    # THE INVARIANT, checked against the very property the surfaces read rather
    # than against a proxy for it. ``full`` feeds uaFullVersion/fullVersionList
    # and ``reduced`` feeds the UA; a checker cross-references the two, so a tag
    # whose full version IS the frozen UA form hands it byte-identical fields —
    # the tell this module exists to remove. Note this cannot be folded into the
    # count guard above: '151.0.0.0' states four components and pads nothing,
    # while '151.5' fails the count guard yet satisfies this one.
    if version.full == version.reduced:
        raise EngineVersionUnreadableError(
            f"refusing Chromium engine tag {tag!r}: it states no real build — its "
            f"full version ({version.full}) is identical to the frozen user-agent "
            "form, a shape no real Chrome reports; advertising it would make "
            "uaFullVersion and the UA agree exactly, which is the tell persona "
            "derives the version to avoid"
        )
    return version


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


def carries_measuretext_fix(installed_tag: str, fix_min_tag: str) -> bool:
    """Does the engine named by ``installed_tag`` carry the PS-345 fix?

    The pure half of the ``measuretext_ext`` gate, separated from the read so the
    decision can be exercised over its whole truth table without an engine on
    disk. ``process.py`` consumes it through
    :func:`measuretext_repair_required`, which is the half that reads.

    ⛔ IT COMPARES RAW TAGS, NOT ``ChromiumVersion.full``, AND THAT IS THE ONE
    DECISION IN THIS FUNCTION THAT MUST NOT BE "TIDIED". ``parse`` above
    truncates to four components so the version a page is told stays a
    well-formed Chromium version — and PS-406 established that the fixed assets
    must be published under a FIFTH component (``152.0.7977.75.1``), because the
    tag cannot repeat the string every existing install already carries and must
    not bump the fourth component into a Chromium build that does not exist. So
    the component that distinguishes a fixed engine from the broken one it
    replaces is EXACTLY the component ``parse`` drops. Comparing ``.full``
    would make a fixed engine and an unfixed one identical to this gate, which
    is the permissive failure on every install.

    ``updater.parse_version`` is the comparator for the same reason it is the
    one the update offer itself uses: a gate that ordered versions differently
    from the mechanism that decides what gets installed could disagree with it.

    FAILS OPEN — returns ``False`` (the engine does NOT carry the fix, so the
    repair is needed) on every uncertainty: no threshold in force, an unreadable
    installed version, a tag with no numeric component. The asymmetry is
    deliberate and is this ticket's governing rule: a wrong ``False`` leaves an
    extension installed that repairs nothing, while a wrong ``True`` hands the
    user ~1e-6 geometry with nothing repairing it and breaks Google Sheets.
    ⚠️ Note the first cost is the NARROWER one the reading established rather
    than the "observable wrapper" the ticket argued: on Chromium PS-368's leaf
    cloak makes the wrapper stringify identically to a native method, so the tell
    the ticket described is a FIREFOX reading (PR #327's Arm N). See
    ``readings/ps409-2026-09-11/EVIDENCE.md`` §3 — and note the asymmetry is
    unaffected, because the strict failure is unchanged and is far worse.
    """
    from ..engine.updater import parse_version

    if not fix_min_tag:
        # No threshold in force. "We cannot name a fixed build" is not "every
        # build is fixed" — see the fail-open paragraph above.
        return False
    installed = parse_version(installed_tag)
    minimum = parse_version(fix_min_tag)
    if not installed or not minimum:
        # An unparseable tag on either side. ``parse_version`` yields an empty
        # tuple, which compares BELOW everything — so an empty INSTALLED tuple
        # would already answer False, but an empty MINIMUM would compare at or
        # below every install and read as "everything is fixed". Both are
        # refused explicitly rather than left to tuple ordering.
        return False
    return installed >= minimum


def measuretext_repair_required() -> bool:
    """Does the INSTALLED engine still need ``measuretext_ext``'s repair?

    The gate ``browser/process.py`` consults before appending
    ``build_measuretext_extension``. Reads the version from what is ACTUALLY
    INSTALLED (``updater.current_version()`` → ``ENGINE_DIR/version.txt``, the
    same record every other advertised shape is derived from) and the threshold
    from ``engine/policy`` — never from a constant in this module.

    ⚠️ THIS DOES NOT GO THROUGH :func:`installed_chromium_version`, and the
    difference is load-bearing rather than stylistic. That function returns a
    ``ChromiumVersion``, whose ``full`` is truncated to four components — the
    very component the fixed release is distinguished by. It also REFUSES a tag
    that states no real build, which is the correct answer when persona is about
    to ADVERTISE a version and the wrong one here: a profile whose version.txt
    cannot be read must still get the repair, not a refusal, because refusing
    would break Sheets for exactly the population this gate exists to protect.
    So the raw string is read and compared, and an unreadable one fails open.

    Returns ``True`` (install the repair) whenever the engine does not provably
    carry the fix.
    """
    from ..engine import policy
    from ..engine.updater import current_version

    try:
        installed_tag = current_version()
    except Exception:  # pragma: no cover - current_version swallows its own IO
        # ``current_version`` already returns "" on a missing or undecodable
        # file, so this is belt-and-braces for a reader: whatever goes wrong
        # reading the engine's own record, the answer is "the repair is needed".
        installed_tag = ""
    return not carries_measuretext_fix(
        installed_tag, policy.measuretext_fix_min_version()
    )
