"""What the launched browser is allowed to inherit from the operator's session.

Three properties live here, all of them "what the child inherits from whoever
started persona": its ENVIRONMENT (the two scrub lists below), its WORKING
DIRECTORY (``browser_child_cwd``) and its SCRATCH DIRECTORY
(``browser_child_tmpdir``). They are separate concerns kept in one module for
one reason — they have the same failure mode, which is that a value decided at
one engine seam and forgotten at the other looks fixed and is not.

THE ENVIRONMENT
---------------

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
  applies its own timezone. Which of the two wins WAS not established here;
  it now is, measured on the real engine from a running page (PS-132,
  chromium 148.0.7778.215): launched with ``--timezone=Europe/Warsaw`` and
  ``TZ=America/Chicago`` in its environment, the page reported
  ``Europe/Warsaw`` on all three surfaces that carry the zone — the IANA name
  from ``Intl.DateTimeFormat().resolvedOptions()``, the offset ``Date``
  reports, and formatting derived from it. **The flag wins; an inherited
  ``TZ`` does not reach the page.** So this is still not a leak and is still
  not touched — but now because it was checked, rather than because the
  question was left open.
* ``LANG`` / ``LC_ALL`` — already normalized to ``C.UTF-8`` in ``src/main.py``;
  re-deriving that here would duplicate an existing authority.
* ``TMPDIR`` — deferred here as "real ground, but a path-pinning problem
  (where files land) rather than an inherited-capability one". That reading
  still stands and is why it is NOT on either scrub list: pinning it is the
  third property below, not a fourth name to delete. Scrubbing it would be
  actively wrong — the child would fall back to ``/tmp`` and land right back
  outside the perimeter.

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

THE WORKING DIRECTORY
---------------------

Same shape, one axis over. Before this was centralized the chromium launcher
pinned ``cwd=os.path.expanduser("~")`` inline and the firefox seam set nothing
at all, so its child inherited whatever directory persona itself was sitting
in — and ``grep -rnE 'cwd=|chdir' src/services/browser/`` returned exactly one
hit in the whole package. That is not an edge case reachable only after a
fault: ``src/main.py:_ensure_valid_cwd`` RETURNS EARLY whenever ``getcwd()``
works, so on an ordinary launch persona's cwd is simply the directory the
operator started it from, and the two engines part company there. (After a
self-update re-exec leaves persona in an unmounted AppImage directory, that
same guard can walk as far as ``/tmp`` or ``/``, and the firefox child would
land there.)

``browser_child_cwd`` is now the one authority. Neither seam names a path.

WHAT THIS IS NOT. This is not a page-observable leak and is not claimed as
one: a working directory is process state, no JS surface exposes it, and no
fingerprint surface is touched. The value is what it closes structurally — the
next person to change the browser child's working directory changes it in ONE
place and cannot fix one engine while forgetting the other.

APPLYING IT, and the platform gap that is deliberate:

* Chromium passes the value to ``subprocess.Popen(cwd=...)``, which sets the
  directory in the CHILD only. Safe on every platform by construction.
* Firefox on Linux FORKS, and a fork has separate memory, so its child may
  ``os.chdir`` its own process — see ``chdir_current_process``.
* Firefox on Windows/macOS runs that same child as a THREAD of the manager
  process, where the working directory is process-global state exactly like
  ``os.environ``. An ``os.chdir`` there would move persona's OWN cwd and every
  concurrently-open profile's — strictly worse than the divergence being
  fixed. That path is therefore left alone, guarded identically to the
  environment scrub: an honest, recorded absence rather than a guarantee that
  silently does not hold.

THE SCRATCH DIRECTORY
---------------------

Same shape again, and the one property here that is POINTED rather than
removed. persona already scopes its OWN scratch into the profile — the
``dir=`` argument to ``mkstemp`` in ``invisible_launch``, so a crash cannot
strand a ``persona-*`` artifact in the host's shared temp dir — while the
child that actually executes untrusted remote code got no such treatment and
wrote to ``/tmp``. Measured before this was fixed: a SIGKILLed session left
``org.chromium.Chromium.*`` directories, product-identifying by name, plus the
engine's ~714MB AppImage extraction, all of it outliving the profile.

``browser_child_tmpdir`` is now the one authority, and it points every name in
``TEMP_DIR_VARS`` — not ``TMPDIR`` alone, because Windows resolves ``TMP`` /
``TEMP`` and Python's own ``tempfile`` consults all three. Setting one is how a
value looks pinned on the developer's platform and is not on the operator's.

NOT A SCRUB, which is why ``TMPDIR`` is deliberately absent from both lists
above. Deleting it would send the child to ``/tmp`` — straight back outside the
perimeter. The docstring entry there says so at the point a reader would
otherwise "finish the job" by adding it.

BOUNDED TO ONE SESSION, which is the half that makes this a perimeter
guarantee rather than a relocation. Pinning scratch inside the profile puts it
where ``delete_profile`` (which renames the data dir into the trash), the trash
and ``wipe_all_profiles`` can reach it; it does not stop it accumulating. The
engine's extraction survives a clean exit, so without a sweep what used to be
ONE shared copy in ``/tmp`` becomes one PERMANENT copy per profile, and a
deleted profile parks it in the trash for the full 30-day retention
(``services/trash/store.py``). ``prepare_child_tmpdir`` therefore sweeps before
it creates, binding scratch to a single session exactly as
``cert.terminator.sweep_key_material`` binds decrypted key material to its
directory — and for the same stated reason: cleanup that runs at EXIT never
runs when the session dies without one, which is precisely the SIGKILL case
that motivated this.

THE COST, stated rather than buried: the AppImage extraction is content-
addressed and reused, so sweeping re-extracts it on every launch. Measured on
the reference engine, cold vs warm ``TMPDIR``: ~3.1s against ~0.6s, so roughly
2.5s of added launch latency, every launch, bought for a bounded perimeter.
That is a real regression in startup time and whoever revisits this should
weigh it knowing the number. It sits against a 90s CDP-ready timeout that
already anticipates cold-extraction slowness. The extraction is a pure cache of
the engine binary — byte-identical across profiles, carrying nothing
identity-bearing — so nothing but time is lost by discarding it.

WHAT THIS IS NOT. Not a page-observable leak and not claimed as one: no JS
surface reads ``TMPDIR`` and no fingerprint surface is touched. This is the
local-code / forensic-residue threat model — invariant #0, a profile must not
strand data outside what ``delete_profile``, the trash and
``wipe_all_profiles`` can reach.

APPLYING IT, and the same platform gap, one notch sharper:

* Chromium gets it in the ``env`` COPY handed to ``subprocess.Popen(env=...)``,
  so it is pinned on all three platforms by construction and the parent's own
  environment is untouched.
* Firefox on Linux FORKS, so its child pins its own ``os.environ`` — see
  ``pin_current_process_tmpdir``.
* Firefox on Windows/macOS runs that child as a THREAD. Pinning there would
  move persona's own temp dir and EVERY concurrently-open profile's, pointing
  them all at ONE profile's scratch directory — worse than leaving them in
  ``/tmp``, because it would cross-contaminate profiles rather than merely
  miss the perimeter. Left alone, guarded identically: a recorded absence.
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


def browser_child_cwd():
    """THE working directory both engine seams give the browser child.

    THE VALUE IS ``os.path.expanduser("~")``, AND IT IS DELIBERATELY UNCHANGED.
    This function exists to centralize the value, not to revisit it: before it,
    the chromium launcher pinned exactly this expression inline with no comment
    at all, so a reader could not tell whether it was deliberate isolation or
    an incidental default, and the firefox seam pinned nothing.

    What can honestly be said for it is this and no more: it is the value both
    seams now agree on. CHANGING it is a product decision held elsewhere — the
    owner ruled on the neighbouring question (2026-08-21, PS-34 cancelled:
    downloaded files must land on the host, so a directory inside the profile's
    own data dir was rejected), which is why picking a directory is not a
    tidy-up a reader of this module should make on their own. If a different
    directory is right, that is its own ticket with that directive in front of
    it. No rationale for ``~`` beyond the above is asserted here, because none
    is established.

    Computed per call rather than frozen into a module constant: ``HOME`` can
    legitimately differ by the time a browser is launched (it is read at import
    time exactly once otherwise), and this mirrors what the inline expression
    at the chromium seam already did.
    """
    import os

    return os.path.expanduser("~")


# THE SCRATCH DIRECTORY — rationale in the module docstring's section of that
# name (why it is pointed rather than scrubbed, why it is swept, and the
# latency that buys).
#
# All three names, not just TMPDIR: TMPDIR is the POSIX one, TMP/TEMP are what
# Windows resolves, and Python's own tempfile checks all three in that order.
# Setting one and leaving the others is how a value looks pinned on the
# developer's platform and is not on the operator's.
TEMP_DIR_VARS = ("TMPDIR", "TMP", "TEMP")

# Dotted so it sorts beside the other persona-owned subdirectories the profile
# already carries (.invisible-profile, .persona-mtls, .persona-*-ext).
CHILD_TMPDIR_NAME = ".persona-tmp"


def browser_child_tmpdir(profile_dir):
    """THE scratch directory both engine seams give the browser child.

    INSIDE THE PROFILE, which is the whole point: everything under the
    profile's data dir is reached by ``delete_profile`` (which RENAMES the
    data dir into the trash), by the trash, and by ``wipe_all_profiles``
    (which ``rmtree``s it). Scratch left in the host's shared temp dir is
    reached by none of the three and outlives the profile it belonged to.

    This is the same argument persona already applies to its OWN scratch —
    ``invisible_launch`` passes ``dir=`` to ``mkstemp`` so a crash cannot
    strand a ``persona-*`` artifact in the host temp dir. The child that
    executes untrusted remote code was the one seam not getting it.

    Computed per call rather than frozen, mirroring ``browser_child_cwd``:
    the profile directory is an argument, so nothing here is cached across
    profiles.
    """
    import os

    return os.path.join(profile_dir, CHILD_TMPDIR_NAME)


def prepare_child_tmpdir(profile_dir):
    """Sweep the scratch directory, recreate it empty, and return it.

    Created BEFORE the launch on purpose: a TMPDIR that does not exist or is
    not writable can stop the engine starting, which would turn a residue fix
    into a launch bug.

    SWEPT FIRST, which is the half that makes the pin a perimeter guarantee
    rather than a relocation. Pinning scratch inside the profile only decides
    WHERE it lands; on its own it lets scratch accumulate forever, so what used
    to be one shared copy in ``/tmp`` (which the OS clears on reboot) becomes
    one PERMANENT copy per profile that nothing in the tree ever reclaims — and
    a deleted profile parks it in the trash for the full 30-day retention.
    Sweeping binds it to a single session: at most one session's scratch is
    ever on disk.

    Deliberately at LAUNCH rather than at exit, copying
    ``cert.terminator.sweep_key_material`` — which binds decrypted key material
    to its directory for exactly this reason, that cleanup hung off session
    teardown "never runs when a session ends without it". The residue this
    ticket measured came from a SIGKILLed session, so an exit-time sweep would
    miss the very case that motivated it.

    THE COST, stated because it is real: the engine's AppImage extraction is
    content-addressed and reused, so discarding it re-extracts ~714MB on the
    next launch. Measured cold vs warm on the reference engine: ~3.1s against
    ~0.6s, i.e. roughly 2.5s of added launch latency every time, against a 90s
    CDP-ready timeout that already anticipates cold-extraction slowness. Only
    time is lost — the extraction is a pure cache of the engine binary,
    byte-identical across profiles and carrying nothing identity-bearing.

    ``ignore_errors`` on the sweep, not on the create: a scratch directory that
    cannot be cleared is a stale-cache problem and must not fail the launch,
    whereas one that cannot be CREATED means the pin did not happen, and the
    caller refuses the launch over it rather than hand the engine ``/tmp``.
    """
    import os
    import shutil

    target = browser_child_tmpdir(profile_dir)
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(target, exist_ok=True)
    return target


def pin_child_tmpdir(env, profile_dir):
    """THE entry point both engine seams call. Pins every name in ``env``.

    Takes the mapping to write rather than reaching for ``os.environ``
    itself, exactly like ``scrub_operator_identity``: the chromium caller
    holds a COPY and must not be able to mutate the parent's environment
    through it. Returns the directory, for the caller to log.

    Single entry point for the same reason the scrub has one — see
    ``scrub_inherited_environment``. A seam that wrote ``env["TMPDIR"] = ...``
    inline would pin one name on one engine, and adding TMP/TEMP later would
    have to be remembered twice.
    """
    target = prepare_child_tmpdir(profile_dir)
    for var in TEMP_DIR_VARS:
        env[var] = target
    return target


def pin_current_process_tmpdir(profile_dir):
    """Pin THIS process's own ``os.environ`` at the child's scratch directory.

    Only ever safe in a FORKED child, which has its own memory: calling it on
    a thread of the manager process would move persona's own temp dir and
    every concurrently-open profile's — the same hazard, and the same
    division of labour, as ``scrub_current_process_environ``. The one caller
    guards on exactly that (see ``invisible_launch._child``); this function
    does not guess, it just does what it is told.
    """
    import os

    return pin_child_tmpdir(os.environ, profile_dir)


def chdir_current_process(target=None):
    """Move THIS process to the browser child's working directory.

    Only ever safe in a FORKED child, which has its own process-global state:
    calling it on a thread of the manager process would move persona's own
    working directory and every other profile's open at the time. The one
    caller guards on exactly that (see ``invisible_launch._child``); this
    function does not guess, it just does what it is told — the same division
    of labour as ``scrub_current_process_environ``.

    RAISES ``OSError`` if the directory is unreachable — an unmounted home, the
    fault case ``src/main.py:_ensure_valid_cwd`` exists for. It is deliberately
    NOT caught here and NOT given a fallback chain: falling back would mean
    choosing a second directory, which is exactly the product decision this
    module declines to take (see ``browser_child_cwd``), and silently
    continuing would restore the divergence — the firefox child would once
    again be standing wherever persona happened to be. The chromium seam
    behaves the same way by construction: ``Popen(cwd=...)`` raises rather than
    launching somewhere else. What the caller owes is AUDIBILITY, not a
    fallback; ``invisible_launch._child`` calls this only once its pipe is open
    so the failure is reported rather than read by the parent as a bare EOF.

    ``target`` lets the caller pass a value it already read from
    ``browser_child_cwd``. The firefox seam does exactly that: it must read the
    value BEFORE it scrubs its own environment (``expanduser`` reads ``HOME``)
    but apply it AFTER its pipe exists. Omitted, the value is read now.

    Returns the directory it moved to, for the caller to log.
    """
    import os

    if target is None:
        target = browser_child_cwd()
    os.chdir(target)
    return target
