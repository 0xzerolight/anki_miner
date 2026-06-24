"""Extracted panel widgets for cleaner tab organization."""

from .anki_settings_panel import AnkiSettingsPanel
from .asr_settings_panel import AsrSettingsPanel
from .audio_pack_settings_panel import AudioPackSettingsPanel
from .dictionary_settings_panel import DictionarySettingsPanel
from .filtering_settings_panel import FilteringSettingsPanel
from .language_panel import LanguagePanel
from .media_settings_panel import MediaSettingsPanel
from .queue_panel import QueuePanel
from .subtitles_settings_panel import SubtitlesSettingsPanel
from .themes_panel import ThemesPanel
from .youtube_settings_panel import YouTubeSettingsPanel

__all__ = [
    "AnkiSettingsPanel",
    "AsrSettingsPanel",
    "AudioPackSettingsPanel",
    "MediaSettingsPanel",
    "DictionarySettingsPanel",
    "FilteringSettingsPanel",
    "LanguagePanel",
    "QueuePanel",
    "SubtitlesSettingsPanel",
    "ThemesPanel",
    "YouTubeSettingsPanel",
]
