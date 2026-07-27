"""Deterministic transfer, elapsed and silence calculations for progress readouts.

Pure computation: no Qt timers, no widgets, no wall-clock reads. Callers pass the
current monotonic time in, which is what makes every case exactly testable instead
of sleep-dependent.

Three rules from the accepted decisions are encoded here rather than left to each
call site, because each was previously re-invented and got a different answer:

* **Never show a number the app cannot back.** A rate needs two real samples; an
  ETA additionally needs enough samples, elapsed time and moved bytes to be worth
  printing, and a total to divide into. Otherwise the field is ``None`` and the
  formatter omits it rather than printing a guess.
* **Never assert liveness.** When bytes stop moving the rate and ETA are
  *withdrawn* and the observed silence is stated instead. A number left on screen
  would be the app claiming something it no longer knows.
* **Elapsed means active time.** A machine that slept for an hour did not spend an
  hour working, and a rate averaged across the gap is meaningless. Suspends are
  excluded from elapsed and reset the rate window; the resumption is flagged so
  the UI can say so.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PyQt6.QtCore import QLocale

#: Rate smoothing time constant, in seconds. Larger is steadier but slower to
#: react; 3s keeps a chunked HTTP transfer legible without lagging a real stall.
_RATE_TAU_S = 3.0

#: An ETA is withheld until all three gates pass. Early ETAs swing wildly, and a
#: remaining-time estimate that visibly jumps reads as broken.
ETA_MIN_SAMPLES = 3
ETA_MIN_ELAPSED_S = 2.0
ETA_MIN_BYTES = 256 * 1024

#: With no movement for this long, the rate and ETA are withdrawn.
STALL_AFTER_S = 2.0

#: A gap larger than this between updates is treated as the machine having
#: suspended rather than the event loop being busy. Deliberately far above any
#: plausible GUI stall: misclassifying lag as a suspend would silently stop the
#: elapsed clock, which is the exact dishonesty this module exists to prevent.
SUSPEND_GAP_S = 30.0


@dataclass(frozen=True)
class ActiveDuration:
    """How long a run actually worked, and how much of the span it slept through."""

    active_s: float
    suspended_s: float

    @property
    def suspended(self) -> bool:
        """Whether the machine slept long enough to be worth saying so."""
        return self.suspended_s >= SUSPEND_GAP_S


def active_duration(
    *,
    monotonic_start: float,
    monotonic_now: float,
    wall_start: float,
    wall_now: float,
) -> ActiveDuration:
    """Measure a span in active time, excluding any suspend, and flag it.

    The two clocks are the whole mechanism. A suspended machine freezes the
    monotonic clock and never the wall clock, so the *difference* between the
    two spans is the time the process was not running. Reading it this way needs
    no ticker and produces no false positive: on a platform whose monotonic
    clock does keep counting through sleep the two spans simply agree, and
    nothing is excluded rather than something being guessed.

    Args:
        monotonic_start: ``time.monotonic()`` when the run began.
        monotonic_now: ``time.monotonic()`` now.
        wall_start: ``time.time()`` when the run began.
        wall_now: ``time.time()`` now.
    """
    active = max(0.0, monotonic_now - monotonic_start)
    # Clamped: a wall clock corrected backwards (NTP, a manual change) must not
    # subtract from a span it knows nothing about.
    suspended = max(0.0, (wall_now - wall_start) - active)
    return ActiveDuration(active_s=active, suspended_s=suspended)


@dataclass(frozen=True)
class TransferStats:
    """A snapshot of one transfer. ``None`` means "not known", never "zero"."""

    downloaded: int
    total: int | None
    active_elapsed_s: float
    rate_bytes_per_s: float | None
    eta_s: float | None
    no_update_age_s: float
    resumed: bool

    @property
    def fraction(self) -> float | None:
        """Completed fraction, or None when there is no honest denominator."""
        if not self.total:
            return None
        return min(1.0, self.downloaded / self.total)


class TransferEstimator:
    """Turns successive (bytes, time) observations into a truthful snapshot.

    One instance per transfer. Feed it every progress observation; it owns no
    timer, so a caller that wants the elapsed clock to keep moving during silence
    must keep calling ``update`` with the unchanged byte count.
    """

    def __init__(self) -> None:
        self._started_at: float | None = None
        self._last_time: float | None = None
        self._last_bytes = 0
        self._suspended_s = 0.0
        self._samples = 0
        self._ema: float | None = None
        self._last_movement_at: float | None = None
        self._resumed = False

    def reset_window(self) -> None:
        """Discard the rate window, keeping elapsed and byte totals.

        Used when a figure computed across the gap would be meaningless -- after
        a suspend, or when a retry restarts the byte count.
        """
        self._samples = 0
        self._ema = None

    def update(self, *, downloaded: int, total: int | None, now: float) -> TransferStats:
        """Record an observation and return the resulting snapshot.

        Args:
            downloaded: Bytes transferred so far.
            total: Expected total, or None when the server sent no length.
            now: A monotonic timestamp in seconds.
        """
        if self._started_at is None:
            self._started_at = now
            self._last_time = now
            self._last_bytes = downloaded
            self._last_movement_at = now
            return self._snapshot(downloaded, total, now)

        assert self._last_time is not None
        dt = now - self._last_time

        if dt >= SUSPEND_GAP_S:
            # Time passed but the machine was not working. Exclude it from
            # elapsed and drop the rate window rather than averaging across it.
            self._suspended_s += dt
            self._resumed = True
            self.reset_window()
        elif downloaded < self._last_bytes:
            # A retry restarted the transfer; a negative delta is not a rate.
            self.reset_window()
        elif dt > 0 and downloaded > self._last_bytes:
            instant = (downloaded - self._last_bytes) / dt
            alpha = 1.0 - math.exp(-dt / _RATE_TAU_S)
            self._ema = instant if self._ema is None else self._ema + alpha * (instant - self._ema)
            self._samples += 1

        if downloaded != self._last_bytes:
            self._last_movement_at = now

        self._last_time = now
        self._last_bytes = downloaded
        return self._snapshot(downloaded, total, now)

    def _snapshot(self, downloaded: int, total: int | None, now: float) -> TransferStats:
        started = self._started_at if self._started_at is not None else now
        active = max(0.0, now - started - self._suspended_s)
        age = 0.0 if self._last_movement_at is None else max(0.0, now - self._last_movement_at)

        rate = self._ema
        if rate is not None and (age >= STALL_AFTER_S or rate <= 0):
            rate = None  # Stopped moving: withdraw rather than leave it stale.

        eta = None
        if (
            rate
            and total
            and self._samples >= ETA_MIN_SAMPLES
            and active >= ETA_MIN_ELAPSED_S
            and downloaded >= ETA_MIN_BYTES
            and total > downloaded
        ):
            eta = (total - downloaded) / rate

        return TransferStats(
            downloaded=downloaded,
            total=total,
            active_elapsed_s=active,
            rate_bytes_per_s=rate,
            eta_s=eta,
            no_update_age_s=age,
            resumed=self._resumed,
        )


def format_data_size(locale: QLocale, value: int) -> str:
    """Format a byte count the way the rest of the desktop does.

    ``DataSizeTraditionalFormat`` with one decimal is what yields ``600.0 MB``;
    the default gives ``600.00 MiB`` and the SI format gives ``629.1 MB``.
    """
    return locale.formattedDataSize(value, 1, QLocale.DataSizeFormat.DataSizeTraditionalFormat)


def format_clock(seconds: float) -> str:
    """Format a duration as ``mm:ss``, or ``h:mm:ss`` once it reaches an hour."""
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_duration_words(seconds: float) -> str:
    """Format a duration as ``40m 12s``, or ``3h 04m 12s`` once it reaches an hour.

    The spelled-out form is for prose -- a finished run's receipt reads as a
    sentence, and ``40:12`` inside one reads as a timestamp. Live readouts keep
    :func:`format_clock`, which is what a clock ticking in place should look
    like.
    """
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes:02d}m {secs:02d}s"


def format_transfer(locale: QLocale, stats: TransferStats) -> str:
    """Render a transfer as one line, omitting every field that is not known.

    With a total: ``10.4 MB / 600.0 MB · 1.1 MB/s · Elapsed 00:37 · 02:20 left``
    Without one: ``10.4 MB downloaded · 1.1 MB/s · Elapsed 00:37``
    Stalled:     ``10.4 MB / 600.0 MB · Elapsed 00:41 · No update for 20 s``
    """
    amount = format_data_size(locale, stats.downloaded)
    total = f" / {format_data_size(locale, stats.total)}" if stats.total else " downloaded"
    parts = [f"{amount}{total}"]

    if stats.rate_bytes_per_s:
        parts.append(f"{format_data_size(locale, int(stats.rate_bytes_per_s))}/s")

    elapsed = f"Elapsed {format_clock(stats.active_elapsed_s)}"
    if stats.resumed:
        elapsed = f"{elapsed} · Resumed"
    parts.append(elapsed)

    if stats.eta_s is not None:
        parts.append(f"{format_clock(stats.eta_s)} left")

    if stats.no_update_age_s >= STALL_AFTER_S:
        parts.append(f"No update for {int(stats.no_update_age_s)} s")

    return " · ".join(parts)
