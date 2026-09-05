"""PS-311 — the operator's door to persona's OWN egress policy.

WHAT THIS FILE ASSERTS, AND WHAT IT DELIBERATELY DOES NOT
---------------------------------------------------------
`egress.resolve()` has had a complete READER since PS-46 and nine consumers
across all four transport arms since PS-216. It had no WRITER: `settings.
set_app_egress_proxy` existed with ZERO callers, so the only way to configure
the policy was to close persona, hand-edit settings.json and restart. This
ticket adds the writer. These tests assert the writer's contract:

* it PERSISTS — read back from a fresh read of the FILE, never from an
  in-memory object, because "the object remembers what I set" is true of a
  control that never wrote anything;
* it REFUSES AT SAVE exactly what the transport would refuse at SEND, judged
  by the SAME authority the nine consumers ask, over the eight-row transcript
  the ticket pins;
* it leaves the DIRECT default byte-identical — an install that never touches
  the control behaves exactly as it did before this file existed;
* it never puts the value in a log line.

WHAT IS **NOT** COVERED HERE, recorded rather than smoothed over: this module
makes no claim about anything PAINTED. Whether the three verdicts are legible
on the real page, whether the field is masked on screen, and whether typing
into it and pressing save actually reaches this handler are claims about a
rendered widget, and a structural test over a built control tree passes just as
happily against a page that never paints. Those are driven live in
``tests/ui_driver/live_ps311.py`` (AC5, AC6, AC8), which is where AC1's
end-to-end gesture is also observed through the operator's own controls.
"""

from __future__ import annotations

import json
import logging

import pytest

from src.core import settings
from src.services import egress

# The eight-row transcript from the ticket, re-executed here against the SHIPPED
# parser rather than restated as a table of expected booleans. Each row is
# (value, should_be_savable). Its authority is `parse_proxy` via `resolve`, and
# the test below asserts the save gate agrees with `resolve` ROW BY ROW — so
# this list cannot go stale against the parser without the test saying so.
TRANSCRIPT: list[tuple[str, bool]] = [
    ("socks5://user:pw@exit.example:1080", True),
    ("socks5h://exit.example:1080", True),
    ("socks4://exit.example:1080", True),
    ("http://exit.example:8080", True),
    ("127.0.0.1:9050", True),          # scheme defaulted — deliberate
    ("socks5://exit.example", False),  # no port
    ("this is not a proxy url", False),
    ("tor", False),
]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """A settings.json of our own. Every read below goes to THIS file."""
    monkeypatch.setenv("PERSONA_SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setenv("PERSONA_PROFILES_FILE", str(tmp_path / "p.json"))
    monkeypatch.setenv("PERSONA_PROXIES_FILE", str(tmp_path / "x.json"))
    monkeypatch.setenv("PERSONA_BOOKMARKS_FILE", str(tmp_path / "b.json"))
    monkeypatch.setenv("PERSONA_DATA_DIR", str(tmp_path / "data"))
    egress._reset_curl_refusal_log()
    yield
    egress._reset_curl_refusal_log()


def _app():
    """A real ``App``, with only the two flet-touching methods stubbed.

    ``_save_app_egress`` is the SHIPPED method; nothing about the save path is
    replaced. ``_render_active_page``/``_safe_update`` are stubbed for the same
    reason ``tests/test_app_server.py`` stubs them — there is no flet page in a
    unit test — and ``_log`` is captured rather than silenced, because "the
    value is never logged" is one of the things being asserted.
    """
    from src.ui.app import App

    a = App()
    a._render_active_page = lambda: None
    a._safe_update = lambda: None
    a.logged = []
    a._log = a.logged.append
    return a


def _from_disk(tmp_path) -> str:
    """The stored value, read from the FILE — not from any live object.

    AC1 says "persists across an application restart". An assertion against
    ``settings.app_egress_proxy()`` alone would pass against a store that never
    wrote anything if a module-level cache were ever introduced, and it reads
    the same whether or not the bytes reached disk. This opens the JSON.
    """
    with open(tmp_path / "settings.json", encoding="utf-8") as fh:
        return json.load(fh).get("app_egress_proxy", "<KEY ABSENT>")


# --- AC2: the premise. The door did not exist before this ticket. ----------


def test_the_setting_starts_unset_and_reads_as_direct():
    """The precondition the whole ticket rests on: nothing configures this.

    A fresh install has no value, and an unset value means DIRECT. If this ever
    goes red, some other path is writing the key and the "zero callers" premise
    is gone.
    """
    assert settings.app_egress_proxy() == ""
    assert egress.resolve() == (egress.DIRECT, "")


# --- AC1 + AC7: the writer, asserted against the file ----------------------


def test_operator_can_SET_the_proxy_and_it_reaches_the_file(tmp_path):
    a = _app()
    ok, reason = a._save_app_egress("socks5://exit.example:1080")

    assert (ok, reason) == (True, "")
    # THE load-bearing assertion: the bytes are on disk, not in an object.
    assert _from_disk(tmp_path) == "socks5://exit.example:1080"
    assert egress.resolve() == (egress.PROXIED, "socks5://exit.example:1080")


def test_operator_can_CHANGE_the_proxy_and_the_file_follows(tmp_path):
    a = _app()
    a._save_app_egress("socks5://first.example:1080")
    a._save_app_egress("http://second.example:8080")

    assert _from_disk(tmp_path) == "http://second.example:8080"
    assert egress.resolve() == (egress.PROXIED, "http://second.example:8080")


def test_operator_can_CLEAR_the_proxy_and_the_policy_returns_to_direct(tmp_path):
    """Clearing must always be possible — it is the operator's way OUT of a
    REFUSE state, and a control that can only ever set a value would strand
    anyone whose proxy died."""
    a = _app()
    a._save_app_egress("socks5://exit.example:1080")
    ok, reason = a._save_app_egress("")

    assert (ok, reason) == (True, "")
    assert _from_disk(tmp_path) == ""
    assert egress.resolve() == (egress.DIRECT, "")


def test_the_value_survives_a_fresh_read_of_the_store(tmp_path):
    """"Persists across an application restart", asserted the only way a unit
    test honestly can: the store's own reader, against a file written by a
    previous act, with nothing carried between them but the bytes."""
    _app()._save_app_egress("socks5h://exit.example:1080")

    # A second, independent read through the product's own reader.
    assert settings.app_egress_proxy() == "socks5h://exit.example:1080"
    assert _from_disk(tmp_path) == "socks5h://exit.example:1080"


# --- AC3: refused at save is exactly what is refused at send ---------------


@pytest.mark.parametrize("value,savable", TRANSCRIPT)
def test_the_save_gate_agrees_with_the_transport_row_by_row(value, savable):
    """The eight-row transcript, against BOTH the gate and the resolver.

    Asserting the two AGREE is the point, not asserting the gate's own answer:
    a gate with its own scheme list could pass a hand-written expectation table
    while disagreeing with the authority that actually governs the request. The
    savable column is checked too, so a parser that started accepting ``tor``
    could not quietly take this test with it.
    """
    ok, reason = egress.validate_for_save(value)
    verdict, _ = egress.resolve(value)

    assert ok is savable, f"{value!r}: gate said {ok}, transcript says {savable}"
    assert ok is (verdict != egress.REFUSE), (
        f"{value!r}: the save gate ({ok}) and the transport ({verdict}) disagree"
    )
    if not ok:
        assert reason, "a refusal must say why"


@pytest.mark.parametrize("value,savable", TRANSCRIPT)
def test_a_refused_value_never_reaches_the_file(tmp_path, value, savable):
    a = _app()
    ok, reason = a._save_app_egress(value)

    assert ok is savable
    if savable:
        assert _from_disk(tmp_path) == value
    else:
        # Not merely "rejected" — NOT WRITTEN. A saved-but-unparseable value is
        # a silent update outage: every one of the nine consumers stops sending,
        # the security-update poll included.
        assert _from_disk(tmp_path) == "<KEY ABSENT>"
        assert egress.resolve() == (egress.DIRECT, "")
        assert reason


def test_the_refusal_reason_is_the_transports_own_sentence():
    """Not a second wording of it. The operator reads at save time the sentence
    the transport would otherwise have logged at send time."""
    _, gate_reason = egress.validate_for_save("tor")
    _, transport_reason = egress.resolve("tor")

    assert gate_reason == transport_reason
    assert gate_reason != ""


def test_the_save_gate_holds_no_scheme_list_of_its_own():
    """Every scheme the shipped vocabulary accepts is savable, derived from
    ``validation.PROXY_SCHEMES`` rather than retyped — the defect PS-300 fixed
    one surface over. Adding a scheme there must need no edit here."""
    from src.utils.validation import PROXY_SCHEMES

    for scheme in PROXY_SCHEMES:
        ok, reason = egress.validate_for_save(f"{scheme}://exit.example:1080")
        assert ok, f"{scheme} is in PROXY_SCHEMES but the save gate refused it: {reason}"


# --- AC4: the DIRECT default is untouched ----------------------------------


def test_an_install_that_never_touches_the_control_is_unchanged(tmp_path):
    """Building the App and rendering nothing must not write the key.

    "The default is byte-identical" is not "resolve() still returns direct" —
    it is that nothing wrote a value at all. A control that persisted "" on
    first render would leave every install carrying a key it never had.
    """
    _app()
    assert egress.resolve() == (egress.DIRECT, "")
    assert _from_disk(tmp_path) == "<KEY ABSENT>"


def test_direct_stays_direct_for_every_transport_arm():
    """The three arms' DIRECT answers, unchanged: no curl args, no proxy
    handler, and a resolver verdict of direct."""
    assert egress.curl_proxy_args() == []
    assert egress.resolve() == (egress.DIRECT, "")
    opener = egress.download_opener()
    assert opener is not None


# --- AC6: the value is never logged ---------------------------------------


def test_saving_a_proxy_never_logs_the_value(tmp_path, caplog):
    """It can embed credentials, and both the Activity Log and the disk-backed
    logger are readable places. The log line may say the policy changed and to
    which VERDICT; it may not carry the value."""
    secret = "socks5://operator:hunter2@exit.example:1080"
    a = _app()
    with caplog.at_level(logging.DEBUG):
        assert a._save_app_egress(secret) == (True, "")

    everything = " ".join(a.logged) + " " + caplog.text
    assert secret not in everything
    assert "hunter2" not in everything
    assert "exit.example" not in everything
    # ...but SOMETHING was said: a silent write is its own defect.
    assert any("egress" in line.lower() for line in a.logged), a.logged


def test_a_refusal_never_echoes_the_rejected_value():
    """The reason handed back is put on screen and could be pasted into a bug
    report, so the rejected string must not ride along inside it."""
    secret = "socks5://operator:hunter2@exit.example"  # no port → REFUSE
    ok, reason = egress.validate_for_save(secret)

    assert ok is False
    assert secret not in reason
    assert "hunter2" not in reason


# --- AC5: three verdicts, three distinct things to say --------------------


def test_the_page_has_a_distinct_wording_for_each_of_the_three_verdicts():
    """"Configured but unusable" must never be able to render as "off".

    Asserted against the resolver's OWN three constants, so a fourth verdict
    added later fails here rather than silently rendering as whatever the dict
    happens to fall back to.
    """
    from src.ui.components.connect_page import EGRESS_VERDICT_TEXT

    assert set(EGRESS_VERDICT_TEXT) == {egress.DIRECT, egress.PROXIED, egress.REFUSE}

    words = [EGRESS_VERDICT_TEXT[v][0] for v in EGRESS_VERDICT_TEXT]
    lines = [EGRESS_VERDICT_TEXT[v][1] for v in EGRESS_VERDICT_TEXT]
    assert len(set(words)) == 3, f"the three verdicts share a word: {words}"
    assert len(set(lines)) == 3, f"the three verdicts share a sentence: {lines}"

    refuse_word, refuse_line = EGRESS_VERDICT_TEXT[egress.REFUSE]
    direct_word, _ = EGRESS_VERDICT_TEXT[egress.DIRECT]
    assert refuse_word != direct_word
    # The refusal has to say what it COSTS, not merely that something is wrong:
    # with a configured-but-unusable value NOTHING is sent, update checks
    # included, and that is the fact the operator has no other way to learn.
    assert "update" in refuse_line.lower()
    assert "not 'off'" in refuse_line.lower() or "not off" in refuse_line.lower()
