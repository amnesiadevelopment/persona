"""What the launched browser is allowed to inherit from the operator's session.

The browser's whole job is to execute untrusted remote code, so its environment
is part of the profile's perimeter — not a detail of however the operator's
shell happened to be set up. This module owns ONE list, shared by both engines,
because two divergent literal tuples in two launchers is the predictable
regression: a name added to the chromium seam and forgotten at the firefox one
is a leak that looks fixed.

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

APPLYING IT. The two engines reach this list by different routes, and the
difference is not cosmetic:

* Chromium builds an ``env`` COPY and hands it to ``subprocess.Popen(env=...)``,
  so ``scrub_operator_identity`` is called on that copy and the parent process
  is untouched by construction.
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


def scrub_operator_identity(env):
    """Remove the operator-identity variables from ``env`` IN PLACE.

    Takes the mapping to clean rather than reading ``os.environ`` itself, so a
    caller holding a copy (the chromium launcher) cannot accidentally mutate the
    parent's environment through it. Returns ``env`` for convenience.
    """
    for var in OPERATOR_IDENTITY_VARS:
        env.pop(var, None)
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

    return scrub_operator_identity(os.environ)
