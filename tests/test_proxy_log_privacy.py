"""The proxy activity-log message must never carry the exit IP — a disk-backed,
UI-visible IP history de-anonymizes the operator and links personas.
"""
import re

from src.utils.proxy_checker import proxy_ok_message


def test_message_has_country_not_ip():
    msg = proxy_ok_message("PL", "Poland")
    assert "Poland" in msg
    assert "Proxy working" in msg
    # no dotted-quad or bracketed IPv6 in the user-facing message
    assert not re.search(r"\d{1,3}(?:\.\d{1,3}){3}", msg)
    assert ":" not in msg  # no IPv6 either


def test_message_without_country_is_still_clean():
    msg = proxy_ok_message("", "")
    assert msg == "Proxy working."
    assert not re.search(r"\d{1,3}(?:\.\d{1,3}){3}", msg)
