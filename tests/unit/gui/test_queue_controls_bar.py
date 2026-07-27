"""Tests for the queue controls bar (D28).

The bar is the queue's manipulation surface: five filter chips, a search box, a
live counter, and the three actions that operate on the selection. It owns no
queue -- it reports what the user asked for and renders the counts it is given.
"""

from __future__ import annotations

from anki_miner.gui.widgets.queue_controls_bar import QUEUE_FILTERS, QueueControlsBar


def _bar(qtbot) -> QueueControlsBar:
    bar = QueueControlsBar()
    qtbot.addWidget(bar)
    return bar


# ---------------------------------------------------------------------------
# Filter chips
# ---------------------------------------------------------------------------


def test_five_filters_in_a_fixed_order() -> None:
    assert QUEUE_FILTERS == ("all", "ready", "running", "failed", "complete")


def test_all_is_the_default_filter(qtbot) -> None:
    bar = _bar(qtbot)
    assert bar.active_filter() == "all"


def test_chips_are_exclusive(qtbot) -> None:
    bar = _bar(qtbot)

    bar.filter_buttons["failed"].click()

    assert bar.active_filter() == "failed"
    assert bar.filter_buttons["all"].isChecked() is False


def test_choosing_a_chip_emits_the_filter(qtbot) -> None:
    bar = _bar(qtbot)
    seen: list[str] = []
    bar.filter_changed.connect(seen.append)

    bar.filter_buttons["ready"].click()

    assert seen == ["ready"]


def test_reclicking_the_active_chip_does_not_deselect_it(qtbot) -> None:
    """A filter is always active; there is no fourth 'no filter' state."""
    bar = _bar(qtbot)
    bar.filter_buttons["ready"].click()

    bar.filter_buttons["ready"].click()

    assert bar.active_filter() == "ready"


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def test_search_starts_empty(qtbot) -> None:
    bar = _bar(qtbot)
    assert bar.search_text() == ""


def test_typing_emits_the_search_text(qtbot) -> None:
    bar = _bar(qtbot)
    seen: list[str] = []
    bar.search_changed.connect(seen.append)

    bar.search_edit.setText("episode 3")

    assert seen == ["episode 3"]
    assert bar.search_text() == "episode 3"


# ---------------------------------------------------------------------------
# Counter
# ---------------------------------------------------------------------------


def test_counter_states_the_four_numbers(qtbot) -> None:
    bar = _bar(qtbot)

    bar.set_counts(total=200, ready=153, failed=7, complete=28)

    assert bar.counter_label.text() == "200 queued · 153 ready · 7 failed · 28 complete"


def test_counter_starts_at_zero(qtbot) -> None:
    bar = _bar(qtbot)
    assert bar.counter_label.text() == "0 queued · 0 ready · 0 failed · 0 complete"


# ---------------------------------------------------------------------------
# Selection actions
# ---------------------------------------------------------------------------


def test_actions_start_disabled(qtbot) -> None:
    """Nothing is selected at construction, so nothing can act on a selection."""
    bar = _bar(qtbot)

    assert bar.run_button.isEnabled() is False
    assert bar.retry_button.isEnabled() is False
    assert bar.remove_button.isEnabled() is False


def test_each_action_emits_its_own_signal(qtbot) -> None:
    bar = _bar(qtbot)
    bar.set_actions_enabled(run=True, retry=True, remove=True)
    fired: list[str] = []
    bar.run_selected.connect(lambda: fired.append("run"))
    bar.retry_selected.connect(lambda: fired.append("retry"))
    bar.remove_selected.connect(lambda: fired.append("remove"))

    bar.run_button.click()
    bar.retry_button.click()
    bar.remove_button.click()

    assert fired == ["run", "retry", "remove"]


def test_actions_can_be_enabled_independently(qtbot) -> None:
    bar = _bar(qtbot)

    bar.set_actions_enabled(run=False, retry=True, remove=True)

    assert bar.run_button.isEnabled() is False
    assert bar.retry_button.isEnabled() is True
    assert bar.remove_button.isEnabled() is True


def test_remove_is_a_reversible_removal_not_a_destruction(qtbot) -> None:
    """D41: red outline for a reversible removal; solid red is reserved."""
    bar = _bar(qtbot)
    assert bar.remove_button.objectName() == "danger"
