"""Deterministic transfer/elapsed calculations behind every progress readout.

Pure functions and one small estimator: no Qt timers, no widgets, and time is
always passed in, so every case here is exact rather than sleep-dependent.

The rules encoded below come from the accepted decisions:
  D17 -- never assert liveness; report the observed no-update age instead.
  D18 -- never show a number the app cannot back with real counts.
  D19/D23 -- elapsed counts *active* time, and a rate the app can no longer
             stand behind is withdrawn rather than left stale on screen.
"""

from PyQt6.QtCore import QLocale

from anki_miner.gui.utils.progress_telemetry import (
    ETA_MIN_BYTES,
    ETA_MIN_ELAPSED_S,
    ETA_MIN_SAMPLES,
    STALL_AFTER_S,
    SUSPEND_GAP_S,
    TransferEstimator,
    active_duration,
    format_clock,
    format_data_size,
    format_duration_words,
    format_transfer,
)

MB = 1024 * 1024


def _locale() -> QLocale:
    return QLocale(QLocale.Language.English, QLocale.Country.UnitedStates)


class TestFormatDataSize:
    def test_renders_the_owners_requested_strings(self):
        """The brief names these two literals specifically."""
        assert format_data_size(_locale(), 10 * MB + 400 * 1024) == "10.4 MB"
        assert format_data_size(_locale(), 600 * MB) == "600.0 MB"

    def test_small_values_use_bytes(self):
        assert format_data_size(_locale(), 999) == "999 bytes"

    def test_scales_to_gigabytes(self):
        assert format_data_size(_locale(), 3 * 1024**3) == "3.0 GB"


class TestFormatClock:
    def test_sub_hour_is_mm_ss(self):
        assert format_clock(0) == "00:00"
        assert format_clock(37) == "00:37"
        assert format_clock(61) == "01:01"
        assert format_clock(59 * 60 + 59) == "59:59"

    def test_hour_and_over_is_h_mm_ss(self):
        assert format_clock(3600) == "1:00:00"
        assert format_clock(3 * 3600 + 4 * 60 + 12) == "3:04:12"

    def test_negative_is_clamped(self):
        assert format_clock(-5) == "00:00"


class TestFormatDurationWords:
    def test_renders_the_receipt_form(self):
        """The receipt spells its duration out; the strip uses the clock form."""
        assert format_duration_words(40 * 60 + 12) == "40m 12s"
        assert format_duration_words(8 * 60 + 17) == "08m 17s"

    def test_seconds_only_runs_still_name_their_minutes(self):
        assert format_duration_words(9) == "00m 09s"

    def test_hours_are_prefixed(self):
        assert format_duration_words(3 * 3600 + 4 * 60 + 12) == "3h 04m 12s"

    def test_negative_is_clamped(self):
        assert format_duration_words(-5) == "00m 00s"


class TestActiveDuration:
    def test_the_two_clocks_agreeing_means_no_sleep(self):
        duration = active_duration(monotonic_start=10.0, monotonic_now=70.0, wall_start=500.0, wall_now=560.0)

        assert duration.active_s == 60.0
        assert duration.suspended_s == 0.0
        assert duration.suspended is False

    def test_wall_time_the_monotonic_clock_did_not_see_is_sleep(self):
        """A suspended machine freezes the monotonic clock, never the wall one."""
        duration = active_duration(monotonic_start=10.0, monotonic_now=70.0, wall_start=500.0, wall_now=4160.0)

        assert duration.active_s == 60.0
        assert duration.suspended_s == 3600.0
        assert duration.suspended is True

    def test_a_small_divergence_is_not_reported_as_sleep(self):
        duration = active_duration(
            monotonic_start=0.0,
            monotonic_now=60.0,
            wall_start=0.0,
            wall_now=60.0 + SUSPEND_GAP_S - 1,
        )

        assert duration.suspended is False

    def test_a_backwards_wall_clock_never_produces_negative_sleep(self):
        duration = active_duration(monotonic_start=0.0, monotonic_now=60.0, wall_start=0.0, wall_now=10.0)

        assert duration.active_s == 60.0
        assert duration.suspended_s == 0.0


class TestTransferEstimator:
    def test_first_sample_reports_no_rate(self):
        """One data point is not a rate."""
        est = TransferEstimator()

        stats = est.update(downloaded=0, total=600 * MB, now=0.0)

        assert stats.rate_bytes_per_s is None
        assert stats.eta_s is None

    def test_rate_emerges_from_steady_movement(self):
        est = TransferEstimator()
        est.update(downloaded=0, total=600 * MB, now=0.0)
        for i in range(1, 11):
            stats = est.update(downloaded=i * MB, total=600 * MB, now=float(i))

        assert stats.rate_bytes_per_s is not None
        # ~1 MB/s, smoothed -- assert the order of magnitude, not the exact EMA.
        assert 0.5 * MB < stats.rate_bytes_per_s < 1.5 * MB

    def test_eta_is_withheld_until_the_gates_pass(self):
        """No ETA from a couple of samples over a fraction of a second."""
        est = TransferEstimator()
        est.update(downloaded=0, total=600 * MB, now=0.0)
        stats = est.update(downloaded=ETA_MIN_BYTES + 1, total=600 * MB, now=0.1)

        assert stats.eta_s is None

    def test_eta_appears_once_gated(self):
        est = TransferEstimator()
        est.update(downloaded=0, total=600 * MB, now=0.0)
        for i in range(1, ETA_MIN_SAMPLES + int(ETA_MIN_ELAPSED_S) + 4):
            stats = est.update(downloaded=i * MB, total=600 * MB, now=float(i))

        assert stats.eta_s is not None
        assert stats.eta_s > 0

    def test_no_eta_without_a_total(self):
        """An absent Content-Length must not produce an invented remaining time."""
        est = TransferEstimator()
        est.update(downloaded=0, total=None, now=0.0)
        for i in range(1, 12):
            stats = est.update(downloaded=i * MB, total=None, now=float(i))

        assert stats.rate_bytes_per_s is not None
        assert stats.eta_s is None

    def test_rate_and_eta_are_withdrawn_once_movement_stops(self):
        est = TransferEstimator()
        est.update(downloaded=0, total=600 * MB, now=0.0)
        for i in range(1, 12):
            est.update(downloaded=i * MB, total=600 * MB, now=float(i))

        stalled = est.update(downloaded=11 * MB, total=600 * MB, now=11.0 + STALL_AFTER_S + 1)

        assert stalled.rate_bytes_per_s is None
        assert stalled.eta_s is None

    def test_reports_the_observed_no_update_age(self):
        """D17: state what was last seen, never that the worker is alive."""
        est = TransferEstimator()
        est.update(downloaded=0, total=600 * MB, now=0.0)
        est.update(downloaded=5 * MB, total=600 * MB, now=5.0)

        stats = est.update(downloaded=5 * MB, total=600 * MB, now=23.0)

        assert stats.no_update_age_s == 18.0

    def test_movement_resets_the_no_update_age(self):
        est = TransferEstimator()
        est.update(downloaded=0, total=600 * MB, now=0.0)
        est.update(downloaded=5 * MB, total=600 * MB, now=5.0)

        stats = est.update(downloaded=6 * MB, total=600 * MB, now=23.0)

        assert stats.no_update_age_s == 0.0

    def test_elapsed_excludes_a_suspend(self):
        """D23: a laptop that slept for an hour did not spend an hour working."""
        est = TransferEstimator()
        est.update(downloaded=0, total=600 * MB, now=0.0)
        est.update(downloaded=5 * MB, total=600 * MB, now=10.0)

        after_sleep = est.update(downloaded=6 * MB, total=600 * MB, now=3610.0)

        assert after_sleep.resumed is True
        assert after_sleep.active_elapsed_s < 20.0

    def test_a_busy_event_loop_is_not_a_suspend(self):
        """A few seconds of lag must not be misread as the machine sleeping."""
        est = TransferEstimator()
        est.update(downloaded=0, total=600 * MB, now=0.0)

        laggy = est.update(downloaded=1 * MB, total=600 * MB, now=4.0)

        assert laggy.resumed is False
        assert laggy.active_elapsed_s == 4.0

    def test_bytes_survive_a_suspend(self):
        est = TransferEstimator()
        est.update(downloaded=0, total=600 * MB, now=0.0)
        est.update(downloaded=312 * MB, total=600 * MB, now=10.0)

        after_sleep = est.update(downloaded=312 * MB, total=600 * MB, now=3610.0)

        assert after_sleep.downloaded == 312 * MB

    def test_a_backwards_byte_count_restarts_cleanly(self):
        """A retry that re-downloads from zero must not produce a negative rate."""
        est = TransferEstimator()
        est.update(downloaded=0, total=600 * MB, now=0.0)
        for i in range(1, 12):
            est.update(downloaded=i * MB, total=600 * MB, now=float(i))

        restarted = est.update(downloaded=0, total=600 * MB, now=12.0)

        assert restarted.rate_bytes_per_s is None
        assert restarted.eta_s is None


class TestFormatTransfer:
    def test_known_total_reads_as_the_owner_asked(self):
        est = TransferEstimator()
        est.update(downloaded=0, total=600 * MB, now=0.0)
        for i in range(1, 12):
            stats = est.update(downloaded=i * MB, total=600 * MB, now=float(i))

        line = format_transfer(_locale(), stats)

        assert line.startswith("11.0 MB / 600.0 MB")
        assert "Elapsed 00:11" in line

    def test_unknown_total_omits_the_denominator_and_eta(self):
        est = TransferEstimator()
        est.update(downloaded=0, total=None, now=0.0)
        for i in range(1, 12):
            stats = est.update(downloaded=i * MB, total=None, now=float(i))

        line = format_transfer(_locale(), stats)

        assert " / " not in line  # no denominator; the "MB/s" slash is not one
        assert "left" not in line
        assert line.startswith("11.0 MB downloaded")

    def test_a_stalled_transfer_states_the_silence(self):
        est = TransferEstimator()
        est.update(downloaded=0, total=600 * MB, now=0.0)
        est.update(downloaded=5 * MB, total=600 * MB, now=5.0)
        stats = est.update(downloaded=5 * MB, total=600 * MB, now=25.0)

        line = format_transfer(_locale(), stats)

        assert "No update for 20 s" in line
        assert "/s" not in line  # a rate it can no longer stand behind is withdrawn

    def test_a_resumed_transfer_is_marked(self):
        est = TransferEstimator()
        est.update(downloaded=0, total=600 * MB, now=0.0)
        est.update(downloaded=5 * MB, total=600 * MB, now=10.0)
        stats = est.update(downloaded=6 * MB, total=600 * MB, now=3610.0)

        assert "Resumed" in format_transfer(_locale(), stats)
