"""Tests for the family/variant grouped ThemesPanel."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QToolButton, QTreeWidget, QTreeWidgetItem

from anki_miner.gui.resources.styles.theme import REQUIRED_COLOR_KEYS, Theme
from anki_miner.gui.widgets.panels.themes_panel import (
    _FAMILY_STAR_PARTIAL_OPACITY,
    _STAR_FILLED,
    _STAR_OUTLINE,
    ThemesPanel,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _theme_dict(name: str, **overrides) -> dict:
    data: dict = {
        "name": name,
        "colors": dict.fromkeys(REQUIRED_COLOR_KEYS, "#000000"),
    }
    data.update(overrides)
    return data


@pytest.fixture
def themes_dir(tmp_path: Path) -> Path:
    d = tmp_path / "themes"
    d.mkdir()
    # Two ungrouped themes
    (d / "light.json").write_text(json.dumps(_theme_dict("Light")))
    (d / "dark.json").write_text(json.dumps(_theme_dict("Dark")))
    # One family with two variants
    (d / "catppuccin-mocha.json").write_text(
        json.dumps(_theme_dict("Catppuccin Mocha", family="Catppuccin", variant="Mocha"))
    )
    (d / "catppuccin-latte.json").write_text(
        json.dumps(_theme_dict("Catppuccin Latte", family="Catppuccin", variant="Latte"))
    )
    return d


@pytest.fixture
def panel(qapp, themes_dir: Path) -> ThemesPanel:
    Theme.initialize(
        active="catppuccin-mocha",
        favorites=("light",),
        shipped_dir=themes_dir,
    )
    return ThemesPanel(themes_dir)


def _walk(item: QTreeWidgetItem):
    yield item
    for i in range(item.childCount()):
        yield from _walk(item.child(i))


def _find_top_level(panel: ThemesPanel, name: str) -> QTreeWidgetItem:
    root = panel.tree.invisibleRootItem()
    for i in range(root.childCount()):
        if root.child(i).text(panel.COL_NAME) == name:
            return root.child(i)
    raise AssertionError(f"Top-level item {name!r} not found")


class TestTreeStructure:
    def test_widget_is_tree(self, panel: ThemesPanel) -> None:
        assert isinstance(panel.tree, QTreeWidget)

    def test_columns_in_spec_order(self, panel: ThemesPanel) -> None:
        assert panel.tree.columnCount() == 3
        labels = [panel.tree.headerItem().text(i) for i in range(3)]
        assert labels == ["Name", "Status", ""]

    def test_column_constants(self, panel: ThemesPanel) -> None:
        assert (panel.COL_NAME, panel.COL_STATUS, panel.COL_STAR) == (0, 1, 2)

    def test_standalone_themes_top_level_with_no_children(self, panel: ThemesPanel) -> None:
        keys_at_top: list[str] = []
        root = panel.tree.invisibleRootItem()
        for i in range(root.childCount()):
            item = root.child(i)
            data = item.data(panel.COL_NAME, Qt.ItemDataRole.UserRole)
            if isinstance(data, str):
                keys_at_top.append(data)
                assert item.childCount() == 0
        assert "light" in keys_at_top
        assert "dark" in keys_at_top

    def test_family_groups_variants(self, panel: ThemesPanel) -> None:
        family_item = _find_top_level(panel, "Catppuccin")
        assert family_item.childCount() == 2
        variant_keys = {
            family_item.child(i).data(panel.COL_NAME, Qt.ItemDataRole.UserRole) for i in range(family_item.childCount())
        }
        assert variant_keys == {"catppuccin-mocha", "catppuccin-latte"}

    def test_family_variant_uses_variant_name(self, panel: ThemesPanel) -> None:
        family_item = _find_top_level(panel, "Catppuccin")
        variant_labels = {family_item.child(i).text(panel.COL_NAME) for i in range(family_item.childCount())}
        assert variant_labels == {"Mocha", "Latte"}

    def test_active_family_auto_expanded(self, panel: ThemesPanel) -> None:
        family_item = _find_top_level(panel, "Catppuccin")
        assert family_item.isExpanded()


class TestActiveMarker:
    def test_active_label_on_active_variant_only(self, panel: ThemesPanel) -> None:
        root = panel.tree.invisibleRootItem()
        active_keys: list[str] = []
        for i in range(root.childCount()):
            for d in _walk(root.child(i)):
                if d.text(panel.COL_STATUS) == "Active":
                    key = d.data(panel.COL_NAME, Qt.ItemDataRole.UserRole)
                    if isinstance(key, str):
                        active_keys.append(key)
        assert active_keys == ["catppuccin-mocha"]


class TestSelectionEmitsStateChanged:
    def test_selecting_variant_emits_signal(self, panel: ThemesPanel) -> None:
        captured: list[tuple[str, tuple]] = []
        panel.state_changed.connect(lambda active, favs: captured.append((active, favs)))
        family = _find_top_level(panel, "Catppuccin")
        latte = next(
            family.child(i)
            for i in range(family.childCount())
            if family.child(i).data(panel.COL_NAME, Qt.ItemDataRole.UserRole) == "catppuccin-latte"
        )
        panel.tree.setCurrentItem(latte)
        assert captured, "state_changed was not emitted"
        active, favs = captured[-1]
        assert active == "catppuccin-latte"
        assert isinstance(favs, tuple)
        assert Theme.get_current_mode() == "catppuccin-latte"


class TestVariantStarCell:
    """Regression guards for the per-variant star button."""

    def _variant_star_button(self, panel: ThemesPanel, key: str) -> QToolButton:
        root = panel.tree.invisibleRootItem()
        for i in range(root.childCount()):
            for descendant in _walk(root.child(i)):
                data = descendant.data(panel.COL_NAME, Qt.ItemDataRole.UserRole)
                if data == key:
                    widget = panel.tree.itemWidget(descendant, panel.COL_STAR)
                    btn = widget.findChild(QToolButton)
                    assert btn is not None
                    return btn
        raise AssertionError(f"Variant {key!r} not found")

    def test_object_name_is_star_toggle(self, panel: ThemesPanel) -> None:
        # QSS scope hook — themes target #starToggle for star color.
        btn = self._variant_star_button(panel, "catppuccin-mocha")
        assert btn.objectName() == "starToggle"

    def test_auto_raise_enabled(self, panel: ThemesPanel) -> None:
        # Ghost-button appearance: transparent background until hover.
        btn = self._variant_star_button(panel, "catppuccin-mocha")
        assert btn.autoRaise() is True

    def test_pointing_hand_cursor(self, panel: ThemesPanel) -> None:
        btn = self._variant_star_button(panel, "catppuccin-mocha")
        assert btn.cursor().shape() == Qt.CursorShape.PointingHandCursor


def _family_star_button(panel: ThemesPanel, family_name: str) -> QToolButton:
    family = _find_top_level(panel, family_name)
    widget = panel.tree.itemWidget(family, panel.COL_STAR)
    btn = widget.findChild(QToolButton)
    assert btn is not None, "family star button missing"
    return btn


class TestFamilyStarTriState:
    def test_outline_when_no_variant_favorited(self, panel: ThemesPanel) -> None:
        Theme.set_favorites(["light"])
        panel._populate()
        btn = _family_star_button(panel, "Catppuccin")
        assert btn.text() == _STAR_OUTLINE
        # No opacity effect applied in the none-favorited path.
        assert btn.graphicsEffect() is None

    def test_filled_when_all_variants_favorited(self, panel: ThemesPanel) -> None:
        Theme.set_favorites(["light", "catppuccin-mocha", "catppuccin-latte"])
        panel._populate()
        btn = _family_star_button(panel, "Catppuccin")
        assert btn.text() == _STAR_FILLED
        assert btn.graphicsEffect() is None  # no dimming when all-favorited

    def test_dimmed_when_partial(self, panel: ThemesPanel) -> None:
        Theme.set_favorites(["catppuccin-mocha"])
        panel._populate()
        btn = _family_star_button(panel, "Catppuccin")
        assert btn.text() == _STAR_FILLED
        effect = btn.graphicsEffect()
        assert effect is not None
        # Opacity matches the configured partial alpha.
        assert effect.opacity() == _FAMILY_STAR_PARTIAL_OPACITY


class TestFamilyStarBulkToggle:
    def test_none_or_partial_clicks_favorite_all(self, panel: ThemesPanel) -> None:
        Theme.set_favorites(["catppuccin-mocha"])  # partial state
        panel._populate()
        captured: list[tuple[str, tuple]] = []
        panel.state_changed.connect(lambda a, f: captured.append((a, f)))
        btn = _family_star_button(panel, "Catppuccin")
        btn.click()
        favs = set(Theme.get_favorites())
        assert {"catppuccin-mocha", "catppuccin-latte"}.issubset(favs)
        assert len(captured) == 1  # single batched emission

    def test_all_clicks_unfavorite_all(self, panel: ThemesPanel) -> None:
        Theme.set_favorites(["catppuccin-mocha", "catppuccin-latte"])
        panel._populate()
        btn = _family_star_button(panel, "Catppuccin")
        btn.click()
        favs = set(Theme.get_favorites())
        assert "catppuccin-mocha" not in favs
        assert "catppuccin-latte" not in favs

    def test_bulk_toggle_emits_favorites_changed(self, panel: ThemesPanel) -> None:
        # HeaderWidget refreshes its combo on favorites_changed; regression
        # guard for the family-star path.
        Theme.set_favorites(["catppuccin-mocha"])
        panel._populate()
        emitted: list[None] = []
        panel.favorites_changed.connect(lambda: emitted.append(None))
        btn = _family_star_button(panel, "Catppuccin")
        btn.click()
        assert len(emitted) == 1


class TestFamilyRowNotSelectable:
    def test_family_row_is_not_selectable(self, panel: ThemesPanel) -> None:
        family = _find_top_level(panel, "Catppuccin")
        assert not bool(family.flags() & Qt.ItemFlag.ItemIsSelectable)
