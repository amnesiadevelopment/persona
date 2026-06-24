from src.ui import progress_fmt as pf


def test_fmt_mb():
    assert pf.fmt_mb(0) == "0.0 MB"
    assert pf.fmt_mb(118_000_000) == "118.0 MB"


def test_fmt_speed():
    assert pf.fmt_speed(2_100_000) == "2.1 MB/s"
    assert pf.fmt_speed(0) == "0.0 MB/s"


def test_fmt_eta_normal():
    # 60 MB left at 2 MB/s -> 30s
    assert pf.fmt_eta(done=58_000_000, total=118_000_000, rate=2_000_000) == "30s left"


def test_fmt_eta_minutes():
    assert pf.fmt_eta(done=0, total=240_000_000, rate=2_000_000) == "2m 00s left"


def test_fmt_eta_done_or_unknown():
    assert pf.fmt_eta(done=100, total=100, rate=5) == ""
    assert pf.fmt_eta(done=10, total=0, rate=5) == ""
    assert pf.fmt_eta(done=10, total=100, rate=0) == ""


def test_fraction():
    assert pf.fraction(50, 100) == 0.5
    assert pf.fraction(0, 0) is None  # indeterminate


def test_percent():
    assert pf.percent(33, 100) == 33
    assert pf.percent(0, 0) == 0


def test_fmt_line_full():
    line = pf.fmt_line(done=50_000_000, total=118_000_000, elapsed=25.0)
    assert "MB" in line
    assert "MB/s" in line
    assert "of" in line


def test_fmt_line_zero_total():
    # streaming with unknown size: just show downloaded amount
    line = pf.fmt_line(done=5_000_000, total=0, elapsed=2.0)
    assert "MB" in line
