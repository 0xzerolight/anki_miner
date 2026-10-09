"""FileSelector: an emptied field is restyled (A13), and file-or-folder mode (D7-B)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.enhanced import FileSelector


def _selector(qtbot, **kwargs) -> FileSelector:
    selector = FileSelector(label="Video File:", **kwargs)
    qtbot.addWidget(selector)
    return selector


class TestEmptiedFieldIsRestyled:
    def test_clearing_a_valid_path_drops_the_success_state_and_repolishes(self, qtbot, tmp_path):
        video = tmp_path / "ep01.mkv"
        video.touch()
        selector = _selector(qtbot)
        selector.set_path(str(video))
        assert selector.input.property("success") is True

        style = MagicMock()
        selector.input.style = lambda: style  # type: ignore[method-assign]
        selector.clear()

        assert selector.input.property("success") is False
        style.unpolish.assert_called_with(selector.input)
        style.polish.assert_called_with(selector.input)


class TestFileOrFolder:
    def test_default_selector_has_one_browse_button(self, qtbot):
        selector = _selector(qtbot)
        assert selector.folder_button is None
        assert selector.browse_button.text() == "Browse..."

    def test_a_folder_picker_cannot_also_take_folders(self, qtbot):
        """allow_folder adds a Folder… button beside a FILE picker; on a folder
        picker "File…" would open a second folder picker (B1.1)."""
        with pytest.raises(ValueError, match="allow_folder"):
            FileSelector(label="Folder:", file_mode=False, allow_folder=True)

    def test_file_or_folder_mode_offers_both_buttons(self, qtbot):
        selector = _selector(qtbot, allow_folder=True)
        assert selector.browse_button.text() == "File…"
        assert selector.folder_button is not None
        assert selector.folder_button.text() == "Folder…"

    def test_a_folder_is_valid(self, qtbot, tmp_path):
        selector = _selector(qtbot, allow_folder=True)
        selector.set_path(str(tmp_path))
        assert selector.is_valid() is True

    def test_a_file_is_valid(self, qtbot, tmp_path):
        book = tmp_path / "book.epub"
        book.touch()
        selector = _selector(qtbot, allow_folder=True)
        selector.set_path(str(book))
        assert selector.is_valid() is True

    def test_a_missing_path_is_not_valid(self, qtbot, tmp_path):
        selector = _selector(qtbot, allow_folder=True)
        selector.set_path(str(tmp_path / "gone"))
        assert selector.is_valid() is False
        assert selector.status_label.full_text == "Not found. Choose an existing file or folder."

    def test_the_folder_button_opens_a_folder_picker(self, qtbot, monkeypatch, tmp_path):
        import anki_miner.gui.widgets.enhanced.file_selector as module

        seen: list[str] = []

        def fake_pick_directory(parent, caption, directory, *, on_done):
            seen.append(caption)
            on_done(str(tmp_path))

        monkeypatch.setattr(module.file_dialogs, "pick_directory", fake_pick_directory)
        selector = _selector(qtbot, allow_folder=True)

        selector.folder_button.click()

        assert seen
        assert selector.get_path() == str(tmp_path)

    def test_a_dropped_folder_is_accepted(self, qtbot, tmp_path):
        from PyQt6.QtCore import QMimeData, QPointF, Qt, QUrl
        from PyQt6.QtGui import QDropEvent

        selector = _selector(qtbot, allow_folder=True)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(tmp_path))])
        event = QDropEvent(
            QPointF(1.0, 1.0),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )

        selector.dropEvent(event)

        assert selector.get_path() == str(tmp_path)
