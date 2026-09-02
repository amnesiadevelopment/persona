import glob
import json
import logging
import os
import pathlib
import shutil
import threading
import zlib
from collections.abc import Callable

from ...core.config import DATA_DIR, PROFILES_FILE
from ...core.logging import get_logger
from ...models.hardware_generation import CURRENT_HARDWARE_GENERATION
from ...models.profile import Profile, mint_fingerprint_seed
from ...utils.atomic import atomic_write_json
from ...utils.store_guard import StoreGuardMixin
from ...utils.trashable import TrashableMixin
from ...utils.validation import validate_profile_name
from .cert_assignment import (
    CertDirective,
    cert_for_new_profile,
    resolve_cert_assignment,
)
from .coherence import (
    DEFAULT_DEVICE_TYPE,
    assert_coherent,
    assert_device_type_coherent,
    assert_storable_os_type,
    coherence_error,
    coherent_engine,
    normalize_engine,
)
from .pool_assignment import (
    POOL_UNCHANGED,
    PoolDirective,
    pool_for_new_profile,
    resolve_pool_assignment,
)
from .proxy_assignment import (
    PROXY_NONE,
    PROXY_UNCHANGED,
    ProxyDirective,
    proxy_for_new_profile,
    resolve_proxy_assignment,
)
from .transfer import export_to_zip, import_from_zip, peek_profile_name

logger = get_logger("profile.manager")

#: Directory name of the park area where trashed profile data dirs live. It sits
#: BESIDE DATA_DIR, never inside it.
TRASH_DIR_NAME = "trash_data"


def trash_data_root() -> str:
    """The park area for trashed profile data dirs: a SIBLING of DATA_DIR, never
    a subdirectory of it.

    An in-DATA_DIR park area was addressable as a profile. validate_profile_name
    (".trash") is valid, so _data_path(".trash") resolved to the park area
    ITSELF: every other profile's trashed cookies were parked inside a live,
    launchable profile's data dir, and deleting that profile failed outright
    (renaming a directory into itself). Outside DATA_DIR no profile name can
    name the park area at all, so "nothing in the trash is reachable as a live
    record" holds by CONSTRUCTION rather than by reserving a name a validator
    might later accept again.

    Derived from DATA_DIR's parent at call time, not frozen at import, for two
    reasons: it keeps the park area on the SAME filesystem as the data dir, so
    the move-not-copy rename cannot fail with EXDEV; and DATA_DIR is monkey-
    patched per-test, so a derived root follows it instead of leaking trashed
    profile data into the real PERSONA_HOME during a test run.
    """
    return os.path.join(os.path.dirname(os.path.normpath(DATA_DIR)), TRASH_DIR_NAME)


class InvalidProfileName(ValueError):
    """A profile name that fails validation or would escape the data dir."""


class InvalidTrashToken(ValueError):
    """A trash token that isn't an opaque hex id or would escape the data dir."""


class ProfileManager(StoreGuardMixin, TrashableMixin):
    _guard_logger = logger
    _guard_noun_plural = "profiles"
    _guard_noun_singular = "profile"

    def __init__(self) -> None:
        self.profiles: dict[str, Profile] = {}
        self._save_blocked = False
        # Set by the app once the launcher exists: called with a profile name
        # right before its data dir is rmtree'd, so delete/wipe never rmtree a
        # profile dir out from under a live browser (corrupt cache / Windows
        # rmtree failure). No-op by default (headless/tests).
        #
        # RETURNS `object`, not None (PS-165): the hook this is actually set to
        # is BrowserLauncher.stop_profile, which returns bool. _stop_if_running
        # DISCARDS whatever comes back — the contract is "call this with a name",
        # not "call this and observe the result" — so `object` states the truth,
        # where `None` claimed the callee must return nothing and made the one
        # real call site a type error.
        self._stop_hook: "Callable[[str], object] | None" = None
        # Set by the app once the launcher exists: called with a profile name
        # whose IDENTITY has gone away (delete, wipe, rename-away, overwrite),
        # so per-name state the launcher holds is not inherited by whatever
        # takes the name next. Distinct from _stop_hook above, which is about a
        # LIVE session; see set_forget_identity_hook. No-op by default.
        self._forget_identity_hook: "Callable[[str], None] | None" = None
        # Serializes load+save so a concurrent read during a bulk import/tagging
        # flow can't see a half-written file and a concurrent add can't be lost.
        self._lock = threading.RLock()
        self._load_profiles()
        pathlib.Path(DATA_DIR).mkdir(exist_ok=True, parents=True)

    def _data_path(self, name: str) -> str:
        # The single choke point for every profile filesystem op (mkdir, rmtree,
        # rename). Validate here so no caller — import, the MCP tool, a future
        # entry point — can slip a traversal name through and turn these into
        # arbitrary-path primitives. Belt AND braces: reject names that fail
        # validation, then confirm the resolved path stays inside DATA_DIR.
        valid, msg = validate_profile_name(name)
        if not valid:
            raise InvalidProfileName(msg)
        base = os.path.realpath(DATA_DIR)
        target = os.path.realpath(os.path.join(DATA_DIR, name))
        if os.path.commonpath([base, target]) != base:
            raise InvalidProfileName(f"profile name escapes the data dir: {name!r}")
        return os.path.join(DATA_DIR, name)

    def _trash_data_path(self, token: str) -> str:
        """Where a trashed profile's data dir lives, keyed by its opaque trash
        token. The trash must not become a NEW arbitrary-path primitive beside
        _data_path, so it gets the same treatment: the token is constrained to
        hex (no separators, no dots, so no traversal is even expressible), and
        the resolved path is then confirmed to stay inside the park area — belt
        AND braces, exactly as for a profile name. The token, not the profile
        name, names the directory: the trash area then carries no profile name in
        cleartext, matching why the desktop entry is removed on delete.

        The park area is trash_data_root(), OUTSIDE DATA_DIR — see its docstring:
        no profile name can address a directory that isn't under the profile data
        dir, so a trashed data dir can never land inside a live profile's own
        directory."""
        if not token or not all(c in "0123456789abcdefABCDEF" for c in token):
            raise InvalidTrashToken(f"not an opaque trash token: {token!r}")
        root = trash_data_root()
        base = os.path.realpath(root)
        target_dir = os.path.join(root, token)
        # realpath of a not-yet-existing leaf is still its lexical parent + leaf,
        # so this check is meaningful before the move as well as after it.
        target = os.path.realpath(target_dir)
        if os.path.commonpath([base, target]) != base or target == base:
            raise InvalidTrashToken(f"trash token escapes the trash area: {token!r}")
        return target_dir

    def _load_profiles(self) -> None:
        with self._lock:
            self._load_profiles_locked()

    def _load_profiles_locked(self) -> None:
        if pathlib.Path(PROFILES_FILE).exists():
            try:
                with pathlib.Path(PROFILES_FILE).open(encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    raise TypeError(
                        f"profiles root is {type(data).__name__}, not dict"
                    )
                skipped = 0
                for name, p_data in data.items():
                    # One malformed record must not abort the whole load — the
                    # next save would overwrite profiles.json with only what
                    # loaded, silently losing every good profile.
                    try:
                        clean_data = {
                            "name": p_data.get("name"),
                            "proxy": p_data.get("proxy"),
                            "os_type": p_data.get(
                                "os_type",
                                p_data.get("config", {}).get("os", "windows"),
                            ),
                            # device_type is NOT judged here (Rule 3, PS-188:
                            # ACCEPT AND RECORD). This door reads records
                            # written by an older build, so it is a RECOVERY
                            # door like import and restore, and refusing would
                            # drop a profile the operator already owns off the
                            # bottom of the list. An absent key defaults to
                            # "desktop", which is coherent with every os_type;
                            # a stored "mobile" beside a desktop os_type lands
                            # verbatim and is reported by
                            # `Profile.device_type_incoherence` rather than
                            # rewritten. Repairing is not available to this
                            # door either: which of the two fields is the lie
                            # is not knowable from the record.
                            "device_type": p_data.get(
                                "device_type", "desktop"
                            ),
                            # "camoufox" was the retired Firefox engine; map any
                            # saved profile onto the current "firefox" engine.
                            "engine": (
                                "firefox"
                                if p_data.get("engine") == "camoufox"
                                else p_data.get("engine", "chromium")
                            ),
                            "resolution": p_data.get("resolution", "auto"),
                            "search_engine": p_data.get(
                                "search_engine", "duckduckgo"
                            ),
                            "bookmark_pool": p_data.get("bookmark_pool"),
                            # Absent key = a pre-this-field profile → None so it
                            # keeps getting the default bookmarks. A saved [] is
                            # an intentional empty selection and is preserved.
                            "bookmarks": p_data.get("bookmarks"),
                            "certificate": p_data.get("certificate"),
                            "cookie_import_status": p_data.get(
                                "cookie_import_status"
                            ),
                            # This allow-list is hand-enumerated, so a field
                            # missing HERE is silently dropped on reload even
                            # though to_dict() saved it (see transfer.py:117 —
                            # cookie_import_status was the last field to hit
                            # this exact bug).
                            "cert_trust_status": p_data.get(
                                "cert_trust_status"
                            ),
                            "tags": p_data.get("tags", []),
                            "notes": p_data.get("notes", ""),
                            "ai_control": p_data.get("ai_control", False),
                            # Absent key = a profile written before the seed
                            # was persisted → None, which makes
                            # fingerprint_seed fall back to crc32(name) and
                            # present exactly what that profile has always
                            # presented. That fallback is the entire migration
                            # for existing profiles; do NOT default this to
                            # crc32(name) here, which would silently freeze a
                            # derived value and make a later rename of an OLD
                            # profile behave differently depending on whether
                            # it had been reloaded since. And per the
                            # allow-list note above, omitting this line is not
                            # an inert oversight: the seed would be saved by
                            # to_dict() and dropped on the next load, so every
                            # profile would quietly re-derive from its name at
                            # restart and the freeze would evaporate while the
                            # in-memory tests still passed.
                            "fingerprint_seed_value": p_data.get(
                                "fingerprint_seed_value"
                            ),
                            # Same allow-list trap as the seed above, and the
                            # same reason it matters: omit this line and
                            # to_dict() would save the generation while every
                            # reload dropped it, so each restart would silently
                            # re-read the profile as generation 0 — invisible
                            # until a list grew, then a mass re-roll of exactly
                            # the profiles this field exists to hold still.
                            # Absent key = a profile written before the field
                            # existed → None → generation 0, which sees the
                            # lists as they shipped, i.e. precisely what that
                            # profile has always presented. That fallback IS
                            # the migration; do NOT default it to
                            # CURRENT_HARDWARE_GENERATION, which would hand
                            # every existing profile the newest pool and re-roll
                            # the whole machine at once.
                            "hardware_generation_value": p_data.get(
                                "hardware_generation_value"
                            ),
                            # Absent key = a profile that has not launched
                            # since the field was added → None, which reads as
                            # "not known". Do NOT default these to the
                            # currently-installed build: that would invent a
                            # provenance record for a launch that was never
                            # observed, and the whole value of the field is
                            # that a difference against it is interpretable.
                            # A guessed stamp makes that comparison return a
                            # false answer, which is worse than no answer.
                            "last_launch_engine": p_data.get(
                                "last_launch_engine"
                            ),
                            "last_launch_build": p_data.get(
                                "last_launch_build"
                            ),
                        }
                        self.profiles[name] = Profile(**clean_data)
                    except Exception:
                        skipped += 1
                        logger.exception("Skipping malformed profile %r", name)
                if skipped:
                    logger.warning(
                        "Skipped %d malformed profile record(s)", skipped
                    )
                logger.info("Loaded %d profiles", len(self.profiles))
            except Exception as e:
                logger.exception("Error loading profiles: %s", e)
                self._quarantine_profiles_file()

    def _store_path(self) -> str:
        # Read the module global on every call: the profile specs monkeypatch
        # manager.PROFILES_FILE, so a value bound at import time elsewhere
        # would bypass the patch and touch the real ~/.persona/profiles.json.
        return PROFILES_FILE

    def _quarantine_profiles_file(self) -> None:
        # An unreadable profiles.json still holds every profile the user has;
        # move it aside so the next save_profiles() can't overwrite it with
        # the empty in-memory dict.
        self._quarantine_store_file()

    def save_profiles(self) -> None:
        if self._save_is_blocked():
            return
        try:
            with self._lock:
                atomic_write_json(
                    PROFILES_FILE,
                    {name: p.to_dict() for name, p in self.profiles.items()},
                )
            logger.debug("Profiles saved")
        except Exception as e:
            logger.exception("Error saving profiles: %s", e)

    def _reserved_seeds(self) -> set[int]:
        """Every fingerprint seed a live OR trashed profile is holding.

        The isolation half of the seed invariant is enforced against this set:
        a new or imported profile must not be minted onto a seed another
        profile is already presenting, or the two share a resolution, a device
        preset, touch points and a --fingerprint= — i.e. they are linkable to
        each other, which is the one thing this whole area exists to prevent.

        Reads `fingerprint_seed` (the property), not `fingerprint_seed_value`
        (the field), deliberately: a profile that predates the field still
        PRESENTS a seed — its crc32(name) fallback — and a collision with that
        is just as linkable as a collision with a frozen one. Comparing raw
        fields would see None for every old profile and miss all of them.

        TRASHED profiles are counted, and that is load-bearing rather than
        cautious. A trashed profile can come BACK, and restore_profile rebuilds
        it verbatim from its stored payload — it must, since re-rolling a
        restored profile's seed would hand its cookie jar back under a changed
        machine, the very thing that path refuses in writing. So a trashed
        seed cannot be re-minted later; it has to be held aside NOW. Otherwise
        the codebase's own documented remedy ("Free the name and restore
        again") manufactures the collision: trash 'alpha', recreate 'alpha',
        rename it to 'alpha-2', restore — and both sit on crc32('alpha').
        Reserving here is what lets restore stay verbatim.

        A trash store that cannot be read is treated as reserving nothing: a
        create must not fail because trash.json is unreadable, and the live
        half of the set is still enforced.

        Call under `self._lock`. It is a snapshot, so a caller that mints from
        it after releasing the lock can still race another create.
        """
        seeds = {p.fingerprint_seed for p in self.profiles.values()}
        try:
            for entry in self._trash().list("profile"):
                payload = entry.payload or {}
                value = payload.get("fingerprint_seed_value")
                if isinstance(value, int) and not isinstance(value, bool):
                    seeds.add(value)
                elif payload.get("name"):
                    # Pre-field trashed record: it would restore onto the
                    # crc32(name) fallback, so that is the value to reserve.
                    #
                    # zlib.crc32 DIRECTLY, not mint_fingerprint_seed(). This
                    # line models `Profile.fingerprint_seed`'s LEGACY FALLBACK
                    # (profile.py: "return zlib.crc32(self.name...)"), which is
                    # the value a restore genuinely lands on — it is not a
                    # mint, and it never was. It merely read identically while
                    # the mint happened to equal crc32(name).
                    #
                    # Once the mint became SALTED those two stopped being the
                    # same number, and calling the mint here would silently
                    # reserve a value NOTHING restores onto while leaving the
                    # real fallback free — so a pre-field trashed profile
                    # restoring onto its crc32(name) seed could collide with a
                    # live profile that had since been handed it. Two live
                    # profiles, one presented machine: the exact isolation
                    # failure the reservation exists to prevent, reintroduced
                    # by the secrecy fix. Do NOT "tidy" this back into the
                    # mint; it must track the fallback, and the fallback is
                    # deliberately unsalted legacy behaviour.
                    seeds.add(zlib.crc32(str(payload["name"]).encode("utf-8")))
        except Exception:
            logger.exception("Could not read trashed seeds; reserving live only")
        return seeds

    def add_profile(
        self,
        name: str,
        proxy: str | ProxyDirective | None,
        os_type: str,
        search_engine: str = "duckduckgo",
        bookmark_pool: str | PoolDirective | None = None,
        bookmarks: list[str] | None = None,
        tags: list[str] | None = None,
        device_type: str = "desktop",
        notes: str = "",
        engine: str = "chromium",
        resolution: str = "auto",
        certificate: str | CertDirective | None = None,
        ai_control: bool = False,
    ) -> bool:
        # Validate up front so an invalid/traversal name is rejected before it's
        # registered — import and the MCP tool used to reach here unchecked.
        valid, _ = validate_profile_name(name)
        if not valid:
            return False
        # The coherence rules live below every door (see coherence.py). They
        # used to live only in the profile dialog, so the REST lane composed
        # profiles the dialog exists to prevent — a macOS record on the Firefox
        # engine, which launches presenting Windows. Raised rather than returned
        # False: False here means "already exists" (409), while an incoherent
        # profile is a different refusal with a reason the caller can act on, and
        # a door that forgets to handle it fails loudly instead of silently
        # storing a lie.
        #
        # device_type is passed too (Rule 3). The dialog has no control for it,
        # so the ONLY way a `windows` + `mobile` profile can be composed is a
        # door that inherits none of the dialog's narrowing — which is precisely
        # the class of caller this module exists for. A create composes a new
        # machine from whole cloth, so it is judged on all three fields.
        assert_coherent(os_type, engine, device_type)
        # Rule 4 (PS-187): the os_type SPELLING, not a pair. `win` is a value
        # our fold recognises and the ENGINE does not — it reaches
        # --fingerprint-platform unchanged and the engine answers with its own
        # software renderer, so the host's real GPU strings reach the page.
        #
        # A create AUTHORS a machine, so the caller is TOLD rather than having
        # their input silently rewritten. The model repairs the value regardless
        # (Profile.__setattr__), so this refusal is the loud half of a rule that
        # holds either way — not the thing standing between `win` and the disk.
        assert_storable_os_type(os_type)
        # Hold the lock across the check-then-insert so two concurrent adds of
        # the same name can't both pass the `name in self.profiles` check and one
        # silently overwrite the other (RLock: save_profiles below re-enters it).
        with self._lock:
            if name in self.profiles:
                return False
            self.profiles[name] = Profile(
                name=name,
                # Creation has nothing to preserve, so both directives mean "no
                # proxy" here. Routed through the helper so a directive object
                # can never be stored as if it were a proxy name.
                proxy=proxy_for_new_profile(proxy),
                os_type=os_type,
                device_type=device_type,
                engine=engine,
                resolution=resolution,
                search_engine=search_engine,
                # Creation has nothing to preserve, so both directives mean "no
                # pool" here — exactly as proxy_for_new_profile documents for
                # proxy. Routed through the helper because the profile dialog
                # shares one value across create and edit, so a directive
                # legitimately reaches this path and must never be stored as if
                # it were a pool name.
                bookmark_pool=pool_for_new_profile(bookmark_pool),
                bookmarks=bookmarks,
                # Creation has nothing to preserve, so CERT_UNCHANGED means "no
                # certificate" here — as proxy_for_new_profile documents. The
                # unresolved state cannot arise on create, but the dialog shares
                # ONE value across create and edit, so route it through the
                # helper: a directive object is TRUTHY, and `certificate or None`
                # would have stored it as if it were a certificate NAME.
                certificate=cert_for_new_profile(certificate),
                tags=tags or [],
                notes=notes,
                ai_control=ai_control,
                # Freeze the seed HERE, at the one moment the profile's
                # identity is born. Minted from the creation name, so a new
                # profile presents exactly what it would have presented when
                # the seed was re-derived on every read — but from now on a
                # rename moves the name and the data dir while the presented
                # machine stays put. Mint only on CREATE: writing this on any
                # edit path would re-roll the very thing it exists to pin.
                #
                # `taken` is what keeps the OTHER half of the invariant alive.
                # Freezing the seed made the name reusable-with-consequences:
                # rename 'acme-bank' away and create it again, and plain
                # crc32(name) hands the new profile the seed its predecessor is
                # still holding — two live profiles, one presented machine.
                # Passing the live seeds makes the mint skip a value already in
                # use. Read INSIDE the same `with self._lock` as the
                # check-then-insert above, so two concurrent adds cannot both
                # see a stale set and mint the same seed.
                fingerprint_seed_value=mint_fingerprint_seed(
                    name, self._reserved_seeds()
                ),
                # Freeze the hardware generation at the same moment, for the
                # same reason. The seed pins WHICH INDEX this profile picks;
                # this pins WHAT THAT INDEX MEANS — the pool the index divides
                # into. A new profile is minted into the CURRENT generation, so
                # it can be picked onto hardware added since the lists shipped;
                # every older profile keeps its own frozen generation and thus
                # its own pool, contents, order and divisor unchanged.
                #
                # Mint only on CREATE, exactly like the seed: writing this on an
                # edit path would move the profile to a newer pool and re-roll
                # the very hardware it exists to pin.
                hardware_generation_value=CURRENT_HARDWARE_GENERATION,
            )
            self.save_profiles()
            pathlib.Path(self._data_path(name)).mkdir(exist_ok=True, parents=True)
        logger.info("Created profile: %s", name)
        return True

    def update_profile(
        self,
        original_name: str,
        new_name: str,
        new_proxy: str | ProxyDirective | None = PROXY_UNCHANGED,
        new_os: str | None = None,
        new_search_engine: str | None = None,
        new_bookmark_pool: str | PoolDirective | None = POOL_UNCHANGED,
        new_bookmarks: list[str] | None = None,
        new_tags: list[str] | None = None,
        new_ai_control: bool | None = None,
        new_device_type: str | None = None,
        new_notes: str | None = None,
        new_engine: str | None = None,
        new_resolution: str | None = None,
        new_certificate: str | CertDirective | None = None,
    ) -> bool:
        """Apply an edit to a profile.

        ``new_proxy`` takes a proxy name, or one of the two directives in
        ``proxy_assignment.py``: ``PROXY_UNCHANGED`` (the default — leave the
        stored assignment alone) or ``PROXY_NONE`` (clear it, the operator chose
        DIRECT). An empty value reads as UNCHANGED, never as a clear: clearing a
        proxy is something a caller has to SAY. See that module for why absence
        used to mean "clear" and what that cost.

        ``new_bookmark_pool`` takes a pool name, or one of the two directives in
        ``pool_assignment.py``: ``POOL_UNCHANGED`` (the default — leave the
        stored assignment alone) or ``POOL_NONE`` (clear it, the operator chose
        no pool). An empty value reads as UNCHANGED, never as a clear, for the
        same reason the proxy does: an edit made for an unrelated reason must
        not discard the assignment. Here what it costs is recoverability —
        ``delete_pool`` records the profiles referencing a pool so ``restore``
        can put them back, and it computes that list from this very field.

        ``new_certificate`` takes a certificate name, ``""`` to CLEAR the
        assignment, ``None`` (the default) to leave it alone, or the single
        directive in ``cert_assignment.py``: ``CERT_UNCHANGED``. Note the
        deliberate asymmetry with the two fields above — on this field ``""``
        still means CLEAR and always has, because absence already preserved
        here, so there was never a moment where "clear" needed a new spelling.
        What was missing is the opposite statement: a caller that CANNOT ACCOUNT
        for the stored assignment (the dialog, when the name is absent from the
        certificate list) used to send ``""`` and thereby promote its own
        ignorance into an explicit clear. It now sends ``CERT_UNCHANGED``.

        ``new_os`` defaults to None = leave unchanged, for the same reason and
        because Python cannot have a required argument follow a defaulted one.
        Coherence is still judged on the pair the edit RESULTS IN, so an omitted
        os_type is checked against the stored one exactly as an omitted engine
        already was.
        """
        with self._lock:
            if original_name not in self.profiles:
                return False

            if new_name != original_name and new_name in self.profiles:
                return False

            # Coherence (see coherence.py) is checked on the pair this edit would
            # RESULT IN, not on the fields it happens to supply: a PATCH carrying
            # only os_type must be judged against the engine already stored, or
            # `PATCH {"os_type": "macos"}` would sail through on a profile that is
            # already firefox. Both values are in hand here and only here, which
            # is the second reason the rule belongs in the model rather than at a
            # route.
            #
            # Refused only when the edit INTRODUCES the incoherence. A record
            # written before these rules existed (or through the unguarded REST
            # lane) stays editable: blocking it would make an ordinary edit to a
            # note or a tag fail on a conflict that edit did not create, and
            # leave the profile permanently uneditable — including the edit that
            # would FIX the pair.
            # "Introduces" is judged PER RULE FAMILY, never with one flag for
            # all three. The rules read different fields — the pair rules read
            # (os_type, engine), Rule 3 reads (os_type, device_type) — so a
            # single "something changed" gate in front of a single
            # `assert_coherent(os, engine, device_type)` would submit EVERY
            # field to judgement whenever ANY field moved. A device_type-only
            # edit would then be refused by Rule 2 for a pair it never touched,
            # and on a record violating both families (reachable via the exempt
            # `restore_profile` and via `import_profile`, which reconciles only
            # the pair) the edit that REPAIRS Rule 3 would be refused by Rule 2
            # — the stranding this whole block exists to prevent, arriving
            # through the door the exemption deliberately keeps open.
            _current = self.profiles[original_name]
            _current_engine = getattr(_current, "engine", "chromium")
            _current_device_type = getattr(
                _current, "device_type", DEFAULT_DEVICE_TYPE
            )
            _resulting_engine = (
                new_engine if new_engine is not None else _current_engine
            )
            _resulting_os = new_os if new_os is not None else _current.os_type
            # device_type joins the resulting-value read for the same reason
            # os_type and engine did: a PATCH carrying only device_type must be
            # judged against the os_type already stored, or
            # `PATCH {"device_type": "mobile"}` would sail through on a windows
            # profile — which is the exact door this rule was added to close.
            _resulting_device_type = (
                new_device_type if new_device_type is not None
                else _current_device_type
            )
            _pair_changed = (
                _resulting_os != _current.os_type
                or _resulting_engine != _current_engine
            )
            # Rule 3's inputs are (os_type, device_type), so an os_type edit can
            # introduce it too — the two gates overlap on os_type by design.
            _device_type_changed = (
                _resulting_device_type != _current_device_type
                or _resulting_os != _current.os_type
            )
            if _pair_changed:
                assert_coherent(_resulting_os, _resulting_engine)
            if _device_type_changed:
                assert_device_type_coherent(
                    _resulting_os, _resulting_device_type
                )
            # Rule 4 (PS-187) — the os_type SPELLING. Fired only when the edit
            # SUPPLIES an os_type, which is the same "introduces it" policy the
            # block above documents at length, applied to the one field this
            # rule reads. A PATCH touching only a note must not be refused
            # because of a spelling it did not author, or the edit that would
            # FIX the record becomes the edit the rule forbids.
            #
            # A legacy record cannot reach this check carrying a bad spelling
            # anyway: it was repaired by Profile.__setattr__ as it loaded. So
            # this refuses the operator who is authoring a NEW bad value, and
            # strands nobody who inherited one.
            if new_os is not None:
                assert_storable_os_type(new_os)

            # Rename the data dir BEFORE touching any in-memory field, so a
            # locked/failed dir-rename (routine on Windows when the browser is
            # running) leaves the profile completely unchanged — no half-applied
            # fields and no memory/disk divergence.
            if new_name != original_name:
                old_dir = self._data_path(original_name)
                if pathlib.Path(old_dir).exists():
                    try:
                        pathlib.Path(old_dir).rename(self._data_path(new_name))
                    except OSError as e:
                        logger.warning(
                            "Rename of profile data dir %r failed, keeping %r: %s",
                            original_name, original_name, e,
                        )
                        return False
                self.profiles = {
                    (new_name if k == original_name else k): v
                    for k, v in self.profiles.items()
                }
                # The desktop entry is keyed by NAME and embeds it in cleartext,
                # but delete/wipe only ever remove the CURRENT name (audit6 LOW
                # c). Drop the old name's entry here or it is stranded on the
                # host forever, outside PERSONA_HOME and unreachable by both.
                # Nothing is written back: the next launch rewrites it under the
                # new name (process.py). Must come AFTER the dir rename, whose
                # failure returns early and must leave everything untouched.
                self._remove_window_entry(original_name)
                # The ORIGINAL name is now free, so the launcher must stop
                # holding state under it. Same site and same reason as the
                # window entry above: a rename re-keys `self.profiles` and the
                # launcher is never told, so a refusal recorded against
                # `original_name` is orphaned there — invisible to the operator
                # (the card now looks under the NEW name) while sitting on a key
                # a future profile can take, which turns the quiet failure into
                # the dishonest one.
                #
                # DROPPED, NOT MOVED to the new key, and that is a deliberate
                # choice rather than a shortcut. The refusal's `detail` is the
                # settled sentence composed in process.py with the profile named
                # inside it ("Profile 'acme' has proxy ... Refusing to launch"),
                # so re-keying it would render a chip whose full text names a
                # profile that no longer exists. Restating the sentence to match
                # is exactly the forking this design refuses (see refusal.py).
                # "No verdict yet" is a state the card renders honestly, and the
                # next launch attempt re-establishes the real answer against the
                # proxy's CURRENT evidence — which is the only answer worth
                # showing anyway.
                self._forget_identity(original_name)

                # Freeze a PRE-FIELD profile's seed to what it presents RIGHT
                # NOW, before the new name can re-derive it.
                #
                # A profile created since the seed became a field carries its
                # own frozen value and a rename cannot move it. A profile that
                # predates the field has `fingerprint_seed_value = None`, so its
                # seed is crc32(self.name) recomputed on EVERY read — and the
                # line below reassigns `name`. That makes the rename path a
                # third place a seed can change, and unlike add_profile and
                # import_profile it consults no reserved set, so the new name's
                # crc32 can land straight on a seed another profile is already
                # holding: two live profiles, one presented machine. That is
                # reachable on 100% of the installed base, since the absent-field
                # fallback is deliberately the whole migration, so every profile
                # on disk at upgrade is a derived-seed profile until recreated.
                #
                # Freezing it to the value this profile is ALREADY presenting
                # is what makes this safe, and the choice is load-bearing
                # rather than incidental: it CANNOT introduce a collision,
                # because the set of presented seeds is unchanged by the write.
                # Every profile still presents exactly what it did a moment
                # ago, so no reserved-set consult is needed on this path at
                # all, and no fingerprint moves at rest (AC3). Do NOT "improve"
                # this into minting from the NEW name — that re-rolls the
                # machine under a live cookie jar, which is the entire defect
                # this ticket exists to fix.
                #
                # Read via the `fingerprint_seed` PROPERTY rather than
                # recomputing crc32 here, which keeps one owner for the
                # fallback formula. ORDERING IS LOAD-BEARING: the dict
                # comprehension above only re-KEYS the mapping, so
                # `_renamed.name` is still the ORIGINAL name at this point and
                # the property returns the old derived value. The assignment
                # that moves it is `profile.name = new_name` below. This block
                # must stay ABOVE that line — moving it below silently starts
                # freezing the NEW name's seed, which is the defect, not the
                # fix. The tests pin the value, so that mistake fails loudly.
                #
                # Sits INSIDE the `new_name != original_name` block and AFTER
                # the dir-rename success check on purpose: a rename that
                # returned False must leave the profile completely unchanged, a
                # seed mint included (AC7).
                _renamed = self.profiles[new_name]
                if _renamed.fingerprint_seed_value is None:
                    _renamed.fingerprint_seed_value = _renamed.fingerprint_seed

            profile = self.profiles[new_name]
            profile.name = new_name
            # The whole point of this ticket. `profile.proxy = new_proxy or None`
            # made absence and emptiness both CLEAR the assignment, so an edit
            # made for an unrelated reason (a rename, a note, a device type)
            # silently un-assigned the proxy and the profile launched DIRECT on
            # the operator's real IP. The launch guard could not object: it keys
            # on the assignment being present, and there was no longer one to
            # guard. Clearing is now something a caller SAYS (PROXY_NONE), never
            # something it does by omitting a value.
            profile.proxy = resolve_proxy_assignment(new_proxy, profile.proxy)
            if new_os is not None:
                profile.os_type = new_os
            if new_device_type is not None:
                profile.device_type = new_device_type
            if new_engine is not None:
                profile.engine = new_engine
            if new_resolution is not None:
                profile.resolution = new_resolution
            if new_search_engine is not None:
                profile.search_engine = new_search_engine
            # The last surviving instance of the shape the comment above ends,
            # twenty-four lines below it. `profile.bookmark_pool =
            # new_bookmark_pool or None` made absence and emptiness both CLEAR
            # the assignment, so an edit made for an unrelated reason (a note, a
            # search engine, a rename) silently discarded it. What that defeats
            # is recoverability, not a protection: delete_pool RECORDS the
            # profiles referencing a pool before clearing them, and computes
            # that list from THIS field — so a reference wiped beforehand meant
            # the trash entry named nobody and a restore returned a pool nothing
            # pointed at, the exact end-state delete_pool owns both halves to
            # make impossible. Clearing is now something a caller SAYS
            # (POOL_NONE), never something it does by omitting a value.
            profile.bookmark_pool = resolve_pool_assignment(
                new_bookmark_pool, profile.bookmark_pool
            )
            if new_bookmarks is not None:
                profile.bookmarks = new_bookmarks
            if new_tags is not None:
                profile.tags = new_tags
            if new_notes is not None:
                profile.notes = new_notes
            # The whole point of this ticket, and NOT the shape the two
            # resolvers above end. Absence already preserved here (the `is not
            # None` guard below), so "" was free to keep meaning CLEAR — and it
            # does, unchanged, pinned by test_update_can_clear_certificate.
            # What was missing is the state BETWEEN those two: a caller that
            # cannot account for the stored assignment. The dialog sent "" from
            # it, so an edit made for an unrelated reason turned "I could not
            # find this certificate" into "the operator chose none" — and took
            # the recorded cert_trust_status verdict with it as collateral.
            # That state now travels as CERT_UNCHANGED, which the resolver maps
            # back to the stored value, so the conditional below sees no change
            # and the verdict survives as a consequence.
            #
            # The guard stays: it is the reading that made absence safe here in
            # the first place, and the resolver agrees with it (None -> stored),
            # so the two cannot disagree about a caller that said nothing.
            if new_certificate is not None:
                _new_cert = resolve_cert_assignment(
                    new_certificate, profile.certificate
                )
                if _new_cert != profile.certificate:
                    # cert_trust_status records the outcome of the LAST CA trust
                    # attempt, which was made for the certificate being replaced
                    # here. Carried over it would render a verdict describing a
                    # different CA — including a stale "trusted", an affirmative
                    # clean bill of health for a certificate whose trust was
                    # never attempted. None is the field's own "never attempted".
                    # Conditional on purpose: update_profile runs on EVERY field
                    # edit, so an unconditional clear would discard a real
                    # verdict on a rename or a notes edit.
                    profile.cert_trust_status = None
                profile.certificate = _new_cert
            if new_ai_control is not None:
                profile.ai_control = new_ai_control

            self.save_profiles()
        logger.info("Updated profile: %s -> %s", original_name, new_name)
        return True

    def set_cookie_status(self, name: str, status: str) -> bool:
        with self._lock:
            if name not in self.profiles:
                return False
            self.profiles[name].cookie_import_status = status
            self.save_profiles()
        return True

    def set_cert_trust_status(self, name: str, status: str | None) -> bool:
        """Record the outcome of the last mTLS CA trust attempt for a profile.

        The Firefox CA import soft-fails (the launch proceeds untrusted), so
        this is the only thing that survives the session to tell the operator
        the assigned certificate is not actually trusted.

        ``status=None`` is a first-class value, not a caller's mistake: it is
        the field's own "no verdict for the certificate now assigned", the
        same value ``update_profile`` writes when a certificate is replaced.
        The launcher passes it at the START of a launch attempt to invalidate
        the previous session's verdict, so an older ``trusted`` cannot stand
        over a session that ran untrusted (PS-198). Widened rather than given a
        second method for the same reason ``set_last_launch_build`` takes
        ``build: str | None`` — one writer per field, and absence is a value it
        can express.
        """
        with self._lock:
            if name not in self.profiles:
                return False
            self.profiles[name].cert_trust_status = status
            self.save_profiles()
        return True

    def set_last_launch_build(
        self, name: str, engine: str, build: str | None
    ) -> bool:
        """Record the engine + build a profile was just launched under.

        Stores the pair VERBATIM — the two engines report different shapes
        (``firefox-NN`` vs a dotted Chromium version) and normalising them
        would lose which engine produced the string, which is the half that
        makes a later comparison interpretable at all.

        ``build`` may be None: that is the resolver's "could not read the
        installed build", and it is recorded as-is rather than being replaced
        with a guess. The engine is still worth recording without it.

        Returns False for an unknown profile (deleted mid-launch), which the
        caller treats as a non-event — recording provenance must never be able
        to fail a launch.
        """
        with self._lock:
            if name not in self.profiles:
                return False
            self.profiles[name].last_launch_engine = engine
            self.profiles[name].last_launch_build = build
            self.save_profiles()
        return True

    def assign_tag(self, names: list[str], tag: str) -> int:
        """Add a tag to each named profile (no duplicates). Returns count changed."""
        tag = tag.strip()
        if not tag:
            return 0
        with self._lock:
            changed = 0
            for name in names:
                p = self.profiles.get(name)
                if p is not None and tag not in p.tags:
                    p.tags.append(tag)
                    changed += 1
            if changed:
                self.save_profiles()
        return changed

    def remove_tag(self, tag: str) -> int:
        """Remove a tag from every profile that has it, case-insensitively, so a
        "Work"/"work" mix doesn't leave a variant behind after a chip ✕ — the
        chip cloud and the tag filter both treat tags case-insensitively (audit5
        LOW). Returns count changed."""
        t = tag.lower()
        with self._lock:
            changed = 0
            for p in self.profiles.values():
                kept = [x for x in p.tags if x.lower() != t]
                if len(kept) != len(p.tags):
                    p.tags = kept
                    changed += 1
            if changed:
                self.save_profiles()
        return changed

    def clear_proxy(self, proxy_name: str) -> int:
        """Drop a proxy reference from every profile that uses it, so a deleted
        proxy leaves no dangling name behind (which stranded the profile page).
        Returns count changed."""
        with self._lock:
            changed = 0
            for p in self.profiles.values():
                if p.proxy == proxy_name:
                    p.proxy = None
                    changed += 1
            if changed:
                self.save_profiles()
        return changed

    def clear_bookmark_pool(self, pool_name: str) -> int:
        """Drop a bookmark-pool reference from every profile that uses it, so a
        deleted pool leaves no dangling name behind (which made the profile launch
        with an empty toolbar). Mirror of clear_proxy. Returns count changed."""
        with self._lock:
            changed = 0
            for p in self.profiles.values():
                if p.bookmark_pool == pool_name:
                    p.bookmark_pool = None
                    changed += 1
            if changed:
                self.save_profiles()
        return changed

    def rename_bookmark_pool(self, old_name: str, new_name: str) -> int:
        """Propagate a pool rename to every profile referencing the old name, so
        the reference stays valid. Returns count changed."""
        if not new_name or new_name == old_name:
            return 0
        with self._lock:
            changed = 0
            for p in self.profiles.values():
                if p.bookmark_pool == old_name:
                    p.bookmark_pool = new_name
                    changed += 1
            if changed:
                self.save_profiles()
        return changed

    def set_ai_control(self, name: str, enabled: bool) -> bool:
        with self._lock:
            p = self.profiles.get(name)
            if p is None:
                return False
            p.ai_control = enabled
            self.save_profiles()
        return True

    def set_notes(self, name: str, notes: str) -> bool:
        """Save a profile's notes under the manager lock. The inline-edit path
        used to mutate the Profile and save_profiles() directly off the lock —
        the one write escaping the lock discipline, which could lose notes or
        clobber a concurrent rename (audit5 LOW). Returns True when changed."""
        with self._lock:
            p = self.profiles.get(name)
            if p is None or p.notes == notes:
                return False
            p.notes = notes
            self.save_profiles()
        return True

    def set_stop_hook(self, hook: "Callable[[str], object] | None") -> None:
        self._stop_hook = hook

    def _stop_if_running(self, name: str) -> None:
        if self._stop_hook is not None:
            try:
                self._stop_hook(name)
            except Exception as e:
                logger.warning("stop hook failed for %s: %s", name, e)

    def set_forget_identity_hook(
        self, hook: "Callable[[str], None] | None"
    ) -> None:
        """Register the callback fired when a profile NAME stops meaning what it
        meant — deleted, wiped, renamed away, or overwritten by an import.

        A SECOND hook rather than more work on ``_stop_hook``, because the two
        answer different questions and the difference is load-bearing. The stop
        hook means "this browser must not be running while I touch its data
        dir". This one means "the identity behind this name is gone; drop what
        you were remembering ABOUT it". Session state must survive the first and
        die on the second, so a caller that conflated them would either kill a
        marker meant to outlive a teardown or keep one attached to a name a
        different profile now holds.
        """
        self._forget_identity_hook = hook

    def _forget_identity(self, name: str) -> None:
        """Tell the launcher that ``name`` no longer identifies this profile.

        Best-effort and swallowing, exactly like ``_stop_if_running``: this runs
        on delete/wipe/rename paths that have already committed, and a failure to
        drop a cached verdict must never turn a successful delete into a failed
        one. The consequence of the swallow is a stale marker, which the next
        launch attempt supersedes; the consequence of raising would be a
        half-deleted profile.
        """
        if self._forget_identity_hook is not None:
            try:
                self._forget_identity_hook(name)
            except Exception as e:
                logger.warning("forget-identity hook failed for %s: %s", name, e)

    def delete_profile(self, name: str) -> bool:
        """Move a profile to the trash: it leaves the profile list and its data
        dir leaves the launchable area, but both are recoverable via
        restore_profile until retention expires or the operator deletes it
        permanently.

        The data dir is MOVED, never copied — a browser profile is large, and two
        copies of one identity would diverge. The move goes through the same
        validated choke point as every other profile filesystem op.
        """
        if name not in self.profiles:
            return False
        # Stop a live browser BEFORE moving its data dir (outside our lock — stop
        # is blocking): a data dir cannot be moved out from under a live engine
        # any more than it could be removed.
        self._stop_if_running(name)
        with self._lock:
            if name not in self.profiles:
                return False
            profile = self.profiles[name]
            token = self._trash().new_id()
            live_dir = self._data_path(name)
            parked = ""
            if pathlib.Path(live_dir).exists():
                dest = self._trash_data_path(token)
                try:
                    pathlib.Path(dest).parent.mkdir(parents=True, exist_ok=True)
                    pathlib.Path(live_dir).rename(dest)
                    parked = dest
                except OSError as e:
                    # Keep the profile completely unchanged rather than delete a
                    # data dir we could not park — a failed trash must not become
                    # the irreversible delete the trash exists to replace.
                    logger.warning(
                        "Could not move profile data dir for %r to the trash: %s",
                        name, e,
                    )
                    return False
            del self.profiles[name]
            self.save_profiles()
            self._trash().add(
                "profile",
                name,
                profile.to_dict(),
                entry_id=token,
                material_path=parked,
            )
            # The desktop entry carries the profile name in cleartext, so it goes
            # the moment the profile is trashed (audit6 LOW c) and comes back only
            # on restore — a trashed profile leaves no more trace than a deleted
            # one did.
            self._remove_window_entry(name)
            # The name is now free, so anything the launcher remembers under it
            # would be inherited by whatever claims it next. A refused-launch
            # verdict is the one that matters: left behind, a brand-new profile
            # that has never been clicked renders a red "refused" chip aged
            # against the current clock, i.e. "just now". _stop_if_running above
            # does NOT cover this — it only tears down LIVE-session facts, and a
            # refusal is deliberately built to outlive those.
            #
            # Fires for the trash move even though a restore can bring the
            # profile back, and that is the honest direction: a restored profile
            # carries NO verdict rather than a resurrected one. The refusal
            # describes an attempt against a proxy whose state may have moved on
            # entirely while the profile sat in the trash, and "no verdict yet"
            # is a state the card already renders truthfully — the next launch
            # re-establishes the real answer.
            self._forget_identity(name)
            logger.info("Moved profile to trash: %s", name)
            return True

    def restore_profile(self, entry) -> tuple[bool, str]:
        """Put a trashed profile back exactly as it was, under its own name.

        Refused when the name is taken — and refused rather than renamed on
        purpose. A profile's entire presented machine derives from crc32(name)
        (Profile.fingerprint_seed), so restoring under a different name would
        hand back the cookie jar attached to a DIFFERENT fingerprint: a silently
        changed identity. Free the name and restore again.
        """
        name = entry.name
        with self._lock:
            if name in self.profiles:
                return False, (
                    f"A profile named '{name}' already exists. A profile's "
                    "fingerprint is derived from its name, so restoring under a "
                    "different name would return its cookies under a different "
                    "identity. Rename or delete the existing profile, then "
                    "restore again."
                )
            live_dir = self._data_path(name)
            parked = entry.material_path
            if parked and pathlib.Path(parked).exists():
                if pathlib.Path(live_dir).exists():
                    return False, (
                        f"A data directory for '{name}' is already in place; "
                        "move it aside before restoring."
                    )
                try:
                    pathlib.Path(parked).rename(live_dir)
                except OSError as e:
                    return False, f"Could not restore the profile's data: {e}"
            try:
                # Intentionally EXEMPT from the coherence rules (coherence.py).
                # Restore replays a record that already existed, so it cannot
                # introduce incoherence — and refusing (or rewriting) here would
                # strand a trashed profile behind a conflict it did not create,
                # which is exactly what the "already-stored records are not
                # stranded" policy forbids. Do not "fix" this into a guard.
                #
                # RULE 3 (device_type), RE-EXAMINED AND DELIBERATELY LEFT
                # EXEMPT (PS-188). The exemption above was checked rather than
                # inherited: PS-188 asked whether symmetry with create/update
                # should override it, and the answer is NO, on TWO grounds that
                # both still hold.
                #
                #   1. The recorded reason is intact. Restore's whole contract
                #      is to put a record back "exactly as it was" — the
                #      docstring refuses even to RENAME, because a rename would
                #      hand back a cookie jar under a different fingerprint. A
                #      door bound that tightly to fidelity cannot also refuse or
                #      rewrite: refusing would strand a trashed profile behind a
                #      conflict the restore did not create, and a repair would
                #      break the "exactly as it was" contract outright.
                #   2. It is structurally incapable of introducing the fault.
                #      The payload is a record that ALREADY passed through the
                #      store; delete→restore is a round trip. Refusing here
                #      would refuse a pair the product itself accepted earlier.
                #
                # What DOES change is that the incoherence is no longer silent:
                # `Profile.device_type_incoherence` makes it askable of the
                # restored record, and the log line below makes the restore
                # itself observable. Exempt from the GUARD, not from the RECORD.
                self.profiles[name] = Profile(**entry.payload)
                _restored_incoherence = self.profiles[name].device_type_incoherence
                if _restored_incoherence is not None:
                    logger.warning(
                        "Restored profile %r carries an incoherent "
                        "os_type/device_type pair (%s/%s) and was replayed AS "
                        "IS — restore is exempt from the coherence rules by "
                        "design, because it returns a record that already "
                        "existed (PS-188). It is editable, not stranded. "
                        "Reason: %s",
                        name,
                        self.profiles[name].os_type,
                        self.profiles[name].device_type,
                        _restored_incoherence,
                    )
            except Exception as e:
                # Put the data dir back in the trash area so a failed restore
                # leaves the profile still recoverable, not half-moved.
                if parked and pathlib.Path(live_dir).exists():
                    try:
                        pathlib.Path(live_dir).rename(parked)
                    except OSError:
                        logger.exception(
                            "Could not re-park profile data for %r", name
                        )
                logger.exception("Could not rebuild trashed profile %r", name)
                return False, f"Could not restore the profile record: {e}"
            self.save_profiles()
        logger.info("Restored profile from trash: %s", name)
        return True, ""

    @staticmethod
    def destroy_trashed_material(material_path: str) -> None:
        """Destroy a trashed profile's parked data dir, for good. Called on
        permanent deletion, retention expiry and the panic wipe — the three
        paths that are genuinely irreversible."""
        if not material_path:
            return
        base = os.path.realpath(trash_data_root())
        target = os.path.realpath(material_path)
        # Never rmtree a path outside the park area, whatever a hand-edited
        # trash.json claims. The trash must not become a path by which a delete
        # escapes into arbitrary filesystem locations. Parked dirs live under
        # trash_data_root() only, so that — not DATA_DIR — is the containment
        # root; a LIVE profile's data dir is deliberately NOT deletable here.
        if os.path.commonpath([base, target]) != base or target == base:
            logger.warning(
                "Refusing to delete trashed material outside the trash area: %s",
                material_path,
            )
            return
        shutil.rmtree(material_path, ignore_errors=True)

    @staticmethod
    def _remove_window_entry(name: str) -> None:
        # Drop the Linux desktop entry (embeds the profile name in cleartext) so
        # a delete/wipe leaves no forensic trace (audit6 LOW c). No-op elsewhere.
        try:
            from ..browser.window_entry import remove_window_entry

            remove_window_entry(name)
        except Exception:
            pass

    def wipe_all_profiles(self) -> int:
        """Delete EVERY profile and its data in one pass — a panic wipe for an
        instant clean-out. Genuinely irreversible, unlike delete_profile: each
        profile's data dir is rmtree'd, profiles.json is emptied, and the trash
        is PURGED — the wipe bypasses the trash entirely and destroys whatever is
        already in it, so nothing survives it in a recoverable form. Returns how
        many profiles were removed. The UI gates this behind a typed
        confirmation whose "this cannot be undone" is therefore true."""
        for name in list(self.profiles.keys()):
            self._stop_if_running(name)
        with self._lock:
            names = list(self.profiles.keys())
            for name in names:
                shutil.rmtree(self._data_path(name), ignore_errors=True)
                self._remove_window_entry(name)
                # Every name is now free — same reasoning as delete_profile, and
                # a wipe frees them all at once, so a re-created profile taking
                # any of these names would otherwise inherit a verdict.
                self._forget_identity(name)
            self.profiles.clear()
            self.save_profiles()
        # Purge the trash as part of the wipe. A wipe that quietly parked fifty
        # logged-in profiles in a recoverable store would be the interface
        # claiming a protection the code does not deliver.
        self._purge_trash_for_wipe()
        # Clear the file log LAST, after every step above has finished logging —
        # the destruction path itself names profiles as it goes, so anything
        # cleared earlier would simply be re-written by the lines that follow.
        # LOG_DIR is a SIBLING of DATA_DIR, so nothing above reaches it: the
        # wipe destroys the profiles and leaves a file naming every one of them,
        # which the Activity Log then reads straight back out (src/ui/state.py
        # _load_recent_log_lines). SESSION_MARKER DECISION: truncating today's
        # file also destroys the marker the seed scans backwards for, so we
        # RE-EMIT it — without it the seed silently falls through to
        # raw[-limit:], which is correct only by accident. Re-emitting keeps the
        # anchor the marker exists to provide, so the post-wipe Activity Log
        # starts unambiguously at the wipe.
        self._clear_logs_for_wipe()
        # Destroy the quarantined store copies the corrupt-file guard leaves in
        # PERSONA_HOME. Same reasoning as the log clear one directory over: the
        # wipe purges the trash and then leaves a verbatim byte-copy of it —
        # carrying proxy creds, SSH passwords and .p12 passwords — sitting
        # beside the file it copied.
        self._clear_quarantine_files_for_wipe()
        # Logged AFTER the clear so the wipe leaves a record of itself in the
        # fresh file (this line names no profile). Unconditional, unlike the old
        # `if names`, because a wipe with nothing live still purges the trash.
        logger.info("Wiped all %d profiles", len(names))
        return len(names)

    def _clear_logs_for_wipe(self) -> None:
        """Destroy the profile names the file log holds in cleartext.

        Two shapes, because one file is special: the live FileHandler holds
        today's file open, so it is TRUNCATED IN PLACE (the handler keeps
        writing to the same open descriptor — tearing logging down and
        rebuilding it would risk losing the handler or minting a second file).
        Older `persona_*.log` day-files are unlinked outright; a long-lived
        install accumulates every name it has ever run, and a planted old
        day-file otherwise survives the wipe with its names intact.

        Best-effort and NON-FATAL, mirroring _purge_trash_for_wipe: a wipe that
        raised because a log file was locked (Windows) would be worse than the
        residue it failed to clear. Each file is handled independently so one
        locked file cannot stop the rest."""
        try:
            # Resolved at CALL time, not frozen at import: LOG_DIR tracks the
            # cwd under a relative PERSONA_LOG_DIR override, exactly as
            # trash_data_root() re-derives DATA_DIR's parent per call.
            from ...core import config

            log_dir = config.LOG_DIR
            # The files the live handler holds open — the ones we must truncate
            # rather than unlink, asked of logging itself instead of guessed
            # from today's date (a session running over midnight still holds
            # yesterday's file open).
            held_open = set()
            for handler in list(logging.getLogger("persona").handlers):
                filename = getattr(handler, "baseFilename", None)
                if filename:
                    held_open.add(os.path.realpath(filename))

            truncated_any = False
            # glob.escape ONLY the directory half. glob interprets
            # metacharacters across the WHOLE pattern, including the directory
            # portion; PERSONA_HOME is operator-overridable (config.py:15-16,
            # "e.g. for a portable layout") and LOG_DIR is derived from it
            # (LOG_DIR = _under_home("logs", ...)), so a home whose name
            # contains `[` would produce a pattern matching nothing and this
            # clear would be a SILENT no-op: no exception, no empty-result
            # branch, the wipe reports success while the log still names every
            # profile it just destroyed. `[` and `]` are legal on both POSIX and
            # Windows, and user-named portable directories are exactly where an
            # overridden home comes from.
            # The "persona_*.log" half must keep its metacharacters.
            pattern = os.path.join(glob.escape(log_dir), "persona_*.log")
            for path in glob.glob(pattern):
                try:
                    if os.path.realpath(path) in held_open:
                        os.truncate(path, 0)
                        truncated_any = True
                    else:
                        os.remove(path)
                except OSError:
                    logger.exception("Could not clear log file during the wipe")

            if truncated_any:
                # Imported HERE, beside its use, rather than at the top of this
                # try: the marker re-emit is the least important step, and an
                # import failure up there would skip ALL the clearing below it.
                from ...core.logging import emit_session_marker

                emit_session_marker(logging.getLogger("persona"))
        except Exception:
            logger.exception("Could not clear the logs during the wipe")

    def _clear_quarantine_files_for_wipe(self) -> None:
        """Destroy the quarantined store copies the corrupt-file guard leaves.

        When a store file fails to parse, StoreGuardMixin._quarantine_store_file
        renames it to `<path>.corrupt-<ts>` so the next save writes a fresh file
        beside the original instead of over it (core/settings.py hand-rolls the
        same thing). That guard is CORRECT and is not touched here — leaving the
        unreadable file in place is recoverable, overwriting it is not.

        What was missing is that nothing ever removed what it leaves. The
        quarantined file is a verbatim byte-copy of the store, and three of the
        guarded stores — proxies.json, ssh_hosts.json, certificates.json — hold
        SOCKS5 credentials, SSH passwords/passphrases and .p12 bundle passwords
        (which is why atomic.py writes exactly those `private=True`). trash.json
        carries all three kinds verbatim in its payloads. The suffix embeds
        int(time.time()), so every corruption event mints a UNIQUE name and they
        accumulate one per event, forever.

        The wipe destroys every profile, purges the trash and clears the logs,
        so it is the one path with no recoverability requirement — nothing it
        clears is owed to anything. Left alone, a wipe that claims everything is
        gone would be false while a credential-bearing copy sat beside the file
        it copied: trash/store.py states outright that the trash is "never a
        second, less-guarded copy of the operator's identity", and a surviving
        trash.json.corrupt-<ts> is exactly that second copy. This is the SAME
        sweep as _clear_logs_for_wipe one directory over: that one reaches into
        LOG_DIR to clear profile NAMES, and this one globs the home it is
        standing in to clear proxy PASSWORDS.

        SCOPE, MEASURED — do not widen this into a claim the wipe does not make.
        The wipe does NOT destroy the LIVE credential stores (proxies.json,
        ssh_hosts.json, certificates.json); they survive it with their contents
        intact, which is existing behaviour this sweep does not change. Only the
        quarantined COPIES are cleared here. Beware the confound when probing
        this: quarantining RENAMES the live file away, so a probe that corrupts
        a store first will see it "missing" after the wipe for a reason that has
        nothing to do with the wipe.

        DELIBERATELY NOT delete_profile or a permanent record delete: those are
        recoverable by design, and a quarantined file may be the operator's only
        copy of data they have not recovered yet. Destroying it on a RECOVERABLE
        gesture would defeat the guard's stated purpose.

        BOUND: store paths are env-overridable (PERSONA_PROXIES_FILE and
        friends) and ProxyStore takes an explicit `path=`. A store relocated
        OUTSIDE the home quarantines outside the home, where this glob does not
        reach. This is the default-path fix; chasing every override is a
        separate question.

        Best-effort and NON-FATAL, mirroring _clear_logs_for_wipe and
        _purge_trash_for_wipe: a wipe that raised because one file was locked
        (Windows) would be worse than the residue it failed to clear. Each file
        is handled independently so one locked file cannot stop the rest."""
        try:
            # Resolved at CALL time, not frozen at import, exactly as
            # _clear_logs_for_wipe resolves LOG_DIR — the specs monkeypatch it.
            from ...core import config

            # glob.escape ONLY the directory half. glob interprets
            # metacharacters across the WHOLE pattern, including the directory
            # portion, and PERSONA_HOME is operator-overridable (config.py:15-16,
            # "e.g. for a portable layout") — a home whose name contains `[`
            # would produce a pattern matching nothing, and the sweep would be a
            # SILENT no-op: no exception, no empty-result branch, the wipe
            # reports success while the credential-bearing copy survives. `[` and
            # `]` are legal on both POSIX and Windows, and user-named portable
            # directories are exactly where an overridden home comes from.
            # The "*.corrupt-*" half must keep its metacharacters.
            pattern = os.path.join(glob.escape(config.PERSONA_HOME), "*.corrupt-*")
            for path in glob.glob(pattern):
                try:
                    os.remove(path)
                except OSError:
                    logger.exception(
                        "Could not clear a quarantined store file during the wipe"
                    )
        except Exception:
            logger.exception(
                "Could not clear the quarantined store files during the wipe"
            )

    def _purge_trash_for_wipe(self) -> None:
        try:
            from ..trash.service import destroy_entry

            for entry in self._trash().clear():
                destroy_entry(entry, self)
        except Exception:
            logger.exception("Could not purge the trash during the wipe")

    def list_profiles(self) -> list[Profile]:
        with self._lock:
            return list(self.profiles.values())

    def export_profile(
        self,
        name: str,
        export_path: str,
        include_data: bool = True,
    ) -> tuple[bool, str]:
        if name not in self.profiles:
            return False, "Profile not found"
        # Stop a live browser before copying its data dir — the Cookies SQLite
        # (+ -wal/-journal) is copied live otherwise, giving a torn/corrupt copy
        # in the export. delete/update already stop first (audit5 LOW).
        if include_data:
            self._stop_if_running(name)
        return export_to_zip(
            self.profiles[name],
            self._data_path(name),
            export_path,
            include_data,
        )

    def import_profile(
        self,
        zip_path: str,
        overwrite: bool = False,
    ) -> tuple[bool, str]:
        # Check the name collision BEFORE extracting — import_from_zip writes the
        # archive's data over DATA_DIR/<name>, so doing it first (as before) let a
        # non-overwrite import clobber an existing profile's data and only THEN
        # report "already exists". Peek the name, gate under the lock, then extract.
        peek_ok, peeked = peek_profile_name(zip_path)
        if not peek_ok:
            return False, peeked
        with self._lock:
            if peeked in self.profiles and not overwrite:
                return False, f"Profile '{peeked}' already exists"

            success, result = import_from_zip(zip_path, DATA_DIR)

            # import_from_zip's contract is (True, Profile) | (False, message),
            # so `result` is a union until it is narrowed. Narrowed ONCE, above
            # both branches, and by an isinstance CHECK rather than an assert: a
            # contract breach then reports as an ordinary import failure the
            # operator can read, instead of raising out of a door whose whole
            # purpose is to recover a profile without stranding it.
            if not success:
                return False, result if isinstance(result, str) else "Import failed"
            if not isinstance(result, Profile):
                return False, "Import failed: the archive yielded no profile record"

            profile = result
            # Import is a door into the model, so it crosses the coherence rules
            # (coherence.py) like every other one — but it crosses them by
            # NORMALISING, not by refusing.
            #
            # The choice, stated deliberately: an archive is closer to an
            # already-stored legacy record than to a fresh REST request. It was
            # written by an older build, possibly before these rules existed, and
            # the operator importing it is recovering a profile rather than
            # composing one. Refusing would make those archives permanently
            # unimportable — the stranding the ticket forbids — and it would
            # refuse at the one moment the operator has no way to edit the record
            # into shape. So the incoherent pair is reconciled the same way a
            # stored one is at launch: fall back to the engine that HONORS
            # os_type, which makes the imported record match the machine it will
            # actually present. The PAIR lands coherent; nothing is lost but a
            # claim that was never true.
            #
            # Rule 3 (device_type) is NOT reconciled here. coherent_engine
            # answers "which engine?", and Rule 3 has no engine remedy — a
            # windows + mobile profile is contradictory on chromium and on
            # firefox alike — so an imported windows + mobile archive lands as a
            # tolerated already-stored record: editable, never stranded, exactly
            # like a legacy record predating these rules. Reconciling it would
            # mean rewriting a field at launch, which is process.py's job and
            # not this door's. See coherence.device_type_error.
            resolved = coherent_engine(profile.os_type, profile.engine)
            if resolved != normalize_engine(profile.engine):
                logger.warning(
                    "Imported profile %r carried an incoherent os_type/engine "
                    "pair (%s/%s); stored as %s. Reason: %s",
                    profile.name,
                    profile.os_type,
                    profile.engine,
                    resolved,
                    coherence_error(profile.os_type, profile.engine),
                )
            profile.engine = resolved

            # RULE 3 (device_type), DECIDED: ACCEPT AND RECORD (PS-188).
            #
            # The three options were refuse / normalise / accept-and-record.
            #
            # REFUSE is wrong here for the reason this door already refuses to
            # refuse the PAIR: an archive is a RECOVERY, not an authoring act,
            # and a door that refuses turns a recoverable backup into a
            # permanently unimportable one at the one moment the operator has no
            # way to edit the record into shape.
            #
            # NORMALISE has no honest form. coherent_engine reconciles the pair
            # because there IS an engine that satisfies it; Rule 3 has no such
            # remedy. Coercing device_type to "desktop" or os_type to a mobile
            # family both rewrite a field the operator did not ask to change,
            # and WHICH of the two is the lie is not knowable from the record.
            #
            # So the pair lands coherent and the triple lands VERBATIM — which
            # is what this door already did. What was actually wrong is that it
            # did so SILENTLY: the imported record was indistinguishable from a
            # coherent one, so nothing downstream and nobody upstream could tell.
            # It is now recorded. `Profile.device_type_incoherence` makes the
            # verdict askable of the stored record from any door at any time
            # (it is Rule 3's own message, computed, never a stored flag), and
            # the log line below makes the import itself observable.
            #
            # What such a record DOES at launch, measured rather than assumed —
            # this is the blast radius that justifies recording it at all:
            #   * chromium: is_mobile is True, so an Android device preset drives
            #     the UA/screen/touch, while the GPU POOL ARM and the voice
            #     roster are still built from os_type ("windows") — a D3D11
            #     renderer and Microsoft desktop voices under an Android UA.
            #     (PS-161 round 4 fixed the AUTHORSHIP half of the GPU vector —
            #     engine_platform is one computation over both fields — so the
            #     host-rasteriser leak is closed. The pool ARM is a separate
            #     question and still reads os_type alone.)
            #   * firefox: the launch path reads NEITHER field (#211), so
            #     device_type is dropped entirely and the profile presents a
            #     desktop Windows machine while its record claims a phone.
            # Reconciling either belongs on the launch path, not at this door.
            if profile.device_type_incoherence is not None:
                logger.warning(
                    "Imported profile %r carries an incoherent "
                    "os_type/device_type pair (%s/%s) and was stored AS IS — "
                    "import recovers records, it does not refuse or rewrite "
                    "them (PS-188). It is editable, not stranded. Reason: %s",
                    profile.name,
                    profile.os_type,
                    profile.device_type,
                    profile.device_type_incoherence,
                )

            # The archive chose this profile's fingerprint seed. import_from_zip
            # has already thrown out anything that is not a real crc32 integer,
            # but a WELL-FORMED value can still be the wrong one: it may be the
            # exact seed a profile already on this machine is presenting. That
            # happens innocently (export 'client-alpha', rename it to
            # 'client-alpha-old', re-import the archive — both now claim
            # crc32('client-alpha')) and deliberately (a shared archive
            # hand-edited to a victim profile's seed, pinning the import onto
            # its resolution, device preset, touch points and --fingerprint=).
            # Either way the result is two profiles presenting one machine.
            #
            # Re-mint rather than refuse, matching the normalise-don't-refuse
            # choice made for the coherence pair just above: an operator
            # recovering a profile should not be stranded by a clash they cannot
            # see or edit. The archive's seed is KEPT whenever it is free, so the
            # ordinary "move my profile to another machine" import carries its
            # identity across untouched — which is the whole point of exporting
            # one. `overwrite` re-imports over the profile's own record, so its
            # seed is excluded from the reserved set; otherwise a profile would
            # collide with itself and get needlessly re-rolled.
            reserved = self._reserved_seeds()
            if overwrite and peeked in self.profiles:
                reserved.discard(self.profiles[peeked].fingerprint_seed)
            if profile.fingerprint_seed in reserved:
                previous = profile.fingerprint_seed
                profile.fingerprint_seed_value = mint_fingerprint_seed(
                    profile.name, reserved
                )
                logger.warning(
                    "Imported profile %r carried a fingerprint seed (%s) already "
                    "held by another profile; minted %s instead so the two do "
                    "not present the same machine.",
                    profile.name, previous, profile.fingerprint_seed_value,
                )

            # An overwrite REPLACES the profile that held this name with a
            # different record — the same identity-is-gone event as a delete,
            # reached by a different door. Any verdict held under the name
            # belongs to the profile being replaced, not to the one arriving, so
            # it goes with it. Guarded on `overwrite` because a fresh import
            # (the non-overwrite arm) took a name that was already free, and
            # there is nothing to inherit.
            if overwrite:
                self._forget_identity(profile.name)
            self.profiles[profile.name] = profile
            self.save_profiles()
        logger.info("Registered imported profile: %s", profile.name)
        return True, profile.name
