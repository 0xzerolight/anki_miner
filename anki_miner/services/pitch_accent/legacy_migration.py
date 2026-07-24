"""One-time migration of the legacy single ``pitch_accent.csv`` into the chain.

Pre-multi-source builds stored a single pitch file at ``config.pitch_accent_path``
(default ``~/.anki_miner/pitch_accent.csv``) loaded by the old single-file pitch
service. The new model layers multiple per-source SQLite indexes under
``config.pitch_root`` referenced by ``config.pitch_chain`` (first-hit-wins).
This module folds the old single file into a ``legacy-pitch`` source on first
launch so existing users keep their pitch data without re-importing anything.

The entry point is idempotent — it no-ops once the chain is populated or the
legacy index already exists — so it is safe to call on every startup. The
original CSV is left in place (graceful downgrade to older app versions this
release; retire alongside ``pitch_accent_path`` later, mirroring how the
frequency migrator was retired after soaking).
"""

from __future__ import annotations

import dataclasses
import logging

from anki_miner.config import AnkiMinerConfig, PitchSourceEntry
from anki_miner.services.pitch_accent.source_importer import import_pitch_source

logger = logging.getLogger(__name__)

_LEGACY_SOURCE_ID = "legacy-pitch"
_LEGACY_SOURCE_NAME = "Pitch Accent"


def migrate_legacy_pitch_csv(config: AnkiMinerConfig) -> AnkiMinerConfig | None:
    """One-time: fold a legacy single pitch_accent.csv into the multi-source chain.

    Returns an updated config (with pitch_chain set) when a migration was
    performed or back-filled, else None (nothing to do). Pure except for the
    one-shot import I/O; safe to call on every launch — it no-ops once migrated.
    """
    # Already on the multi-source model: leave the user's chain untouched.
    if config.pitch_chain:
        return None

    legacy_db = config.pitch_root / _LEGACY_SOURCE_ID / "index.sqlite"
    if legacy_db.exists():
        # Index already built on a prior launch but the chain reference was
        # lost (e.g. config reset). Back-fill the reference without re-importing.
        return dataclasses.replace(config, pitch_chain=(PitchSourceEntry(_LEGACY_SOURCE_ID),))

    # No legacy file to migrate. (Pre-chain builds had no separate on/off flag —
    # file presence WAS the activation switch — so presence alone implies the
    # user was using pitch data.)
    if not config.pitch_accent_path.is_file():
        return None

    try:
        import_pitch_source(
            config.pitch_accent_path,
            config.pitch_root,
            source_id=_LEGACY_SOURCE_ID,
            source_name=_LEGACY_SOURCE_NAME,
        )
    except Exception:
        # A bad/corrupt legacy file must never crash startup; leave the user on
        # the empty chain and let them import sources manually.
        logger.warning(
            "Could not migrate legacy pitch accent file %s into a pitch source",
            config.pitch_accent_path,
            exc_info=True,
        )
        return None

    return dataclasses.replace(config, pitch_chain=(PitchSourceEntry(_LEGACY_SOURCE_ID),))
