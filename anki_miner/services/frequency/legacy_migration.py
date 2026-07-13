"""One-time repair of the legacy frequency source's display name.

A reimport bug collapsed the ``legacy-frequency`` source's display name to the
generic ``"source"`` stem for some users. This module's startup step rewrites
that name back to a friendly label so cards render "Frequency: N" instead of
"source: N". It is idempotent and self-guarded, so it is safe to call on every
startup.
"""

from __future__ import annotations

import logging

from anki_miner.config import AnkiMinerConfig
from anki_miner.services.frequency import storage

logger = logging.getLogger(__name__)

_LEGACY_SOURCE_ID = "legacy-frequency"
# Display name the legacy source collapsed to when reimported before the
# reimport-preserves-name fix (the generic "source.csv" stem). Repaired to a
# friendly label so cards render "Frequency: N" instead of "source: N".
_COLLAPSED_LEGACY_NAME = "source"
_FRIENDLY_LEGACY_NAME = "Frequency"


def repair_legacy_frequency_source_name(config: AnkiMinerConfig) -> None:
    """Rename the legacy frequency source from "source" to a friendly label.

    Standalone, condition-guarded, idempotent startup step that runs
    unconditionally regardless of the user's frequency chain state (the affected
    population's chain is already populated). Rewrites the authoritative SQLite
    meta (``write_meta`` also
    refreshes the sidecar) only when the ``legacy-frequency`` source's current
    name is the collapsed ``"source"`` stem. Self-clearing (post-rewrite the
    condition can't re-match) and self-healing (retries on a failed write); the
    ``legacy-frequency`` id scope means it can never touch another source that a
    user happened to name "source". Never crashes startup.
    """
    legacy_db = config.freqs_root / _LEGACY_SOURCE_ID / "index.sqlite"
    if not legacy_db.exists():
        return
    try:
        if storage.read_meta(legacy_db).get("source_name") == _COLLAPSED_LEGACY_NAME:
            storage.write_meta(legacy_db, {"source_name": _FRIENDLY_LEGACY_NAME})
            logger.info("Repaired legacy frequency source name to %r", _FRIENDLY_LEGACY_NAME)
    except Exception:
        logger.warning("Could not repair legacy frequency source name", exc_info=True)
