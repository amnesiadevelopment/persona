"""PS-272 — the trash's 30-day clock gets a signal on the rail.

The trash's retention window is enforced at app start and, until this ticket,
was announced NOWHERE the operator would look before it fired: the only
countdown lives on the trash page, which is the page you open once you already
know something is there. A rail with three key bundles about to be destroyed
was pixel-identical to an empty one.

Three properties are checked here, and the third is the one that matters:

1. The query that answers "what is about to go" is READ-ONLY, asserted on the
   BYTES ON DISK rather than on the returned list. A question the rail asks on
   every repaint must not be able to age, remove or destroy anything.
2. The retention floor is untouched. ``RETENTION_DAYS`` is still 30 and
   ``purge_expired`` still behaves exactly as it did — this ticket adds a
   signal, it does not weaken or extend the floor.
3. The signal is CONDITIONAL. It appears when something is inside the window
   and is ABSENT otherwise — the ``_status_needs_reveal`` rule: an affordance
   on a line that is already whole is noise, and a badge that is always lit
   stops meaning "act now".

WHAT IS ASSERTED HERE AND WHAT IS DRIVEN ELSEWHERE. These are structural
checks over the built control tree. The RENDERED nav signal — a real badge
painted in a real served app, appearing with a near-expiry entry and absent
without one — is driven live in ``tests/ui_driver/live_ps272.py``, including
the falsification pass that proves the check can go red.
"""
import json
import os
import pathlib

import flet as ft
import pytest

from src.services.bookmark.store import BookmarkStore
from src.services.cert.store import CertStore
from src.services.profile.manager import ProfileManager
from src.services.proxy.store import ProxyStore
from src.services.ssh.store import SSHHostStore
from src.services.trash.service import TrashService
from src.services.trash.store import (
    EXPIRY_WARNING_DAYS,
    RETENTION_DAYS,
    TrashStore,
)
from src.ui.components.sidebar import EXPIRY_BADGE_TIP, build_sidebar

DAY = 86400.0


@pytest.fixture
def env(tmp_path, monkeypatch):
    """The same store wiring the container uses, on an injectable clock."""
    import src.core.config as cfg
    import src.services.profile.manager as mod

    for m in (cfg, mod):
        monkeypatch.setattr(
            m, "PROFILES_FILE", str(tmp_path / "profiles.json"), raising=False
        )
        monkeypatch.setattr(m, "DATA_DIR", str(tmp_path / "data"), raising=False)
    monkeypatch.setenv("PERSONA_TRASH_FILE", str(tmp_path / "trash.json"))
    monkeypatch.setenv("PERSONA_SSH_HOSTS_FILE", str(tmp_path / "ssh.json"))
    monkeypatch.setenv("PERSONA_CERTS_FILE", str(tmp_path / "certs.json"))
    monkeypatch.setenv("PERSONA_CERTS_DIR", str(tmp_path / "certificates"))

    clock = {"t": 1000.0}
    trash = TrashStore(now=lambda: clock["t"])
    pm = ProfileManager()
    bstore = BookmarkStore(path=str(tmp_path / "bookmarks.json"))
    pstore = ProxyStore(path=str(tmp_path / "proxies.json"))
    sshstore = SSHHostStore()
    cstore = CertStore()
    for store in (pm, bstore, pstore, sshstore, cstore):
        store.set_trash(trash)
    for store in (bstore, pstore):
        store.set_profile_manager(pm)
    svc = TrashService(
        trash,
        profile_manager=pm,
        bookmark_store=bstore,
        proxy_store=pstore,
        ssh_host_store=sshstore,
        cert_store=cstore,
    )
    return type(
        "Env",
        (),
        {
            "clock": clock, "trash": trash, "pm": pm, "bstore": bstore,
            "svc": svc, "tmp_path": tmp_path,
        },
    )


def _trash_json(env) -> str:
    return str(env.tmp_path / "trash.json")


# ---------------------------------------------------------------------------
# 1. The query: what it selects
# ---------------------------------------------------------------------------


def test_a_fresh_entry_is_not_near_expiry(env):
    env.bstore.add("fresh", "https://a")
    env.bstore.delete("fresh")
    assert env.svc.expiring_within() == []


def test_an_entry_just_inside_the_window_is_reported(env):
    env.bstore.add("old-jar", "https://a")
    env.bstore.delete("old-jar")
    # One day past the point where destruction falls inside the warning window.
    env.clock["t"] = 1000.0 + (RETENTION_DAYS - EXPIRY_WARNING_DAYS + 1) * DAY
    assert [e.name for e in env.svc.expiring_within()] == ["old-jar"]


def test_an_entry_just_outside_the_window_is_not_reported(env):
    env.bstore.add("still-fine", "https://a")
    env.bstore.delete("still-fine")
    # One day short of entering the warning window.
    env.clock["t"] = 1000.0 + (RETENTION_DAYS - EXPIRY_WARNING_DAYS - 1) * DAY
    assert env.svc.expiring_within() == []


def test_an_already_expired_entry_still_counts(env):
    """It is not gone until the next app start — and it is the most urgent
    thing the trash can hold. Excluding it would leave the rail silent for
    exactly the entry that dies on the next launch."""
    env.bstore.add("doomed", "https://a")
    env.bstore.delete("doomed")
    env.clock["t"] = 1000.0 + (RETENTION_DAYS + 3) * DAY
    assert [e.name for e in env.svc.expiring_within()] == ["doomed"]


def test_the_nearest_deadline_comes_first(env):
    env.bstore.add("older", "https://a")
    env.bstore.delete("older")
    env.clock["t"] = 1000.0 + 2 * DAY
    env.bstore.add("newer", "https://b")
    env.bstore.delete("newer")
    env.clock["t"] = 1000.0 + (RETENTION_DAYS - 1) * DAY
    assert [e.name for e in env.svc.expiring_within()] == ["older", "newer"]


def test_the_window_is_explicitly_widenable(env):
    env.bstore.add("mid", "https://a")
    env.bstore.delete("mid")
    env.clock["t"] = 1000.0 + 15 * DAY
    assert env.trash.expiring_within(days=7) == []
    assert [e.name for e in env.trash.expiring_within(days=20)] == ["mid"]


# ---------------------------------------------------------------------------
# 2. AC2 — the query is READ-ONLY, asserted on bytes on disk
# ---------------------------------------------------------------------------


def test_the_query_does_not_rewrite_trash_json(env):
    """Asserted on BYTES, not on the returned list. A read that quietly
    re-saves would move nothing visible and still be the wrong thing: the
    rail asks this question on every repaint."""
    env.bstore.add("old-jar", "https://a")
    env.bstore.delete("old-jar")
    env.clock["t"] = 1000.0 + (RETENTION_DAYS - 1) * DAY

    path = pathlib.Path(_trash_json(env))
    before_bytes = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns

    assert len(env.svc.expiring_within()) == 1

    assert path.read_bytes() == before_bytes
    assert path.stat().st_mtime_ns == before_mtime


def test_the_query_removes_no_entry(env):
    env.bstore.add("old-jar", "https://a")
    env.bstore.delete("old-jar")
    env.clock["t"] = 1000.0 + (RETENTION_DAYS + 5) * DAY

    env.svc.expiring_within()

    on_disk = json.loads(pathlib.Path(_trash_json(env)).read_text(encoding="utf-8"))
    assert [d["name"] for d in on_disk.values()] == ["old-jar"]
    assert [e.name for e in env.svc.list()] == ["old-jar"]


def test_the_query_does_not_move_deleted_at(env):
    """Looking at the clock must not restart it. If merely asking moved
    ``deleted_at``, warning about an entry would keep it alive forever."""
    env.bstore.add("old-jar", "https://a")
    env.bstore.delete("old-jar")
    env.clock["t"] = 1000.0 + (RETENTION_DAYS - 2) * DAY

    before = env.svc.list()[0].deleted_at
    env.svc.expiring_within()
    after = env.svc.list()[0].deleted_at
    assert after == before

    on_disk = json.loads(pathlib.Path(_trash_json(env)).read_text(encoding="utf-8"))
    assert [d["deleted_at"] for d in on_disk.values()] == [before]


def test_the_query_destroys_no_material(env):
    """A profile's parked data dir is the cookie jar this whole floor exists to
    protect. Asking about it must leave it on disk."""
    env.pm.add_profile("alpha", "", "windows")
    os.makedirs(env.pm._data_path("alpha"), exist_ok=True)
    env.pm.delete_profile("alpha")
    parked = env.svc.list()[0].material_path
    assert os.path.exists(parked)

    env.clock["t"] = 1000.0 + (RETENTION_DAYS + 1) * DAY
    assert len(env.svc.expiring_within()) == 1
    assert os.path.exists(parked), (
        "the near-expiry READ destroyed the parked profile data. It is a "
        "query, not a purge."
    )


# ---------------------------------------------------------------------------
# 3. AC3 — the retention floor is untouched
# ---------------------------------------------------------------------------


def test_the_retention_window_is_still_thirty_days():
    assert RETENTION_DAYS == 30


def test_the_warning_window_is_well_inside_the_floor():
    """A warning that covered most of the entry's life would be chrome, not a
    state — the noise `_status_needs_reveal` refuses."""
    assert 0 < EXPIRY_WARNING_DAYS < RETENTION_DAYS / 4


def test_a_near_expiry_query_does_not_purge_anything(env):
    """The two are siblings and must stay strangers: asking what is CLOSE must
    never do what purging does."""
    env.bstore.add("old-jar", "https://a")
    env.bstore.delete("old-jar")
    env.clock["t"] = 1000.0 + (RETENTION_DAYS + 1) * DAY

    env.svc.expiring_within()
    # Still there, and still purgeable — the purge is what removes it.
    assert len(env.svc.list()) == 1
    assert env.svc.purge_expired() == 1
    assert env.svc.list() == []


# ---------------------------------------------------------------------------
# 4. AC1 / AC5 — the conditional signal on the rail
# ---------------------------------------------------------------------------


def _walk(control):
    yield control
    for attr in ("content", "controls", "actions"):
        v = getattr(control, attr, None)
        if v is None:
            continue
        children = v if isinstance(v, list) else [v]
        for child in children:
            if isinstance(child, ft.BaseControl):
                yield from _walk(child)


def _sidebar(**kwargs):
    return build_sidebar(
        active_page="profiles",
        on_navigate=lambda k: None,
        log_panel=ft.Text("log"),
        **kwargs,
    )


def _nav_row(sidebar, key: str):
    """The nav button whose label is ``key``, as its Row of cells."""
    for c in _walk(sidebar):
        if isinstance(c, ft.Row):
            texts = [x for x in c.controls if isinstance(x, ft.Text)]
            if any(t.value == key for t in texts):
                return c
    raise AssertionError(f"no nav row for {key!r}")


def _badges(sidebar):
    return [
        c
        for c in _walk(sidebar)
        if isinstance(c, ft.Container) and c.tooltip == EXPIRY_BADGE_TIP
    ]


def test_an_empty_trash_puts_nothing_on_the_rail():
    assert _badges(_sidebar()) == []


def test_far_from_expiry_entries_put_nothing_on_the_rail():
    """The trash is NOT empty — it just holds nothing near its deadline. This
    is the case that separates 'a signal' from 'a trash counter'."""
    assert _badges(_sidebar(trash_expiring=0)) == []


def test_a_near_expiry_entry_puts_a_signal_on_the_trash_item():
    sidebar = _sidebar(trash_expiring=3)
    badges = _badges(sidebar)
    assert len(badges) == 1
    assert badges[0] in list(_walk(_nav_row(sidebar, "trash")))


def test_the_signal_carries_the_count():
    badge = _badges(_sidebar(trash_expiring=3))[0]
    text = next(c for c in _walk(badge) if isinstance(c, ft.Text))
    assert text.value == "3"


def test_no_other_nav_item_ever_carries_the_signal():
    sidebar = _sidebar(trash_expiring=4)
    for key in ("profiles", "network", "bookmarks", "tags", "certificates",
                "connect"):
        row = _nav_row(sidebar, key)
        assert not [
            c
            for c in _walk(row)
            if isinstance(c, ft.Container) and c.tooltip == EXPIRY_BADGE_TIP
        ], f"{key} carried the trash badge"


def test_the_rail_gains_no_text_string(env):
    """AC5. The 200px rail's text budget is contested (PS-229's 22-char bound,
    carried across the version panel by PS-271). A count is fine; a phrase is
    not — so every Text this adds must be a bare number."""
    before = {id(t) for t in _walk(_sidebar()) if isinstance(t, ft.Text)}
    added = [
        t
        for t in _walk(_sidebar(trash_expiring=12))
        if isinstance(t, ft.Text) and id(t) not in before
    ]
    # id() is not stable across two builds, so compare by VALUE: whatever the
    # badge adds must be digits and nothing else.
    values_with = {
        str(t.value) for t in _walk(_sidebar(trash_expiring=12))
        if isinstance(t, ft.Text)
    }
    values_without = {
        str(t.value) for t in _walk(_sidebar()) if isinstance(t, ft.Text)
    }
    new_strings = values_with - values_without
    assert new_strings == {"12"}, (
        f"the badge added prose to the rail: {new_strings!r}"
    )
    assert added  # the badge really did add a control, so the check has teeth


def test_the_signal_is_addressable_by_a_human_readable_tip():
    """A bare number answers 'how many' and not 'how many WHAT'. The tooltip
    paints in an overlay and costs the rail zero width — and it is also what
    makes the badge findable in the semantics tree the driven harness reads."""
    badge = _badges(_sidebar(trash_expiring=1))[0]
    assert badge.tooltip == EXPIRY_BADGE_TIP


def test_the_default_caller_gets_the_old_rail():
    """Four kwargs, exactly as tests/test_sidebar_logo.py's helper passes —
    the parameter is optional and silent by default."""
    sidebar = build_sidebar(
        active_page="profiles",
        on_navigate=lambda k: None,
        log_panel=ft.Text("log"),
    )
    assert _badges(sidebar) == []


# ---------------------------------------------------------------------------
# 5. The app end: the count the rail is fed comes from the service
# ---------------------------------------------------------------------------


def test_the_app_reads_the_count_off_the_service():
    from src.ui.app import App

    class _Svc:
        def __init__(self):
            self.calls = 0

        def expiring_within(self):
            self.calls += 1
            return [object(), object()]

    app = App.__new__(App)
    app.trash_service = _Svc()
    assert app._trash_expiring_count() == 2
    assert app.trash_service.calls == 1


def test_a_broken_trash_costs_the_badge_and_not_the_window():
    """A quarantined or corrupt trash.json must not stop the rail painting."""
    from src.ui.app import App

    class _Broken:
        def expiring_within(self):
            raise OSError("trash.json is quarantined")

    app = App.__new__(App)
    app.trash_service = _Broken()
    assert app._trash_expiring_count() == 0


# ---------------------------------------------------------------------------
# 6. Scope item 4 — the purge line is no longer the dimmest thing on screen
# ---------------------------------------------------------------------------


def test_the_purge_line_is_not_classified_as_idle():
    from src.ui.log_console import SEV_IDLE, severity

    line = "Purged 1 trash entry/entries past the 30-day retention window"
    assert severity(line) != SEV_IDLE


def test_widening_severity_reclassified_nothing_else():
    """A token added to `severity`'s substring lists affects EVERY message in
    the app, so the widening is bounded by checking the messages around it."""
    from src.ui.log_console import SEV_FAIL, SEV_IDLE, SEV_INFO, SEV_OK, severity

    assert severity("Trashed bookmark: old-jar") == SEV_IDLE
    assert severity("Moved bookmark to trash: old-jar") == SEV_IDLE
    assert severity("emptied trash (3 item(s))") == SEV_IDLE
    assert severity("permanently deleted profile: alpha") == SEV_IDLE
    assert severity("restored bookmark: old-jar") == SEV_IDLE
    assert severity("Launching shop-de-03") == SEV_INFO
    assert severity("Engine update available") == SEV_INFO
    assert severity("LAUNCH_FAILED: engine firefox-142 missing") == SEV_FAIL
    assert severity("persona session started 3.0.2") == SEV_OK
