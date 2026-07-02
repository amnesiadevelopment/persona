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


def test_throttle_first_call_always_fires():
    t = pf.ProgressThrottle(min_interval=0.1)
    assert t.should_emit(done=0, total=100, now=0.0) is True


def test_throttle_suppresses_rapid_same_percent():
    t = pf.ProgressThrottle(min_interval=0.1)
    assert t.should_emit(done=0, total=1000, now=0.0) is True
    # 1ms later, still 0% -> suppressed
    assert t.should_emit(done=1, total=1000, now=0.001) is False
    assert t.should_emit(done=2, total=1000, now=0.002) is False


def test_throttle_fires_on_percent_change():
    t = pf.ProgressThrottle(min_interval=10.0)  # long interval
    assert t.should_emit(done=0, total=100, now=0.0) is True
    # same instant, but the whole percent advanced -> fire
    assert t.should_emit(done=1, total=100, now=0.0) is True
    assert t.should_emit(done=2, total=100, now=0.0) is True


def test_throttle_fires_after_interval_even_without_percent_change():
    t = pf.ProgressThrottle(min_interval=0.1)
    assert t.should_emit(done=0, total=0, now=0.0) is True  # unknown total, 0%
    assert t.should_emit(done=1_000_000, total=0, now=0.05) is False
    # interval elapsed -> fire so an unknown-total download still ticks
    assert t.should_emit(done=2_000_000, total=0, now=0.2) is True


def test_throttle_always_fires_on_completion():
    t = pf.ProgressThrottle(min_interval=10.0)
    assert t.should_emit(done=0, total=100, now=0.0) is True
    assert t.should_emit(done=50, total=100, now=0.0) is True
    # even within the interval and after a recent emit, completion must show
    t.should_emit(done=51, total=100, now=0.01)  # 51%, fires
    assert t.should_emit(done=100, total=100, now=0.02) is True


def test_progress_state_percent_is_monotonic():
    s = pf.ProgressState()
    s.update(done=50, total=100, now=1.0)
    assert s.percent == 50
    # a lower reading (e.g. a retry restarting the byte count) must NOT move
    # the displayed percent backwards
    s.update(done=10, total=100, now=2.0)
    assert s.percent == 50
    s.update(done=80, total=100, now=3.0)
    assert s.percent == 80


def test_progress_state_fraction_is_monotonic():
    s = pf.ProgressState()
    s.update(done=60, total=100, now=1.0)
    assert s.fraction == 0.6
    s.update(done=20, total=100, now=2.0)  # retry reset
    assert s.fraction == 0.6  # bar does not jump back


def test_progress_state_speed_is_ema_smoothed():
    s = pf.ProgressState(alpha=0.3)
    # first update has no prior sample; second seeds the EMA at a steady rate
    s.update(done=0, total=100_000_000, now=0.0)
    s.update(done=1_000_000, total=100_000_000, now=1.0)  # seed EMA ~1 MB/s
    seeded = s.speed
    assert 0 < seeded <= 1_200_000
    # a big instantaneous spike must be damped by the EMA, not shown raw
    s.update(done=51_000_000, total=100_000_000, now=2.0)  # ~50 MB/s raw sample
    assert s.speed < 50_000_000  # smoothed, well below the raw spike
    assert s.speed > seeded  # but it did move toward the spike


def test_progress_state_unknown_total_keeps_bytes():
    s = pf.ProgressState()
    s.update(done=5_000_000, total=0, now=1.0)
    assert s.percent == 0
    assert s.fraction is None
    assert s.done == 5_000_000
