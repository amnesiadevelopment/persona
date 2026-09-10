"""What a launch writes OUTSIDE the profile's own directory — enumerated, once.

PS-8's definition-of-done item #3 asks for a noun this tree did not have:

    *"Every artifact a launch creates outside the profile's own directory is
    enumerated, and each is either removed or recorded as a decided exception
    with its reason."*

WHY A LIST, AND WHY IT IS NOT THE DELIVERABLE
----------------------------------------------
Six commits over this project's life fixed one class of defect — something a
launch writes where a profile delete, a rename or a panic wipe cannot reach it:

    0978603  [PS-234]  a deleted SSH host leaves its known_hosts pin
    1932a6d  [PS-283]  a refused launch writes a host artifact before refusing
    23894fc  [PS-175]  session-restore prefs lost across an engine build bump
    5540e8c  [PS-129]  the engine child's scratch dir is unpinned
    dc30331  [PS-57]   the mTLS CA import's scratch file lands in the host temp
    a211839  [PS-16]   the desktop entry survives a rename

⛔ THE RATE IS NOT THE ARGUMENT. Six over a project's life is low, and "keep
fixing them as they appear" is a reasonable answer to a rate. What is NOT
reasonable is the DETECTION METHOD: every one of the six was found by a person
or an agent READING CODE. None was found by anything that runs. The seventh
would have joined them the same way, or not at all.

So a hand-written list is worth nothing on its own — it is exactly the "check
that cannot fail" PS-8's own caution names. This module's list is the INPUT to
:data:`~.behaviour_checks.CHECKS`' ``launch-perimeter-inventory``, which walks
the launch surface and goes red when the tree and this list disagree. Delete an
entry and the check names the site it can no longer account for; add a write
site and the check names the file.

THE PERIMETER IS TWO CIRCLES, NOT ONE, AND THE TICKET'S WORDING MEANS THE OUTER
-------------------------------------------------------------------------------
"Outside the profile's own directory" admits two readings, and MEASUREMENT
(see the module-level note in ``tests/test_ps355_launch_perimeter.py``) shows
they hold different artifacts:

* **Outside PERSONA_HOME entirely** — on the host, where nothing persona owns
  can reach it. Exactly ONE artifact is here: the Linux ``.desktop`` entry.
* **Inside PERSONA_HOME, outside the profile's data dir** — reachable by the
  panic wipe but NOT by ``delete_profile``'s rmtree of one profile's directory.
  Measured: ``running_sessions.json``, ``bookmarks.json``, ``logs/``.

Both are enumerated, and each entry says which circle it is in
(:data:`SCOPE_HOST` / :data:`SCOPE_HOME`), because the reach that clears them is
different and a list that conflated them would report a wipe-reachable file and
a host file as the same kind of thing.

THE PLATFORM COLUMN IS LOAD-BEARING
------------------------------------
The desktop entry is written only under
``platform.supports_linux_desktop_integration()``, which returns ``IS_LINUX``.
An inventory with no platform column, checked by a gate running on one OS,
would either report a phantom missing artifact on Windows/macOS or pass there
in silence. So every entry names the platforms it is EXPECTED on, and the check
reads that column rather than assuming the runner's own OS.

WHAT THIS MODULE IS NOT
------------------------
⛔ It fixes nothing and closes no leak. Every artifact below is reached by some
path today; none is claimed to be shipping as residue. This is a COVERAGE gap,
not an Invariant #0 defect, and the perimeter vocabulary must not be read as a
claim of one.

⛔ It does not close the three platform gaps recorded below. They are correct as
they stand — see each entry's ``reason`` — and this module RECORDS them, which
is what DoD#3 asks for in its own words.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- the two circles --------------------------------------------------------

#: Outside ``PERSONA_HOME`` altogether — on the host, where neither
#: ``delete_profile`` nor ``wipe_all_profiles`` reaches by construction. An
#: artifact here needs its own named removal path or it is there forever.
SCOPE_HOST = "host"

#: Inside ``PERSONA_HOME`` but outside the profile's own data dir. The panic
#: wipe reaches this circle; ``delete_profile``, which rmtree's ONE profile's
#: directory, does not — so an artifact here survives a single delete unless
#: something removes it by name.
SCOPE_HOME = "home"

# --- how an entry is accounted for ------------------------------------------

#: Something in the tree removes this artifact. ``reached_by`` names it.
DISPOSITION_REMOVED = "removed"

#: Nothing removes it, and that is a DECIDED position with a reason on record —
#: DoD#3's "recorded as a decided exception with its reason". ``reason`` carries
#: the argument, taken from the code that made the decision rather than invented
#: here.
DISPOSITION_EXCEPTION = "exception"

#: Scoped INSIDE the profile's directory by construction, so profile deletion
#: reaches it and it needs no removal path of its own. Present in the inventory
#: because the SITE is one a reader would otherwise have to re-derive, and
#: because three of the six historical defects were precisely an artifact that
#: was NOT scoped this way. An entry here is a claim about the site, not about
#: an absence.
DISPOSITION_SCOPED = "scoped"

#: The site names a path outside a profile directory but CREATES NO ARTIFACT
#: there — it reads an existing file, resolves the perimeter's own root, or
#: hands a directory to a child that writes nothing into it.
#:
#: ⚠️ THE MOST DANGEROUS LABEL IN THIS FILE, because it is the one that says
#: "nothing to account for" — the exact sentence the six historical defects
#: would each have been given by a reader who did not look hard enough. Every
#: entry carrying it therefore states MEASURED evidence for the negative, not
#: an argument that a write looked unlikely: the launch was run and the
#: directory was watched. :func:`validate_inventory` refuses one with no
#: reason, for the same purpose the exception arm does.
DISPOSITION_NO_ARTIFACT = "no-artifact"

#: Every platform, i.e. the entry is expected wherever persona runs.
ALL_PLATFORMS = ("linux", "windows", "macos")

#: Linux only. The three fork-path gaps and the desktop entry share this, for
#: two unrelated reasons — ``needs_fork_launch()`` and
#: ``supports_linux_desktop_integration()`` both return ``IS_LINUX``.
LINUX_ONLY = ("linux",)


@dataclass(frozen=True)
class Artifact:
    """One thing a launch writes outside the profile's own directory.

    ``site`` is a ``path:symbol`` coordinate rather than a line number on
    purpose: a line number is stale the moment anything above it moves, and the
    gate resolves the symbol rather than the line. ``reached_by`` and ``reason``
    are mutually exclusive by disposition — a removed artifact names its remover
    and an exception names its argument, and an entry carrying neither is
    rejected by :func:`validate_inventory`.
    """

    #: What it is, in an operator's words.
    artifact: str
    #: ``module/path.py:symbol`` — where it is written.
    site: str
    #: :data:`SCOPE_HOST` or :data:`SCOPE_HOME`.
    scope: str
    #: The platforms this artifact is EXPECTED on. Checked rather than assumed.
    platforms: tuple[str, ...]
    #: One of the three DISPOSITION_* constants.
    disposition: str
    #: For :data:`DISPOSITION_REMOVED`: what removes it.
    reached_by: str = ""
    #: For :data:`DISPOSITION_REMOVED`: the REMOVAL SITES, as
    #: ``path.py:caller->callee`` triples, that the gate verifies still exist.
    #:
    #: ⭐ THE HALF THAT CATCHES PS-16, AND IT WAS ADDED BECAUSE THE GATE FAILED
    #: ITS OWN FALSIFICATION WITHOUT IT. Three of the six historical defects —
    #: PS-16 above all — were not a NEW write anywhere; they were an EXISTING
    #: artifact that one reach path forgot to remove. Reverting PS-16 (dropping
    #: ``self._remove_window_entry(original_name)`` from ``update_profile``)
    #: adds no write site and removes none, so a gate watching only writes goes
    #: happily green over the exact defect it was built for. Measured: it did.
    #:
    #: So a ``removed`` entry does not merely ASSERT that something reaches it —
    #: it names the call sites, and the gate goes red when one disappears. That
    #: turns ``reached_by`` from prose into a claim the tree can contradict.
    #:
    #: ⛔ THIS PROVES THE CALL EXISTS, NOT THAT IT WORKS. The gate reads a call
    #: graph, so a reach path that is present but broken (wrong argument, wrong
    #: order, swallowed exception) still satisfies it. That bound is stated in
    #: ``UNCOVERED_SURFACES`` rather than glossed: what it catches is the reach
    #: path that was DELETED or never joined, which is the shape PS-16 had.
    removal_sites: tuple[str, ...] = ()
    #: For :data:`DISPOSITION_EXCEPTION`: why nothing does, on record.
    reason: str = ""
    #: Whether the STATIC SCANNER is expected to find this site.
    #:
    #: ⭐ THE FIELD THAT KEEPS THE GATE HONEST, and it exists because the two
    #: halves of this inventory are known in two different ways. A ``True``
    #: entry is a claim the scanner can CHECK — its site must be found, and a
    #: found site with no entry is a finding. A ``False`` entry is a decision
    #: recorded from prose in the tree (the three platform gaps) or a fact about
    #: an argument the scanner deliberately treats as correct (a ``dir=``-scoped
    #: tempfile is not a write site at all, so there is nothing for it to
    #: report).
    #:
    #: ⛔ ``False`` IS NOT A WAY TO SILENCE A SITE. It says "no static site
    #: corresponds to this entry", and :func:`validate_inventory` refuses one
    #: without a reason. The gate cross-checks it in BOTH directions: a
    #: ``detected=True`` entry whose site has vanished is a finding just as
    #: loudly as an unaccounted site, so an entry cannot be kept alive by
    #: flipping this flag.
    detected: bool = True


#: EVERY ARTIFACT A LAUNCH CREATES OUTSIDE THE PROFILE'S OWN DIRECTORY.
#:
#: ⭐ MEASURED AGAINST A REAL LAUNCH, NOT ASSEMBLED FROM GREP. The proposal's
#: table was code-shape only — its own honest bound said so — and a real
#: firefox launch under a scratch ``PERSONA_HOME`` (xvfb, personium firefox-20)
#: corrected it in one direction and confirmed it in the other:
#:
#:   * OUTSIDE PERSONA_HOME the launch created exactly ONE path, the desktop
#:     entry, and ``delete_profile`` removed it. Nothing else on the host moved.
#:   * INSIDE PERSONA_HOME, outside the profile dir, it created
#:     ``running_sessions.json`` and — NOT ON THE PROPOSAL'S TABLE —
#:     ``bookmarks.json``, and it appended profile names to today's log file.
#:
#: ``bookmarks.json`` is the correction. It is a first-class store rather than
#: residue (see its entry), but a list that omitted it would have been wrong
#: about what a launch writes on its very first run, which is precisely the
#: silence this inventory exists to end.
#:
#: ⛔ APPEND HERE WHEN A LAUNCH LEARNS TO WRITE SOMETHING NEW. That is the
#: point: the gate below reads this tuple, so an unlisted write site turns it
#: red and names the file rather than joining the six above unnoticed.
PERIMETER_ARTIFACTS: tuple[Artifact, ...] = (
    Artifact(
        artifact=(
            "the host .desktop entry (~/.local/share/applications/"
            "persona-<name>-<crc>.desktop), which carries the profile name in "
            "cleartext in both its filename and its Name= field"
        ),
        site="src/services/browser/window_entry.py:_entry_dir",
        scope=SCOPE_HOST,
        # ⭐ THE ONE ENTRY WHOSE PLATFORM COLUMN CHANGES THE GATE'S ANSWER.
        # Both call sites (process.py, the firefox and chromium seams) are
        # wrapped in `if _platform.supports_linux_desktop_integration():`,
        # which returns IS_LINUX. A gate that assumed this everywhere would
        # report a phantom missing artifact on Windows and macOS.
        platforms=LINUX_ONLY,
        disposition=DISPOSITION_REMOVED,
        reached_by=(
            "window_entry.remove_window_entry, called from "
            "ProfileManager._remove_window_entry on delete_profile, on "
            "wipe_all_profiles, and — the PS-16 fix — on the OLD name after a "
            "rename, which is the one caller a reader would not predict"
        ),
        # ⭐ ALL THREE, AND THE RENAME ONE IS THE POINT. `update_profile` is
        # PS-16 itself: dropping that single call strands the OLD name's entry
        # on the host with nothing able to reach it, while adding and removing
        # no write site anywhere. The gate is red the moment this line has no
        # counterpart in the tree.
        removal_sites=(
            "src/services/profile/manager.py:update_profile->_remove_window_entry",
            "src/services/profile/manager.py:delete_profile->_remove_window_entry",
            "src/services/profile/manager.py:wipe_all_profiles->_remove_window_entry",
            "src/services/profile/manager.py:_remove_window_entry->remove_window_entry",
        ),
    ),
    Artifact(
        artifact=(
            "running_sessions.json — the durable record of which profiles had "
            "a browser running, carrying profile names, pids and pgids"
        ),
        site="src/services/browser/session_registry.py:default_registry",
        scope=SCOPE_HOME,
        platforms=ALL_PLATFORMS,
        disposition=DISPOSITION_REMOVED,
        reached_by=(
            "SessionRegistry.forget on a clean session end, and "
            "launcher.forget_identity for the name — wired in production at "
            "ui/app.py via pm.set_forget_identity_hook(L.forget_identity), "
            "which is what makes a delete/wipe/rename reach it (PS-278/PS-138)"
        ),
        # The identity hook's three doors plus the registry write itself. The
        # proposer's own refuted candidate for this ticket was "the sessions
        # file survives a wipe" — refuted precisely because these calls exist,
        # so pinning them is pinning the thing that made the answer no.
        removal_sites=(
            "src/services/profile/manager.py:update_profile->_forget_identity",
            "src/services/profile/manager.py:delete_profile->_forget_identity",
            "src/services/profile/manager.py:wipe_all_profiles->_forget_identity",
            "src/services/browser/launcher.py:_forget_session_facts->forget",
        ),
    ),
    Artifact(
        artifact=(
            "bookmarks.json — created on the first launch that resolves a "
            "bookmark selection, because BookmarkStore seeds its defaults when "
            "the file does not exist"
        ),
        site="src/services/bookmark/store.py:__init__",
        scope=SCOPE_HOME,
        platforms=ALL_PLATFORMS,
        disposition=DISPOSITION_EXCEPTION,
        # ⚠️ THE ENTRY THE PROPOSAL'S TABLE DID NOT HAVE, found by measuring a
        # real launch rather than by reading code. Recorded as an EXCEPTION
        # rather than as residue, and the distinction is the whole reason it is
        # written out: this is a first-class STORE with its own operator-facing
        # UI, on a par with profiles.json, not something a launch leaves
        # behind. It is deliberately not per-profile.
        reason=(
            "a shared, operator-owned STORE rather than a launch artifact: it "
            "holds the bookmark library every profile draws from, it has its "
            "own UI, and it is seeded with defaults that carry NO profile name "
            "(verified: the file's bytes after a launch name no profile). "
            "Removing it when a profile is deleted would destroy a library "
            "belonging to every other profile. It is inside PERSONA_HOME, so "
            "the panic wipe's own reach covers the operator's 'destroy "
            "everything' gesture; a single delete leaving it is correct."
        ),
    ),
    Artifact(
        artifact=(
            "profile names in the day's log file (logs/persona_YYYYMMDD.log) — "
            "written in cleartext as the launch narrates itself"
        ),
        site="src/core/logging.py:setup_logging",
        scope=SCOPE_HOME,
        platforms=ALL_PLATFORMS,
        disposition=DISPOSITION_REMOVED,
        # ⚠️ NOT SCANNER-VISIBLE, and the reason is worth stating rather than
        # flagging. `setup_logging` takes the directory as a PARAMETER
        # (`log_dir: str = "logs"`); the LOG_DIR constant is bound by
        # `core/container.py`, which a launch does not enter — the handler is
        # already installed by the time one starts. So the write is real and
        # measured (a launch appends profile names to the day file), while the
        # static site the scanner looks for genuinely does not exist on the
        # launch surface. Recorded as measured rather than inferred.
        detected=False,
        reached_by=(
            "ProfileManager._clear_logs_for_wipe, called LAST from "
            "wipe_all_profiles — after every step that names a profile has "
            "finished logging, or the lines below would re-write what was "
            "cleared. Reached by the panic wipe ONLY; a single delete_profile "
            "deliberately leaves the operator's log intact."
        ),
        removal_sites=(
            "src/services/profile/manager.py:wipe_all_profiles->_clear_logs_for_wipe",
        ),
        reason=(
            "not statically visible on the launch surface: setup_logging "
            "receives the directory as an argument and the LOG_DIR constant is "
            "bound in core/container.py, which no launch enters. Measured "
            "instead — a real firefox launch appended its profile's name to "
            "logs/persona_<date>.log."
        ),
    ),
    Artifact(
        artifact=(
            "the mTLS CA import's NSS password scratch file "
            "(persona-mtls-nsspw-*)"
        ),
        site="src/services/browser/invisible_launch.py:_import_mtls_ca",
        scope=SCOPE_HOME,
        platforms=ALL_PLATFORMS,
        disposition=DISPOSITION_SCOPED,
        # ⭐ THE SCANNER FINDS NOTHING HERE **BECAUSE THE FIX IS IN PLACE**, and
        # that is the strongest reading this flag has. A `dir=`-scoped mkstemp
        # is not an out-of-perimeter write at all, so there is no site to
        # report — and the day somebody drops that keyword the scanner starts
        # reporting an UNACCOUNTED host-temp site at this exact symbol, which
        # is PS-57 arriving as a red gate instead of as a person reading code.
        detected=False,
        # PS-57 is the fix that put it here: the `dir=` argument to mkstemp is
        # what scopes it into the profile instead of the host temp dir.
        reason=(
            "mkstemp is given dir=os.path.dirname(ca_path), which puts it "
            "inside the profile's own .persona-mtls directory rather than the "
            "host temp dir (PS-57). It is therefore INSIDE the perimeter and "
            "reached by profile deletion; it is listed because the SITE is "
            "what a future edit could silently un-scope by dropping one "
            "keyword argument."
        ),
    ),
    Artifact(
        artifact="the mTLS terminator's decrypted key material and leaf cert",
        site="src/services/cert/terminator.py:sweep_key_material",
        scope=SCOPE_HOME,
        platforms=ALL_PLATFORMS,
        disposition=DISPOSITION_SCOPED,
        # Inside the profile by construction (the terminator writes under the
        # profile's own .persona-mtls), so no out-of-perimeter site exists to
        # find. Same reading as the entry above: an unscoped rewrite would
        # surface as a new unaccounted site rather than as silence.
        detected=False,
        reason=(
            "written under the profile's .persona-mtls directory and swept AT "
            "LAUNCH rather than at exit, binding the decrypted key's lifetime "
            "to the DIRECTORY: a new session sweeps before writing, so at most "
            "one session's key material is ever on disk. ⛔ The launch-time "
            "sweep is deliberate and is NOT to be reopened — a SIGKILLed "
            "session runs no exit handler, so an exit-time sweep would be the "
            "guarantee that silently does not hold."
        ),
    ),
    Artifact(
        artifact="the engine child's scratch directory (.persona-tmp)",
        site="src/services/browser/env_policy.py:browser_child_tmpdir",
        scope=SCOPE_HOME,
        platforms=ALL_PLATFORMS,
        disposition=DISPOSITION_SCOPED,
        # `os.path.join(profile_dir, CHILD_TMPDIR_NAME)` names no host path and
        # calls no tempfile factory, so there is nothing for the scanner to
        # report — which is precisely what PS-129 achieved. A regression to
        # `tempfile.mkdtemp()` would surface here as an unaccounted host-temp
        # site.
        detected=False,
        reason=(
            "pinned INSIDE the profile's data dir (PS-129), so delete_profile "
            "(which renames the dir into the trash), the trash and "
            "wipe_all_profiles all reach it. Before PS-129 it was engine "
            "scratch in the host's shared temp dir, reachable by nothing "
            "persona owns — one of the six. Listed because that pin is one "
            "join away from being lost."
        ),
    ),
    # --- sites the scan finds that create nothing --------------------------
    #
    # ⚠️ EACH OF THESE IS A NEGATIVE, AND A NEGATIVE IS THE CLAIM THIS
    # INVENTORY IS LEAST ENTITLED TO MAKE FROM READING CODE. "It looks like it
    # writes nothing" is exactly the sentence that would have dismissed all six
    # historical defects. So each was MEASURED on the same real launch that
    # produced the entries above: the file or directory was watched across a
    # launch, and the reading is quoted in the reason rather than reasoned to.
    Artifact(
        artifact=(
            "PERSONA_HOME's own root, resolved from ~/.persona — the perimeter "
            "itself, not something written outside it"
        ),
        site="src/core/config.py:_home",
        scope=SCOPE_HOST,
        platforms=ALL_PLATFORMS,
        disposition=DISPOSITION_NO_ARTIFACT,
        reason=(
            "this expanduser RESOLVES the perimeter's root; everything it "
            "produces is by definition inside it. The scan sees a host-home "
            "path because that is literally what the constant is, which is the "
            "correct reading of the line and not a finding."
        ),
    ),
    Artifact(
        artifact=(
            "the ~/.persona FALLBACK home, used only when the configured "
            "PERSONA_HOME cannot be created"
        ),
        site="src/core/config.py:_ensure_home",
        scope=SCOPE_HOST,
        platforms=ALL_PLATFORMS,
        disposition=DISPOSITION_NO_ARTIFACT,
        reason=(
            "reached only on the error path where the configured home could "
            "not be made, and it then BECOMES the perimeter rather than "
            "sitting outside one. Measured on a launch under a scratch home: "
            "~/.persona was not created or touched at all."
        ),
    ),
    Artifact(
        artifact=(
            "the browser child's working directory on the LINUX fork path — "
            "the operator's home, handed to the child, written to by nobody"
        ),
        site="src/services/browser/env_policy.py:browser_child_cwd",
        scope=SCOPE_HOST,
        platforms=ALL_PLATFORMS,
        disposition=DISPOSITION_NO_ARTIFACT,
        # ⭐ THE ENTRY MOST WORTH READING TWICE, because the same symbol has an
        # exception entry above it and they are DIFFERENT CLAIMS. This one says
        # the value the function returns creates no artifact; the exception
        # says the thread path never applies it. Neither implies the other.
        reason=(
            "returns os.path.expanduser('~') as the child's cwd — a directory "
            "the child STARTS IN, not one persona writes to. Measured: a real "
            "firefox launch created exactly ONE path anywhere under the "
            "operator's home (the .desktop entry), and nothing in the home "
            "directory itself. ⛔ The value is deliberately unchanged; this "
            "records it, and see the _apply_child_cwd entry for the separate "
            "question of which platforms apply it."
        ),
    ),
    Artifact(
        artifact=(
            "the engine binary's path under ENGINE_DIR, resolved at import "
            "time by the chromium seam"
        ),
        site="src/services/browser/process.py:<module>",
        scope=SCOPE_HOME,
        platforms=ALL_PLATFORMS,
        disposition=DISPOSITION_NO_ARTIFACT,
        reason=(
            "a READ path: FINGERPRINT_CHROMIUM names the executable to launch. "
            "The engine is installed by the updater, never by a launch. "
            "Measured: ENGINE_DIR did not exist before a firefox launch and "
            "did not exist after it."
        ),
    ),
    Artifact(
        artifact="proxies.json, opened by ProxyStore during a launch",
        site="src/services/proxy/store.py:__init__",
        scope=SCOPE_HOME,
        platforms=ALL_PLATFORMS,
        disposition=DISPOSITION_NO_ARTIFACT,
        reason=(
            "a launch READS the proxy store to resolve its profile's "
            "assignment; unlike BookmarkStore it seeds no defaults, so no file "
            "is created. Measured: proxies.json did not exist before a launch "
            "with a DIRECT profile and did not exist after it. ⚠️ A launch of "
            "a profile that HAS a proxy reads a file the operator already "
            "created — still no artifact this launch made."
        ),
    ),
    # --- the three recorded platform gaps ----------------------------------
    #
    # ⭐ THESE THREE ARE THE SHARPEST ENTRIES IN THE INVENTORY AND THEY INVENT
    # NOTHING. Each reason below is lifted from the comment block already
    # sitting at its site — prose authored when the decision was made, reachable
    # by no reader who did not already know to look. Lifting them here is
    # literally DoD#3's "recorded as a decided exception with its reason".
    #
    # ⛔ THIS RECORDS THEM; IT DOES NOT CLOSE THEM. Do not weaken any of the
    # three guards to make an entry go away — each is correct as it stands, and
    # the thing being fixed is that they were invisible, not that they exist.
    Artifact(
        artifact=(
            "the browser child's WORKING DIRECTORY is not pinned on the thread "
            "launch path (Windows/macOS), so the child inherits persona's"
        ),
        site="src/services/browser/invisible_launch.py:_apply_child_cwd",
        scope=SCOPE_HOST,
        # ⚠️ READ THE PLATFORM COLUMN BACKWARDS HERE. The PIN is Linux-only, so
        # the GAP is on the other two — but the gap is an artifact of the
        # THREAD path, and the entry is expected (as an exception) on the
        # platforms that take it.
        platforms=("windows", "macos"),
        disposition=DISPOSITION_EXCEPTION,
        # An ABSENCE, so there is no write site to find: the artifact is what
        # this platform does NOT do. The record is the deliverable here — the
        # reason below was authored in a comment at this symbol and was
        # reachable by no reader who did not already know to look.
        detected=False,
        reason=(
            "on the thread path — Windows/macOS, where re-exec cannot work — "
            "the child is a THREAD of the manager process, and a working "
            "directory is process-global state exactly like os.environ: "
            "os.chdir there would move persona's OWN cwd and every "
            "concurrently-open profile's, which is strictly worse than the "
            "divergence being fixed. A recorded absence, not a guarantee that "
            "silently doesn't hold. The chromium launcher, which passes cwd= "
            "to Popen and so sets it in the child only, IS pinned on all "
            "platforms."
        ),
    ),
    Artifact(
        artifact=(
            "the browser child's TMPDIR is not pinned on the thread launch "
            "path (Windows/macOS), so engine scratch lands in the host's "
            "shared temp dir"
        ),
        site="src/services/browser/invisible_launch.py:_pin_tmpdir_here",
        scope=SCOPE_HOST,
        platforms=("windows", "macos"),
        disposition=DISPOSITION_EXCEPTION,
        # An ABSENCE, so there is no write site to find: the artifact is what
        # this platform does NOT do. The record is the deliverable here — the
        # reason below was authored in a comment at this symbol and was
        # reachable by no reader who did not already know to look.
        detected=False,
        reason=(
            "on the thread path this child is a THREAD of the manager process, "
            "where os.environ IS persona's own: pinning TMPDIR there would "
            "move persona's temp dir and EVERY concurrently-open profile's — "
            "pointing them all at ONE profile's scratch directory, which is "
            "worse than leaving them in /tmp. A recorded absence, not a "
            "guarantee that silently doesn't hold. The chromium launcher, "
            "which hands Popen an env= copy, IS pinned on all platforms."
        ),
    ),
    Artifact(
        artifact=(
            "the child's inherited ENVIRONMENT is not scrubbed on the thread "
            "launch path (Windows/macOS), so the browser inherits the "
            "operator's identity — above all SSH_AUTH_SOCK"
        ),
        site="src/services/browser/invisible_launch.py:scrub_current_process_environ",
        scope=SCOPE_HOST,
        platforms=("windows", "macos"),
        disposition=DISPOSITION_EXCEPTION,
        # An ABSENCE, so there is no write site to find: the artifact is what
        # this platform does NOT do. The record is the deliverable here — the
        # reason below was authored in a comment at this symbol and was
        # reachable by no reader who did not already know to look.
        detected=False,
        reason=(
            "when stop_event is set we are a THREAD of the manager process "
            "(Windows/macOS, where re-exec can't work) and there is no "
            "separate environment to scrub: mutating os.environ there would "
            "strip persona's OWN environment and every other concurrently open "
            "profile's. A recorded absence, not a guarantee that silently "
            "doesn't hold. The chromium launcher, which passes an env= copy to "
            "Popen, IS scrubbed on all platforms."
        ),
    ),
    Artifact(
        artifact=(
            "the browser child's Wayland app_id (MOZ_APP_REMOTINGNAME) is not "
            "set on the thread launch path (Windows/macOS, and Linux under "
            "in_process=True), so the taskbar icon is not matched to the "
            "profile's .desktop entry on a Wayland session"
        ),
        site="src/services/browser/invisible_launch.py:_remoting_name",
        scope=SCOPE_HOST,
        # ⚠️ NOT ("windows", "macos") LIKE ITS THREE SIBLINGS, and the
        # difference is the entry's whole point. The other three gaps are
        # PLATFORM gaps: their guards read `not in_thread and IS_LINUX`, so on
        # Linux they always apply. This one is guarded the same way, but its
        # ABSENCE is reachable on Linux too — `in_process=True` forces the
        # thread arm there (verify/baseline.py's recorder), which is exactly
        # the combination PS-360 measured. Recording it as Windows/macOS-only
        # would repeat the reading error the ticket was raised to correct.
        platforms=ALL_PLATFORMS,
        disposition=DISPOSITION_EXCEPTION,
        # An ABSENCE, so there is no write site to find: the artifact is what
        # this path does NOT do.
        detected=False,
        reason=(
            "PS-360. On the thread path os.environ IS persona's own, and this "
            "variable is on no scrub list and is never cleared, so writing it "
            "there (1) outlives the session, (2) makes a later unnamed "
            "profile start under the previous profile's identity, and (3) "
            "makes two CONCURRENT sessions both read the last writer's name — "
            "the very collision a per-profile-unique remoting name exists to "
            "prevent. All three were measured before the guard was added. "
            "Set-and-restore was considered and REFUSED: it is the shape "
            "env_policy.neutralise_vendored_credentials already argues "
            "against, and a finally would clear a concurrent session's live "
            "value. A recorded absence, not a guarantee that silently doesn't "
            "hold. The X11 half (--name) is a per-launch Popen-style ARGUMENT "
            "rather than process-global state, so IT is set on all platforms; "
            "the engine seam exposes no env= for the Wayland half to use."
        ),
    ),
)


# --- the surface the gate walks ---------------------------------------------

#: THE MODULES A REAL LAUNCH EXECUTES, and the population the gate scans.
#:
#: ⭐ TRACED, NOT CURATED. A real firefox launch under xvfb was run with
#: ``sys.settrace``/``threading.settrace`` installed, and this is what it
#: touched under ``src/`` — so a module here is one a launch demonstrably
#: enters, rather than one that looked relevant.
#:
#: ⚠️ ``env_policy`` IS THE ONE ADDITION TO THE TRACE, and it is stated rather
#: than smuggled in: the trace ran on the FIREFOX arm, which reaches
#: ``env_policy`` inside the forked child (a separate process the tracer does
#: not follow), and the chromium arm calls it directly. It holds two of the
#: inventory's entries, so scanning the launch surface without it would leave
#: the tmpdir pin unwatched.
#:
#: ⛔ NARROWER THAN "the whole tree", ON PURPOSE. This is the population the
#: check's claim is about, and its narrowness is admitted in
#: ``UNCOVERED_SURFACES`` rather than left for a reader to discover: a new
#: out-of-perimeter write in a module NOT on this list is not seen. Widening it
#: is cheap; pretending it is already wide is what this project refuses.
LAUNCH_SURFACE: tuple[str, ...] = (
    "src/core/config.py",
    "src/core/logging.py",
    "src/core/platform.py",
    "src/core/settings.py",
    "src/services/bookmark/store.py",
    "src/services/browser/automation_channel.py",
    "src/services/browser/device_presets.py",
    "src/services/browser/engine_install.py",
    "src/services/browser/env_policy.py",
    "src/services/browser/firefox_bookmarks.py",
    "src/services/browser/invisible_launch.py",
    "src/services/browser/launch_policy.py",
    "src/services/browser/launch_provenance.py",
    "src/services/browser/launcher.py",
    "src/services/browser/process.py",
    "src/services/browser/process_group.py",
    "src/services/browser/resolution.py",
    "src/services/browser/session_registry.py",
    "src/services/browser/window_entry.py",
    "src/services/cert/terminator.py",
    "src/services/engine/firefox.py",
    "src/services/profile/coherence.py",
    "src/services/proxy/store.py",
    "src/utils/atomic.py",
    "src/utils/store_guard.py",
)

#: The ``core.config`` names that resolve to a path in :data:`SCOPE_HOME` —
#: inside ``PERSONA_HOME``, outside any one profile's directory. Naming a
#: constant from this set on the launch surface is a write site in the outer
#: circle, whatever the surrounding code looks like.
#:
#: ``DATA_DIR`` is deliberately ABSENT: it is the parent of the profile
#: directories, so joining a profile name onto it lands INSIDE the perimeter,
#: which is the correct shape rather than a finding.
HOME_SCOPED_STORES: frozenset[str] = frozenset(
    {
        "BOOKMARKS_FILE",
        "CERTS_DIR",
        "CERTS_FILE",
        "ENGINE_DIR",
        "LOG_DIR",
        "PROFILES_FILE",
        "PROXIES_FILE",
        "SESSIONS_FILE",
    }
)

#: ``tempfile`` factories that place a file. Called WITHOUT ``dir=`` each of
#: these lands in the host's shared temp dir — which is PS-57's defect exactly,
#: and PS-129's — so the gate reads the keyword rather than the call.
TEMPFILE_FACTORIES: frozenset[str] = frozenset(
    {
        "mkstemp",
        "mkdtemp",
        "NamedTemporaryFile",
        "TemporaryDirectory",
    }
)


def validate_inventory(
    artifacts: "tuple[Artifact, ...] | None" = None,
) -> list[str]:
    """Structural complaints about the inventory itself, as a list of strings.

    An inventory whose own entries are malformed cannot be the input to
    anything, and the failure mode is quiet: an entry with an empty ``reason``
    reads, at a glance, exactly like an entry with a considered one. Empty list
    means no complaint.
    """
    entries = PERIMETER_ARTIFACTS if artifacts is None else artifacts
    problems: list[str] = []
    seen: set[str] = set()
    for a in entries:
        where = a.site or "<no site>"
        if not a.artifact.strip():
            problems.append(f"{where}: entry has no artifact description")
        if ":" not in a.site:
            problems.append(
                f"{where}: site must be 'path.py:symbol', so the gate can "
                "resolve it after the lines around it move"
            )
        if a.site in seen:
            problems.append(f"{where}: duplicated site")
        seen.add(a.site)
        if a.scope not in (SCOPE_HOST, SCOPE_HOME):
            problems.append(f"{where}: unknown scope {a.scope!r}")
        if not a.platforms:
            problems.append(
                f"{where}: no platform column — an artifact expected nowhere "
                "cannot be checked anywhere"
            )
        for p in a.platforms:
            if p not in ALL_PLATFORMS:
                problems.append(f"{where}: unknown platform {p!r}")
        if a.disposition == DISPOSITION_REMOVED:
            if not a.reached_by.strip():
                problems.append(
                    f"{where}: disposition 'removed' with nothing named as the "
                    "remover — which is the claim, not a label"
                )
            if not a.removal_sites:
                problems.append(
                    f"{where}: disposition 'removed' with no removal_sites. "
                    "Prose alone is what PS-16 had: the reach was described "
                    "and one caller was missing, and nothing could tell."
                )
            for site in a.removal_sites:
                if ":" not in site or "->" not in site:
                    problems.append(
                        f"{where}: removal site {site!r} must be "
                        "'path.py:caller->callee'"
                    )
        elif a.disposition in (
            DISPOSITION_EXCEPTION,
            DISPOSITION_SCOPED,
            DISPOSITION_NO_ARTIFACT,
        ):
            if not a.reason.strip():
                problems.append(
                    f"{where}: disposition {a.disposition!r} with no reason. "
                    "DoD#3 asks for a decided exception WITH ITS REASON; "
                    "without one this is scope-by-omission wearing a label."
                )
        else:
            problems.append(f"{where}: unknown disposition {a.disposition!r}")
        if not a.detected and not a.reason.strip():
            problems.append(
                f"{where}: detected=False with no reason. That flag turns off "
                "the static cross-check for this entry, so an unreasoned one "
                "is a site excused from the gate by assertion."
            )
    return problems


def inventory_sites() -> frozenset[str]:
    """Every ``path.py:symbol`` the inventory accounts for."""
    return frozenset(a.site for a in PERIMETER_ARTIFACTS)


def inventory_files() -> frozenset[str]:
    """Every file the inventory names, without its symbols."""
    return frozenset(a.site.rsplit(":", 1)[0] for a in PERIMETER_ARTIFACTS)


__all__ = [
    "ALL_PLATFORMS",
    "DISPOSITION_EXCEPTION",
    "DISPOSITION_NO_ARTIFACT",
    "DISPOSITION_REMOVED",
    "DISPOSITION_SCOPED",
    "HOME_SCOPED_STORES",
    "LAUNCH_SURFACE",
    "LINUX_ONLY",
    "PERIMETER_ARTIFACTS",
    "SCOPE_HOME",
    "SCOPE_HOST",
    "TEMPFILE_FACTORIES",
    "Artifact",
    "inventory_files",
    "inventory_sites",
    "validate_inventory",
]
