"""The file tools and Card Backfill pin their run actions too (D6-B).

Generate, Retime and Condense share ``_ToolTabBase``, so the bar is installed
once there; Backfill stays a plain ``QWidget`` and keeps both Scan and Apply,
because a preview you cannot rebuild is a dead end.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from PyQt6.QtWidgets import QScrollArea, QWidget

from anki_miner.gui.widgets.backfill_tab import CardBackfillTab
from anki_miner.gui.widgets.base import WorkflowActionBar
from anki_miner.gui.widgets.condense_tab import CondenseTab
from anki_miner.gui.widgets.subtitle_creation_tab import SubtitleCreationTab
from anki_miner.gui.widgets.subtitle_retime_tab import SubtitleRetimeTab
from anki_miner.services.card_backfiller import BackfillOptions, BackfillPlan, FieldChange, NotePlan

_TOOLS = {
    "generate": (SubtitleCreationTab, "generate_button"),
    "retime": (SubtitleRetimeTab, "retime_button"),
    "condense": (CondenseTab, "condense_button"),
}


def _bar(widget: QWidget) -> WorkflowActionBar:
    bars = widget.findChildren(WorkflowActionBar)
    assert len(bars) == 1, "a screen has exactly one action host"
    return bars[0]


def _ancestors(widget: QWidget) -> list[QWidget]:
    chain: list[QWidget] = []
    node = widget.parentWidget()
    while node is not None:
        chain.append(node)
        node = node.parentWidget()
    return chain


def _page_scroll(widget: QWidget) -> QScrollArea:
    scrolls = [s for s in widget.findChildren(QScrollArea) if s.objectName() == "page-scroll"]
    assert scrolls, "every tool screen has a scrolled page column"
    return scrolls[0]


@pytest.fixture(params=list(_TOOLS))
def tool(request, qtbot, test_config):
    cls, primary = _TOOLS[request.param]
    widget = cls(test_config, suppress_optional_startup=True)
    qtbot.addWidget(widget)
    return widget, getattr(widget, primary)


def test_tool_primary_and_cancel_are_the_original_objects_in_the_bar(tool):
    widget, primary = tool
    bar = _bar(widget)

    assert _ancestors(primary)[0] is bar
    assert _ancestors(widget.cancel_button)[0] is bar
    assert widget._primary_button is primary


def test_tool_bar_and_log_sit_outside_the_scroll(tool):
    widget, _primary = tool
    scroll = _page_scroll(widget)

    assert scroll not in _ancestors(_bar(widget))
    assert scroll not in _ancestors(widget.log_widget)


def test_tool_activity_opens_on_the_first_problem(tool):
    widget, _primary = tool
    bar = _bar(widget)
    bar.begin_attempt()

    widget.log_widget.append_error("one file could not be processed")

    assert bar.is_activity_open()


# ----------------------------------------------------------------- backfill


@pytest.fixture
def backfill(qtbot, test_config):
    widget = CardBackfillTab(
        replace(
            test_config,
            anki_fields={**test_config.anki_fields, "frequency": "Frequency"},
        )
    )
    qtbot.addWidget(widget)
    return widget


def _valid_plan() -> BackfillPlan:
    changes = (FieldChange("frequency", "Frequency", "old", "new"),)
    return BackfillPlan(
        options=BackfillOptions(field_keys=frozenset({"frequency"})),
        notes=(NotePlan(1, "word", changes),),
        scanned=1,
        skipped_no_identity=0,
        unavailable_fields=(),
        sentinel_only_sorts=0,
        expression_field="Expression",
        config_version=0,
    )


def test_backfill_keeps_exactly_one_scan_and_one_apply(backfill):
    bar = _bar(backfill)

    assert _ancestors(backfill.scan_button)[0] is bar
    assert _ancestors(backfill.apply_button)[0] is bar
    assert [b for b in bar.findChildren(type(backfill.scan_button)) if b.text() == backfill.scan_button.text()] == [
        backfill.scan_button
    ]


def test_backfill_scan_is_primary_before_a_preview(backfill):
    assert backfill.scan_button.objectName() == "primary"
    assert backfill.apply_button.objectName() == "secondary"
    assert not backfill.apply_button.isEnabled()


def test_backfill_apply_takes_over_after_a_valid_preview(backfill):
    backfill._on_scan_finished(_valid_plan())

    assert backfill.apply_button.objectName() == "primary"
    assert backfill.apply_button.isEnabled()
    # Rescanning must stay possible: a preview built against the wrong deck is
    # otherwise a dead end.
    assert backfill.scan_button.objectName() == "secondary"
    assert backfill.scan_button.isEnabled()
    assert _ancestors(backfill.scan_button)[0] is _bar(backfill)


def test_backfill_hides_activity_because_it_has_no_log(backfill):
    bar = _bar(backfill)

    assert bar.activity_button.isHidden()
    assert not bar.is_activity_open()


def test_backfill_cancel_and_progress_sit_outside_the_scroll(backfill):
    scroll = _page_scroll(backfill)

    assert scroll not in _ancestors(backfill.cancel_button)
    assert scroll not in _ancestors(backfill.progress_bar)
    assert scroll not in _ancestors(backfill.status_label)


def test_backfill_worker_enumeration_is_unchanged(backfill):
    assert list(backfill.iter_close_workers()) == []
