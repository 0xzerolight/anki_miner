"""Tests for ProgressWidget (Issue: batch progress bar jumped to ~70%).

The QProgressBar's native scale is fixed at 0-100 because ``set_progress``
always converts ``current/total`` to a percentage. ``set_determinate`` must
NOT change the bar's maximum to ``maximum`` (the item count), otherwise
``setValue(percentage)`` against ``setMaximum(item_count)`` paints garbage
(e.g. value=8 on max=12 → 67% width on the first of 12 episodes).
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.progress_widget import ProgressWidget


@pytest.fixture
def widget(qapp, qtbot):
    w = ProgressWidget()
    qtbot.addWidget(w)
    yield w
    w.deleteLater()


def test_set_determinate_keeps_progress_bar_max_at_100(widget):
    """Regression: set_determinate(12) must NOT set bar max to 12."""
    widget.set_determinate(12)
    assert widget.progress_bar.maximum() == 100


def test_set_determinate_stores_total_for_stats(widget):
    """Item count is still tracked separately for ETA/rate stats."""
    widget.set_determinate(12)
    assert widget.total == 12


def test_set_progress_first_step_renders_8_percent_on_12_items(widget):
    """Regression: 1/12 should render at 8%, not 67% (bug 1 repro)."""
    widget.set_determinate(12)
    widget.set_progress(1, 12, "Episode 1")
    assert widget.progress_bar.value() == 8


def test_set_progress_full_renders_100_percent(widget):
    widget.set_determinate(12)
    widget.set_progress(12, 12, "Done")
    assert widget.progress_bar.value() == 100


def test_set_progress_advances_smoothly_episode_by_episode(widget):
    """Each step adds roughly 100/N percent; no jump on first step."""
    widget.set_determinate(5)
    expected = [0, 20, 40, 60, 80, 100]
    actual = []
    for i in range(6):
        widget.set_progress(i, 5, f"Step {i}")
        actual.append(widget.progress_bar.value())
    assert actual == expected


def test_progress_bar_text_hidden(widget):
    """No centered X/100 text painted on the bar, in any state."""
    assert widget.progress_bar.isTextVisible() is False
    widget.set_determinate(12)
    widget.set_progress(1, 12, "Episode 1")
    assert widget.progress_bar.isTextVisible() is False


def test_reset_restores_default_state(widget):
    widget.set_determinate(7)
    widget.set_progress(3, 7, "Mid")
    widget.reset()
    assert widget.progress_bar.maximum() == 100
    assert widget.progress_bar.value() == 0
    assert widget.status_label.text() == "Ready"


# ---------------------------------------------------------------------------
# set_percent / set_composed / show_completion (progress overhaul)
# ---------------------------------------------------------------------------


def test_set_percent_clamps_and_sets_value(widget):
    widget.set_percent(150)
    assert widget.progress_bar.value() == 100
    widget.set_percent(-5)
    assert widget.progress_bar.value() == 0
    widget.set_percent(42)
    assert widget.progress_bar.value() == 42


def test_set_percent_recovers_from_indeterminate(widget):
    widget.set_indeterminate()
    assert widget.progress_bar.maximum() == 0
    widget.set_percent(30)
    assert widget.progress_bar.maximum() == 100
    assert widget.progress_bar.value() == 30


def test_set_percent_keeps_eta_units_in_percent(widget):
    widget.set_determinate(12)  # would seed _total_items=12
    widget.set_percent(50)
    assert widget.total == 100


def test_set_percent_falsy_status_does_not_blank_label(widget):
    widget.set_percent(10, "Fetching definitions")
    widget.set_percent(100, "")
    assert widget.status_label.text() == "Fetching definitions"
    widget.set_percent(100, None)
    assert widget.status_label.text() == "Fetching definitions"


def test_set_composed_counts_finished_items(widget):
    """D18: the bar is finished items over total, nothing else."""
    widget.set_composed(2, 0, 4, "Episode 3/4")
    assert widget.progress_bar.value() == 50
    assert widget.status_label.text() == "Episode 3/4"


def test_set_composed_ignores_the_current_items_own_progress(widget):
    """A part-done item is not a part-done run: only whole items count.

    Believing otherwise is what made a queue race through five short files and
    then look frozen for an hour on a long one.
    """
    widget.set_composed(2, 99, 4)
    assert widget.progress_bar.value() == 50


def test_set_composed_zero_total_is_noop(widget):
    widget.set_percent(37)
    widget.set_composed(0, 50, 0, "nope")
    assert widget.progress_bar.value() == 37
    assert widget.status_label.text() != "nope"


# ---------------------------------------------------------------------------
# Freezing on cancel (D22)
# ---------------------------------------------------------------------------


def test_freeze_holds_the_last_true_value(widget):
    widget.set_percent(40, "Extracting media")
    widget.freeze()
    assert widget.progress_bar.value() == 40


def test_a_frozen_bar_ignores_later_progress(widget):
    widget.set_percent(40, "Extracting media")
    widget.freeze()

    widget.set_percent(90)
    widget.set_composed(9, 0, 10)
    widget.set_progress(9, 10)
    widget.set_value(95)

    assert widget.progress_bar.value() == 40


def test_a_frozen_bar_still_accepts_words(widget):
    widget.set_percent(40, "Extracting media")
    widget.freeze()

    widget.set_status("Cancelled")

    assert widget.status_label.text() == "Cancelled"
    assert widget.progress_bar.value() == 40


def test_freezing_a_marquee_stops_it_spinning(widget):
    """An indeterminate bar left running after cancel reads as work still going."""
    widget.set_indeterminate()
    widget.freeze()
    assert widget.progress_bar.maximum() == 100


def test_reset_thaws_the_bar_for_the_next_run(widget):
    widget.set_percent(40)
    widget.freeze()

    widget.reset()
    widget.set_percent(70)

    assert widget.progress_bar.value() == 70


def test_show_completion_pins_100_and_freezes_stats(widget):
    widget.set_percent(40, "working")
    widget.show_completion("Complete — 87 cards created")
    assert widget.progress_bar.value() == 100
    assert widget.status_label.text() == "Complete — 87 cards created"
    # Late straggler updates must not resurrect the ETA line.
    stats_after = widget.stats_label.text()
    widget.set_percent(100, "")
    assert "ETA" not in widget.stats_label.text()
    assert widget.progress_bar.value() == 100
    del stats_after


def test_stats_line_has_no_rate_display(widget):
    widget.set_percent(50, "working")
    assert "/sec" not in widget.stats_label.text()


class TestClockTypeface:
    """The running clock used to ask for 'Consolas' at a constant 12px.

    Consolas exists on Windows and nowhere else, and the constant meant the
    clock alone ignored the text-size setting while the label beside it grew
    (decision D44-B).
    """

    def test_the_clock_uses_the_platform_fixed_font(self, widget):
        from anki_miner.gui.utils.fonts import resolved_families

        assert widget.stats_label.font().family() == resolved_families().monospace

    def test_the_clock_never_asks_for_a_windows_only_family(self, widget):
        assert widget.stats_label.font().family() != "Consolas"

    def test_the_clock_follows_the_text_size_setting(self, qtbot, text_scale):
        from anki_miner.gui.widgets.progress_widget import ProgressWidget

        baseline = ProgressWidget()
        qtbot.addWidget(baseline)
        small = baseline.stats_label.font().pixelSize()

        text_scale(2.0)
        scaled = ProgressWidget()
        qtbot.addWidget(scaled)
        assert scaled.stats_label.font().pixelSize() == 2 * small


@pytest.fixture
def text_scale():
    """Yield ``apply(scale)``, restoring the global text scale afterwards.

    Only the scale is changed, never the application stylesheet: these widgets
    are never shown, so their Python font is the one that answers.
    """
    from anki_miner.gui.resources.styles.theme import Theme

    original = Theme.get_font_scale()
    yield Theme.set_font_scale
    Theme.set_font_scale(original)
