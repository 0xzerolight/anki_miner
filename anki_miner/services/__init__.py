"""Business logic services for Anki Miner."""

from typing import TYPE_CHECKING

from .anki_service import AnkiService
from .definition_service import DefinitionService
from .dictionary.providers import IndexedDictProvider, JishoProvider
from .export_service import ExportService
from .media_extractor import MediaExtractorService
from .shortcut_service import ShortcutResult, ShortcutService
from .stats_service import StatsService
from .validation_service import ValidationService
from .word_filter import WordFilterService

if TYPE_CHECKING:
    from .subtitle_parser import SubtitleParserService


def __getattr__(name: str) -> object:
    if name == "SubtitleParserService":
        from .subtitle_parser import SubtitleParserService

        return SubtitleParserService
    raise AttributeError(name)


__all__ = [
    "SubtitleParserService",
    "WordFilterService",
    "MediaExtractorService",
    "DefinitionService",
    "AnkiService",
    "ExportService",
    "ValidationService",
    "StatsService",
    "IndexedDictProvider",
    "JishoProvider",
    "ShortcutService",
    "ShortcutResult",
]
