"""Governance for the fingerprint-chromium engine version: which builds persona
is willing to install.

WHY THIS FILE EXISTS
--------------------
The Firefox engine's version is a governed fact. ``engine/firefox.fetch_latest``
skips drafts and prereleases, skips an explicit ``BROKEN_VERSIONS`` list, and
caps the build at what the bundled driver can actually drive. The Chromium
engine had none of that: ``updater.fetch_latest_full`` asked
``/releases/latest`` and installed whatever came back. "Is it newer?" was the
only question asked — so a build that upstream published by mistake, or one that
breaks a spoof, installed exactly as readily as a good one. That matters more
here than it would elsewhere, because the Chromium engine carries the whole
extension-based masking layer (``gpu_ext``, ``device_ext``, ``voice_ext``,
``webgl_ext``, ``native_ext`` and the realm registry) against a third-party fork.

WHERE THE DATA LIVES, AND WHY HERE
----------------------------------
Firefox reads ``BROKEN_VERSIONS`` from ``invisible_playwright.constants``
because that package is the driver — the knowledge about which builds it can
drive belongs with it. Chromium has no such package: the engine is a plain
release asset and nothing downstream of persona has an opinion about it. So the
knowledge lives here, in persona, and it lives in TWO layers:

1. **The committed defaults below** — what this persona build shipped knowing.
   Updating them is a normal code change that goes out with a persona release.
2. **A local policy file** (``PERSONA_HOME/engine-policy.json``) — an operator
   escape hatch, read at call time. A build discovered to be bad can be refused
   BY NAME without waiting for a persona release, and an operator who wants a
   newer engine than persona has tested can raise the ceiling deliberately.

Deliberately NOT a remote blocklist. Fetching this policy over the network would
put the decision back in the hands of whatever answered the request — the exact
property this file exists to remove — and a suppressed or swapped response would
silently disable the guard. Security is invariant #0; a guard that an attacker
can turn off is not a guard. The local file is an explicit operator action on a
machine they already control.

Deliberately NOT ``engine-baseline.txt``. That file is Firefox's floor, enforced
by a numeric ``firefox-NN`` compare in ``release.yml``; putting a second engine's
versioning in it would break that guard's simplicity.

THE CEILING USED TO BE A HAND-MAINTAINED CLAIM — IT IS NOT ANYMORE
------------------------------------------------------------------
Firefox's cap is a mechanical fact: a newer ``firefox-NN`` speaks a juggler
contract the shipped driver cannot drive, so the bound is derivable from the
package itself and cannot drift. Chromium's ceiling had no such backing.
``MAX_TESTED_MAJOR`` was a record of the newest Chromium major persona's masking
layer had been exercised against — a human claim, not a computed one — and it
existed because the Chromium major was DUPLICATED into the masking layer by
hand (the mobile extension's Client Hints brands, and every Android preset's
``Chrome/NNN.0.0.0`` user agent). If the engine moved ahead of those constants,
a profile would tell a site "I am Chrome 148" while the engine underneath
behaved as 149 — exactly the mismatch a checker notices. The ceiling prevented
that by preventing the engine from ever getting ahead.

**That duplication is gone.** ``browser/engine_version.py`` now DERIVES the
advertised version from the engine that is actually installed, and every
advertised shape — the user agent, the brand list's bare major, and the
full-version list — is a projection of that one reading. An engine bump moves
all of them together because there is only one of them. There is consequently
no constant left for the engine to get ahead OF, and no committed ceiling here:
a routine engine update no longer needs a person to authorise it.

WHAT STILL REFUSES A BUILD
--------------------------
Removing the ceiling is not the same as removing the governance. Two guards
remain, and they are the two that were never claims about a constant:

* ``KNOWN_BAD_VERSIONS`` — a build persona knows to be broken is still refused
  BY NAME, and an operator can add one locally without waiting for a release.
* ``max_tested_major()`` — the operator's local policy file can still impose a
  ceiling. It is now OPT-IN: absent an override there is no ceiling at all.
  An operator who wants to pin their engine below some major (a checker
  regression they are waiting out, say) still can.

The ABOVE_CEILING verdict therefore survives, because an operator who sets a
ceiling deliberately must still be told "persona declined this" rather than
"the download failed" — a decision persona made must never read as a network
error. It is simply no longer reachable unless an operator asks for it.

WHAT WOULD MAKE A CEILING NECESSARY AGAIN
-----------------------------------------
If a future change reintroduces a hardcoded Chromium version anywhere in the
masking layer, this reasoning collapses and the ceiling has to come back with
it. The guard against that is not vigilance — it is that
``engine_version.installed_chromium_version()`` has no default to fall back to:
a profile that cannot read the engine's version refuses to launch rather than
advertising a guess. Keep it that way.
"""

import json
import os

from ...core.config import PERSONA_HOME

# --------------------------------------------------------------------------
# Committed defaults — what THIS persona build ships knowing.
# --------------------------------------------------------------------------

# Specific fingerprint-chromium releases persona refuses to install, by tag.
# Empty today: no upstream build is currently known to be bad. That is the
# honest state — an invented entry would make the mechanism look exercised when
# it is not. The refusal path is covered by tests, so the list being empty does
# not mean the guard is untested.
#
# Add a tag here (exact ``tag_name`` as published, e.g. "148.0.7778.215") the
# moment a build is found to be broken, with a comment saying WHAT it broke, so
# the next person can tell a real known-bad from a stale one.
KNOWN_BAD_VERSIONS: frozenset[str] = frozenset()

# NO COMMITTED CEILING. See the module docstring: the Chromium major is no
# longer duplicated into the masking layer, so there is no constant for an
# engine to get ahead of, and a routine engine update needs no human to
# authorise it. ``browser/engine_version.py`` derives the advertised version
# from the engine that is installed.
#
# A ceiling is still EXPRESSIBLE — an operator can set ``max_tested_major`` in
# their local policy file to pin their engine below some major — but persona
# ships without one. ``NO_CEILING`` is the sentinel for "no ceiling in force";
# every major compares at or below it, so the ceiling test can stay a plain
# numeric compare instead of growing a None branch at each call site.
NO_CEILING = float("inf")

# Operator override, read at call time (not import time) so an edit takes effect
# without restarting the app.
POLICY_FILE = os.getenv(
    "PERSONA_ENGINE_POLICY_FILE", os.path.join(PERSONA_HOME, "engine-policy.json")
)

# Verdict kinds. The caller distinguishes these so the UI can say "needs a
# persona update" rather than "download failed" — the Firefox path already draws
# that line and the two engines must not report the same situation differently.
OK = "ok"
KNOWN_BAD = "known_bad"
ABOVE_CEILING = "above_ceiling"


def major(tag: str) -> int:
    """The Chromium major from a release tag: '148.0.7778.215' → 148.

    Returns -1 when there is no leading numeric component to read. That is a
    DISTINGUISHABLE "no major here" for callers to read — it is NOT a guard.
    Be precise about this, because an earlier version of this docstring claimed
    it was: the ceiling test is ``num > ceiling``, under which -1 and 0 behave
    identically, so an unparseable tag is installable exactly as it would be at
    major 0. ``check()`` does not refuse it.

    That is deliberate, and it is safe for a reason outside this function: an
    unparseable tag can never be OFFERED as an update, because ``is_newer()``
    parses it to an empty tuple which compares below every installed version.
    The only path it can reach is a FIRST install, where persona takes an
    untested build rather than leave the app with no browser at all — the same
    answer the ABOVE_CEILING asymmetry already gives. Refusing here would
    contradict that decision, not reinforce it.
    """
    lead = (tag or "").strip().lstrip("v").split(".", 1)[0]
    digits = "".join(c for c in lead if c.isdigit())
    return int(digits) if digits else -1


def _local_policy() -> dict:
    """The operator's local overrides, or {} when absent/unreadable/malformed.

    Fails OPEN to the committed defaults on purpose: a corrupt policy file must
    not brick engine updating, and the shipped defaults are themselves a safe
    answer. A file that cannot be parsed is simply not an override.
    """
    try:
        with open(POLICY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def known_bad_versions() -> frozenset[str]:
    """Committed known-bad tags plus any the operator added locally.

    Local entries only ever ADD. A local file cannot un-block a build persona
    shipped knowing is broken — that would let a stale local file silently
    re-enable a known-bad engine, and the shipped list is the stronger claim.
    """
    extra = _local_policy().get("known_bad_versions")
    if not isinstance(extra, list):
        return KNOWN_BAD_VERSIONS
    return KNOWN_BAD_VERSIONS | {str(t).strip() for t in extra if str(t).strip()}


def max_tested_major() -> float:
    """The effective ceiling: the operator's value when they set one, else
    ``NO_CEILING``.

    persona ships WITHOUT a ceiling. The advertised Chromium version is derived
    from the installed engine (see the module docstring), so there is no
    constant a newer engine can get ahead of, and a routine update needs no
    human in the loop.

    An operator MAY still impose one — pinning their engine below some major
    while they wait out a regression, say — by setting ``max_tested_major`` in
    their local policy file. That is an explicit decision on a machine they
    control, which is exactly the kind of refusal this module keeps. A
    non-integer or negative override is ignored rather than obeyed, so a typo
    cannot accidentally block every update; it falls back to no ceiling.
    """
    val = _local_policy().get("max_tested_major")
    if isinstance(val, bool) or not isinstance(val, (int, str)):
        return NO_CEILING
    try:
        num = int(val)
    except (TypeError, ValueError):
        return NO_CEILING
    return num if num >= 0 else NO_CEILING


def check(tag: str) -> tuple[str, str]:
    """Decide whether ``tag`` may be installed. Returns (kind, message).

    ``kind`` is OK / KNOWN_BAD / ABOVE_CEILING; ``message`` is operator-facing
    and explains the refusal (empty when OK). An empty or unparseable tag is OK
    here — "no tag" is a fetch failure, which the caller already reports, and
    turning it into a governance refusal would mislabel a network problem.

    ABOVE_CEILING is unreachable unless the OPERATOR set a ceiling in their
    local policy file — persona ships without one — so its message names their
    own setting rather than telling them to update persona, which would be an
    instruction they cannot act on for a limit they themselves imposed.
    """
    tag = (tag or "").strip()
    if not tag:
        return OK, ""
    if tag in known_bad_versions():
        return (
            KNOWN_BAD,
            f"Chromium engine {tag} is on persona's known-bad list — not installing it.",
        )
    ceiling = max_tested_major()
    num = major(tag)
    if num > ceiling:
        return (
            ABOVE_CEILING,
            f"Chromium engine {tag} is above the maximum Chromium major set in "
            f"your engine policy file (Chromium {ceiling:g}) — raise or remove "
            f"max_tested_major in {POLICY_FILE} to install it.",
        )
    return OK, ""


def is_installable(tag: str) -> bool:
    """True when ``tag`` passes both the known-bad list and any operator ceiling."""
    kind, _ = check(tag)
    return kind == OK


__all__ = [
    "ABOVE_CEILING",
    "KNOWN_BAD",
    "KNOWN_BAD_VERSIONS",
    "NO_CEILING",
    "OK",
    "POLICY_FILE",
    "check",
    "is_installable",
    "known_bad_versions",
    "major",
    "max_tested_major",
]
