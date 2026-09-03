import json
import logging
import os

import pytest

from src.core import settings


@pytest.fixture
def settings_path(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setenv("PERSONA_SETTINGS_FILE", str(path))
    # _save_blocked is MODULE state, not per-instance like StoreGuardMixin's
    # flag: a test that trips it would poison every later test in the process.
    # Reset it for every test in this file rather than relying on ordering.
    monkeypatch.setattr(settings, "_save_blocked", False, raising=False)
    return path


def test_corrupt_file_backed_up_before_defaults(settings_path):
    raw = "{not json"
    settings_path.write_text(raw, encoding="utf-8")

    assert settings.get("x", "d") == "d"

    backups = list(settings_path.parent.glob("settings.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == raw


def test_set_after_corruption_does_not_lose_backup(settings_path):
    raw = json.dumps({"onboarding_done": True})[:-1]  # truncated json
    settings_path.write_text(raw, encoding="utf-8")

    settings.set("theme", "dark")

    backups = list(settings_path.parent.glob("settings.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == raw
    assert json.loads(settings_path.read_text(encoding="utf-8")) == {
        "theme": "dark"
    }


def test_missing_file_creates_no_backup(settings_path):
    assert settings.get("x", "d") == "d"
    assert not list(settings_path.parent.glob("settings.json.corrupt-*"))


def test_non_dict_json_backed_up(settings_path):
    raw = json.dumps([1, 2, 3])
    settings_path.write_text(raw, encoding="utf-8")

    assert settings.get("x", "d") == "d"

    backups = list(settings_path.parent.glob("settings.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == raw


def test_transient_lock_does_not_read_as_absent(settings_path, monkeypatch):
    # #226: after a Windows update the relaunched persona can hit a momentary
    # lock on settings.json (the installer's /CLOSEAPPLICATIONS, an AV sweep, or
    # the relaunch purge still holding a handle). A single failed read must NOT
    # be treated as "no settings" — that read onboarding_done as False and
    # re-ran the full onboarding after every update. get() retries a locked file
    # and only then falls back, so the real value survives a transient lock.
    settings_path.write_text(json.dumps({"onboarding_done": True}), encoding="utf-8")

    real_open = open
    calls = {"n": 0}

    def flaky_open(path, *a, **kw):
        if str(path) == str(settings_path):
            calls["n"] += 1
            if calls["n"] <= 2:  # first reads hit the lock
                raise PermissionError("locked")
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", flaky_open)

    assert settings.is_onboarding_done() is True
    assert calls["n"] >= 2  # it retried rather than giving up on the first fail


def _refuse_quarantine_rename(monkeypatch):
    """Make ONLY the quarantine rename fail, leaving the atomic write working.

    The two paths call the same primitive (os.replace), so a blanket patch
    would break the write as well and the experiment would prove nothing: the
    prefs would survive because nothing could write, not because the guard
    held. Discriminate on the DESTINATION — the quarantine target is the only
    one named ``*.corrupt-<ts>``; atomic_write_json renames a ``.new`` temp
    over the real path. This is the reachable pairing the ticket measured (a
    filename-length ceiling the 19-char quarantine suffix crosses and the
    13-char temp suffix does not, or a rename-specific refusal by an AV /
    indexer holding the source name).
    """
    real_replace = os.replace

    def picky_replace(src, dst, *a, **kw):
        if ".corrupt-" in str(dst):
            raise OSError("rename refused")
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(os, "replace", picky_replace)


def test_failed_quarantine_blocks_save_of_unparseable_file(
    settings_path, monkeypatch
):
    # The quarantine comment states the whole intent: move the unreadable file
    # aside "so the next _save() can't silently overwrite it". When the rename
    # FAILS the file is still on disk, still the only copy of the operator's
    # real preferences — so the save it was protecting against must not happen.
    raw = json.dumps(
        {
            "onboarding_done": True,
            "app_egress_proxy": "socks5://user:pw@exit.example:1080",
            "last_seen_version": "3.0.2",
        }
    )[:-1]  # truncated json -> ValueError -> _quarantine
    settings_path.write_text(raw, encoding="utf-8")
    _refuse_quarantine_rename(monkeypatch)

    settings.set("theme", "dark")

    # The bytes on disk, not a helper call: the operator's only copy of their
    # preferences must be exactly as it was.
    assert settings_path.read_text(encoding="utf-8") == raw
    assert not list(settings_path.parent.glob("settings.json.corrupt-*"))


def test_failed_quarantine_blocks_save_of_non_dict_file(settings_path, monkeypatch):
    # The second of _read's two quarantine call sites: valid JSON that isn't a
    # dict. It must block the save exactly as the unparseable arm does.
    raw = json.dumps([1, 2, 3])
    settings_path.write_text(raw, encoding="utf-8")
    _refuse_quarantine_rename(monkeypatch)

    settings.set("theme", "dark")

    assert settings_path.read_text(encoding="utf-8") == raw
    assert not list(settings_path.parent.glob("settings.json.corrupt-*"))


def test_blocked_save_stays_blocked_on_a_second_set(settings_path, monkeypatch):
    # The refusal is standing, not one-shot. The corrupt file is still the only
    # copy of the operator's preferences on the second set() too, so a repeated
    # write attempt must not be the one that finally destroys it.
    raw = json.dumps({"onboarding_done": True})[:-1]
    settings_path.write_text(raw, encoding="utf-8")
    _refuse_quarantine_rename(monkeypatch)

    settings.set("theme", "dark")
    settings.set("theme", "light")

    assert settings_path.read_text(encoding="utf-8") == raw


def test_blocked_save_is_logged(settings_path, monkeypatch, caplog):
    # A blocked save must be visible rather than silent — the operator's
    # preference write did not land, and only the log can say so.
    raw = json.dumps({"onboarding_done": True})[:-1]
    settings_path.write_text(raw, encoding="utf-8")
    _refuse_quarantine_rename(monkeypatch)

    with caplog.at_level(logging.DEBUG, logger="persona.core.settings"):
        settings.set("theme", "dark")

    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "could not be backed up" in messages
    assert "Not saving" in messages
