"""Business logic services for Anki Miner."""

from typing import TYPE_CHECKING

from .definition_service import DefinitionService
from .dictionary.providers import IndexedDictProvider, JishoProvider
from .media_extractor import MediaExtractorService
from .shortcut_service import ShortcutResult, ShortcutService
from .stats_service import StatsService
from .word_filter import WordFilterService

if TYPE_CHECKING:
    from .anki_service import AnkiService
    from .export_service import ExportService
    from .subtitle_parser import SubtitleParserService
    from .validation_service import ValidationService


def __getattr__(name: str) -> object:
    # AnkiService and ValidationService each pull in `requests` at their own
    # module top; ExportService is lazy alongside them for the same reason.
    # main_window.py still imports both directly at real GUI boot (unchanged
    # by this), so the win here is narrower: a bare `import anki_miner.services`
    # from a lightweight, non-GUI consumer (e.g. the ffsubsync child process)
    # no longer carries that ~40-60ms `requests` cost.
    if name == "AnkiService":
        from .anki_service import AnkiService

        return AnkiService
    if name == "ExportService":
        from .export_service import ExportService

        return ExportService
    if name == "SubtitleParserService":
        from .subtitle_parser import SubtitleParserService

        return SubtitleParserService
    if name == "ValidationService":
        from .validation_service import ValidationService

        return ValidationService
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
