"""Tests for QueuePanel row identity and stats (T-30).

The panel used to match rows by display name (``set_item_status``, first match
wins) and by "first row whose status is processing" (``set_processing_item_complete``),
so two queue rows that share a series name had status and card counts land on
the wrong row. Rows are now keyed by the stable ``item_id`` threaded from the
worker. These tests use a real QueuePanel (offscreen, no mocks).
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.panels.queue_panel import QueuePanel
from anki_miner.gui.widgets.queue_item_widget import QueueItemWidget


@pytest.fixture
def panel(qapp, qtbot):
    p = QueuePanel()
    qtbot.addWidget(p)
    yield p
    p.deleteLater()


def _add_widget(panel, display_name, item_id, video=None, subtitle=None, offset=0.0):
    """Add a configured QueueItemWidget directly to the panel.

    Mirrors what _add_series + the queue-population path do, without driving
    the QInputDialog: create the widget, stamp its item_id, set folders, and
    register it with the panel's layout/list.
    """
    widget = QueueItemWidget(display_name=display_name, parent=panel.queue_container)
    widget.item_id = item_id
    if video is not None and subtitle is not None:
        widget.set_folders(video, subtitle)
    widget.subtitle_offset = offset
    panel.queue_layout.insertWidget(len(panel.queue_item_widgets), widget)
    panel.queue_item_widgets.append(widget)
    panel._update_stats()
    return widget


def test_set_item_status_targets_row_by_id_not_name(panel):
    """Two same-name rows: status lands on the row with the matching id."""
    first = _add_widget(panel, "Naruto", "id-1")
    second = _add_widget(panel, "Naruto", "id-2")

    panel.set_item_status("id-2", "processing")

    assert first.get_status() == "pending"
    assert second.get_status() == "processing"


def test_set_processing_item_complete_targets_row_by_id(panel):
    """Completion/card-count lands on the addressed row, not the first 'processing'.

    Both same-name rows are marked processing (as a real run does at pick time);
    completing 'id-1' must update only id-1's status and card count.
    """
    first = _add_widget(panel, "Bleach", "id-1")
    second = _add_widget(panel, "Bleach", "id-2")
    first.set_status("processing")
    second.set_status("processing")

    panel.set_processing_item_complete("id-1", cards_created=7)

    assert first.get_status() == "complete"
    assert first.get_cards_created() == 7
    assert second.get_status() == "processing"
    assert second.get_cards_created() == 0


def test_set_item_status_unknown_id_is_noop(panel):
    """An id that matches no row leaves every row untouched."""
    w = _add_widget(panel, "One Piece", "id-1")
    panel.set_item_status("missing", "processing")
    assert w.get_status() == "pending"


def test_set_processing_item_complete_unknown_id_is_noop(panel):
    """Completing an unknown id changes no row but still refreshes stats."""
    w = _add_widget(panel, "One Piece", "id-1")
    w.set_status("processing")
    panel.set_processing_item_complete("missing", cards_created=3)
    assert w.get_status() == "processing"
    assert w.get_cards_created() == 0


def test_update_stats_text(panel, tmp_path):
    """Stats line reflects series/episode/card counts across rows."""
    assert "empty" in panel.queue_stats_label.text().lower()

    w1 = _add_widget(panel, "A", "id-1")
    w1.set_episode_count(3)
    w2 = _add_widget(panel, "B", "id-2")
    w2.set_episode_count(2)
    panel._update_stats()

    text = panel.queue_stats_label.text()
    assert "2 series" in text
    assert "5 episodes" in text
    assert "Ready to process" in text

    # Once cards are created, the line switches to a cards-created summary.
    w1.set_status("complete")
    w1.set_cards_created(4)
    panel.set_processing_item_complete("id-2", cards_created=0)  # refresh path
    panel._update_stats()
    assert "4 cards created" in panel.queue_stats_label.text()


def test_get_valid_pairs_and_incomplete_items(panel, tmp_path):
    """Valid rows (existing folders) are returned; incomplete/invalid are flagged."""
    video = tmp_path / "video"
    subs = tmp_path / "subs"
    video.mkdir()
    subs.mkdir()

    valid = _add_widget(panel, "Valid", "id-1", video=video, subtitle=subs)
    _add_widget(panel, "NoFolders", "id-2")  # incomplete: no folders set
    _add_widget(panel, "Missing", "id-3", video=tmp_path / "nope", subtitle=tmp_path / "gone")

    pairs = panel.get_valid_pairs()
    # Exactly the valid row is returned, carrying its widget for id stamping.
    assert len(pairs) == 1
    assert valid in pairs[0]
    assert (video, subs) == (pairs[0][0], pairs[0][1])

    incomplete = panel.get_incomplete_items()
    issues = {w.display_name: kind for w, kind in incomplete}
    assert issues == {"NoFolders": "incomplete", "Missing": "invalid"}


def test_remove_item_during_run_keeps_other_rows_addressable(panel):
    """Removing one row leaves the rest matchable by id (no index drift)."""
    first = _add_widget(panel, "Same", "id-1")
    second = _add_widget(panel, "Same", "id-2")

    panel._remove_item(first)

    assert panel.item_count == 1
    panel.set_item_status("id-2", "processing")
    assert second.get_status() == "processing"


def test_clear_queue_empties_rows(panel, monkeypatch):
    """Clearing removes every row and resets the stats line."""
    import anki_miner.gui.widgets.panels.queue_panel as qp

    _add_widget(panel, "A", "id-1")
    _add_widget(panel, "B", "id-2")

    # _clear_queue asks for confirmation; auto-confirm Yes.
    monkeypatch.setattr(qp.QMessageBox, "question", lambda *a, **k: qp.QMessageBox.StandardButton.Yes)
    panel._clear_queue()

    assert panel.item_count == 0
    assert "empty" in panel.queue_stats_label.text().lower()
