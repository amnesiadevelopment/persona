"""The ONE source of the platform value the engine is told.

``--fingerprint-platform`` has exactly one correct value per profile, and every
module that reasons about it without holding THAT value is guessing. This module
exists so there is nowhere else to guess from: it owns both the vocabulary the
engine honours and the computation that produces the value, and both consumers —
the launch flag and the GPU extension's authorship decision — are handed the
same string.

WHY THIS IS A MODULE AND NOT TWO LINES IN ``process.py``
--------------------------------------------------------
It was two lines in ``process.py``, and the split cost two review rounds in a
row, in the same direction, for the same reason:

* Round 2: authorship was keyed on OUR fold's vocabulary (the spellings
  ``gpu_ext`` recognises). ``win`` is a spelling we recognise and the engine
  REJECTS, so our layer stood down for an engine that answered SwiftShader.
* Round 3: authorship was keyed on ``os_type``. But the engine is not told
  ``os_type`` — a mobile profile is rewritten to ``linux``/``macos``, so
  ``--fingerprint-platform`` is a function of ``(os_type, device_type)``. On
  ``windows`` + ``mobile`` our layer stood down expecting the engine to author
  ``windows`` while the engine was handed ``linux``.

Both are the same failure: TWO authors, each deciding from its own copy of the
question, and a profile where the copies disagree gets NEITHER author writing
the WebGL identity pair. ``getParameter(UNMASKED_*)`` then falls through to the
real implementation and the HOST's GL strings reach the page — Invariant #0, and
a ``HOST_LEAK`` row under ``verify.matrix_consistency``, which ranks it above a
mere ``CONTRADICTION`` and needs no second row to fire.

So the fix is not a third guard. A guard would leave a fourth axis for the next
round to find. The fix is that the value is computed ONCE, here, and handed to
both consumers — which makes "the two agree" true by CONSTRUCTION rather than by
coincidence, and there is no second computation left to drift.

WHAT THIS MODULE DELIBERATELY DOES NOT DECIDE
---------------------------------------------
It answers "what is the engine told?", never "which GPU pool does this profile
draw from?". Those are different questions with different right answers: a
mobile profile is backed by ``linux`` at the engine while its GPU pool must stay
on its own arm, because a Mesa desktop string on a phone UA is exactly the
impossible value ``gpu_ext``'s android arm exists to prevent. ``gpu_ext`` keeps
taking ``os_type`` for the pool and takes this value for authorship.
"""

from .device_presets import is_mobile_profile

# The platform values the ENGINE itself honours. Kept identical to
# ``verify.browser_tier.DECLARED_MACHINES`` — the repo's other statement of the
# same fact — and pinned equal to it by
# ``test_engine_honoured_platforms_match_the_declared_machines``. Restated here
# rather than imported because this module sits on the launch path and must not
# pull the whole verify tier in to answer one question.
#
# ⚠️ THIS IS NOT THE SET OF SPELLINGS WE RECOGNISE, AND THE DIFFERENCE IS A HOST
# LEAK. ``gpu_ext``'s fold accepts aliases the engine does not — ``win``,
# ``mac``, ``darwin``, ``iphone``. Authorship must be resolved from what the
# ENGINE accepts, because deferring on a spelling the engine rejects means
# neither author writes the identity pair.
#
# Measured 2026-08-25 against fingerprint-chromium/148.0.7778.215, layer OFF,
# seed 9001 (``readings/ps161-engine-vocabulary-2026-08-25/``):
#
#   --fingerprint-platform=   engine's own identity, layer OFF
#   windows / WINDOWS         Google Inc. (AMD) / …Radeon(TM) (0x00001638) D3D11
#   macos   / MACOS           Google Inc. (Apple) / …Metal Renderer: Apple M2
#   win     / Win             Google Inc. (Google) / SwiftShader   <- NOT HONOURED
#
# Two things that reading establishes, neither assumed: the engine REJECTS the
# ``win`` alias, and it LOWERCASES its platform argument (so case folding is
# measured rather than hoped for). It remains a claim about a third party's
# build — if a future engine stops folding case, that reading is what to re-run.
ENGINE_HONOURED_PLATFORMS = frozenset({"windows", "macos", "linux"})


def engine_platform_for(os_type: str, device_type: str = "desktop") -> str:
    """The exact string this profile's engine is launched with.

    This is the ONLY place the value is computed. Both consumers take it from
    here: ``process.py`` emits it as ``--fingerprint-platform`` and hands the
    same string to ``build_gpu_extension`` as ``engine_platform``, so the flag
    and the authorship decision are provably one value.

    ⚠️ IT IS A FUNCTION OF ``(os_type, device_type)``, NOT OF ``os_type`` ALONE,
    and that is the whole reason this function exists. The engine has no
    Android/iOS platform, so a MOBILE profile is backed by the nearest desktop
    platform the engine DOES spoof — ``macos`` for iOS, ``linux`` for everything
    else — while the UA, window size and mobile extension supply the actual
    mobile signals. A profile is mobile when its OS is a mobile family OR its
    ``device_type`` says so, which is why ``device_type`` cannot be dropped:
    ``windows`` + ``mobile`` is told ``linux``, and a caller that asked
    ``os_type`` alone would answer ``windows`` and be wrong in the direction
    that leaks.

    The value is returned RAW, not folded onto the honoured vocabulary. An
    ``os_type`` the engine does not honour (``win``, ``freebsd``) is passed
    through unchanged, because that IS what the engine is told — and callers
    must be able to see that it is not honoured rather than have it quietly
    normalised into looking like it is. :func:`engine_honours` is the question
    to ask about the result.
    """
    if is_mobile_profile(os_type, device_type):
        return "macos" if os_type == "ios" else "linux"
    return os_type


def engine_honours(engine_platform: str) -> bool:
    """Whether the engine actually spoofs the platform it is being told.

    Answered about the value the engine RECEIVES, never about ``os_type``.
    ``False`` means the engine was handed a platform it does not act on, so
    persona's own layers must author everything themselves — the fail-safe
    direction.
    """
    return str(engine_platform).lower() in ENGINE_HONOURED_PLATFORMS
