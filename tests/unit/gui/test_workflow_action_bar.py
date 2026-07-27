"""Tests for the pinned action bar and its Activity drawer (D6-B).

Three properties are being defended:

* the bar and the log are siblings of the page scroll, never inside it -- that
  is the entire fix for a run button below the window edge;
* the bar renders ``TaskSnapshot``s and invents nothing, so a run with no
  denominator gets an indeterminate bar rather than a percentage;
* Activity opens itself on the first problem of an attempt and then respects
  whatever the user decides for the rest of that attempt.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QPushButton, QScrollArea, QVBoxLayout, QWidget

from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.controllers.task_registry import TaskRegistry, TaskSpec
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.widgets.base import PageWidth, WorkflowActionBar, install_workflow_shell
from anki_miner.gui.widgets.log_widget import LogWidget


@pytest.fixture
def registry(qtbot):
    reg = TaskRegistry()
    yield reg
    reg.shutdown()


def _bar(qtbot) -> WorkflowActionBar:
    bar = WorkflowActionBar()
    qtbot.addWidget(bar)
    return bar


def _page(qtbot, *, with_log: bool = True):
    """Build a minimal page through the real shell helper."""
    page = QWidget()
    layout = QVBoxLayout()
    scroll = QScrollArea()
    content = QWidget()
    content.setLayout(QVBoxLayout())
    log = LogWidget() if with_log else None
    if log is not None:
        content.layout().addWidget(log)
    bar = install_workflow_shell(layout, scroll, content, PageWidth.FORM, log=log)
    page.setLayout(layout)
    qtbot.addWidget(page)
    return page, scroll, bar, log


def _has_ancestor(widget: QWidget, ancestor: QWidget) -> bool:
    node = widget.parentWidget()
    while node is not None:
        if node is ancestor:
            return True
        node = node.parentWidget()
    return False


def _start(registry: TaskRegistry, task_id: str = "screen.demo", *, now: float = 0.0):
    return registry.start(
        TaskSpec(task_id=task_id, title="Demo run", owner=CapabilityTarget("video", "single")),
        now=now,
    )


# ---------------------------------------------------------------- placement


def test_bar_and_log_sit_outside_the_page_scroll(qtbot):
    _page_widget, scroll, bar, log = _page(qtbot)

    assert not _has_ancestor(bar, scroll)
    assert not _has_ancestor(log, scroll)


def test_bar_shares_the_scrolled_column_width_cap(qtbot):
    _page_widget, scroll, bar, _log = _page(qtbot)

    content = scroll.widget()
    assert bar.maximumWidth() == content.maximumWidth()


def test_bar_renders_on_the_same_centre_line_as_the_content(qtbot):
    """A cap the bar never reaches is not a shared column.

    The bar's own size hint is far narrower than the page, so it only fills the
    column if it carries the stretch in its host row.
    """
    page, scroll, bar, _log = _page(qtbot)
    page.resize(1400, 700)
    page.show()
    qtbot.waitExposed(page)

    content = scroll.widget()
    assert bar.width() == content.width()
    bar_centre = bar.mapTo(page, bar.rect().center()).x()
    content_centre = content.mapTo(page, content.rect().center()).x()
    assert abs(bar_centre - content_centre) <= 1


def test_shell_without_a_log_hides_the_activity_control(qtbot):
    _page_widget, _scroll, bar, _log = _page(qtbot, with_log=False)

    assert bar.activity_button.isHidden()
    assert not bar.is_activity_open()


# ------------------------------------------------------------------ actions


def test_set_actions_moves_the_exact_button_objects(qtbot):
    bar = _bar(qtbot)
    primary = QPushButton("Mine")
    cancel = QPushButton("Cancel")
    pressed: list[str] = []
    primary.clicked.connect(lambda: pressed.append("mine"))

    bar.set_actions(primary, (cancel,))

    assert primary.parentWidget() is bar
    assert cancel.parentWidget() is bar
    primary.click()
    assert pressed == ["mine"]


def test_set_actions_reorders_without_duplicating(qtbot):
    bar = _bar(qtbot)
    scan = QPushButton("Scan")
    apply_button = QPushButton("Apply")

    bar.set_actions(scan, ())
    bar.set_actions(apply_button, (scan,))

    buttons = bar.findChildren(QPushButton)
    assert buttons.count(scan) == 1
    assert buttons.count(apply_button) == 1


# ----------------------------------------------------------------- progress


def test_idle_bar_shows_no_stage_progress_or_clock(qtbot, registry):
    bar = _bar(qtbot)
    bar.bind_task(registry, "screen.demo")

    assert bar.stage_label.full_text == ""
    assert bar.progress_bar.isHidden()
    assert bar.elapsed_label.isHidden()


def test_running_task_renders_stage_and_clock(qtbot, registry):
    bar = _bar(qtbot)
    bar.bind_task(registry, "screen.demo")
    handle = _start(registry)
    handle.stage(index=2, total=5, name="Extracting media", now=0.0)
    registry.tick(now=42.0)

    assert "Extracting media" in bar.stage_label.full_text
    assert bar.elapsed_label.text() == "00:42"


def test_no_denominator_renders_indeterminate(qtbot, registry):
    bar = _bar(qtbot)
    bar.bind_task(registry, "screen.demo")
    handle = _start(registry)
    handle.count(current=3, total=None, detail="word lookups", now=0.0)

    assert bar.progress_bar.minimum() == 0
    assert bar.progress_bar.maximum() == 0


def test_real_denominator_renders_the_true_fraction(qtbot, registry):
    bar = _bar(qtbot)
    bar.bind_task(registry, "screen.demo")
    handle = _start(registry)
    handle.count(current=2, total=8, detail="", now=0.0)

    assert bar.progress_bar.maximum() == 100
    assert bar.progress_bar.value() == 25


def test_a_superseded_run_cannot_repaint_the_bar(qtbot, registry):
    bar = _bar(qtbot)
    bar.bind_task(registry, "screen.demo")
    stale = _start(registry)
    _start(registry)  # the run the user is now watching

    stale.count(current=99, total=100, detail="stale", now=0.0)

    assert "stale" not in bar.stage_label.full_text


def test_another_task_changing_leaves_the_bar_alone(qtbot, registry):
    bar = _bar(qtbot)
    bar.bind_task(registry, "screen.demo")
    other = _start(registry, "queue.youtube")
    other.stage(index=1, total=3, name="Downloading", now=0.0)

    assert bar.stage_label.full_text == ""


def test_cancelling_says_so_instead_of_advancing(qtbot, registry):
    bar = _bar(qtbot)
    bar.bind_task(registry, "screen.demo")
    handle = _start(registry)
    handle.stage(index=1, total=4, name="Parsing", now=0.0)
    handle.cancelling(now=1.0)

    assert bar.stage_label.full_text == "Cancelling…"


# ----------------------------------------------------------------- activity


def test_hidden_log_still_accumulates(qtbot):
    _page_widget, _scroll, bar, log = _page(qtbot)

    assert not bar.is_activity_open()
    log.append_info("first")
    log.append_info("second")

    assert "first" in log.full_text()
    assert "second" in log.full_text()


def test_info_and_success_never_open_activity(qtbot):
    _page_widget, _scroll, bar, log = _page(qtbot)
    bar.begin_attempt()

    log.append_info("started")
    log.append_success("done")

    assert not bar.is_activity_open()


def test_first_warning_opens_activity(qtbot):
    _page_widget, _scroll, bar, log = _page(qtbot)
    bar.begin_attempt()

    log.append_warning("no subtitle track")

    assert bar.is_activity_open()


def test_manual_close_prevents_a_second_forced_open_in_the_same_attempt(qtbot):
    _page_widget, _scroll, bar, log = _page(qtbot)
    bar.begin_attempt()
    log.append_warning("first problem")
    bar.activity_button.setChecked(False)
    assert not bar.is_activity_open()

    log.append_error("second problem")

    assert not bar.is_activity_open()


def test_begin_attempt_rearms_the_auto_open(qtbot):
    _page_widget, _scroll, bar, log = _page(qtbot)
    bar.begin_attempt()
    log.append_warning("first problem")
    bar.activity_button.setChecked(False)

    bar.begin_attempt()
    log.append_error("a new attempt failed")

    assert bar.is_activity_open()


def test_begin_attempt_does_not_close_a_drawer_the_user_opened(qtbot):
    _page_widget, _scroll, bar, _log = _page(qtbot)
    bar.activity_button.setChecked(True)
    assert bar.is_activity_open()

    bar.begin_attempt()

    assert bar.is_activity_open()


def test_activity_button_toggles_the_drawer(qtbot):
    _page_widget, _scroll, bar, _log = _page(qtbot)

    bar.activity_button.setChecked(True)
    assert bar.is_activity_open()
    bar.activity_button.setChecked(False)
    assert not bar.is_activity_open()


# -------------------------------------------------------------- fixed height


def test_the_bar_reports_its_action_height_before_it_is_ever_shown(qtbot):
    """The height the bar advertises must already account for its buttons.

    The actions live in a layout nested inside the row inside the bar, and a Qt
    box layout does not mark itself dirty when a *child* layout gains an item.
    The row therefore kept answering with the empty-bar height it cached at
    construction until something forced a full relayout -- so any page that
    asked how tall the bar was before it was ever on screen (which is what
    sizing a window does) laid itself out short and then jumped.
    """
    _page_widget, _scroll, bar, _log = _page(qtbot)
    primary = QPushButton("Start mining")

    bar.set_actions(primary, ())

    assert bar.sizeHint().height() >= primary.sizeHint().height() + 2 * SPACING.xs


def test_the_activity_control_counts_towards_the_bar_height_immediately(qtbot):
    """Same nested-layout cache, reached through the shell rather than a screen."""
    _page_widget, _scroll, bar, _log = _page(qtbot)

    assert not bar.activity_button.isHidden()
    assert bar.sizeHint().height() >= bar.activity_button.sizeHint().height() + 2 * SPACING.xs
