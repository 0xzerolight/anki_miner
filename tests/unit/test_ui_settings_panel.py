"""Tests for the family/variant grouped UISettingsPanel."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt

from anki_miner.gui.i18n import available_languages
from anki_miner.gui.resources.styles.theme import REQUIRED_COLOR_KEYS, Theme
from anki_miner.gui.widgets.base import ScreenIssue
from anki_miner.gui.widgets.enhanced.theme_gallery import (
    FAMILY_STAR_PARTIAL_OPACITY,
    STAR_FILLED,
    STAR_OUTLINE,
    ThemeGalleryWidget,
)
from anki_miner.gui.widgets.panels.ui_settings_panel import UISettingsPanel


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
def panel(qapp, qtbot, themes_dir: Path) -> UISettingsPanel:
    Theme.initialize(
        active="catppuccin-mocha",
        favorites=("light",),
        shipped_dir=themes_dir,
    )
    p = UISettingsPanel(themes_dir)
    qtbot.addWidget(p)
    return p


class TestThemeGalleryHosting:
    """Task 3 seam: the panel hosts the shared gallery, not a tree."""

    def test_panel_hosts_a_gallery_not_a_tree(self, qtbot, tmp_path) -> None:
        p = UISettingsPanel(themes_root=tmp_path)
        qtbot.addWidget(p)
        assert isinstance(p.gallery, ThemeGalleryWidget)
        assert not hasattr(p, "tree")

    def test_activating_a_card_applies_the_theme_and_emits_state(self, qtbot, tmp_path) -> None:
        p = UISettingsPanel(themes_root=tmp_path)
        qtbot.addWidget(p)
        with qtbot.waitSignal(p.state_changed) as blocker:
            p.gallery.card("nord").click()
        assert Theme.get_current_mode() == "nord"
        assert blocker.args[0] == "nord"

    def test_revert_restores_the_baseline_theme(self, qtbot, tmp_path) -> None:
        Theme.set_mode("light")
        p = UISettingsPanel(themes_root=tmp_path)
        qtbot.addWidget(p)
        p.show()
        qtbot.waitExposed(p)
        p.gallery.card("nord").click()
        assert Theme.get_current_mode() == "nord"
        p.revert_btn.click()
        assert Theme.get_current_mode() == "light"

    def test_star_click_toggles_the_favorite(self, qtbot, tmp_path) -> None:
        p = UISettingsPanel(themes_root=tmp_path)
        qtbot.addWidget(p)
        assert not Theme.is_favorite("nord")
        with qtbot.waitSignal(p.favorites_changed):
            p.gallery.star("nord").click()
        assert Theme.is_favorite("nord")

    def test_family_star_favorites_the_whole_family(self, qtbot, tmp_path) -> None:
        p = UISettingsPanel(themes_root=tmp_path)
        qtbot.addWidget(p)
        p.gallery.family_star("Catppuccin").click()
        assert Theme.is_favorite("catppuccin-mocha")
        assert Theme.is_favorite("catppuccin-latte")


class TestGalleryStructure:
    def test_standalone_themes_are_top_level_cards(self, panel: UISettingsPanel) -> None:
        keys = panel.gallery.card_keys()
        assert "light" in keys
        assert "dark" in keys

    def test_family_groups_variants(self, panel: UISettingsPanel) -> None:
        assert "Catppuccin" in panel.gallery.family_titles()
        keys = set(panel.gallery.card_keys())
        assert {"catppuccin-mocha", "catppuccin-latte"} <= keys

    def test_family_variant_uses_variant_name(self, panel: UISettingsPanel) -> None:
        mocha = panel.gallery.card("catppuccin-mocha")
        latte = panel.gallery.card("catppuccin-latte")
        assert mocha is not None and mocha.name_label.text() == "Mocha"
        assert latte is not None and latte.name_label.text() == "Latte"


class TestActiveMarker:
    def test_active_label_on_active_variant_only(self, panel: UISettingsPanel) -> None:
        active_keys = [
            key
            for key in panel.gallery.card_keys()
            if (card := panel.gallery.card(key)) is not None and card.active_label.text() == "Active"
        ]
        assert active_keys == ["catppuccin-mocha"]


class TestSelectionEmitsStateChanged:
    def test_selecting_variant_emits_signal(self, panel: UISettingsPanel) -> None:
        captured: list[tuple[str, tuple]] = []
        panel.state_changed.connect(lambda active, favs: captured.append((active, favs)))
        panel.gallery.card("catppuccin-latte").click()
        assert captured, "state_changed was not emitted"
        active, favs = captured[-1]
        assert active == "catppuccin-latte"
        assert isinstance(favs, tuple)
        assert Theme.get_current_mode() == "catppuccin-latte"


class TestVariantStarCell:
    """Regression guards for the per-variant star button."""

    def test_object_name_is_star_toggle(self, panel: UISettingsPanel) -> None:
        # QSS scope hook — themes target #starToggle for star color.
        btn = panel.gallery.star("catppuccin-mocha")
        assert btn.objectName() == "starToggle"

    def test_auto_raise_enabled(self, panel: UISettingsPanel) -> None:
        # Ghost-button appearance: transparent background until hover.
        btn = panel.gallery.star("catppuccin-mocha")
        assert btn.autoRaise() is True

    def test_pointing_hand_cursor(self, panel: UISettingsPanel) -> None:
        btn = panel.gallery.star("catppuccin-mocha")
        assert btn.cursor().shape() == Qt.CursorShape.PointingHandCursor


class TestFamilyStarTriState:
    def test_outline_when_no_variant_favorited(self, panel: UISettingsPanel) -> None:
        Theme.set_favorites(["light"])
        panel._populate()
        btn = panel.gallery.family_star("Catppuccin")
        assert btn.text() == STAR_OUTLINE
        # No opacity effect applied in the none-favorited path.
        assert btn.graphicsEffect() is None

    def test_filled_when_all_variants_favorited(self, panel: UISettingsPanel) -> None:
        Theme.set_favorites(["light", "catppuccin-mocha", "catppuccin-latte"])
        panel._populate()
        btn = panel.gallery.family_star("Catppuccin")
        assert btn.text() == STAR_FILLED
        assert btn.graphicsEffect() is None  # no dimming when all-favorited

    def test_dimmed_when_partial(self, panel: UISettingsPanel) -> None:
        Theme.set_favorites(["catppuccin-mocha"])
        panel._populate()
        btn = panel.gallery.family_star("Catppuccin")
        assert btn.text() == STAR_FILLED
        effect = btn.graphicsEffect()
        assert effect is not None
        # Opacity matches the configured partial alpha.
        assert effect.opacity() == FAMILY_STAR_PARTIAL_OPACITY


class TestFamilyStarBulkToggle:
    def test_none_or_partial_clicks_favorite_all(self, panel: UISettingsPanel) -> None:
        Theme.set_favorites(["catppuccin-mocha"])  # partial state
        panel._populate()
        captured: list[tuple[str, tuple]] = []
        panel.state_changed.connect(lambda a, f: captured.append((a, f)))
        btn = panel.gallery.family_star("Catppuccin")
        btn.click()
        favs = set(Theme.get_favorites())
        assert {"catppuccin-mocha", "catppuccin-latte"}.issubset(favs)
        assert len(captured) == 1  # single batched emission

    def test_all_clicks_unfavorite_all(self, panel: UISettingsPanel) -> None:
        Theme.set_favorites(["catppuccin-mocha", "catppuccin-latte"])
        panel._populate()
        btn = panel.gallery.family_star("Catppuccin")
        btn.click()
        favs = set(Theme.get_favorites())
        assert "catppuccin-mocha" not in favs
        assert "catppuccin-latte" not in favs

    def test_bulk_toggle_emits_favorites_changed(self, panel: UISettingsPanel) -> None:
        # HeaderWidget refreshes its combo on favorites_changed; regression
        # guard for the family-star path.
        Theme.set_favorites(["catppuccin-mocha"])
        panel._populate()
        emitted: list[None] = []
        panel.favorites_changed.connect(lambda: emitted.append(None))
        btn = panel.gallery.family_star("Catppuccin")
        btn.click()
        assert len(emitted) == 1


class TestLanguage:
    """UI-language combo, merged in from the former LanguagePanel."""

    def test_combo_populated_from_available_languages(self, panel: UISettingsPanel) -> None:
        langs = available_languages()
        assert panel.language_combo.count() == len(langs)
        codes = {panel.language_combo.itemData(i) for i in range(panel.language_combo.count())}
        assert codes == set(langs)

    def test_restart_note_hidden_initially(self, panel: UISettingsPanel) -> None:
        assert panel.language_restart_note.isHidden() is True

    def test_set_language_is_silent_and_selects(self, panel: UISettingsPanel) -> None:
        emitted: list[str] = []
        panel.language_changed.connect(emitted.append)
        panel.set_language("ja")
        assert panel.language_combo.currentData() == "ja"
        assert emitted == []  # programmatic set never emits
        assert panel.language_restart_note.isHidden() is True  # nor reveals the note

    def test_unknown_language_falls_back_to_en(self, panel: UISettingsPanel) -> None:
        panel.set_language("zz-not-a-language")
        assert panel.language_combo.currentData() == "en"

    def test_selection_emits_language_changed_and_reveals_note(self, panel: UISettingsPanel) -> None:
        emitted: list[str] = []
        panel.language_changed.connect(emitted.append)
        idx = panel.language_combo.findData("ja")
        panel._on_language_selected(idx)
        assert emitted == ["ja"]
        assert panel.language_restart_note.isHidden() is False

    def test_activated_signal_drives_emit(self, panel: UISettingsPanel) -> None:
        emitted: list[str] = []
        panel.language_changed.connect(emitted.append)
        idx = panel.language_combo.findData("fr")
        panel.language_combo.activated.emit(idx)
        assert emitted == ["fr"]


class TestThemesFolderFailureIsVisible:
    """Opening the themes folder used to fail into the log alone (D24, string 12)."""

    def test_a_failed_mkdir_raises_a_screen_issue(self, qtbot, tmp_path, monkeypatch):
        blocked = tmp_path / "blocked" / "themes"
        panel = UISettingsPanel(blocked)
        qtbot.addWidget(panel)

        def _refuse(*args, **kwargs):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(Path, "mkdir", _refuse)
        panel._open_themes_folder()

        issue = panel.issue_banner().current_issue()
        assert issue is not None
        assert issue.summary == "The themes folder could not be opened."
        assert "Permission denied" not in issue.summary
        assert "Permission denied" in issue.details
        assert str(blocked) in issue.details

    def test_the_repair_opens_the_parent_folder(self, qtbot, tmp_path, monkeypatch):
        blocked = tmp_path / "blocked" / "themes"
        panel = UISettingsPanel(blocked)
        qtbot.addWidget(panel)

        def _refuse(*args, **kwargs):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(Path, "mkdir", _refuse)
        opened: list[str] = []
        monkeypatch.setattr(
            "anki_miner.gui.widgets.panels.ui_settings_panel.QDesktopServices.openUrl",
            lambda url: opened.append(url.toLocalFile()),
        )
        panel._open_themes_folder()
        panel.issue_banner().action_button.click()
        assert opened == [str(blocked.parent)]

    def test_a_successful_open_clears_a_stale_issue(self, qtbot, tmp_path, monkeypatch):
        target = tmp_path / "themes"
        panel = UISettingsPanel(target)
        qtbot.addWidget(panel)
        panel.show_screen_issue(
            ScreenIssue(summary="The themes folder could not be opened."),
        )
        monkeypatch.setattr(
            "anki_miner.gui.widgets.panels.ui_settings_panel.QDesktopServices.openUrl",
            lambda url: None,
        )
        panel._open_themes_folder()
        assert panel.issue_banner().current_issue() is None


class TestVideoPreviewCheckbox:
    """The escape hatch a user reaches when the curator kills their app.

    Its stable id is load-bearing beyond the panel: the crash-recovery banner's
    button calls ``reveal_setting("video_preview")``, and settings search
    addresses the same id.
    """

    def test_defaults_to_checked(self, panel):
        assert panel.video_preview_checkbox.isChecked()

    def test_seeds_from_the_constructor(self, qtbot, themes_dir: Path):
        p = UISettingsPanel(themes_dir, video_preview_enabled=False)
        qtbot.addWidget(p)
        assert not p.video_preview_checkbox.isChecked()

    def test_toggling_emits(self, panel, qtbot):
        with qtbot.waitSignal(panel.video_preview_changed) as blocker:
            panel.video_preview_checkbox.setChecked(False)
        assert blocker.args == [False]

    def test_load_from_config_reseeds_without_re_emitting(self, panel, test_config):
        """A config fan-out must not look like a user toggle, or a profile
        switch would write the value straight back out again."""
        from dataclasses import replace

        emitted: list[bool] = []
        panel.video_preview_changed.connect(emitted.append)
        panel.load_from_config(replace(test_config, video_preview_enabled=False))
        assert not panel.video_preview_checkbox.isChecked()
        assert emitted == []

    def test_registered_under_the_id_the_banner_uses(self, panel):
        ids = {anchor.stable_id for anchor in panel.setting_anchors()}
        assert "ui.video_preview" in ids
