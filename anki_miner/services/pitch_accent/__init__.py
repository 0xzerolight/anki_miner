"""Multi-source pitch accent: importer, registry, provider, first-hit chain."""

from anki_miner.services.pitch_accent.legacy_migration import migrate_legacy_pitch_csv
from anki_miner.services.pitch_accent.multi_pitch_service import MultiPitchAccentService
from anki_miner.services.pitch_accent.provider import IndexedPitchProvider
from anki_miner.services.pitch_accent.registry import PitchSourceMeta, PitchSourceRegistry
from anki_miner.services.pitch_accent.source_importer import (
    PITCH_SOURCE_SUFFIXES,
    PitchSourceImportResult,
    import_pitch_source,
    repair_pitch_source,
)
from anki_miner.services.pitch_accent.yomitan_pitch_importer import (
    YomitanPitchImportResult,
    extract_pitch_rows,
    import_yomitan_pitch_zip,
)

__all__ = [
    "PITCH_SOURCE_SUFFIXES",
    "IndexedPitchProvider",
    "MultiPitchAccentService",
    "PitchSourceImportResult",
    "PitchSourceMeta",
    "PitchSourceRegistry",
    "YomitanPitchImportResult",
    "extract_pitch_rows",
    "import_pitch_source",
    "import_yomitan_pitch_zip",
    "migrate_legacy_pitch_csv",
    "repair_pitch_source",
]
