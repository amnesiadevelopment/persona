"""Whether a launch opens an unauthenticated CDP (remote-debugging) channel.

This answers ONE question — "does launching this profile open a remote-debugging
port?" — for two callers that must not answer it differently: ``process.py``,
which decides whether to append ``--remote-debugging-port=0``, and the profile
card, which tells the operator whether a channel is open on a running session.

It lives in ``src/services/`` rather than in the renderer, following the
precedent ``services/proxy/freshness.py`` set when ``proxy_indicator_state`` was
moved out of ``ui/components/profile_card.py``: ``src/services/`` cannot import
from ``src/ui/`` (the render layer is a leaf), so a predicate the launcher needs
must not be trapped in a UI module. The repo just paid to move one out; this one
starts on the correct side.

RENDER-ONLY, and precisely so — state which part crosses, per the convention
freshness.py established: **none of it crosses**. Nothing here refuses,
throttles or closes anything. What the port PERMITS is decided elsewhere (the
launch lanes' ``ai_control`` guard); this module only decides what the operator
is TOLD. That boundary is the whole point of the slice.

PURE, and that is load-bearing. It reads the record's fields and nothing else —
no file is opened, no socket is created. ``read_cdp_port`` (a file read) and
``cdp_info_for`` (real ``httpx`` network IO) must never appear on a render path:
an indicator that performs IO on every redraw is a different security object
from one whose cadence is attributable to a human opening a window.

WHY THE ANSWER IS CAPTURED AT LAUNCH, NOT RE-DERIVED AT RENDER
--------------------------------------------------------------
Calling this on a stored ``Profile`` answers "would a launch OPEN a channel",
which is NOT the same question as "is a channel open on the session running
right now". ``ProfileManager.set_ai_control`` mutates and saves the record with
no reference to whether the profile is running, so the two answers diverge
mid-session — and the dangerous direction is the falsely reassuring one, where
the record reads False while a real port is still listening. The launcher
therefore evaluates this ONCE, at session registration, and stores the boolean
it got; the card renders that captured fact. See ``BrowserLauncher.
cdp_channel_open``.
"""

from __future__ import annotations

from ...models.profile import Profile


def opens_cdp_channel(profile: Profile) -> bool:
    """True if launching ``profile`` opens a remote-debugging port.

    Two conditions, and BOTH are required:

    * ``ai_control`` is set — this is what makes ``process.py`` append
      ``--remote-debugging-port=0`` and ``--remote-allow-origins=*``.
    * the engine that will ACTUALLY launch is chromium. Firefox opens no CDP
      port at any ``ai_control`` value (``invisible_launch.py`` never reads the
      field), so reporting a channel on a Firefox session would be a NEW false
      claim — the exact inverse of the defect this predicate exists to fix.

    The engine test goes through ``effective_engine``, never ``profile.engine``.
    A mobile profile that stores ``engine="firefox"`` is reconciled to chromium
    by the coherence rules and therefore DOES launch with a port and DOES open a
    channel; trusting the stored field would miss it and report "closed" over a
    listening port. This is the same stored-vs-effective correction the launch
    lanes already had to make.

    ``effective_engine`` is imported function-locally, the convention every
    other consumer in this repo follows: ``services.profile`` imports
    ``browser.device_presets``, and reaching it runs ``browser/__init__`` →
    ``launcher`` → ``process``, so a module-level import closes an import cycle.
    """
    from .process import effective_engine

    if not getattr(profile, "ai_control", False):
        return False
    return effective_engine(profile) == "chromium"
