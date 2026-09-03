import json
import pathlib
import threading
import time
from collections.abc import Callable

from ...core.config import PROXIES_FILE
from ...core.logging import get_logger
from ...models.proxy import Proxy
from ...utils.atomic import atomic_write_json
from ...utils.proxy_parser import parse_proxy_server
from ...utils.store_guard import StoreGuardMixin
from ...utils.trashable import TrashableMixin, restore_kwargs
from .tz_names import is_declarable_zone

logger = get_logger("proxy.store")


class ProxyStore(StoreGuardMixin, TrashableMixin):
    _guard_logger = logger
    _guard_noun_plural = "proxies"
    _guard_noun_singular = "proxy"

    def __init__(
        self,
        path: str = PROXIES_FILE,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._path = path
        self._now = now
        self.proxies: dict[str, Proxy] = {}
        self._save_blocked = False
        # The container-shared store is mutated from the UI thread, the uvicorn
        # API thread, and proxy-check daemon threads. Serialize every read/write
        # so a mutation can't race a _save iterating self.proxies (RLock so a
        # mutator can call _save while holding it).
        self._lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        if not pathlib.Path(self._path).exists():
            return
        try:
            with pathlib.Path(self._path).open(encoding="utf-8") as f:
                data = json.load(f)
            skipped = 0
            for name, p in data.items():
                # One malformed record must not abort the whole load — the next
                # save would overwrite proxies.json with only what parsed,
                # discarding every proxy's SOCKS5 creds after it.
                try:
                    self.proxies[name] = Proxy(
                        name=p["name"],
                        url=p["url"],
                        rotate_url=p.get("rotate_url", ""),
                        country_code=p.get("country_code", ""),
                        country_name=p.get("country_name", ""),
                        last_ip=p.get("last_ip", ""),
                        timezone=p.get("timezone", ""),
                        lat=p.get("lat"),
                        lon=p.get("lon"),
                        checked_at=p.get("checked_at", 0.0),
                        last_check_ok=p.get("last_check_ok"),
                        # Absent in a pre-PS-274 proxies.json, which is exactly
                        # what the .get defaults handle: an old file upgrades
                        # with no migration, and a new file read by an old
                        # build is ignored key-by-key rather than rejected.
                        manual_timezone=p.get("manual_timezone", ""),
                        manual_timezone_country=p.get(
                            "manual_timezone_country", ""
                        ),
                    )
                except Exception:
                    skipped += 1
                    logger.exception("Skipping malformed proxy %r", name)
            if skipped:
                logger.warning("Skipped %d malformed proxy record(s)", skipped)
            logger.info("Loaded %d proxies", len(self.proxies))
        except Exception as e:
            logger.exception("Error loading proxies: %s", e)
            self._quarantine_proxies_file()

    def _store_path(self) -> str:
        return self._path

    def _quarantine_proxies_file(self) -> None:
        # An unreadable proxies.json still holds every proxy the user saved with
        # its SOCKS5 creds; move it aside so the next _save() can't overwrite it
        # with the empty in-memory dict.
        self._quarantine_store_file()

    def _save(self) -> None:
        if self._save_is_blocked():
            return
        try:
            # Proxy URLs carry SOCKS5 user:pass, so the file is written 0600 and
            # atomically (a crash mid-save must not corrupt every saved proxy).
            atomic_write_json(
                self._path,
                {name: p.to_dict() for name, p in self.proxies.items()},
                private=True,
            )
        except Exception as e:
            logger.exception("Error saving proxies: %s", e)

    def list_proxies(self) -> list[Proxy]:
        with self._lock:
            return list(self.proxies.values())

    def names(self) -> list[str]:
        with self._lock:
            return list(self.proxies.keys())

    def get(self, name: str) -> Proxy | None:
        with self._lock:
            return self.proxies.get(name)

    def url_for(self, name: str | None) -> str | None:
        if not name:
            return None
        with self._lock:
            proxy = self.proxies.get(name)
            return proxy.url if proxy else None

    def resolve(self, ref: str | None) -> str | None:
        """Resolve a profile's proxy reference to a usable proxy URL.

        ``ref`` is a stored proxy name. Falls back to treating ``ref`` as a
        raw proxy URL when no stored proxy matches, so profiles created before
        named proxies existed still launch.
        """
        if not ref:
            return None
        with self._lock:
            proxy = self.proxies.get(ref)
            if proxy:
                # Gate a NAMED proxy on parseability too, not just the raw-url
                # fallback below. A stored url with no port ("socks5://1.2.3.4")
                # or a bare host is truthy but unusable: chromium's parser returns
                # None for it, so --proxy-server is dropped AND the anti-leak block
                # is skipped → DIRECT clearnet on a profile WITH a proxy. Returning
                # None here makes the launch guard fail CLOSED (audit7 #1).
                return proxy.url if parse_proxy_server(proxy.url) else None
        return ref if parse_proxy_server(ref) else None

    def add(self, name: str, url: str, rotate_url: str = "") -> bool:
        with self._lock:
            if not name or name in self.proxies:
                return False
            self.proxies[name] = Proxy(name=name, url=url, rotate_url=rotate_url)
            self._save()
        logger.info("Added proxy: %s", name)
        return True

    def update(
        self,
        original_name: str,
        new_name: str,
        new_url: str,
        new_rotate_url: str = "",
    ) -> bool:
        with self._lock:
            if original_name not in self.proxies:
                return False
            if new_name != original_name and new_name in self.proxies:
                return False
            old = self.proxies.pop(original_name)
            keep_geo = new_url == old.url
            self.proxies[new_name] = Proxy(
                name=new_name,
                url=new_url,
                rotate_url=new_rotate_url,
                country_code=old.country_code if keep_geo else "",
                country_name=old.country_name if keep_geo else "",
                last_ip=old.last_ip if keep_geo else "",
                timezone=old.timezone if keep_geo else "",
                lat=old.lat if keep_geo else None,
                lon=old.lon if keep_geo else None,
                checked_at=old.checked_at if keep_geo else 0.0,
                last_check_ok=old.last_check_ok if keep_geo else None,
                # Rides `keep_geo` with the six measured fields, and for the
                # same reason: a rename or a rotate-url edit leaves the exit
                # exactly where it was, so the declaration still describes it;
                # a URL change moves the exit, so it does not. This constructor
                # is hand-enumerated, so a field omitted here is silently
                # dropped on every rename — see app.py:1389 for the same shape.
                manual_timezone=old.manual_timezone if keep_geo else "",
                manual_timezone_country=(
                    old.manual_timezone_country if keep_geo else ""
                ),
            )
            self._save()
        logger.info("Updated proxy: %s -> %s", original_name, new_name)
        return True

    def set_url(self, name: str, url: str) -> bool:
        """Change a proxy's URL in place, keeping the rotate settings.

        Used after session-token rotation. When the URL actually MOVES, the
        recorded geography is invalidated here — all six geo fields plus the
        check bookkeeping — exactly as ``update()`` does via its ``keep_geo``
        term. A URL that is unchanged keeps everything, which is what makes
        this safe to call unconditionally.

        WHY THIS IS THE MODEL'S JOB AND NOT THE CALLER'S. This used to keep the
        geo, and justified it with a promise about ONE caller: "the follow-up
        check refreshes the geo fields anyway". That is a property of
        ``app.py``'s ``_rotate_proxy``, not of the record, and it does not
        survive contact with reality in two reachable ways:

        - IN FLIGHT. ``_save()`` happens here, the check finishes ~10s later
          (``PROXY_CHECK_TIMEOUT``), and nothing gates a launch on a check that
          is in flight.
        - DURABLE, the sharper one. A crash, kill or quit between the two
          leaves the stale affirmative ON DISK. Re-opening the file with a
          fresh ``ProxyStore`` — as a restart does — reads back the NEW url
          beside the OLD exit's country, timezone, coordinates and a
          ``last_check_ok`` of True. Nothing later re-examines it.

        The result was a record asserting the previous exit's geography under a
        verdict that still read "verified", so a proxied profile declared a
        location that disagreed with the exit actually carrying its traffic.
        Neither shipped refusal covered it: the disproven-geo guard fires on
        ``proxy_indicator_state == "failed"`` and this state read "verified",
        and the no-geo refusal requires the fields to be EMPTY when they were
        populated with the previous exit's values. ``proxy_indicator_state``
        reads only ``last_check_ok`` / ``checked_at``, so a URL change is
        invisible to it by construction — which is why the invalidation has to
        happen at the WRITE, here, rather than being detected downstream.

        WHY ZEROING RATHER THAN ONLY CLEARING THE VERDICT. The alternative was
        to keep the geography and clear only ``last_check_ok`` / ``checked_at``,
        letting the launch path refuse via its unverified route. Executed end to
        end, that is a NO-OP on the launch outcome: the record moves to
        "unverified", but ``_proxy_timezone``'s first branch returns
        ``proxy.timezone`` whenever it is non-empty, and the tri-state
        unverified-with-geography row is deliberately left LAUNCHING (see the
        NOTE in ``launch_policy.py``). The profile would go on declaring the old
        exit's zone. Zeroing is what actually reaches the observer, and it keeps
        this method structurally consistent with its sibling ``update()``.
        """
        with self._lock:
            proxy = self.proxies.get(name)
            if proxy is None:
                return False
            if url != proxy.url:
                # The exit moved: nothing recorded about the OLD one describes
                # the new one. Mirrors update()'s `keep_geo` field-for-field.
                proxy.country_code = ""
                proxy.country_name = ""
                proxy.last_ip = ""
                proxy.timezone = ""
                proxy.lat = None
                proxy.lon = None
                proxy.checked_at = 0.0
                proxy.last_check_ok = None
                # INVALIDATED with the six, not preserved beside them. A
                # declaration is a claim about ONE exit; a rotation replaces
                # the exit, so keeping it would assert the previous exit's
                # clock under a new URL — the durable-stale-affirmative shape
                # this method's docstring was written about. The country gate
                # would usually retire it anyway once a check ran, but a
                # rotation followed by a crash leaves nothing to run the gate,
                # so it is cleared at the WRITE like everything else here.
                proxy.manual_timezone = ""
                proxy.manual_timezone_country = ""
            proxy.url = url
            self._save()
        return True

    def invalidate_geo(self, name: str) -> bool:
        """Drop everything recorded about the exit, unconditionally.

        The UNCONDITIONAL counterpart to ``set_url``'s invalidation, for the
        callers that move the exit WITHOUT moving the URL. It zeroes the same
        eight fields ``set_url`` does (``store.py`` above) and ``update()`` does
        via ``keep_geo`` — the six geo fields plus the two bookkeeping fields —
        and it persists, so an interrupted operation leaves no stale
        affirmative on disk.

        WHY THIS EXISTS BESIDE ``set_url`` RATHER THAN INSIDE IT. ``set_url``
        gates its invalidation on ``url != proxy.url``, and that gate is
        correct FOR THAT METHOD: a URL write that changes nothing must change
        nothing (pinned by ``test_set_url_without_a_url_change_keeps_everything``).
        But the gate is the wrong SIGNAL for a rotation. A rotating/backconnect
        proxy's URL is CONSTANT BY DESIGN — a new exit IP per connection at the
        same endpoint — and two of ``ProxyService.rotate_proxy``'s three arms
        (the provider's rotate endpoint, and the plain fresh connection) return
        the URL unchanged for exactly that reason. So the invalidation the
        rotate path needs is conditioned on the one signal guaranteed not to
        move for the proxy type the rotate button exists to serve.

        The rotate path therefore calls THIS, before it issues the rotation:
        from the moment the exit is asked to move, nothing on file describes
        it. The follow-up check refills the record via ``mark_checked``, so a
        rotation that completes normally ends "verified" exactly as before;
        one that is interrupted, or that is raced by a launch, now reads
        "unverified" with no geography instead of asserting the PREVIOUS exit's
        country, timezone and coordinates under a verdict of "verified".

        ORDERING WARNING FOR CALLERS. ``get()`` hands back the LIVE ``Proxy``
        object, not a copy, so a caller holding one sees these fields emptied
        under it. Read anything you need from the record (``last_ip``, ``url``)
        BEFORE calling this.
        """
        with self._lock:
            proxy = self.proxies.get(name)
            if proxy is None:
                return False
            proxy.country_code = ""
            proxy.country_name = ""
            proxy.last_ip = ""
            proxy.timezone = ""
            proxy.lat = None
            proxy.lon = None
            proxy.checked_at = 0.0
            proxy.last_check_ok = None
            self._save()
        return True

    def mark_checked(
        self,
        name: str,
        country_code: str,
        country_name: str,
        ip: str = "",
        timezone: str = "",
        lat: float | None = None,
        lon: float | None = None,
    ) -> bool:
        with self._lock:
            proxy = self.proxies.get(name)
            if proxy is None:
                return False
            proxy.country_code = country_code
            proxy.country_name = country_name
            proxy.last_ip = ip
            proxy.timezone = timezone
            proxy.lat = lat
            proxy.lon = lon
            proxy.checked_at = self._now()
            proxy.last_check_ok = True
            self._save()
        return True

    def set_manual_timezone(self, name: str, zone: str) -> tuple[bool, str]:
        """Record the zone the OPERATOR declares for this proxy's exit.

        The declaration is stored WITH the country it was made for (the proxy's
        currently recorded ``country_code``), because that pair is what the
        launch path gates on: the zone applies while the exit is still in that
        country and retires itself when it moves. Storing the zone alone would
        make a rotated backconnect exit declare its predecessor's clock.

        VALIDATED HERE, at the write, rather than at the launch. The value is
        handed to a browser engine as fact, so a bad one must never reach disk;
        validating at the read would leave a stored value that looks fine in
        the dialog and refuses at every launch. ``is_declarable_zone`` is a set
        membership test against the VENDORED name list (``tz_names.py``), which
        is why the accepted set is byte-identical on Windows, macOS and Linux
        and why no OS timezone database is consulted.

        An EMPTY zone clears the declaration — that is how an operator takes it
        back — and clears the country with it so no half-record survives.

        ⚠️ RE-DECLARING A ZONE THAT IS ALREADY LIVE IS A NO-OP, NOT A RE-STAMP,
        and that is the load-bearing half of this method rather than an
        optimisation. The country gate is a READ-side guard: it retires a
        declaration when the exit moves. Re-stamping
        ``manual_timezone_country`` from the CURRENT country on every call
        would let any caller re-arm a declaration the gate had already retired
        — exactly the country/clock contradiction this design was chosen to
        make unrepresentable, a zone declared for an RO exit re-affirmed for a
        CZ one. It is reachable without anyone typing anything: the proxy
        dialog prefills its field from the stored value, so a bare ``[ save ]``
        re-submits it. The dialog now skips the call when the operator did not
        touch the field, and this guard is why a SECOND caller cannot skip the
        rule either (the same reasoning as the validation being duplicated at
        the dialog).

        The identical string re-submitted while the declaration is RETIRED is
        REFUSED with a sentence rather than silently ignored: a silent success
        that changes nothing is the shape of defect this method already carries
        one warning about, so the operator is told what is on file, which
        country it was declared for, and how to re-declare it (clear, save,
        enter it again). That two-gesture path is the narrow residue of the
        no-re-stamp rule, and it is fail-closed — the profile refuses to launch
        in the meantime, which is the correct answer.

        ⚠️ A DECLARATION NEEDS A CHECKED EXIT COUNTRY, and is REFUSED without
        one. Storing it with an empty country was the fail-closed direction and
        it was also SILENT AND PERMANENT: the operator typed a valid zone, got
        a success and a closed dialog, and the declaration never activated —
        not even after a later check found the country, because nothing re-binds
        it (``mark_checked`` writes the six measured fields and is deliberately
        untouched by this feature). Adding a proxy and filling in the whole form
        before pressing ``[ check ]`` is an ordinary sequence, not an edge case,
        so it gets a sentence instead of a shrug.

        Returns ``(ok, error)``. The error is the operator-facing sentence; it
        is empty on success.
        """
        zone = (zone or "").strip()
        with self._lock:
            proxy = self.proxies.get(name)
            if proxy is None:
                return False, f"No proxy named {name!r}."
            if not zone:
                proxy.manual_timezone = ""
                proxy.manual_timezone_country = ""
                self._save()
                return True, ""
            country = (proxy.country_code or "").upper()
            declared_for = (proxy.manual_timezone_country or "").upper()
            if zone == proxy.manual_timezone and declared_for:
                if declared_for == country:
                    # Already live for this exit. Nothing is written and
                    # nothing is re-stamped — see the warning above.
                    return True, ""
                return False, (
                    f"{zone!r} is already on file for this proxy, declared for "
                    f"the {declared_for} exit, and the exit is now in "
                    f"{country or 'an unknown country'}. A timezone is declared "
                    "FOR a country, so it is not re-used automatically: clear "
                    "the field and save, then enter the zone for the current "
                    "exit."
                )
            if not is_declarable_zone(zone):
                return False, (
                    f"{zone!r} is not a timezone name. Enter an IANA zone in "
                    "Region/City form, e.g. 'Europe/Bucharest' — an "
                    "abbreviation like 'EET' is not accepted because the "
                    "browser engine is given this value as a real zone."
                )
            if not country:
                return False, (
                    "This proxy has no checked exit country yet, and a "
                    "timezone is declared FOR a country — press [ check ] "
                    "first, then declare the zone."
                )
            proxy.manual_timezone = zone
            proxy.manual_timezone_country = country
            self._save()
        logger.info("Declared timezone for proxy %s: %s", name, zone)
        return True, ""

    def mark_check_failed(self, name: str) -> bool:
        with self._lock:
            proxy = self.proxies.get(name)
            if proxy is None:
                return False
            proxy.checked_at = self._now()
            proxy.last_check_ok = False
            self._save()
        return True

    def delete(self, name: str) -> bool:
        """Move a proxy to the trash. Its credentials go with it — trash.json is
        written 0600 inside PERSONA_HOME, exactly like proxies.json — so trashing
        a proxy does NOT remove its secret material from disk. Permanent deletion
        does; the interface says so rather than implying otherwise.

        This method owns the WHOLE operation, including dropping the proxy from
        every profile that used it. That is deliberate: the reference has to be
        RECORDED before it is cleared, and when the two halves lived in the
        caller the UI did them in the opposite order (clear_proxy first), so the
        store recorded no referencing profiles at all and a restore silently
        returned a proxy nothing pointed at. Owning both here makes that
        ordering impossible to get wrong from a new lane.
        """
        with self._lock:
            if name not in self.proxies:
                return False
            proxy = self.proxies.pop(name)
            self._save()
        # RECORD the referencing profiles first, then clear them — never the
        # reverse. A deleted proxy left lingering as a name stranded the profile
        # page, so the reference must go; recording it here is the only thing
        # that makes restore able to put it back.
        refs = []
        pm = self._profile_manager
        if pm is not None:
            refs = [p.name for p in pm.list_profiles() if p.proxy == name]
            pm.clear_proxy(name)
        self._trash().add(
            "proxy", name, {"proxy": proxy.to_dict(), "profiles": refs}
        )
        logger.info("Moved proxy to trash: %s", name)
        return True

    def restore_proxy(self, entry) -> tuple[bool, str]:
        """Put a trashed proxy back with its credentials and geo, re-pointing
        the profiles that used it."""
        name = entry.name
        with self._lock:
            if name in self.proxies:
                return False, (
                    f"A proxy named '{name}' already exists. Rename or delete "
                    "it, then restore again."
                )
            d = entry.payload.get("proxy") or {}
            # Built by reflection over Proxy's own fields: a field added to the
            # dataclass is written into the payload for free by asdict() and
            # now comes back out for free too. `url` has no dataclass default,
            # so it keeps the enumerated form's "" fallback rather than
            # raising on a payload that never carried it.
            self.proxies[name] = Proxy(
                **restore_kwargs(Proxy, d, name, defaults={"url": ""})
            )
            self._save()
        pm = self._profile_manager
        if pm is not None:
            for profile_name in entry.payload.get("profiles") or []:
                profile = pm.profiles.get(profile_name)
                # Only re-point a profile that is still unassigned: the operator
                # may have picked another proxy in the meantime, and a restore
                # must not silently change a live profile's exit IP.
                if profile is not None and profile.proxy is None:
                    profile.proxy = name
            pm.save_profiles()
        logger.info("Restored proxy from trash: %s", name)
        return True, ""
