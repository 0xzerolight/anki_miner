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

    Mirrors what _add_series does without driving the QInputDialog: create the
    widget, set its folders, register it on the list, then force the item id the
    test addresses it by (registration binds a real one when the folders exist).
    """
    widget = QueueItemWidget(display_name=display_name, parent=panel.list_widget)
    if video is not None and subtitle is not None:
        widget.set_folders(video, subtitle)
    widget.subtitle_offset = offset
    panel.register_widget(widget)
    widget.item_id = item_id
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


@pytest.mark.parametrize("cleared_selector", [0, 1], ids=["video", "subtitle"])
def test_edit_rejects_a_cleared_folder_without_changing_bound_item(
    panel,
    monkeypatch,
    tmp_path,
    cleared_selector,
):
    from PyQt6.QtWidgets import QDialog, QDialogButtonBox

    from anki_miner.gui.widgets.enhanced import FileSelector

    video = tmp_path / "video"
    subtitle = tmp_path / "subtitle"
    video.mkdir()
    subtitle.mkdir()
    widget = _add_widget(panel, "Series", "id-1", video=video, subtitle=subtitle, offset=1.5)
    item = panel._items[id(widget)]

    def clear_and_try_accept(dialog):
        selectors = dialog.findChildren(FileSelector)
        # video, subtitle, translation (F7) — indices 0 and 1 stay the two
        # required folders, which are the ones this test clears.
        assert len(selectors) == 3
        selectors[cleared_selector].set_path("")
        buttons = dialog.findChild(QDialogButtonBox)
        assert buttons is not None
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert ok is not None
        ok.click()
        assert dialog.result() != QDialog.DialogCode.Accepted
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", clear_and_try_accept)

    panel._edit_item(widget)

    assert widget.get_folders() == (video, subtitle)
    assert (item.video_folder, item.subtitle_folder) == (video, subtitle)
    assert item.subtitle_offset == 1.5


class TestImeSafeDialogs:
    """D49 — Return belongs to the input method, never to a dialog's OK button.

    Both of this panel's dialogs own text fields. A default button turns the
    Return that commits a kana composition into "confirm this dialog", which
    makes a Japanese series name impossible to type.
    """

    def test_add_series_prompt_has_no_default_button(self, panel, monkeypatch):
        from PyQt6.QtWidgets import QInputDialog, QPushButton

        seen: list[QInputDialog] = []

        def capture(self):
            seen.append(self)
            self.show()  # Qt promotes a default button from its show handlers
            return 0  # Rejected

        monkeypatch.setattr(QInputDialog, "exec", capture)
        panel._add_series()

        assert seen, "_add_series no longer builds an instantiated QInputDialog"
        buttons = seen[0].findChildren(QPushButton)
        assert buttons
        assert not any(b.isDefault() or b.autoDefault() for b in buttons)

    def test_add_series_prompt_confirms_on_ctrl_return(self, panel, monkeypatch):
        from PyQt6.QtGui import QKeySequence, QShortcut
        from PyQt6.QtWidgets import QInputDialog

        seen: list[QInputDialog] = []
        monkeypatch.setattr(QInputDialog, "exec", lambda self: seen.append(self) or 0)
        panel._add_series()

        keys = {sc.key() for sc in seen[0].findChildren(QShortcut)}
        assert QKeySequence("Ctrl+Return") in keys
        assert QKeySequence("Ctrl+Enter") in keys

    def test_edit_dialog_ok_is_neither_default_nor_auto_default(self, panel, monkeypatch):
        from PyQt6.QtWidgets import QDialog, QPushButton

        widget = _add_widget(panel, "Series", "id-1")
        seen: list[QDialog] = []

        def capture(self):
            seen.append(self)
            self.show()
            return QDialog.DialogCode.Rejected

        monkeypatch.setattr(QDialog, "exec", capture)
        panel._edit_item(widget)

        assert seen
        buttons = seen[0].findChildren(QPushButton)
        assert buttons
        assert not any(b.isDefault() or b.autoDefault() for b in buttons)


class TestListMinHeightFitsCardRows:
    """The list's minimum height is measured in card rows, not text lines.

    A batch queue row is a multi-line QueueItemWidget card (~150px), so a
    minimum derived from ``metric_row_height`` (one text line) held less than
    one card and clipped its Edit/Remove footer in short windows.
    """

    def _frame(self, panel) -> int:
        return 2 * panel.list_widget.frameWidth()

    def test_one_row_fits_fully(self, panel):
        widget = _add_widget(panel, "JJK S1", "id-1")
        hint = panel._list_items[id(widget)].sizeHint().height()

        assert panel.list_widget.minimumHeight() >= hint + self._frame(panel)

    def test_minimum_caps_at_three_cards(self, panel):
        widgets = [_add_widget(panel, f"S{i}", f"id-{i}") for i in range(5)]
        hints = [panel._list_items[id(w)].sizeHint().height() for w in widgets]

        expected = sum(hints[:3]) + self._frame(panel)
        assert panel.list_widget.minimumHeight() == expected
        assert panel.list_widget.minimumHeight() < sum(hints) + self._frame(panel)

    def test_all_rows_hidden_falls_back_to_text_floor(self, panel):
        from anki_miner.gui.widgets.base.sizing import metric_row_height
        from anki_miner.gui.widgets.panels.queue_panel import _VISIBLE_QUEUE_ROWS

        _add_widget(panel, "JJK S1", "id-1")
        panel._on_search_changed("no row matches this")

        floor = _VISIBLE_QUEUE_ROWS * metric_row_height(panel.list_widget)
        assert panel.list_widget.minimumHeight() == floor

    def test_collapsing_a_row_shrinks_the_minimum(self, panel):
        widget = _add_widget(panel, "JJK S1", "id-1")
        expanded = panel.list_widget.minimumHeight()

        widget.toggle_expanded()

        collapsed = panel.list_widget.minimumHeight()
        assert collapsed < expanded
        assert collapsed >= panel._list_items[id(widget)].sizeHint().height() + self._frame(panel)


class TestSecondarySubtitleFolder:
    """A queued series can carry its own translation-subtitle folder (F7)."""

    def test_row_defaults_to_no_translation_folder(self, qtbot):
        widget = QueueItemWidget(display_name="Show")
        qtbot.addWidget(widget)
        assert widget.secondary_folder is None
        assert widget.secondary_offset == 0.0

    def test_set_folders_takes_a_translation_folder(self, qtbot, tmp_path):
        widget = QueueItemWidget(display_name="Show")
        qtbot.addWidget(widget)

        widget.set_folders(tmp_path / "v", tmp_path / "s", tmp_path / "t")

        # get_folders keeps its two-tuple shape; the third rides beside it.
        assert widget.get_folders() == (tmp_path / "v", tmp_path / "s")
        assert widget.secondary_folder == tmp_path / "t"

    def test_a_row_with_translations_says_so(self, qtbot, tmp_path):
        widget = QueueItemWidget(display_name="Show")
        qtbot.addWidget(widget)
        widget.set_folders(tmp_path / "v", tmp_path / "s")
        assert "Translations" not in widget.stats_label.text()

        widget.secondary_folder = tmp_path / "t"

        assert "Translations" in widget.stats_label.text()

    def test_bind_writes_the_translation_folder_onto_the_item(self, panel, tmp_path):
        for name in ("v", "s", "t"):
            (tmp_path / name).mkdir()
        widget = _add_widget(panel, "Show", "id-1", video=tmp_path / "v", subtitle=tmp_path / "s")
        widget.secondary_folder = tmp_path / "t"
        widget.secondary_offset = -0.5

        panel._bind_widget(widget)

        item = panel.queue.get_all_items()[0]
        assert item.secondary_folder == tmp_path / "t"
        assert item.secondary_offset == -0.5

    def test_changing_only_the_translation_folder_keeps_the_receipts(self, panel, tmp_path):
        """A committed pair is already in Anki; new translations are no reason
        to mine it again."""
        for name in ("v", "s", "t"):
            (tmp_path / name).mkdir()
        widget = _add_widget(panel, "Show", "id-1", video=tmp_path / "v", subtitle=tmp_path / "s")
        item = panel.queue.get_all_items()[0]
        receipts = {(tmp_path / "v" / "ep1.mkv", tmp_path / "s" / "ep1.ass")}
        item.committed_pair_keys = set(receipts)

        widget.secondary_folder = tmp_path / "t"
        panel._bind_widget(widget)

        assert item.secondary_folder == tmp_path / "t"
        assert item.committed_pair_keys == receipts

    def test_restore_item_brings_back_the_translation_folder(self, panel, tmp_path):
        for name in ("v", "s", "t"):
            (tmp_path / name).mkdir()

        item = panel.restore_item(
            item_id="abc",
            display_name="Show",
            video_folder=tmp_path / "v",
            subtitle_folder=tmp_path / "s",
            subtitle_offset=0.0,
            status="pending",
            cards_created=0,
            retry_count=0,
            error_message="",
            secondary_folder=tmp_path / "t",
            secondary_offset=1.5,
        )

        assert item is not None
        assert item.secondary_folder == tmp_path / "t"
        assert item.secondary_offset == 1.5
