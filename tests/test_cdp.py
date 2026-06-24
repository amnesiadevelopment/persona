from src.services.browser.cdp import cdp_port_for


def test_port_in_valid_range():
    p = cdp_port_for("test8")
    assert 9222 <= p <= 9322


def test_port_deterministic():
    assert cdp_port_for("acc") == cdp_port_for("acc")


def test_distinct_names_usually_distinct():
    ports = {cdp_port_for(f"p{i}") for i in range(20)}
    # not a hard guarantee, but 20 names should spread across the range
    assert len(ports) > 10
