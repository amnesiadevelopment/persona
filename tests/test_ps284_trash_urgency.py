"""PS-284 — the trash page ordered by urgency, and a legible last day.

PS-272 gave the nav rail a badge counting trash entries inside the 7-day
warning window. The page that badge sends the operator to was ordered AGAINST
it: ``TrashStore.list`` returns ``deleted_at`` DESC, and with a constant
``RETENTION_DAYS`` recency-DESC *is* time-remaining-DESC — so the entry nearest
destruction was last, by construction, for every possible data shape. And the
row's countdown floor-divided by 86400 under a ``max(0, ...)`` clamp, so an
entry with 23h left, one with 2h left, and one already past the window all
printed the identical string ``expires in 0d``.

Three groups of assertions, and the third is the one that protects the rest:

1. **Order.** The new read returns nearest-destruction-first, and every entry
   the badge counted sorts above every entry it did not — asserted as a
   PARTITION over the badge's own query, not as a hand-written expected list,
   so it cannot pass by agreeing with one seeded arrangement.
2. **Legibility.** The three states — hours left, already past the window, a
   full day or more — render three DISTINCT and truthful strings, and the
   already-past one says what actually happens to it (destroyed on the next
   app start; ``src/main.py`` calls ``purge_expired()`` there).
3. **The recency contract is untouched.** ``list()`` still returns newest
   first, so ``GET /trash`` and ``_empty_trash``'s count are unmoved. This is
   the reason a NEW read exists beside ``list`` instead of ``list`` being
   re-sorted in place.

The RENDERED page — the position of the urgent rows on screen and the three
strings as painted — is driven live in ``tests/ui_driver/live_ps284.py``,
including the falsification pass that proves the check can go red.
"""

import time

import pytest

from src.services.trash.service import TrashService
from src.services.trash.store import (
    EXPIRY_WARNING_DAYS,
    RETENTION_DAYS,
    TrashEntry,
    TrashStore,
)
from src.ui.components.trash_page import (
    PAST_WINDOW_PHRASE,
    build_trash_page,
    expiry_phrase,
)

DAY = 86400.0


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONA_TRASH_FILE", str(tmp_path / "trash.json"))
    return TrashStore()


def _aged(store, name: str, age_days: float, kind: str = "bookmark"):
    """Add an entry that was deleted ``age_days`` ago.

    Backdating ``deleted_at`` is the only way to reach a 27-day-old entry
    inside a run, and it is the same field the product writes and reads.
    """
    entry = store.add(kind, name, {})
    entry.deleted_at = time.time() - age_days * DAY
    return entry


# --- 1. ORDER -------------------------------------------------------------


def test_by_urgency_puts_the_entry_nearest_destruction_first(store):
    _aged(store, "fresh", 0)
    _aged(store, "doomed", RETENTION_DAYS - 0.5)
    _aged(store, "middling", 10)

    assert [e.name for e in store.by_urgency()] == ["doomed", "middling", "fresh"]


def test_by_urgency_is_the_exact_inverse_of_the_recency_order(store):
    """With a constant retention window the two orders are each other's mirror.

    This is the structural claim the ticket rests on — not a property of one
    seeded arrangement — so it is asserted rather than illustrated.
    """
    for i in range(7):
        _aged(store, f"item-{i}", i * 3.5)

    assert [e.id for e in store.by_urgency()] == [
        e.id for e in reversed(store.list())
    ]


def test_every_entry_the_badge_counted_sorts_above_every_entry_it_did_not(store):
    """The partition the badge implies, asserted against the badge's OWN query.

    ``expiring_within`` is what the rail counts. Whatever it returns must be a
    PREFIX of the page's order, or the badge points at rows the operator has to
    scroll to find.
    """
    for age in (0, 2, 9, 17, 24, RETENTION_DAYS - 6, RETENTION_DAYS - 1,
                RETENTION_DAYS + 1):
        _aged(store, f"aged-{age}", age)

    urgent = {e.id for e in store.expiring_within()}
    assert 0 < len(urgent) < len(store.by_urgency()), "fixture is degenerate"

    order = [e.id for e in store.by_urgency()]
    positions = [i for i, eid in enumerate(order) if eid in urgent]
    assert positions == list(range(len(urgent)))


def test_by_urgency_counts_an_already_expired_entry_as_the_most_urgent(store):
    """Past the window is not gone — it is destroyed on the NEXT app start.

    So it is the most urgent thing the trash can hold, and it is first. Same
    rule ``expiring_within`` already follows.
    """
    _aged(store, "gone-next-start", RETENTION_DAYS + 2)
    _aged(store, "tomorrow", RETENTION_DAYS - 1)

    assert store.by_urgency()[0].name == "gone-next-start"


def test_by_urgency_filters_by_kind_like_list_does(store):
    _aged(store, "b", 1, kind="bookmark")
    _aged(store, "p", 20, kind="proxy")

    assert [e.name for e in store.by_urgency("proxy")] == ["p"]


def test_by_urgency_is_read_only(store, tmp_path):
    """A read must not age, remove or rewrite anything.

    Asserted on the BYTES ON DISK and on ``deleted_at``, not on the returned
    list: a query the page asks on every repaint must not be able to change
    what it is reporting on.
    """
    _aged(store, "a", 5)
    _aged(store, "b", 25)
    path = tmp_path / "trash.json"
    before_bytes = path.read_bytes()
    before_stamps = {e.id: e.deleted_at for e in store.list()}

    store.by_urgency()

    assert path.read_bytes() == before_bytes
    assert {e.id: e.deleted_at for e in store.list()} == before_stamps
    assert len(store.list()) == 2


def test_the_service_surfaces_the_urgency_read(store):
    """The UI goes through the service; it must not reach past it into the store."""
    _aged(store, "fresh", 0)
    _aged(store, "doomed", RETENTION_DAYS - 1)
    service = TrashService(store)

    assert [e.name for e in service.by_urgency()] == ["doomed", "fresh"]
    assert [e.name for e in service.by_urgency("bookmark")] == ["doomed", "fresh"]


# --- 2. LEGIBILITY --------------------------------------------------------


def _at(hours_left: float) -> tuple[TrashEntry, float]:
    """An entry with exactly ``hours_left`` before destruction, and the clock."""
    now = 1_000_000.0
    deleted_at = now + hours_left * 3600 - RETENTION_DAYS * DAY
    return TrashEntry(id="e", kind="bookmark", name="x", deleted_at=deleted_at), now


@pytest.mark.parametrize(
    "hours_left, expected",
    [
        (24 * 12, "expires in 12d"),   # days remain: unchanged
        (24, "expires in 1d"),         # exactly on the boundary
        (23.9, "expires in 23h"),      # the case that used to say "0d"
        (2.4, "expires in 2h"),        # ditto
        (0.5, "expires in under 1h"),  # ditto, and sub-hour
    ],
)
def test_time_remaining_is_rendered_in_a_unit_that_still_says_something(
    hours_left, expected
):
    entry, now = _at(hours_left)
    assert expiry_phrase(entry, now) == expected


@pytest.mark.parametrize("hours_past", [0.2, 21.6, 24 * 9])
def test_an_entry_past_the_window_says_what_actually_happens_to_it(hours_past):
    """``src/main.py`` calls ``purge_expired()`` on every start, so this is a
    true statement about the entry, not a rounded one — and it is NOT the
    string an entry with time left gets."""
    entry, now = _at(-hours_past)
    assert expiry_phrase(entry, now) == PAST_WINDOW_PHRASE
    assert "expires in" not in PAST_WINDOW_PHRASE


def test_the_three_states_render_three_distinct_strings():
    """The whole defect in one assertion: 23h left, 2h left, 0.2h past and
    21.6h past printed ONE identical six-character string."""
    phrases = [
        expiry_phrase(*_at(24 * 5)),
        expiry_phrase(*_at(21.6)),
        expiry_phrase(*_at(2.4)),
        expiry_phrase(*_at(-0.2)),
    ]
    assert len(set(phrases)) == 4, phrases


def test_no_row_ever_renders_a_negative_number():
    """The ``max(0, ...)`` clamp existed for this; the restraint is kept."""
    for hours_past in (0.1, 5, 24 * 40):
        assert "-" not in expiry_phrase(*_at(-hours_past))


def _texts(control) -> list[str]:
    """Every ``ft.Text`` value in the built tree, IN DOCUMENT ORDER.

    Order-preserving on purpose: half the assertions here are about what the
    operator sees FIRST, and a walk that reverses siblings would let a
    recency-ordered page pass an urgency-ordered assertion.
    """
    import flet as ft

    out: list[str] = []

    def walk(c) -> None:
        if isinstance(c, ft.Text) and isinstance(c.value, str):
            out.append(c.value)
        for attr in ("content", "controls"):
            child = getattr(c, attr, None)
            if isinstance(child, list):
                for item in child:
                    walk(item)
            elif child is not None and not isinstance(child, str):
                walk(child)

    walk(control)
    return out


def test_the_built_page_carries_the_three_states_verbatim():
    """The strings above reach the rendered control tree, not just the helper."""
    now = 1_000_000.0

    def at(hours_left, name):
        return TrashEntry(
            id=name,
            kind="bookmark",
            name=name,
            deleted_at=now + hours_left * 3600 - RETENTION_DAYS * DAY,
        )

    page = build_trash_page(
        [at(24 * 5, "roomy"), at(6, "hours"), at(-3, "past")],
        on_restore=lambda i: None,
        on_delete_permanently=lambda i: None,
        on_empty=lambda: None,
        now=now,
    )
    texts = " ".join(_texts(page))
    assert "expires in 5d" in texts
    assert "expires in 6h" in texts
    assert PAST_WINDOW_PHRASE in texts


def test_the_page_renders_rows_in_the_order_it_is_given():
    """The page does not re-sort; the caller's order is what the operator sees.

    Asserted so the ordering fix cannot be silently undone at either end.
    """
    now = 1_000_000.0
    entries = [
        TrashEntry(id=n, kind="bookmark", name=n, deleted_at=now - i * DAY)
        for i, n in enumerate(("first", "second", "third"))
    ]
    texts = _texts(
        build_trash_page(
            entries,
            on_restore=lambda i: None,
            on_delete_permanently=lambda i: None,
            on_empty=lambda: None,
            now=now,
        )
    )
    assert [t for t in texts if t in {"first", "second", "third"}] == [
        "first",
        "second",
        "third",
    ]


# --- 3. THE CONTRACTS THAT MUST NOT MOVE ----------------------------------


def test_list_still_returns_newest_first(store):
    """``GET /trash`` and ``_empty_trash``'s count read this. Untouched."""
    _aged(store, "old", 20)
    _aged(store, "new", 1)

    assert [e.name for e in store.list()] == ["new", "old"]


def test_the_service_list_passthrough_is_unchanged(store):
    _aged(store, "old", 20)
    _aged(store, "new", 1)

    assert [e.name for e in TrashService(store).list()] == ["new", "old"]


def test_the_rest_lane_still_serves_recency_order(store, monkeypatch):
    """Driven through the actual route handler, not through the store again."""
    from src.api.routes.trash import list_trash

    _aged(store, "old", 20)
    _aged(store, "new", 1)

    response = list_trash(kind=None, ts=TrashService(store))
    assert [e.name for e in response.entries] == ["new", "old"]
    assert response.retention_days == RETENTION_DAYS


def test_the_retention_floor_and_the_warning_window_are_unmoved():
    """This ticket changes what the operator can SEE, never the floor."""
    assert RETENTION_DAYS == 30
    assert EXPIRY_WARNING_DAYS == 7


def test_the_app_builds_the_trash_page_from_the_urgency_read():
    """The wiring itself, asserted — the fix is worthless if the page is still
    built from ``list()``."""
    import inspect

    import src.ui.app as app_mod

    src = inspect.getsource(app_mod.App._render_active_page)
    trash_block = src.split('elif self._active_page == "trash":')[1]
    trash_block = trash_block.split("else:")[0]
    assert "trash_service.by_urgency()" in trash_block
    assert "trash_service.list()" not in trash_block
