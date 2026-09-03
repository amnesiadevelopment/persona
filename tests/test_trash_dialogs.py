"""No dialog may describe an outcome the code does not deliver.

The trash makes the ordinary delete recoverable, so the sentence that guarded it
— "This action cannot be undone." — became a claim outliving the code it
describes, which is exactly the defect the Honest-interface direction exists to
eliminate. These tests pin the wording to the behaviour on both sides: the
recoverable paths must NOT claim irreversibility, and the two genuinely
irreversible ones must.

(The panic wipe dialog's half of this lives in test_trash_wipe.py, beside the
wipe behaviour that makes its claim true.)
"""
import time
import types
from typing import Any

import flet as ft
import pytest

from src.services.trash.store import TrashEntry
from src.ui.app import App


class _FakePage:
    def __init__(self) -> None:
        self.shown: Any = None

    def show_dialog(self, dlg) -> None:
        self.shown = dlg

    def pop_dialog(self) -> None:
        pass

    def update(self) -> None:
        pass


def _dialog_text(dlg) -> str:
    """Title + body of a confirm dialog, as the operator reads it."""
    parts = []
    for node in (dlg.title, dlg.content):
        value = getattr(node, "value", None)
        if value:
            parts.append(value)
        for child in getattr(node, "controls", []) or []:
            if getattr(child, "value", None):
                parts.append(child.value)
    return " ".join(parts)


def _confirm_button(dlg):
    for action in getattr(dlg, "actions", []):
        label = getattr(action, "content", None)
        if isinstance(label, str) and "cancel" not in label.lower():
            return action
    raise AssertionError("no confirm button in the dialog")


# --- the ordinary delete no longer claims to be irreversible ---


def test_the_default_delete_dialog_does_not_claim_irreversibility():
    from src.ui.dialogs.confirm import open_confirm_dialog

    page = _FakePage()
    open_confirm_dialog(page, "alpha", lambda: None)
    text = _dialog_text(page.shown)
    assert "cannot be undone" not in text.lower()


def test_the_default_delete_dialog_says_where_the_record_goes():
    # Truthful AND useful: the operator is told the record is recoverable, not
    # merely left without the old warning.
    from src.ui.dialogs.confirm import open_confirm_dialog

    page = _FakePage()
    open_confirm_dialog(page, "alpha", lambda: None)
    text = _dialog_text(page.shown).lower()
    assert "trash" in text and "restored" in text


def test_the_bulk_delete_dialog_does_not_claim_irreversibility():
    from src.ui.actions.bulk import bulk_delete_profiles

    page = _FakePage()
    bulk_delete_profiles(
        page, ["a", "b"], pm=types.SimpleNamespace(delete_profile=lambda n: None),
        log=lambda m: None, refresh=lambda: None, on_done=lambda: None,
    )
    text = _dialog_text(page.shown).lower()
    assert "cannot be undone" not in text
    assert "trash" in text


def test_the_bulk_delete_dialog_still_names_the_count():
    from src.ui.actions.bulk import bulk_delete_profiles

    page = _FakePage()
    bulk_delete_profiles(
        page, ["a", "b", "c"], pm=types.SimpleNamespace(delete_profile=lambda n: None),
        log=lambda m: None, refresh=lambda: None, on_done=lambda: None,
    )
    assert "3 profiles" in _dialog_text(page.shown)


def test_an_explicit_body_still_wins_over_the_default():
    # Callers that pass their own body (the proxy/bookmark/pool dialogs, which
    # explain a consequence specific to that record) are unaffected.
    from src.ui.dialogs.confirm import open_confirm_dialog

    page = _FakePage()
    open_confirm_dialog(
        page, "x", lambda: None, title="Delete pool 'checks'?",
        body="The bookmarks themselves are kept.",
    )
    assert "The bookmarks themselves are kept." in _dialog_text(page.shown)


# --- permanent deletion DOES claim it, because it is true ---


def _app_with_trash(entries, *, deleted=None, emptied=None):
    """An App-shaped stub carrying a trash service over the given entries."""
    store = {e.id: e for e in entries}

    def delete_permanently(entry_id):
        (deleted if deleted is not None else []).append(entry_id)
        store.pop(entry_id, None)
        return True, ""

    def empty():
        count = len(store)
        store.clear()
        if emptied is not None:
            emptied.append(count)
        return count

    svc = types.SimpleNamespace(
        list=lambda kind=None: list(store.values()),
        get=store.get,
        restore=lambda i: (True, ""),
        delete_permanently=delete_permanently,
        empty=empty,
    )
    page = _FakePage()
    app = types.SimpleNamespace(
        page=page,
        trash_service=svc,
        _log=lambda m: None,
        _render_active_page=lambda: None,
        _safe_update=lambda: None,
        _refresh_profiles=lambda: None,
        # PS-272: restoring / destroying / emptying changes what is counting
        # down, so the rail's near-expiry badge is stale the instant these
        # handlers return and they rebuild it. The stub carries it because the
        # handlers call it, not because these tests assert on it.
        _refresh_sidebar=lambda: None,
    )
    return app, page


def _entry(kind="profile", name="alpha"):
    return TrashEntry(id="e1", kind=kind, name=name, deleted_at=time.time())


def test_the_permanent_delete_dialog_claims_irreversibility():
    app, page = _app_with_trash([_entry()])
    App._delete_from_trash_permanently(app, "e1")
    assert "cannot be undone" in _dialog_text(page.shown).lower()


def test_the_permanent_delete_dialog_names_the_record():
    app, page = _app_with_trash([_entry(kind="proxy", name="exit-us")])
    App._delete_from_trash_permanently(app, "e1")
    text = _dialog_text(page.shown)
    assert "exit-us" in text and "proxy" in text


def test_the_permanent_delete_dialog_warns_about_secret_material():
    # The distinction the operator has to be able to make: THIS is the step that
    # removes the credentials from disk, unlike trashing.
    app, page = _app_with_trash([_entry(kind="certificate", name="admin")])
    App._delete_from_trash_permanently(app, "e1")
    assert "credentials are removed from disk" in _dialog_text(page.shown)


def test_no_secret_warning_for_a_record_that_holds_none():
    app, page = _app_with_trash([_entry(kind="bookmark", name="leaks")])
    App._delete_from_trash_permanently(app, "e1")
    assert "credentials" not in _dialog_text(page.shown)


def test_permanent_deletion_happens_only_after_the_confirm():
    deleted = []
    app, page = _app_with_trash([_entry()], deleted=deleted)
    App._delete_from_trash_permanently(app, "e1")
    assert deleted == [], "the dialog alone must not destroy anything"
    _confirm_button(page.shown).on_click(None)
    assert deleted == ["e1"]


def test_the_empty_trash_dialog_claims_irreversibility_and_names_secrets():
    app, page = _app_with_trash([_entry(), _entry(kind="proxy", name="p")])
    App._empty_trash(app)
    text = _dialog_text(page.shown).lower()
    assert "cannot be undone" in text
    assert "credentials" in text and "key bundle" in text


def test_emptying_happens_only_after_the_confirm():
    emptied = []
    app, page = _app_with_trash([_entry()], emptied=emptied)
    App._empty_trash(app)
    assert emptied == []
    _confirm_button(page.shown).on_click(None)
    assert emptied == [1]


def test_emptying_an_empty_trash_opens_no_dialog():
    app, page = _app_with_trash([])
    App._empty_trash(app)
    assert page.shown is None


# --- a refused restore explains itself instead of failing silently ---


def test_a_refused_restore_tells_the_operator_why():
    entry = _entry()
    page = _FakePage()
    svc = types.SimpleNamespace(
        list=lambda kind=None: [entry],
        get=lambda i: entry,
        restore=lambda i: (False, "A profile named 'alpha' already exists."),
    )
    logged = []
    app = types.SimpleNamespace(
        page=page, trash_service=svc, _log=logged.append,
        _render_active_page=lambda: None, _safe_update=lambda: None,
        _refresh_profiles=lambda: None, _refresh_sidebar=lambda: None,
    )
    App._restore_from_trash(app, "e1")
    assert "already exists" in _dialog_text(page.shown)
    assert any("already exists" in line for line in logged)


def test_a_successful_restore_opens_no_dialog():
    entry = _entry()
    page = _FakePage()
    svc = types.SimpleNamespace(
        list=lambda kind=None: [entry], get=lambda i: entry,
        restore=lambda i: (True, ""),
    )
    app = types.SimpleNamespace(
        page=page, trash_service=svc, _log=lambda m: None,
        _render_active_page=lambda: None, _safe_update=lambda: None,
        _refresh_profiles=lambda: None, _refresh_sidebar=lambda: None,
    )
    App._restore_from_trash(app, "e1")
    assert page.shown is None


# --- the trash page itself ---


def _walk(control):
    yield control
    for attr in ("controls", "actions"):
        for child in getattr(control, attr, []) or []:
            yield from _walk(child)
    content = getattr(control, "content", None)
    if content is not None and not isinstance(content, str):
        yield from _walk(content)


def _texts(control):
    return [
        c.value for c in _walk(control)
        if isinstance(c, ft.Text) and isinstance(c.value, str)
    ]


def _buttons(control):
    out = []
    for c in _walk(control):
        label = getattr(c, "content", None)
        if isinstance(c, ft.Button) and isinstance(label, str):
            out.append(c)
    return out


def _page(entries, **kw):
    from src.ui.components.trash_page import build_trash_page

    return build_trash_page(
        entries,
        on_restore=kw.get("on_restore", lambda i: None),
        on_delete_permanently=kw.get("on_delete_permanently", lambda i: None),
        on_empty=kw.get("on_empty", lambda: None),
        now=kw.get("now"),
    )


def test_the_trash_page_lists_every_trashed_record_by_name():
    page = _page([_entry(name="alpha"), _entry(kind="proxy", name="exit-us")])
    texts = " ".join(_texts(page))
    assert "alpha" in texts and "exit-us" in texts


def test_each_row_shows_when_it_was_deleted_and_when_it_expires():
    now = 1000.0 + 5 * 86400
    entry = TrashEntry(id="e", kind="profile", name="alpha", deleted_at=1000.0)
    texts = " ".join(_texts(_page([entry], now=now)))
    assert "5d ago" in texts
    assert "expires in 25d" in texts


def test_a_secret_bearing_row_says_the_secret_is_still_on_disk():
    entry = TrashEntry(id="e", kind="proxy", name="exit-us", deleted_at=1000.0)
    texts = " ".join(_texts(_page([entry], now=1000.0)))
    assert "still holds its secret material" in texts
    assert "delete permanently" in texts


def test_a_row_without_secret_material_carries_no_such_warning():
    entry = TrashEntry(id="e", kind="bookmark", name="leaks", deleted_at=1000.0)
    texts = " ".join(_texts(_page([entry], now=1000.0)))
    assert "secret material" not in texts


def test_the_page_states_the_retention_window():
    from src.services.trash.store import RETENTION_DAYS

    texts = " ".join(_texts(_page([])))
    assert f"{RETENTION_DAYS} days" in texts


def test_restore_is_wired_to_the_entry_id():
    restored = []
    page = _page([_entry()], on_restore=restored.append)
    button = next(
        b for b in _buttons(page) if "restore" in b.content.lower()
    )
    button.on_click(None)
    assert restored == ["e1"]


def test_permanent_delete_is_wired_to_the_entry_id():
    deleted = []
    page = _page([_entry()], on_delete_permanently=deleted.append)
    button = next(
        b for b in _buttons(page) if "permanently" in b.content.lower()
    )
    button.on_click(None)
    assert deleted == ["e1"]


def test_empty_trash_is_wired_and_disabled_when_the_trash_is_empty():
    emptied = []
    page = _page([_entry()], on_empty=lambda: emptied.append(1))
    button = next(b for b in _buttons(page) if "empty trash" in b.content.lower())
    assert button.disabled is False
    button.on_click(None)
    assert emptied == [1]

    empty_page = _page([])
    button = next(
        b for b in _buttons(empty_page) if "empty trash" in b.content.lower()
    )
    assert button.disabled is True


def test_the_empty_state_names_what_would_appear_here():
    texts = " ".join(_texts(_page([])))
    assert "trash is empty" in texts
