"""Integration tests for the full deck-builder pipeline.

Exercises the aggregate → preview → (gated) build flow end-to-end with only
external I/O mocked: AnkiConnect (via ``anki_service.post_action``), media
extraction (via ``MediaExtractorService.extract_media_batch``), and definition
lookups (via ``DefinitionService.get_definitions_batch``).

Real services used throughout: ``SubtitleParserService`` (with real
fugashi/MeCab), ``WordFilterService``, ``AnkiService``, ``EpisodeProcessor``,
``DeckBuilderWorker``, ``aggregate``, ``select``.

Fixture corpus
--------------
Two .ass subtitle files for the same fictional show::

    ep01: 食べる (×2), 走る (×1)  →  3 tokens
    ep02: 食べる (×1), 本 (×1)    →  2 tokens

Combined: 食べる×3, 走る×1, 本×1 → 5 total tokens, 3 unique lemmas.

- ``食べる`` appears in BOTH episodes — the cross-episode dedup test.
- ``走る`` is unique to ep01.
- ``本`` is unique to ep02.
- ep01 has a repeated token so coverage math is non-trivial.

With ALL selection mode and no known words, the expected preview is:
    total_tokens=5, unique_lemmas=3, candidate_count=3,
    projected_coverage_pct=100.0, known_skipped=0, card_count=3.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pysubs2
import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.workers.deck_builder_worker import DeckBuilderWorker
from anki_miner.models.deck_build import DeckBuildPreview, DeckBuildRequest, DeckSelectionMode
from anki_miner.models.media import MediaData
from anki_miner.models.word import TokenizedWord
from anki_miner.presenters import NullPresenter
from anki_miner.utils.file_pairing import FilePair

# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def _write_ass(path: Path, events: list[tuple[float, float, str]]) -> None:
    """Write a minimal .ass subtitle file. events: [(start_sec, end_sec, text), ...]."""
    subs = pysubs2.SSAFile()
    for start_sec, end_sec, text in events:
        subs.append(pysubs2.SSAEvent(start=int(start_sec * 1000), end=int(end_sec * 1000), text=text))
    subs.save(str(path))


def _make_post_action(known_words: set[str], config: AnkiMinerConfig | None = None) -> Any:
    """Return a ``post_action`` callable dispatching by action name.

    - ``modelNames``      → list containing the configured note type (pre-flight).
    - ``modelFieldNames`` → list of configured field names (pre-flight).
    - ``createDeck``      → fake deck ID.
    - ``findNotes``       → one synthetic ID per known word.
    - ``notesInfo``       → field dicts for the known words.
    - ``addNotes``        → sequential IDs for the submitted batch.
    - anything else       → None (safe default).
    """
    _note_id_counter = [1000]

    def _dispatch(url: str, action: str, params: dict | None = None, timeout: int = 30) -> Any:
        if action == "modelNames":
            note_type = config.anki_note_type if config is not None else "test_type"
            return [note_type]
        if action == "modelFieldNames":
            fields = list(config.anki_fields.values()) if config is not None else []
            return fields
        if action == "createDeck":
            return 1234
        if action == "findNotes":
            return list(range(1, len(known_words) + 1)) if known_words else []
        if action == "notesInfo":
            requested = len((params or {}).get("notes", []))
            return [{"fields": {"word": {"value": w}}} for w in list(known_words)[:requested]]
        if action == "addNotes":
            notes = (params or {}).get("notes", [])
            start = _note_id_counter[0]
            _note_id_counter[0] += len(notes)
            return list(range(start, start + len(notes)))
        return None

    return _dispatch


def _fake_extract_media_batch(
    tmp_path: Path,
) -> Any:
    """Return a ``extract_media_batch`` side_effect that creates stub files.

    For each word in ``words``, writes a small fake JPEG so ``MediaData.has_screenshot``
    returns True, which is the gate that lets words through to card creation.
    The stub file contains only a few bytes — its contents are not inspected
    during card creation (the storeMediaFile call is to AnkiConnect, which we mock).
    """
    screenshot_dir = tmp_path / "screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    def _side_effect(
        video_file: Path,
        words: list[TokenizedWord],
        progress_callback=None,
        cancelled_check=None,
        temp_folder=None,
        *,
        audio_track_override=None,
        audio_only=False,
    ) -> list[tuple[TokenizedWord, MediaData]]:
        results = []
        for word in words:
            safe = word.lemma.replace("/", "_")
            ss = screenshot_dir / f"{safe}.jpg"
            ss.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
            results.append(
                (
                    word,
                    MediaData(
                        screenshot_path=ss,
                        audio_path=None,
                        screenshot_filename=ss.name,
                        audio_filename=None,
                    ),
                )
            )
        return results

    return _side_effect


@pytest.fixture
def base_config(tmp_path):
    """Minimal config with all optional lookups disabled."""
    return AnkiMinerConfig(
        anki_deck_name="original_deck",
        anki_note_type="test_type",
        anki_word_field="word",
        anki_fields={
            "word": "word",
            "sentence": "sentence",
            "definition": "definition",
            "picture": "picture",
            "audio": "audio",
            "expression_furigana": "expression_furigana",
            "sentence_furigana": "sentence_furigana",
        },
        media_temp_folder=tmp_path / "media",
        jmdict_path=tmp_path / "JMdict_e",
        max_parallel_workers=1,
        use_frequency_data=False,
        use_pitch_accent=False,
        use_blacklist=False,
        use_whitelist=False,
        use_known_words_db=False,
        include_known_words=False,
        # Keep the build off the real ~/.anki_miner — in particular the known
        # words DB, which would otherwise poison collection-filter tests.
        dicts_root=tmp_path / "dicts",
        known_words_db_path=tmp_path / "known_words.db",
        history_db_path=tmp_path / "history.db",
        stats_db_path=tmp_path / "stats.db",
    )


@pytest.fixture
def subtitle_pair(tmp_path) -> tuple[FilePair, FilePair]:
    """Two .ass subtitle files + dummy video paths forming a 2-episode corpus.

    ep01: 食べる (×2), 走る (×1)
    ep02: 食べる (×1), 本 (×1)
    """
    ep01_sub = tmp_path / "ep01.ass"
    ep02_sub = tmp_path / "ep02.ass"

    _write_ass(ep01_sub, [(1.0, 3.0, "食べる"), (4.0, 6.0, "走る"), (7.0, 9.0, "食べる")])
    _write_ass(ep02_sub, [(1.0, 3.0, "食べる"), (4.0, 6.0, "本")])

    # Video files must exist on disk (EpisodeProcessor opens a temp dir based on
    # the video stem); their contents are not read — media extraction is mocked.
    ep01_video = tmp_path / "ep01.mkv"
    ep02_video = tmp_path / "ep02.mkv"
    ep01_video.touch()
    ep02_video.touch()

    return FilePair(video=ep01_video, subtitle=ep01_sub), FilePair(video=ep02_video, subtitle=ep02_sub)


def _collect(signal) -> list:
    """Attach a list-appending slot to a signal and return the backing list."""
    received: list = []
    signal.connect(lambda *args: received.append(args if len(args) != 1 else args[0]))
    return received


def _run_worker(
    qapp,
    base_config: AnkiMinerConfig,
    pairs: list[FilePair],
    tmp_path: Path,
    *,
    collection_filter: bool,
    known_words: set[str],
    deck_name: str = "My Anime Deck",
) -> tuple[list[DeckBuildPreview], list, list[dict]]:
    """Run a ``DeckBuilderWorker`` synchronously and return
    ``(previews, finished_signals, add_notes_params_list)``.

    ``add_notes_params_list`` is the list of ``params`` dicts passed to
    ``post_action`` for every ``addNotes`` call, in order.
    """
    request = DeckBuildRequest(
        pairs=pairs,
        deck_name=deck_name,
        mode=DeckSelectionMode.ALL,
        value=0.0,
        collection_filter=collection_filter,
    )

    captured_add_notes: list[dict] = []
    post_action_impl = _make_post_action(known_words, base_config)

    def _post_action_spy(url: str, action: str, params: dict | None = None, timeout: int = 30) -> Any:
        if action == "addNotes":
            captured_add_notes.append(params or {})
        return post_action_impl(url, action, params, timeout)

    worker = DeckBuilderWorker(
        request=request,
        config=base_config,
        presenter=NullPresenter(),
        progress_callback=None,
        stats_service=None,
    )

    previews = _collect(worker.preview_ready)
    finished = _collect(worker.build_finished)
    errors = _collect(worker.error)
    worker.confirm()

    with (
        patch("anki_miner.services.anki_service.post_action", side_effect=_post_action_spy),
        # Media uploads route through anki_media_store; stub its multi POST
        # with one non-error sub-result per action so every file counts as
        # stored without touching the network.
        patch(
            "anki_miner.services.anki_media_store.post_multi",
            side_effect=lambda url, actions, timeout=30: [None] * len(actions),
        ),
        patch("anki_miner.services.anki_media_store.post_action", side_effect=_post_action_spy),
        patch(
            "anki_miner.services.media_extractor.MediaExtractorService.extract_media_batch",
            side_effect=_fake_extract_media_batch(tmp_path),
        ),
        patch(
            "anki_miner.services.definition_service.DefinitionService.get_definitions_batch",
            side_effect=lambda lemmas, *a, **kw: ["test definition"] * len(lemmas),
        ),
        patch(
            "anki_miner.services.definition_service.DefinitionService.get_glossaries_batch",
            side_effect=lambda lemmas, *a, **kw: [None] * len(lemmas),
        ),
    ):
        worker.run()

    assert not errors, f"Worker raised errors: {errors}"
    return previews, finished, captured_add_notes


# --------------------------------------------------------------------------- #
# Test 1: preview numbers
# --------------------------------------------------------------------------- #


class TestDeckBuilderPreview:
    def test_all_mode_preview_numbers(self, qapp, base_config, subtitle_pair, tmp_path):
        """Phase 1 emits a DeckBuildPreview with correct counts for the 2-ep fixture."""
        pair1, pair2 = subtitle_pair

        previews, finished, _ = _run_worker(
            qapp,
            base_config,
            [pair1, pair2],
            tmp_path,
            collection_filter=False,
            known_words=set(),
        )

        assert len(previews) == 1
        p = previews[0]
        assert isinstance(p, DeckBuildPreview)
        # 食べる×3 + 走る×1 + 本×1 = 5 total tokens
        assert p.total_tokens == 5
        # 3 distinct lemmas
        assert p.unique_lemmas == 3
        # ALL mode: all 3 lemmas selected
        assert p.candidate_count == 3
        # ALL mode, no known words: full coverage
        assert p.projected_coverage_pct == pytest.approx(100.0)
        assert p.known_skipped == 0
        assert p.card_count == 3


# --------------------------------------------------------------------------- #
# Test 2: deck routing + createDeck called exactly once
# --------------------------------------------------------------------------- #


class TestDeckBuilderRouting:
    def test_createdeck_called_exactly_once(self, qapp, base_config, subtitle_pair, tmp_path):
        """ensure_deck fires createDeck with the target deck name (at least once)."""
        deck_name = "My Anime Deck"
        pair1, pair2 = subtitle_pair

        create_deck_calls: list[str] = []
        post_action_impl = _make_post_action(set(), base_config)

        def _spy(url: str, action: str, params: dict | None = None, timeout: int = 30) -> Any:
            if action == "createDeck":
                create_deck_calls.append((params or {}).get("deck", ""))
            return post_action_impl(url, action, params, timeout)

        request = DeckBuildRequest(
            pairs=[pair1, pair2],
            deck_name=deck_name,
            mode=DeckSelectionMode.ALL,
            value=0.0,
            collection_filter=False,
        )

        worker = DeckBuilderWorker(
            request=request,
            config=base_config,
            presenter=NullPresenter(),
            progress_callback=None,
            stats_service=None,
        )
        errors = _collect(worker.error)
        worker.confirm()

        with (
            patch("anki_miner.services.anki_service.post_action", side_effect=_spy),
            # Media uploads route through anki_media_store; stub its multi
            # POST with one non-error sub-result per action so every file
            # counts as stored without touching the network.
            patch(
                "anki_miner.services.anki_media_store.post_multi",
                side_effect=lambda url, actions, timeout=30: [None] * len(actions),
            ),
            patch("anki_miner.services.anki_media_store.post_action", side_effect=_spy),
            patch(
                "anki_miner.services.media_extractor.MediaExtractorService.extract_media_batch",
                side_effect=_fake_extract_media_batch(tmp_path),
            ),
            patch(
                "anki_miner.services.definition_service.DefinitionService.get_definitions_batch",
                side_effect=lambda lemmas, *a, **kw: ["definition"] * len(lemmas),
            ),
            patch(
                "anki_miner.services.definition_service.DefinitionService.get_glossaries_batch",
                side_effect=lambda lemmas, *a, **kw: [None] * len(lemmas),
            ),
        ):
            worker.run()

        assert not errors, f"Worker raised errors: {errors}"
        assert create_deck_calls, f"createDeck should be called at least once, got: {create_deck_calls}"
        assert all(
            c == deck_name for c in create_deck_calls
        ), f"All createDeck calls should use {deck_name!r}, got {create_deck_calls}"

    def test_addnotes_routed_to_named_deck(self, qapp, base_config, subtitle_pair, tmp_path):
        """Every note in addNotes payloads must carry the target deck name."""
        deck_name = "My Anime Deck"
        pair1, pair2 = subtitle_pair

        _, _, add_notes_calls = _run_worker(
            qapp,
            base_config,
            [pair1, pair2],
            tmp_path,
            collection_filter=False,
            known_words=set(),
            deck_name=deck_name,
        )

        assert add_notes_calls, "Expected at least one addNotes call"
        for call_params in add_notes_calls:
            notes = call_params.get("notes", [])
            assert notes, "addNotes called with empty notes list"
            for note in notes:
                assert (
                    note["deckName"] == deck_name
                ), f"Note routed to wrong deck: got {note['deckName']!r}, expected {deck_name!r}"


# --------------------------------------------------------------------------- #
# Test 3: cross-episode dedup — 食べる carded once, not twice
# --------------------------------------------------------------------------- #


class TestCrossEpisodeDedup:
    def test_shared_lemma_carded_once(self, qapp, base_config, subtitle_pair, tmp_path):
        """食べる appears in both episodes but must produce exactly one card total."""
        pair1, pair2 = subtitle_pair

        _, finished, add_notes_calls = _run_worker(
            qapp,
            base_config,
            [pair1, pair2],
            tmp_path,
            collection_filter=False,
            known_words=set(),
        )

        all_notes = [note for cp in add_notes_calls for note in cp.get("notes", [])]
        # The word field is mapped through anki_fields: "word" → "word".
        mined_words = [note["fields"]["word"] for note in all_notes]

        # 食べる appears in both episodes but must be carded exactly once.
        assert (
            mined_words.count("食べる") == 1
        ), f"食べる should be carded exactly once (cross-episode dedup), got: {mined_words}"
        # Total: 食べる + 走る + 本 = 3 distinct cards.
        assert len(mined_words) == 3, f"Expected 3 cards total, got {len(mined_words)}: {mined_words}"

        # build_finished sums cards_created across both episodes.
        assert len(finished) == 1
        total_cards, _ = finished[0]
        assert total_cards == 3

    def test_build_finished_emits_coverage(self, qapp, base_config, subtitle_pair, tmp_path):
        """build_finished carries the preview's projected coverage percentage."""
        pair1, pair2 = subtitle_pair

        previews, finished, _ = _run_worker(
            qapp,
            base_config,
            [pair1, pair2],
            tmp_path,
            collection_filter=False,
            known_words=set(),
        )

        assert len(finished) == 1
        _, coverage_pct = finished[0]
        assert coverage_pct == pytest.approx(previews[0].projected_coverage_pct)
        # ALL mode with no known words → 100 % coverage
        assert coverage_pct == pytest.approx(100.0)


# --------------------------------------------------------------------------- #
# Test 4: collection_filter OFF vs ON
# --------------------------------------------------------------------------- #


class TestCollectionFilterToggle:
    def test_filter_off_cards_everything(self, qapp, base_config, subtitle_pair, tmp_path):
        """collection_filter=False: all 3 lemmas are carded even when 走る is 'known'."""
        pair1, pair2 = subtitle_pair

        _, _, add_notes_calls = _run_worker(
            qapp,
            base_config,
            [pair1, pair2],
            tmp_path,
            collection_filter=False,
            # 走る is in the collection, but filter OFF must ignore that.
            known_words={"走る"},
        )

        all_notes = [note for cp in add_notes_calls for note in cp.get("notes", [])]
        mined_words = [note["fields"]["word"] for note in all_notes]
        assert (
            len(mined_words) == 3
        ), f"filter OFF: expected 3 cards (known-words ignored), got {len(mined_words)}: {mined_words}"

    def test_filter_on_skips_known_lemma(self, qapp, base_config, subtitle_pair, tmp_path):
        """collection_filter=True: a lemma already in the collection is not re-carded.

        走る is returned by the mocked findNotes/notesInfo pair as a known word.
        With filter ON, the pipeline calls get_existing_vocabulary() and subtracts it.
        The result must be 2 cards (食べる + 本), not 3.
        """
        pair1, pair2 = subtitle_pair

        _, _, add_notes_calls = _run_worker(
            qapp,
            base_config,
            [pair1, pair2],
            tmp_path,
            collection_filter=True,
            known_words={"走る"},
        )

        all_notes = [note for cp in add_notes_calls for note in cp.get("notes", [])]
        mined_words = [note["fields"]["word"] for note in all_notes]

        assert "走る" not in mined_words, f"filter ON: 走る is known, must not be carded; got: {mined_words}"
        assert len(mined_words) == 2, f"filter ON: expected 2 cards (走る known), got {len(mined_words)}"

    def test_filter_off_produces_more_cards_than_filter_on(self, qapp, base_config, subtitle_pair, tmp_path):
        """filter OFF produces strictly more cards than filter ON when a known word exists."""
        pair1, pair2 = subtitle_pair
        known = {"走る"}

        _, _, calls_off = _run_worker(
            qapp,
            base_config,
            [pair1, pair2],
            tmp_path,
            collection_filter=False,
            known_words=known,
        )
        _, _, calls_on = _run_worker(
            qapp,
            base_config,
            [pair1, pair2],
            tmp_path,
            collection_filter=True,
            known_words=known,
        )

        total_off = sum(len(cp.get("notes", [])) for cp in calls_off)
        total_on = sum(len(cp.get("notes", [])) for cp in calls_on)
        assert total_off > total_on, f"filter OFF should produce more cards than filter ON: {total_off} vs {total_on}"
