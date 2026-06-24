from src.utils.timefmt import humanize_since


def test_never():
    assert humanize_since(0, 1000) == "never"


def test_just_now():
    assert humanize_since(1000, 1030) == "just now"


def test_minutes():
    assert humanize_since(1000, 1000 + 5 * 60) == "5m ago"


def test_hours():
    assert humanize_since(0 + 1000, 1000 + 23 * 3600) == "23h ago"


def test_days():
    assert humanize_since(1000, 1000 + 3 * 86400) == "3d ago"


def test_future_clamped():
    assert humanize_since(2000, 1000) == "just now"
