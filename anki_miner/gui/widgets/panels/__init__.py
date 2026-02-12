"""Extracted panel widgets for cleaner tab organization."""

from .anki_settings_panel import AnkiSettingsPanel
from .dictionary_settings_panel import DictionarySettingsPanel
from .filtering_settings_panel import FilteringSettingsPanel
from .media_settings_panel import MediaSettingsPanel
from .queue_panel import QueuePanel

__all__ = [
    "AnkiSettingsPanel",
    "MediaSettingsPanel",
    "DictionarySettingsPanel",
    "FilteringSettingsPanel",
    "QueuePanel",
]
