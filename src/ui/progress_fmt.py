"""Human-readable formatting for download progress (size, speed, ETA),
shared by the sidebar engine panel and the onboarding dialog.
"""


def fmt_mb(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB"


def fmt_speed(rate: float) -> str:
    return f"{rate / 1_000_000:.1f} MB/s"


def fmt_eta(done: int, total: int, rate: float) -> str:
    if rate <= 0 or total <= 0 or done >= total:
        return ""
    secs = int((total - done) / rate)
    m, s = divmod(secs, 60)
    return f"{m}m {s:02d}s left" if m else f"{s}s left"


def fraction(done: int, total: int) -> float | None:
    """Completion fraction 0..1, or None when total is unknown."""
    if total <= 0:
        return None
    return done / total


def percent(done: int, total: int) -> int:
    if total <= 0:
        return 0
    return done * 100 // total


def fmt_line(done: int, total: int, elapsed: float) -> str:
    """One-line summary: '50.0 MB of 118.0 MB   2.0 MB/s   30s left'.
    With unknown total, falls back to the downloaded amount and speed.
    """
    rate = done / elapsed if elapsed > 0 else 0.0
    if total <= 0:
        return f"{fmt_mb(done)}   {fmt_speed(rate)}"
    eta = fmt_eta(done, total, rate)
    parts = [f"{fmt_mb(done)} of {fmt_mb(total)}", fmt_speed(rate)]
    if eta:
        parts.append(eta)
    return "   ".join(parts)
