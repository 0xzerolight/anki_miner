"""In-memory pitch lookup provider backed by a per-source SQLite index.

One :class:`IndexedPitchProvider` per installed pitch source
(``<pitch_root>/<source_id>/index.sqlite``). Unlike
:class:`~anki_miner.services.frequency.providers.indexed_freq_provider.IndexedFreqProvider`,
this provider does NOT query SQLite at lookup time: ``load()`` does one full
``SELECT``, rebuilds the same two in-memory maps the CSV service
built, and closes the connection immediately — no persistent handle, so
``close()``-style teardown is unnecessary (nothing holds the db file open on
Windows). See :mod:`anki_miner.services.pitch_accent.storage` for why the
SQLite index exists at all (recovery-substrate token, not a query engine).
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from anki_miner.services._sqlite_index import open_readonly
from anki_miner.services.pitch_accent.storage import SCHEMA_VERSION
from anki_miner.services.pitch_accent_service import (
    PitchEntry,
    PitchMapsStore,
    _parse_int_field,
    _ParsedRow,
    build_pitch_maps,
)

logger = logging.getLogger(__name__)


class IndexedPitchProvider(PitchMapsStore):
    """Pitch lookups for one indexed source, loaded fully into memory.

    Inherits the exact-pair / unique-term ``lookup_entry`` and the derived
    lookup API from :class:`PitchMapsStore`; this class only supplies the
    SQLite row source.
    """

    def __init__(self, source_id: str, db_path: Path, display_name: str):
        super().__init__()
        self.source_id = source_id
        self.display_name = display_name
        self._db_path = db_path

    def load(self) -> bool:
        """Read all rows from the index and build the lookup maps.

        Returns False (never raises) on a missing/corrupt/unsupported source so
        the aggregator can simply drop it, mirroring IndexedFreqProvider.load.

        Stored ``nasal``/``devoice`` are the comma-joined digit strings from the
        pitch CSV format; they are parsed back to ``tuple[int, ...]`` here —
        consumers (``render_pitch_text_field``) do int-position membership
        checks, so string tuples would silently disable the indicators.
        """
        try:
            conn = open_readonly(self._db_path)
        except (OSError, sqlite3.Error) as e:
            logger.warning("Could not open pitch source '%s': %s", self.source_id, e)
            return False
        try:
            meta = dict(conn.execute("SELECT key, value FROM meta"))
            version = int(meta.get("schema_version", "0"))
            if version != SCHEMA_VERSION:
                logger.warning(
                    "Pitch source '%s' has unsupported schema_version %s; needs reimport",
                    self.source_id,
                    version,
                )
                return False
            parsed_rows = (
                _ParsedRow(
                    reading=reading,
                    kanji=kanji or "",
                    entry=PitchEntry(
                        pattern=pattern,
                        nasal=_parse_int_field(nasal or ""),
                        devoice=_parse_int_field(devoice or ""),
                    ),
                )
                for reading, kanji, pattern, nasal, devoice in conn.execute(
                    "SELECT reading, kanji, pattern, nasal, devoice FROM entries ORDER BY id"
                )
            )
            self._set_maps(build_pitch_maps(parsed_rows))
        except (sqlite3.Error, TypeError, ValueError) as e:
            logger.warning("Could not load pitch source '%s': %s", self.source_id, e)
            return False
        finally:
            conn.close()
        logger.info(
            "Loaded %d pitch entries from source '%s'",
            self.entry_count,
            self.source_id,
        )
        return True
