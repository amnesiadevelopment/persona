from src.ui.app import _log_line_control, _log_message_color
from src.ui.theme import COLORS


def test_failure_is_red():
    assert _log_message_color("Update download failed") == COLORS["error"]
    assert _log_message_color("Engine update failed") == COLORS["error"]


def test_completion_is_green():
    assert _log_message_color("Browser started!") == COLORS["success"]
    assert _log_message_color("[Test IE] imported 12 cookies") == COLORS["success"]
    assert _log_message_color("Engine installed: v1.1.0") == COLORS["success"]


def test_update_is_blue():
    c = _log_message_color("New version v1.1.0 available")
    assert c not in (COLORS["error"], COLORS["success"], COLORS["text_dim"])


def test_plain_is_dim():
    assert _log_message_color("Tagged 3 profiles") == COLORS["text_dim"]


def test_line_control_splits_timestamp_and_message():
    ctrl = _log_line_control("20:14:03  > Browser started!")
    # two spans: dim timestamp + coloured message
    assert ctrl.spans is not None
    assert len(ctrl.spans) == 2
    assert "20:14:03" in ctrl.spans[0].text
    assert "Browser started!" in ctrl.spans[1].text


def test_line_control_malformed_falls_back():
    ctrl = _log_line_control("no separator here")
    # single dim text, no spans
    assert getattr(ctrl, "value", None) == "no separator here"
