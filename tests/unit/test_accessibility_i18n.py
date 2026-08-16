"""Accessibility metadata and translation regressions."""

from __future__ import annotations

from pathlib import Path
from time import time
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QTranslator
from PyQt6.QtWidgets import QPushButton

from anki_miner.config import AudioSourceEntry, ChainEntry, FreqEntry, PitchSourceEntry
from anki_miner.gui.main_window import MainWindow
from anki_miner.gui.widgets.analytics_tab import AnalyticsTab
from anki_miner.gui.widgets.base.screen_issue_banner import ScreenIssueBanner
from anki_miner.gui.widgets.enhanced.file_selector import FileSelector
from anki_miner.gui.widgets.panels.audio_pack_settings_panel import AudioPackSettingsPanel
from anki_miner.gui.widgets.panels.dictionary_settings_panel import DictionarySettingsPanel
from anki_miner.gui.widgets.panels.frequency_settings_panel import FrequencySettingsPanel
from anki_miner.gui.widgets.panels.pitch_settings_panel import PitchSettingsPanel
from anki_miner.gui.widgets.panels.ui_settings_panel import UISettingsPanel
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab
from anki_miner.gui.widgets.update_banner import UpdateBanner
from anki_miner.services.update_checker import UpdateInfo


class _PrefixTranslator(QTranslator):
    def translate(
        self,
        context: str | None,
        source_text: str | None,
        disambiguation: str | None = None,
        n: int = -1,
    ) -> str:
        del context, disambiguation, n
        return f"translated:{source_text or ''}"


@pytest.fixture
def translated_qapp(qapp):
    translator = _PrefixTranslator()
    qapp.installTranslator(translator)
    try:
        yield
    finally:
        qapp.removeTranslator(translator)


def _assert_translated(*values: str) -> None:
    assert all(value.startswith("translated:") for value in values)


def _update_info() -> UpdateInfo:
    return UpdateInfo(
        version="2.4.0",
        release_page_url="https://example.com/release",
        asset_url=None,
        release_notes="",
    )


def test_accessible_names_are_translated(
    translated_qapp,
    qtbot,
    patch_heavy_init,
    test_config,
):
    patch_heavy_init(test_config)
    window = MainWindow()
    analytics = AnalyticsTab(MagicMock())
    episode = SingleEpisodeTab(test_config, MagicMock(), MagicMock())
    selector = FileSelector(label="Video File:")
    progress = ProgressWidget()
    for widget in (window, analytics, episode, selector, progress):
        qtbot.addWidget(widget)

    _assert_translated(
        window.accessibleName(),
        window.accessibleDescription(),
        window.tabs.accessibleName(),
        window.tabs.accessibleDescription(),
        window.header.accessibleName(),
        window.header.accessibleDescription(),
        window.status_bar.accessibleName(),
        window.status_bar.accessibleDescription(),
        analytics.accessibleName(),
        analytics.accessibleDescription(),
        episode.accessibleName(),
        episode.accessibleDescription(),
        selector.accessibleDescription(),
        selector.input.accessibleName(),
        selector.input.accessibleDescription(),
        selector.browse_button.accessibleName(),
        selector.browse_button.accessibleDescription(),
    )

    progress._start_time = time() - 60
    progress._items_processed = 1
    progress._total_items = 10
    progress._update_stats()
    assert "translated:ETA ~" in progress.stats_label.text()


def test_glyph_buttons_have_nonempty_accessible_names(translated_qapp, qtbot, tmp_path: Path):
    banner = UpdateBanner(_update_info())
    issue_banner = ScreenIssueBanner()
    dictionary = DictionarySettingsPanel(tmp_path / "dicts")
    frequency = FrequencySettingsPanel(tmp_path / "freqs")
    audio = AudioPackSettingsPanel(tmp_path / "audio")
    pitch = PitchSettingsPanel(tmp_path / "pitch")
    ui = UISettingsPanel(tmp_path / "themes")
    for widget in (banner, issue_banner, dictionary, frequency, audio, pitch, ui):
        qtbot.addWidget(widget)

    dismiss = banner.findChild(QPushButton, "dismissBtn")
    # "light" is always among the shipped themes, so the gallery always has a
    # card (and a star) for it regardless of the (empty) tmp_path themes root.
    star = ui.gallery.star("light")

    # The move arrows are per row now, so a chain has to exist before there is
    # anything to name. Pitch is in the sweep too -- it is the fourth panel on
    # the same base and was the one this test used to miss.
    dictionary.set_chain((ChainEntry(kind="indexed", dict_id="a", enabled=True),))
    frequency.set_chain((FreqEntry(source_id="a", enabled=True),), registry_meta={})
    audio.set_chain((AudioSourceEntry(kind="pack", pack_id="a", enabled=True),), registry_meta={})
    pitch.set_chain((PitchSourceEntry(source_id="a", enabled=True),), registry_meta={})

    buttons = [
        dismiss,
        # The screen-issue banner's own ✕ (D24): the word lives only in the
        # accessible name, so a screen reader is the only surface that can
        # announce what pressing it does.
        issue_banner.dismiss_button,
        star,
        *(p._remove_btn for p in (dictionary, frequency, audio, pitch)),
    ]
    for panel in (dictionary, frequency, audio, pitch):
        for row in panel._rows():
            buttons.extend((row.up_button, row.down_button))

    assert all(button is not None for button in buttons)
    _assert_translated(*(button.accessibleName() for button in buttons if button is not None))
