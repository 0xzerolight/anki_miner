"""The twelve corrected error strings (decision D24, T8).

Each of these was wrong in a specific way: it named a Settings page that does
not exist, pasted an exception into the sentence, or told the user to check
something they cannot check. They are asserted verbatim here so a later edit has
to be deliberate.
"""

from __future__ import annotations

import inspect

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui import main_window
from anki_miner.gui.widgets import (
    analytics_tab,
    batch_processing_tab,
    condense_tab,
    settings_tab,
    single_episode_tab,
    subtitle_creation_tab,
    subtitle_retime_tab,
)
from anki_miner.gui.widgets.panels import dictionary_settings_panel, ui_settings_panel


def _source(module) -> str:
    return inspect.getsource(module)


class TestTranscriptionCopy:
    def test_the_engine_notice_names_the_page_that_exists(self):
        """ "Settings → ASR" was never a page; the real one is Transcription & Alignment."""
        source = _source(subtitle_creation_tab)
        assert "Transcription is not ready. Open Settings → Transcription & Alignment to finish setup." in source
        assert "Settings → ASR" not in source
        assert "[asr] extra" not in source

    def test_a_missing_model_names_the_page_that_exists(self):
        source = _source(subtitle_creation_tab)
        assert "The transcription model %1 is not installed. " in source
        assert "Open Settings → Transcription & Alignment to install it." in source


class TestSystemChecks:
    def test_the_issue_list_is_no_longer_the_sentence(self):
        source = _source(main_window)
        assert "Some system checks need attention." in source
        assert "System validation found issues:" not in source

    def test_a_failed_check_says_what_to_do(self):
        source = _source(main_window)
        assert "System check failed. Try again." in source
        assert "Validation error: %1" not in source


class TestSettingsTransfer:
    def test_export_failure_drops_the_path_from_the_sentence(self):
        source = _source(settings_tab)
        assert "Settings could not be exported." in source
        assert "Could not write %1" not in source

    def test_import_failure_drops_the_path_from_the_sentence(self):
        source = _source(settings_tab)
        assert "Settings could not be imported." in source
        assert "Could not import %1" not in source


class TestTrackProbes:
    @pytest.mark.parametrize("module", [single_episode_tab, subtitle_retime_tab, condense_tab])
    def test_audio_track_failure_stops_blaming_ffprobe(self, module):
        """The user cannot verify an ffprobe install from a dialog; the repair button can."""
        source = _source(module)
        assert "Audio tracks could not be read." in source
        assert "Failed to detect audio tracks. Check that ffprobe is installed." not in source

    def test_subtitle_track_failure_stops_blaming_ffprobe(self):
        source = _source(condense_tab)
        assert "Subtitle tracks could not be read." in source
        assert "Failed to detect subtitle tracks. Check that ffprobe is installed." not in source


class TestFolderValidation:
    def test_batch_folder_validation_says_what_to_do(self):
        source = _source(batch_processing_tab)
        assert "Choose existing video and subtitle folders." in source
        assert "Please select valid video and subtitle folders" not in source


class TestPreviouslyLogOnly:
    def test_analytics_refresh_failure_is_a_sentence_now(self):
        assert "Analytics could not be refreshed." in _source(analytics_tab)

    def test_a_registry_scan_failure_is_a_sentence_now(self):
        assert "Installed dictionaries could not be checked." in _source(dictionary_settings_panel)

    def test_the_themes_folder_failure_is_a_sentence_now(self):
        assert "The themes folder could not be opened." in _source(ui_settings_panel)
