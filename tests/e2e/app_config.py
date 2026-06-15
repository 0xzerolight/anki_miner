"""Build the :class:`AnkiMinerConfig` the in-process E2E driver mines with.

:func:`build_app_config` derives a config from the project default via
``dataclasses.replace`` and overrides exactly the fields the harness needs:
the throwaway deck/note-type, the AnkiConnect endpoint, an isolated home for
all on-disk state, and the seeded offline-dictionary chain. It is the single
documented place where the harness pins config so a test never hand-rolls one.

Two non-obvious choices, both VERIFIED against the real pipeline:

``anki_fields`` for note type "Basic"
-------------------------------------
``AnkiService.__init__`` validates that ``anki_fields`` contains every key in
``REQUIRED_FIELD_KEYS`` (``word, sentence, definition, picture, audio,
expression_furigana, sentence_furigana``) — missing keys raise ``ValueError``.
``anki_note_builder.build_note`` then SKIPS any field whose mapped Anki field
name is the empty string (``if anki_field_name:``). So the minimal valid mapping
for a stock two-field "Basic" note (fields ``Front`` / ``Back``) is: map ``word``
→ ``"Front"`` and ``definition`` → ``"Back"``, and set EVERY other key to ``""``
so it is omitted from the note. That yields a Front/Back card carrying the mined
expression and its definition and nothing else — exactly what a no-frills E2E
card needs.

Two config modes (the ``bypass_known_words`` knob)
--------------------------------------------------
``build_app_config`` is the SHARED config builder — the soak runner uses it too,
not just these tests — so its default must be FAITHFUL to what a user exercises
manually in Episode Mining. The single knob ``bypass_known_words`` selects between
two modes:

**Default (``bypass_known_words=False``) — faithful real mining.** Sets
``use_known_words_db=True``, ``include_known_words=False``, and leaves
``deduplicate_sentences`` / ``allow_duplicate_cards`` at their real defaults
(``True`` / ``False``). This is the path the soak/live runner uses precisely so it
exercises known-words subtraction — the prime suspect for the "bug that only
appears after several mining sessions in a row" (``known_words.db`` accumulation).
In the real Phase-2 filter (``EpisodeProcessor._phase2_filter``) BOTH the
``use_known_words_db=True`` branch and the plain ``else`` branch call
``AnkiService.get_existing_vocabulary()``, so this mode REQUIRES a reachable Anki.

**``bypass_known_words=True`` — card-everything / no-Anki / deterministic.** Sets
``include_known_words=True``, ``deduplicate_sentences=False``,
``allow_duplicate_cards=True``. ``include_known_words`` is the ONLY phase-2 path
that makes NO AnkiConnect call (verified empirically: with it on, preview returns
``total_words_found=12, success=True``; with it off — db on OR off — the run fails
with "Cannot connect to AnkiConnect"), so this mode runs fully offscreen. It is
also independent of whatever is in the user's real collection (determinism) and,
with dedup off, mines every fixture word: the deterministic preview + smoke paths
use it. ``allow_duplicate_cards=True`` lets the live smoke test re-card freely so
re-runs don't collide with prior E2E cards.

``deduplicate_sentences``
-------------------------
Left on (the default), sentence dedup collapses the 12 fixture lemmas to one
representative per subtitle line (4). The ``bypass_known_words=True`` mode turns it
off so the preview yields all 12 ``EXPECTED_LEMMAS``, giving the harness a strong,
exact word-set cross-check; the faithful default leaves it at the real default.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from anki_miner.config import AnkiMinerConfig, ChainEntry
from tests.e2e.config import E2EConfig
from tests.e2e.fixtures_dictionary import DEFAULT_DICT_ID

__all__ = ["build_app_config"]


def _basic_note_fields() -> dict[str, str]:
    """Minimal valid ``anki_fields`` for the stock "Basic" note (Front/Back).

    Starts from the default mapping's key set so every REQUIRED + OPTIONAL key is
    present (satisfying ``AnkiService``'s construction-time validation), then maps
    only ``word`` → ``Front`` and ``definition`` → ``Back`` and blanks the rest so
    ``build_note`` omits them. See module docstring for the full rationale.
    """
    fields = dict.fromkeys(AnkiMinerConfig().anki_fields, "")
    fields["word"] = "Front"
    fields["definition"] = "Back"
    return fields


def build_app_config(e2e: E2EConfig, test_home: Path, *, bypass_known_words: bool = False) -> AnkiMinerConfig:
    """Build the mining config the in-process driver uses.

    Args:
        e2e: Harness config supplying the deck name, AnkiConnect URL, and note
            type.
        test_home: Isolated home for all on-disk state. Every ``*_db_path`` /
            ``*_root`` is pinned explicitly under here so the config is correct
            even when ``ANKI_MINER_HOME`` is NOT set in the process (a test using
            ``tmp_path`` rather than the autouse home-isolation fixture).
        bypass_known_words: Selects the config mode (see module docstring).
            ``False`` (default) is FAITHFUL real Episode-Mining
            (``use_known_words_db=True``, ``include_known_words=False``, real
            dedup/dup defaults) — the soak/live runner uses this so it exercises
            known-words subtraction, and it REQUIRES a reachable Anki. ``True`` is
            the card-everything / no-Anki / deterministic mode
            (``include_known_words=True``, ``deduplicate_sentences=False``,
            ``allow_duplicate_cards=True``) for the offscreen preview + smoke
            paths.

    Returns:
        A frozen :class:`AnkiMinerConfig` ready for ``create_episode_processor``.
        The caller must have seeded the offline dict at
        ``test_home/"dicts"/<DEFAULT_DICT_ID>/index.sqlite`` (via
        ``fixtures_dictionary.seed_offline_dict``) before mining.
    """
    test_home = Path(test_home)
    dicts_root = test_home / "dicts"

    # Known-words mode (see module docstring). The faithful default exercises
    # known-words subtraction (needs Anki) and leaves dedup/dup at their real
    # AnkiMinerConfig defaults; only the no-Anki / deterministic mode diverges
    # from real mining behavior (include-everything + dedup/dup off).
    base = AnkiMinerConfig()
    include_known_words = bypass_known_words
    deduplicate_sentences = base.deduplicate_sentences and not bypass_known_words
    allow_duplicate_cards = base.allow_duplicate_cards or bypass_known_words

    return dataclasses.replace(
        base,
        # --- Anki target (throwaway deck + stock note type) ---
        anki_deck_name=e2e.deck_name,
        ankiconnect_url=e2e.ankiconnect_url,
        anki_note_type=e2e.note_type,
        anki_fields=_basic_note_fields(),
        # --- isolated on-disk state (pinned explicitly; not env-derived) ---
        media_temp_folder=test_home / "media_temp",
        dicts_root=dicts_root,
        known_words_db_path=test_home / "known_words.db",
        history_db_path=test_home / "history.db",
        stats_db_path=test_home / "stats.db",
        audio_packs_root=test_home / "audio_packs",
        themes_root=test_home / "themes",
        # --- dictionary chain: the seeded offline dict only, no Jisho/network ---
        dictionary_chain=(ChainEntry(kind="indexed", dict_id=DEFAULT_DICT_ID, enabled=True),),
        # --- known-words mode (faithful default vs. no-Anki/deterministic) ---
        use_known_words_db=True,
        include_known_words=include_known_words,
        deduplicate_sentences=deduplicate_sentences,
        allow_duplicate_cards=allow_duplicate_cards,
        use_frequency_data=False,
        use_pitch_accent=False,
        enable_history=True,
        # Default expression_audio field stays "" (feature off) → no audio fetch.
    )
