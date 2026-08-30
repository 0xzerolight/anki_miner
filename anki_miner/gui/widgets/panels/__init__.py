"""Extracted panel widgets for cleaner tab organization."""

from .anki_settings_panel import AnkiSettingsPanel
from .audio_pack_settings_panel import AudioPackSettingsPanel
from .dictionary_settings_panel import DictionarySettingsPanel
from .filtering_settings_panel import FilteringSettingsPanel
from .frequency_settings_panel import FrequencySettingsPanel
from .media_settings_panel import MediaSettingsPanel
from .mining_language_settings_panel import MiningLanguageSettingsPanel
from .pitch_settings_panel import PitchSettingsPanel
from .queue_panel import QueuePanel
from .subtitles_settings_panel import SubtitlesSettingsPanel
from .ui_settings_panel import UISettingsPanel
from .youtube_settings_panel import YouTubeSettingsPanel

__all__ = [
    "AnkiSettingsPanel",
    "AudioPackSettingsPanel",
    "MediaSettingsPanel",
    "DictionarySettingsPanel",
    "FilteringSettingsPanel",
    "FrequencySettingsPanel",
    "MiningLanguageSettingsPanel",
    "PitchSettingsPanel",
    "QueuePanel",
    "SubtitlesSettingsPanel",
    "UISettingsPanel",
    "YouTubeSettingsPanel",
]
