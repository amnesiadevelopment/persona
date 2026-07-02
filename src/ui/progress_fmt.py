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


class ProgressThrottle:
    """Decides when a download-progress update is worth redrawing.

    A download reports progress on every chunk (~hundreds per second on a fast
    connection). Redrawing the UI that often makes unrelated controls flicker
    and stutter. Emit only when the whole percent advanced, or a minimum
    interval has passed (so an unknown-size download still visibly ticks), or
    the download completed. The first call always emits.
    """

    def __init__(self, min_interval: float = 0.1) -> None:
        self.min_interval = min_interval
        self._last_t: float | None = None
        self._last_pct: int | None = None

    def should_emit(self, done: int, total: int, now: float) -> bool:
        pct = percent(done, total)
        complete = total > 0 and done >= total
        if self._last_t is None:
            self._last_t = now
            self._last_pct = pct
            return True
        if complete or pct != self._last_pct or (now - self._last_t) >= self.min_interval:
            self._last_t = now
            self._last_pct = pct
            return True
        return False


class ProgressState:
    """Smoothed, monotonic view of a download's progress.

    A raw byte count can jump backwards (a retry restarts the transfer, a
    resume re-reports an earlier offset) and the instantaneous rate swings
    wildly chunk-to-chunk. Both make the progress UI look broken. This keeps a
    percent/fraction that never decreases and an EMA-smoothed speed, so the
    displayed values move steadily the way a polished downloader's do.

    alpha is the EMA weight on the newest speed sample (tqdm's default 0.3):
    lower = smoother/laggier, higher = twitchier.
    """

    def __init__(self, alpha: float = 0.3) -> None:
        self.alpha = alpha
        self.done = 0
        self.total = 0
        self._max_frac = 0.0
        self._speed = 0.0
        self._last_done = 0
        self._last_t: float | None = None

    def update(self, done: int, total: int, now: float) -> None:
        # A new total means a new phase of the download (a retry, or a resume
        # that reports a different content length): the old accumulated
        # done/percent no longer refer to the same target, so reset them.
        # Otherwise the percent (from the old total) and the "X of Y" line (from
        # the new total) disagree — e.g. 46% shown next to "102 of 125 MB".
        if total > 0 and total != self.total:
            self.done = 0
            self._max_frac = 0.0
            self._last_done = 0
        self.total = total
        # Monotonic within a phase: never let the shown amount fall below what
        # we've seen for THIS total.
        self.done = max(self.done, done)
        if total > 0:
            self._max_frac = max(self._max_frac, self.done / total)
        # EMA speed from the delta since the last sample. Ignore non-positive
        # deltas (a retry/resume rewind) so they don't produce a negative rate.
        if self._last_t is not None:
            dt = now - self._last_t
            dbytes = self.done - self._last_done
            if dt > 0 and dbytes > 0:
                sample = dbytes / dt
                self._speed = (
                    sample
                    if self._speed <= 0
                    else self.alpha * sample + (1 - self.alpha) * self._speed
                )
        self._last_t = now
        self._last_done = self.done

    @property
    def percent(self) -> int:
        return int(self._max_frac * 100)

    @property
    def fraction(self) -> float | None:
        return self._max_frac if self.total > 0 else None

    @property
    def speed(self) -> float:
        return self._speed

    def line(self) -> str:
        """One-line 'X MB of Y MB   Z MB/s   Ns left' using the smoothed speed."""
        if self.total <= 0:
            return f"{fmt_mb(self.done)}   {fmt_speed(self._speed)}"
        parts = [f"{fmt_mb(self.done)} of {fmt_mb(self.total)}", fmt_speed(self._speed)]
        eta = fmt_eta(self.done, self.total, self._speed)
        if eta:
            parts.append(eta)
        return "   ".join(parts)


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
