"""Show or delete any past run's cards (AI-16).

A run's note ids live in stats.db (``session_notes``). Anki may have lost some
of them since then: the user deleted a card, or pressed Undo on the run's
receipt. Every step here asks Anki which ids still exist and acts on those only
(Review Focus 4). Only existing public ``AnkiService`` methods are used, so an
AnkiConnect key in the URL (AI-02) reaches these calls like every other.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from anki_miner.services.anki_service import AnkiService
from anki_miner.services.known_word_db import KnownWordDB
from anki_miner.services.stats_service import StatsService
from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)

#: Note ids per findNotes query. ``nid:`` takes a comma list; 400 ids keep the
#: query near 6 KB, well inside what AnkiConnect accepts.
_NID_CHUNK = 400


@dataclass(frozen=True)
class RunNotesCheck:
    """How much of a run is still in Anki.

    Args:
        requested: Distinct note ids the run recorded.
        present: Those still in Anki, ascending.
        reviewed: How many present notes have a card that is no longer new.
    """

    requested: int
    present: tuple[int, ...]
    reviewed: int

    @property
    def missing(self) -> int:
        """Recorded notes Anki no longer has."""
        return self.requested - len(self.present)


def _find(anki: AnkiService, note_ids: Sequence[int], extra: str = "") -> list[int]:
    """``findNotes`` over ``note_ids`` in chunks, with an optional extra search term."""
    found: list[int] = []
    for start in range(0, len(note_ids), _NID_CHUNK):
        chunk = note_ids[start : start + _NID_CHUNK]
        found.extend(anki.find_notes("nid:" + ",".join(str(note_id) for note_id in chunk) + extra))
    return found


def check_run_notes(anki: AnkiService, note_ids: Sequence[int]) -> RunNotesCheck:
    """Which of a run's notes Anki still has, and how many of them were reviewed.

    "Reviewed" means the note has a card outside ``is:new``: one search, no
    per-card field parsing.

    Raises:
        AnkiConnectionError: AnkiConnect is unreachable or refused.
    """
    ids = sorted({int(note_id) for note_id in note_ids})
    if not ids:
        return RunNotesCheck(requested=0, present=(), reviewed=0)
    present = tuple(sorted(set(_find(anki, ids))))
    reviewed = len(set(_find(anki, list(present), " -is:new"))) if present else 0
    return RunNotesCheck(requested=len(ids), present=present, reviewed=reviewed)


def delete_run(
    anki: AnkiService,
    stats: StatsService,
    session_id: int,
    *,
    known_words_db_for: Callable[[str], KnownWordDB | None],
) -> int:
    """Delete run ``session_id``'s cards that are still in Anki; return how many.

    Like the receipt's Undo, the run's source='mined' known-words rows are
    removed too, so its words can be mined again. They are removed in the
    run's own language's database, which ``known_words_db_for`` opens. The
    run's lists are then forgotten, and its history row, which stays, is
    marked "cards deleted" (``StatsService.forget_run_notes``).

    Raises:
        AnkiConnectionError: Anki was unreachable. Nothing was forgotten, so
            the user can retry.
    """
    receipt = stats.get_run_receipt(session_id)
    if receipt is None:
        return 0
    check = check_run_notes(anki, receipt.note_ids)
    deleted = anki.delete_notes(list(check.present)) if check.present else 0
    if receipt.mined_forms:
        _revert_mined_forms(receipt.language, set(receipt.mined_forms), known_words_db_for)
    stats.forget_run_notes(session_id)
    log_summary(
        logger,
        "Run cards deleted",
        session=session_id,
        language=receipt.language,
        deleted=deleted,
        missing=check.missing,
    )
    return deleted


def _revert_mined_forms(
    language: str,
    forms: set[str],
    known_words_db_for: Callable[[str], KnownWordDB | None],
) -> None:
    """Remove ``forms``' source='mined' rows from ``language``'s known-words DB, warning on failure."""
    try:
        db = known_words_db_for(language)
        if db is not None and db.is_available():
            db.remove_words(forms, source="mined")
    except (sqlite3.Error, OSError) as exc:
        logger.warning("Run delete: could not revert mined words in the %s known-words DB: %s", language, exc)
