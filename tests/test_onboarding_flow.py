from src.ui import onboarding_flow as flow


def test_has_welcome_and_engine_steps():
    steps = flow.steps()
    ids = [s["id"] for s in steps]
    assert ids[0] == "welcome"
    assert "engine" in ids


def test_welcome_lists_features():
    welcome = flow.steps()[0]
    assert welcome["features"]
    # each feature is (title, description)-ish dict with a label
    assert all("label" in f for f in welcome["features"])


def test_features_describe_persona_not_donut():
    labels = " ".join(f["label"].lower() for f in flow.steps()[0]["features"])
    assert "fingerprint" in labels
    assert "proxy" in labels
    # we don't claim Donut-only features
    assert "wireguard" not in labels


def test_engine_step_is_last():
    assert flow.steps()[-1]["id"] == "engine"


def test_step_count_at_least_two():
    assert len(flow.steps()) >= 2


def test_next_index_clamps():
    n = len(flow.steps())
    assert flow.next_index(0) == 1
    assert flow.next_index(n - 1) == n - 1  # can't go past last


def test_prev_index_clamps():
    assert flow.prev_index(0) == 0
    assert flow.prev_index(2) == 1


def test_is_last_index():
    n = len(flow.steps())
    assert flow.is_last(n - 1) is True
    assert flow.is_last(0) is False
