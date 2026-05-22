"""Tests for the Themes settings panel UI surface.

Pins:

* 3-column layout (Source column removed in v2.4.3).
* Star cell is a centered wrapper with a checkable QToolButton rendering a
  Unicode glyph — guards against the "star looks wrong + barely visible"
  regression caused by the global QPushButton padding rule.
* Row preview avoids a full table repopulate; only the Active marker moves.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QToolButton, QWidget

from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.widgets.panels.themes_panel import ThemesPanel

_app = QApplication.instance() or QApplication([])


def _make_panel(tmp_path) -> ThemesPanel:
    Theme.initialize(active="light", favorites=("light", "dark"), user_dir=None, state_listener=None)
    return ThemesPanel(themes_root=tmp_path / "themes")


class TestColumnLayout:
    def test_table_has_three_columns(self, tmp_path):
        panel = _make_panel(tmp_path)
        assert panel.table.columnCount() == 3

    def test_table_has_themes_panel_object_name(self, tmp_path):
        # objectName scopes the QSS `::item { padding: 0 }` override that
        # keeps the star glyph from clipping at the row boundary. If this
        # rename slips, the clipping bug returns silently.
        panel = _make_panel(tmp_path)
        assert panel.table.objectName() == "themesPanelTable"

    def test_header_labels_drop_source(self, tmp_path):
        panel = _make_panel(tmp_path)
        header = panel.table.horizontalHeader()
        assert header is not None
        labels = [panel.table.horizontalHeaderItem(c).text() for c in range(panel.table.columnCount())]
        assert labels == ["", "Name", "Status"]
        assert "Source" not in labels

    def test_column_indices_are_contiguous(self):
        # COL_STAR=0, COL_NAME=1, COL_STATUS=2 — no COL_SOURCE left behind.
        assert ThemesPanel.COL_STAR == 0
        assert ThemesPanel.COL_NAME == 1
        assert ThemesPanel.COL_STATUS == 2
        assert not hasattr(ThemesPanel, "COL_SOURCE")


class TestStarCell:
    def test_star_cell_is_centered_wrapper(self, tmp_path):
        panel = _make_panel(tmp_path)
        assert panel.table.rowCount() >= 1
        cell = panel.table.cellWidget(0, ThemesPanel.COL_STAR)
        assert isinstance(cell, QWidget)
        layout = cell.layout()
        # Centered wrapper is what fixes the vertical-offset bug.
        assert isinstance(layout, QHBoxLayout)
        assert layout.contentsMargins().left() == 0
        assert layout.contentsMargins().top() == 0
        assert layout.contentsMargins().right() == 0
        assert layout.contentsMargins().bottom() == 0

    def test_star_button_renders_unicode_glyph(self, tmp_path):
        panel = _make_panel(tmp_path)
        cell = panel.table.cellWidget(0, ThemesPanel.COL_STAR)
        buttons = cell.findChildren(QToolButton)
        assert len(buttons) == 1
        button = buttons[0]
        # Unicode glyph routes through the font pipeline; QPainter-drawn
        # icons were crushed to a 4-px sliver by the global QPushButton
        # padding rule. QToolButton sidesteps that rule.
        assert button.text() in ("★", "☆")
        assert button.isCheckable()
        assert button.autoRaise()
        assert button.cursor().shape() == Qt.CursorShape.PointingHandCursor

    def test_star_button_uses_star_toggle_object_name(self, tmp_path):
        # starToggle objectName is the QSS hook for theme-coherent coloring:
        # muted text when empty, warning (gold) when checked.
        panel = _make_panel(tmp_path)
        cell = panel.table.cellWidget(0, ThemesPanel.COL_STAR)
        button = cell.findChildren(QToolButton)[0]
        assert button.objectName() == "starToggle"

    def test_star_button_checked_state_matches_favorite(self, tmp_path):
        # Favorites tuple is ("light", "dark") — both rows render as checked.
        panel = _make_panel(tmp_path)
        favorited = {"light", "dark"}
        for r in range(panel.table.rowCount()):
            name_item = panel.table.item(r, ThemesPanel.COL_NAME)
            key = name_item.data(Qt.ItemDataRole.UserRole)
            cell = panel.table.cellWidget(r, ThemesPanel.COL_STAR)
            button = cell.findChildren(QToolButton)[0]
            assert button.isChecked() == (key in favorited)
            assert button.text() == ("★" if key in favorited else "☆")

    def test_row_height_is_fixed(self, tmp_path):
        from anki_miner.gui.widgets.panels.themes_panel import _ROW_HEIGHT_PX

        panel = _make_panel(tmp_path)
        v_header = panel.table.verticalHeader()
        assert v_header is not None
        # Fixed row height keeps the centered wrapper visually aligned with
        # the row dividers; the constant drives the auto-sized star, so the
        # test reads the constant rather than a literal pixel value.
        assert v_header.defaultSectionSize() == _ROW_HEIGHT_PX

    def test_star_button_sizes_track_row_height(self, tmp_path):
        from anki_miner.gui.widgets.panels.themes_panel import _ROW_HEIGHT_PX

        panel = _make_panel(tmp_path)
        cell = panel.table.cellWidget(0, ThemesPanel.COL_STAR)
        button = cell.findChildren(QToolButton)[0]
        # Button is square and fits within the row so the glyph isn't
        # clipped at top or bottom by the cell boundary.
        assert button.width() == button.height()
        assert button.width() <= _ROW_HEIGHT_PX
        # Font tracks at least 50% of the row height so the glyph stays
        # readable; current implementation targets 60%, set via instance
        # stylesheet so the base QWidget font-size can't override it.
        font_px = int(_ROW_HEIGHT_PX * 0.6)
        assert f"font-size: {font_px}px" in button.styleSheet()


class TestRowSelectionRefreshesActiveMarker:
    def test_active_marker_moves_without_repopulate(self, tmp_path, monkeypatch):
        panel = _make_panel(tmp_path)

        populate_calls = {"n": 0}
        original_populate = panel._populate

        def counting_populate():
            populate_calls["n"] += 1
            original_populate()

        monkeypatch.setattr(panel, "_populate", counting_populate)

        # Stub the expensive app-level QSS apply so the test only exercises
        # the panel-side logic.
        monkeypatch.setattr(panel, "_apply_to_app", lambda _key: None)

        # Find rows for "light" (currently active) and "dark".
        def row_for(key: str) -> int:
            for r in range(panel.table.rowCount()):
                item = panel.table.item(r, ThemesPanel.COL_NAME)
                if item is not None and item.data(Qt.ItemDataRole.UserRole) == key:
                    return r
            raise AssertionError(f"row not found for {key}")

        dark_row = row_for("dark")
        # setCurrentCell drives both the selection model AND the current-item
        # focus, which is what a real mouse click does. selectRow() alone
        # leaves currentItem unchanged, so the handler's None guard bails out.
        panel.table.setCurrentCell(dark_row, ThemesPanel.COL_NAME)

        # The hot path must NOT call _populate (that was the source of the lag).
        assert populate_calls["n"] == 0

        # And the Active marker must have moved to the dark row.
        statuses = {
            panel.table.item(r, ThemesPanel.COL_NAME)
            .data(Qt.ItemDataRole.UserRole): panel.table.item(r, ThemesPanel.COL_STATUS)
            .text()
            for r in range(panel.table.rowCount())
        }
        assert statuses["dark"] == "Active"
        assert statuses["light"] == ""
