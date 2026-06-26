"""One-time migration of a legacy single ``frequency.csv`` into the chain.

Pre-multi-source builds stored a single rank list at ``config.frequency_list_path``
(default ``~/.anki_miner/frequency.csv``) loaded by a single-CSV service. The new
model layers multiple per-source SQLite indexes under ``config.freqs_root`` referenced
by ``config.frequency_chain``. This module folds the old single file into a
``legacy-frequency`` source on first launch so existing users keep their ranks without
re-importing anything.

The entry point is idempotent — it no-ops once the chain is populated or the legacy
index already exists — so it is safe to call on every startup.
"""

from __future__ import annotations

import dataclasses
import logging

from anki_miner.config import AnkiMinerConfig, FreqEntry
from anki_miner.services.frequency.source_importer import import_frequency_source

logger = logging.getLogger(__name__)

_LEGACY_SOURCE_ID = "legacy-frequency"


def migrate_legacy_frequency_csv(config: AnkiMinerConfig) -> AnkiMinerConfig | None:
    """One-time: fold a legacy single frequency.csv into the multi-source chain.

    Returns an updated config (with frequency_chain set) when a migration was
    performed or back-filled, else None (nothing to do). Pure except for the
    one-shot import I/O; safe to call on every launch — it no-ops once migrated.
    """
    # User wasn't using frequency data — don't import anything on their behalf.
    if not config.use_frequency_data:
        return None

    # Already on the multi-source model: leave the user's chain untouched.
    if config.frequency_chain:
        return None

    legacy_db = config.freqs_root / _LEGACY_SOURCE_ID / "index.sqlite"
    if legacy_db.exists():
        # Index already built on a prior launch but the chain reference was
        # lost (e.g. config reset). Back-fill the reference without re-importing.
        return dataclasses.replace(config, frequency_chain=(FreqEntry(_LEGACY_SOURCE_ID),))

    # No legacy file to migrate.
    if not config.frequency_list_path.exists():
        return None

    try:
        import_frequency_source(
            config.frequency_list_path,
            config.freqs_root,
            source_id=_LEGACY_SOURCE_ID,
        )
    except Exception:
        # A bad/corrupt legacy file must never crash startup; leave the user on
        # the empty chain and let them import sources manually.
        logger.warning(
            "Could not migrate legacy frequency file %s into a frequency source",
            config.frequency_list_path,
            exc_info=True,
        )
        return None

    return dataclasses.replace(config, frequency_chain=(FreqEntry(_LEGACY_SOURCE_ID),))
