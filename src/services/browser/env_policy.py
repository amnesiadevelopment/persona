"""What the launched browser is allowed to inherit from the operator's session.

The browser's whole job is to execute untrusted remote code, so its environment
is part of the profile's perimeter — not a detail of however the operator's
shell happened to be set up. This module owns the lists, shared by both engines,
because two divergent literal tuples in two launchers is the predictable
regression: a name added to the chromium seam and forgotten at the firefox one
is a leak that looks fixed.

There are TWO lists here, and they are separate because they are separate
categories, not because one is a continuation of the other:

* ``OPERATOR_IDENTITY_VARS`` — one live capability handle plus four names for
  the operator and their machine. What the child must not LEARN.
* ``STALE_RUNTIME_PATH_VARS`` — paths persona's own runtime exported into
  ``os.environ`` that no longer resolve for the child. What the child must not
  FOLLOW.

Keeping them apart is deliberate: ``FONTCONFIG_FILE`` is not operator identity,
and folding it into that tuple would broaden a named concept without saying so.
Both lists are applied at both seams.

The load-bearing entry is ``SSH_AUTH_SOCK``. It is not a passive string but a
live capability handle: any process holding it can ask the operator's SSH agent
to authenticate AS THE OPERATOR to every host that trusts their key, without
ever touching the key material. The other four are the weaker, passive half —
they name the operator and their machine.

Scrubbing ``SSH_AUTH_SOCK`` breaks no persona feature: SSH support is paramiko
with explicit key files (``src/services/ssh/client.py`` calls
``from_private_key_file(target.key_path, ...)``), which never consults the agent
socket, and the tree contains no ssh-agent usage at all.

Deliberately NOT here, each for a stated reason:

* ``XAUTHORITY`` — on X11 this is how a client authenticates to the display
  server; dropping it plausibly stops the browser opening at all. Not worth the
  launch risk in a slice about a capability handle.
* ``TZ`` — chromium is passed ``--timezone=`` explicitly and the firefox engine
  applies its own timezone. Which of the two wins is not established here, so
  it is neither claimed as a leak nor touched.
* ``LANG`` / ``LC_ALL`` — already normalized to ``C.UTF-8`` in ``src/main.py``;
  re-deriving that here would duplicate an existing authority.
* ``TMPDIR`` — real ground, but a path-pinning problem (where files land)
  rather than an inherited-capability one.

APPLYING IT. Both engines go through ONE entry point,
``scrub_inherited_environment``, which applies every list above. Neither seam
enumerates the lists itself: doing so is what left ``FONTCONFIG_*`` scrubbed
for chromium and inherited by the firefox child, so adding a list here is now
enough to close both seams. What differs is only WHAT each seam hands it, and
that difference is not cosmetic:

* Chromium builds an ``env`` COPY and hands it to ``subprocess.Popen(env=...)``,
  so the scrub runs on that copy and the parent process is untouched by
  construction.
* Firefox on Linux FORKS, and a fork has separate memory, so its child scrubs
  its OWN ``os.environ`` — see ``scrub_current_process_environ``. On Windows and
  macOS that same child runs as a THREAD of the manager process, where there is
  no separate environment to scrub and mutating ``os.environ`` would change
  persona itself and every other concurrently-open profile. That path is
  therefore left alone: an honest, recorded absence rather than a guarantee
  that silently does not hold.
"""

# Identity-bearing variables the browser child has no use for. SSH_AUTH_SOCK
# first because it is the one that is a capability rather than a label.
OPERATOR_IDENTITY_VARS = (
    "SSH_AUTH_SOCK",
    "USER",
    "LOGNAME",
    "HOSTNAME",
    "MAIL",
)

# Paths persona's own runtime exported that do not resolve for the child. This
# rationale moved here verbatim from the chromium launcher, where it sat beside
# an inline tuple that the firefox seam never reached:
#
#   Fonts come from the system fontconfig. A FONTCONFIG_FILE override floods
#   live sessions with "Cannot load default config file" errors from chromium
#   child processes and makes pages render with the wrong fonts; the engine
#   spoofs the JS-visible font list itself, so an override buys no anti-detect
#   value. The app's own runtime can export FONTCONFIG_* into os.environ (an
#   AppImage bundle points them into its mount, which is gone for the
#   relaunched process after a self-update), so scrub them rather than trust
#   the inherited environment.
#
# Nothing in that argument is engine-specific — fonts come from the system
# fontconfig on both — so it belongs on the shared list rather than at one seam.
# This is NOT a fingerprint concern: the harm is error floods, wrong fonts, and
# a dead mount path reaching the child, not anything a page can observe.
STALE_RUNTIME_PATH_VARS = (
    "FONTCONFIG_FILE",
    "FONTCONFIG_PATH",
    "FONTCONFIG_SYSROOT",
)


def scrub_operator_identity(env):
    """Remove the operator-identity variables from ``env`` IN PLACE.

    Takes the mapping to clean rather than reading ``os.environ`` itself, so a
    caller holding a copy (the chromium launcher) cannot accidentally mutate the
    parent's environment through it. Returns ``env`` for convenience.
    """
    for var in OPERATOR_IDENTITY_VARS:
        env.pop(var, None)
    return env


def scrub_stale_runtime_paths(env):
    """Remove the stale runtime-path variables from ``env`` IN PLACE.

    Separate from ``scrub_operator_identity`` because it is a separate
    category, not a longer version of the same one — see the two lists above.
    Returns ``env`` for convenience.
    """
    for var in STALE_RUNTIME_PATH_VARS:
        env.pop(var, None)
    return env


def scrub_inherited_environment(env):
    """THE entry point both engine seams call. Applies every list, IN PLACE.

    Single entry point on purpose: it is what makes "one list, both engines"
    hold for a list added LATER. When each seam enumerates the scrubs itself,
    a new category has to be remembered in two launchers — which is precisely
    how FONTCONFIG_* ended up scrubbed for chromium and inherited by the
    forked firefox child. Adding a list above is now enough to close both.
    """
    scrub_operator_identity(env)
    scrub_stale_runtime_paths(env)
    return env


def scrub_current_process_environ():
    """Scrub THIS process's own ``os.environ``.

    Only ever safe in a FORKED child, which has its own memory: calling it on a
    thread of the manager process would mutate persona and every other profile
    open at the time. The one caller guards on exactly that (see
    ``invisible_launch._child``); this function does not guess, it just does
    what it is told.
    """
    import os

    return scrub_inherited_environment(os.environ)
