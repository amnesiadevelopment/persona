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

THE CEILING IS A CLAIM ABOUT TESTING — KEEP IT HONEST
-----------------------------------------------------
Firefox's cap is a mechanical fact: a newer ``firefox-NN`` speaks a juggler
contract the shipped driver cannot drive, so the bound is derivable from the
package itself and cannot drift. **Chromium's ceiling has no such backing.**
``MAX_TESTED_MAJOR`` is a record of the newest Chromium major persona's masking
layer has actually been exercised against — a human claim, not a computed one.
Nothing in the code can detect that it has gone stale, so it will rot unless it
is maintained deliberately:

* **When a new Chromium major is verified** — run the anti-leak/checker matrix
  against an engine of that major, and if the masking layer holds, raise
  ``MAX_TESTED_MAJOR`` to it IN THE SAME COMMIT as the evidence. Raising it
  because an update was inconvenient is how the guard becomes decoration.
* **When a major is found to break a spoof** — leave the ceiling where it is and
  add the specific build to ``KNOWN_BAD_VERSIONS`` if it is a point release.
* The ceiling is a CEILING, not a floor: it never blocks anything at or below
  the tested major, so routine updating within a known-good major is untouched.

Being above the ceiling is NOT the same as being broken. It means "persona has
not been shown to work against this" — which is why the operator is told a
persona update is needed rather than being told the download failed.
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

# The newest Chromium MAJOR persona's masking layer has been shown to work
# against. See the module docstring: this is a claim about testing, not a
# mechanical fact, and it is maintained by hand.
#
# 148 — the major the shipped masking layer is written against: the Chrome-brand
# UA client hints in browser/mobile_ext.py (brand versions "148") and the mobile
# device presets' Chrome/148.0.0.0 user agents. An engine whose real major
# diverges from those values is exactly the mismatch a checker notices.
MAX_TESTED_MAJOR = 148

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

    Returns -1 when there is no leading numeric component to read, so an
    unparseable tag is never silently treated as major 0 (which would compare as
    below every ceiling and sail through the cap).
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


def max_tested_major() -> int:
    """The effective ceiling: the operator's value when they set one, else the
    committed ``MAX_TESTED_MAJOR``.

    An operator MAY raise this — the point of the guard is that taking an
    untested engine is a visible decision, not that it is impossible. Editing a
    file on their own machine is exactly that decision, made explicitly. A
    non-integer or negative override is ignored rather than obeyed, so a typo
    cannot accidentally block every update.
    """
    val = _local_policy().get("max_tested_major")
    if isinstance(val, bool) or not isinstance(val, (int, str)):
        return MAX_TESTED_MAJOR
    try:
        num = int(val)
    except (TypeError, ValueError):
        return MAX_TESTED_MAJOR
    return num if num >= 0 else MAX_TESTED_MAJOR


def check(tag: str) -> tuple[str, str]:
    """Decide whether ``tag`` may be installed. Returns (kind, message).

    ``kind`` is OK / KNOWN_BAD / ABOVE_CEILING; ``message`` is operator-facing
    and explains the refusal (empty when OK). An empty or unparseable tag is OK
    here — "no tag" is a fetch failure, which the caller already reports, and
    turning it into a governance refusal would mislabel a network problem.
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
            f"Chromium engine {tag} is newer than persona has been tested against "
            f"(Chromium {ceiling}) — update persona to get it.",
        )
    return OK, ""


def is_installable(tag: str) -> bool:
    """True when ``tag`` passes both the known-bad list and the ceiling."""
    kind, _ = check(tag)
    return kind == OK


__all__ = [
    "ABOVE_CEILING",
    "KNOWN_BAD",
    "KNOWN_BAD_VERSIONS",
    "MAX_TESTED_MAJOR",
    "OK",
    "POLICY_FILE",
    "check",
    "is_installable",
    "known_bad_versions",
    "major",
    "max_tested_major",
]
