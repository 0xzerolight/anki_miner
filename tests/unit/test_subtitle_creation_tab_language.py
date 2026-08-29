"""The Generate tab's read-only language label follows the active profile."""

from __future__ import annotations

import dataclasses

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.subtitle_creation_tab import SubtitleCreationTab


def _config(tmp_path):
    return AnkiMinerConfig(asr_models_root=tmp_path / "m", media_temp_folder=tmp_path / "t")


def test_label_is_japanese_for_ja(qtbot, tmp_path):
    tab = SubtitleCreationTab(_config(tmp_path))
    qtbot.addWidget(tab)
    assert "Japanese" in tab.language_label.text()
    assert tab._language_display() == "Japanese"


def test_label_follows_update_config(qtbot, tmp_path):
    tab = SubtitleCreationTab(_config(tmp_path))
    qtbot.addWidget(tab)
    tab.update_config(dataclasses.replace(_config(tmp_path), language="ko"))
    assert tab.language_label.text() == "Korean"
