"""Tests for episode_processor module."""

import re
import threading
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.exceptions import AnkiConnectionError, SetupError, SubtitleParseError
from anki_miner.models import CardPayload, LineLemmas, MediaData, TokenizedWord
from anki_miner.models.youtube import FetchedMedia
from anki_miner.orchestration.episode_processor import (
    EpisodeProcessor,
    _audio_failure_diagnosis,
    _EpisodeContext,
    _sanitize_source_label,
)
from anki_miner.presenters import NullPresenter
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.pitch_accent_service import PitchEntry


def _make_episode_context(tmp_path):
    """Create a minimal _EpisodeContext for direct phase helper tests."""
    import time

    return _EpisodeContext(
        start_time=time.time(),
        video_file_str=str(tmp_path / "v.mkv"),
        subtitle_file_str=str(tmp_path / "s.ass"),
        episode_name="ep01",
        series_name="TestSeries",
        source_label="TestSeries — ep01",
    )


def _make_word(lemma="食べる", surface=None, start_time=1.0, pos="動詞"):
    return TokenizedWord(
        surface=surface or f"{lemma}た",
        lemma=lemma,
        reading="タベル",
        sentence=f"{lemma}のテスト",
        start_time=start_time,
        end_time=start_time + 2.0,
        duration=2.0,
        pos=pos,
    )


def _make_media(prefix="word"):
    return MediaData(
        screenshot_path=Path(f"/tmp/{prefix}.jpg"),
        audio_path=Path(f"/tmp/{prefix}.mp3"),
        screenshot_filename=f"{prefix}.jpg",
        audio_filename=f"{prefix}.mp3",
    )


class TestSanitizeSourceLabel:
    """Tests for _sanitize_source_label (Issue #83)."""

    def test_strips_arr_metadata_block_and_release_group(self):
        label = (
            "Season 01 — Gals Can't Be Kind to Otaku!! (2026) - S01E01 - "
            "Can a Gal Be Kind to Otaku [WEBRip-1080p][10bit][AV1][Opus 2.0][JA]-Trix"
        )
        assert _sanitize_source_label(label) == (
            "Season 01 — Gals Can't Be Kind to Otaku!! (2026) - S01E01 - " "Can a Gal Be Kind to Otaku"
        )

    def test_preserves_mid_title_brackets(self):
        label = "Show [Blu-ray] - S01E01 - Title [JA]"
        assert _sanitize_source_label(label) == "Show [Blu-ray] - S01E01 - Title"

    def test_no_brackets_is_noop(self):
        label = "TestSeries — Episode 1"
        assert _sanitize_source_label(label) == label

    def test_trailing_group_without_release_group(self):
        assert _sanitize_source_label("Some Title [JA]") == "Some Title"

    def test_multiple_adjacent_groups(self):
        label = "Title [1080p] [x265] [JA]-Group"
        assert _sanitize_source_label(label) == "Title"

    def test_strips_surrounding_whitespace(self):
        assert _sanitize_source_label("  Padded Title  ") == "Padded Title"

    def test_empty_string(self):
        assert _sanitize_source_label("") == ""

    def test_all_metadata_collapses_to_empty(self):
        assert _sanitize_source_label("[WEBRip][JA]-Trix") == ""


class TestProcessEpisode:
    """Tests for EpisodeProcessor.process_episode method."""

    @pytest.fixture
    def mock_services(self):
        """Create a set of mock services for the episode processor."""
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda words: words
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    @pytest.fixture
    def processor(self, test_config, mock_services):
        return EpisodeProcessor(
            config=test_config,
            subtitle_parser=mock_services["subtitle_parser"],
            word_filter=mock_services["word_filter"],
            media_extractor=mock_services["media_extractor"],
            definition_service=mock_services["definition_service"],
            anki_service=mock_services["anki_service"],
            presenter=NullPresenter(),
        )

    def test_full_pipeline_happy_path(self, processor, mock_services, tmp_path):
        """All 5 phases complete successfully."""
        video = tmp_path / "ep01.mkv"
        sub = tmp_path / "ep01.ass"

        words = [_make_word("食べる"), _make_word("走る", 5.0)]
        media1, media2 = _make_media("taberu"), _make_media("hashiru")

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = [
            (words[0], media1),
            (words[1], media2),
        ]
        mock_services["definition_service"].get_definitions_batch.return_value = [
            "1. to eat",
            "1. to run",
        ]
        mock_services["anki_service"].create_cards_batch.return_value = 2

        result = processor.process_episode(video, sub)

        assert result.total_words_found == 2
        assert result.new_words_found == 2
        assert result.cards_created == 2
        assert result.success is True
        assert result.elapsed_time > 0

    def test_skipped_duplicates_surfaced_as_warning(self, test_config, mock_services, tmp_path):
        """A non-zero last_skipped_duplicates from card creation is reported."""
        presenter = MagicMock()
        proc = EpisodeProcessor(
            config=test_config,
            subtitle_parser=mock_services["subtitle_parser"],
            word_filter=mock_services["word_filter"],
            media_extractor=mock_services["media_extractor"],
            definition_service=mock_services["definition_service"],
            anki_service=mock_services["anki_service"],
            presenter=presenter,
        )

        words = [_make_word("食べる"), _make_word("走る", start_time=5.0)]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = [
            (words[0], _make_media("taberu")),
            (words[1], _make_media("hashiru")),
        ]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. eat", "1. run"]
        mock_services["anki_service"].create_cards_batch.return_value = 1
        mock_services["anki_service"].last_skipped_duplicates = 1

        proc.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        warnings = [c.args[0] for c in presenter.show_warning.call_args_list]
        assert any("Skipped 1" in w and "duplicate" in w.lower() for w in warnings)

    def test_media_store_failures_surfaced_as_warning(self, test_config, mock_services, tmp_path):
        """A non-zero last_media_store_failures from card creation is reported."""
        presenter = MagicMock()
        proc = EpisodeProcessor(
            config=test_config,
            subtitle_parser=mock_services["subtitle_parser"],
            word_filter=mock_services["word_filter"],
            media_extractor=mock_services["media_extractor"],
            definition_service=mock_services["definition_service"],
            anki_service=mock_services["anki_service"],
            presenter=presenter,
        )

        words = [_make_word("食べる"), _make_word("走る", start_time=5.0)]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = [
            (words[0], _make_media("taberu")),
            (words[1], _make_media("hashiru")),
        ]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. eat", "1. run"]
        mock_services["anki_service"].create_cards_batch.return_value = 2
        mock_services["anki_service"].last_skipped_duplicates = 0
        mock_services["anki_service"].last_media_store_failures = 3

        proc.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        warnings = [c.args[0] for c in presenter.show_warning.call_args_list]
        assert any("3 media file" in w and "no audio or screenshot" in w for w in warnings)

    def test_early_return_no_words(self, processor, mock_services, tmp_path):
        """No words found in subtitles → early return."""
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = []

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.total_words_found == 0
        assert result.cards_created == 0
        mock_services["anki_service"].get_existing_vocabulary.assert_not_called()

    def test_early_return_all_words_known(self, processor, mock_services, tmp_path):
        """All words already in Anki → early return."""
        words = [_make_word()]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = {"食べる"}
        mock_services["word_filter"].filter_unknown.return_value = []

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.total_words_found == 1
        assert result.new_words_found == 0
        assert result.cards_created == 0
        mock_services["media_extractor"].extract_media_batch.assert_not_called()

    def test_preview_mode(self, processor, mock_services, tmp_path):
        """Preview mode should not extract media or create cards."""
        words = [_make_word()]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass", preview_mode=True)

        assert result.new_words_found == 1
        assert result.cards_created == 0
        mock_services["media_extractor"].extract_media_batch.assert_not_called()
        mock_services["anki_service"].create_cards_batch.assert_not_called()

    def test_early_return_no_media(self, processor, mock_services, tmp_path):
        """No media extracted → early return with error."""
        words = [_make_word()]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = []

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.cards_created == 0
        assert len(result.errors) > 0
        mock_services["definition_service"].get_definitions_batch.assert_not_called()

    def test_data_flow_between_phases(self, processor, mock_services, tmp_path, monkeypatch):
        """Verify that outputs of one phase are passed as inputs to the next."""
        video = tmp_path / "v.mkv"
        sub = tmp_path / "s.ass"
        word = _make_word()
        media = _make_media()

        # Neutralize the per-card styling seam so this data-flow assertion stays
        # focused on phase wiring: the definition field now carries a card-wide
        # <style> block (glossary unmapped, definition mapped) — its own dedicated
        # tests cover that. Also avoids real dictionary-registry / SQLite I/O.
        monkeypatch.setattr("anki_miner.orchestration.episode_processor.collect_dictionary_css", lambda cfg: "")
        monkeypatch.setattr("anki_miner.orchestration.episode_processor.build_card_style_block", lambda **k: "")

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor.process_episode(video, sub)

        # Verify subtitle_parser gets the subtitle file
        mock_services["subtitle_parser"].parse_subtitle_file.assert_called_once_with(sub)

        # Verify word_filter gets all_words and existing vocab
        mock_services["word_filter"].filter_unknown.assert_called_once()
        args = mock_services["word_filter"].filter_unknown.call_args
        assert args[0][0] == [word]  # all_words
        assert args[0][1] == set()  # existing_vocabulary

        # Verify media_extractor gets the video and unknown words
        mock_services["media_extractor"].extract_media_batch.assert_called_once()
        me_args = mock_services["media_extractor"].extract_media_batch.call_args
        assert me_args[0][0] == video
        assert me_args[0][1] == [word]

        # Verify definition_service gets lemmas of words with media
        mock_services["definition_service"].get_definitions_batch.assert_called_once()
        ds_args = mock_services["definition_service"].get_definitions_batch.call_args
        assert ds_args[0][0] == [("食べる", "たべる")]
        # Lookup-miss fallback context (5.2): lemma → (orth_base, cType=None).
        assert ds_args[0][2] == {"食べる": ("", None)}

        # Verify anki_service gets combined CardPayload entries
        mock_services["anki_service"].create_cards_batch.assert_called_once()
        as_args = mock_services["anki_service"].create_cards_batch.call_args
        card_data = as_args[0][0]
        assert len(card_data) == 1
        # Every card now carries an unconditional "source" stamp (Issue #69);
        # the field-level opt-in in AnkiService decides whether it lands.
        expected_source = f"{video.parent.name} — {video.stem} @ 00:00:01"
        assert card_data[0] == CardPayload(
            word=word,
            media=media,
            definition="1. to eat",
            extra_fields={"source": expected_source},
        )

    def test_fallback_context_carries_orth_base(self, processor, mock_services, tmp_path):
        """5.2: the miss-only fallback context threads each lemma's orth_base
        (source-orthography variant) so 乞う resolves when the lemma is 請う."""
        video = tmp_path / "v.mkv"
        sub = tmp_path / "s.ass"
        word = _make_word(lemma="請う")
        word.orth_base = "乞う"
        media = _make_media()

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to beg"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor.process_episode(video, sub)

        ds_args = mock_services["definition_service"].get_definitions_batch.call_args
        assert ds_args[0][2] == {"請う": ("乞う", None)}

    def test_audio_only_flag_reaches_extract_media_batch(self, processor, mock_services, tmp_path):
        """audio_only=True is threaded down to extract_media_batch."""
        video = tmp_path / "book.m4b"
        sub = tmp_path / "book.srt"
        word = _make_word()

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, _make_media())]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor.process_episode(video, sub, audio_only=True)

        me_kwargs = mock_services["media_extractor"].extract_media_batch.call_args.kwargs
        assert me_kwargs["audio_only"] is True

    def test_audio_only_defaults_false(self, processor, mock_services, tmp_path):
        """Default process_episode call passes audio_only=False to the extractor."""
        video = tmp_path / "ep01.mkv"
        sub = tmp_path / "ep01.ass"
        word = _make_word()

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, _make_media())]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor.process_episode(video, sub)

        me_kwargs = mock_services["media_extractor"].extract_media_batch.call_args.kwargs
        assert me_kwargs["audio_only"] is False

    def _run_phase3(
        self, test_config, mock_services, tmp_path, *, fmt="avif", resolved: "str | None" = "webp", audio_only=False
    ):
        """Drive process_episode through phase 3 with animated screenshots configured.

        Returns the MagicMock presenter so callers can inspect show_warning.
        """
        cfg = replace(test_config, screenshot_animated=True, screenshot_animated_format=fmt)
        presenter = MagicMock()
        proc = EpisodeProcessor(
            config=cfg,
            subtitle_parser=mock_services["subtitle_parser"],
            word_filter=mock_services["word_filter"],
            media_extractor=mock_services["media_extractor"],
            definition_service=mock_services["definition_service"],
            anki_service=mock_services["anki_service"],
            presenter=presenter,
        )
        word = _make_word()
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].resolve_animated_format.return_value = resolved
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, _make_media())]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1
        proc.process_episode(tmp_path / "ep.mkv", tmp_path / "ep.ass", audio_only=audio_only)
        return presenter

    def test_avif_fallback_warns_and_threads_webp(self, test_config, mock_services, tmp_path):
        """AVIF configured + resolver returns webp → warn once + thread animated_format=webp."""
        presenter = self._run_phase3(test_config, mock_services, tmp_path, fmt="avif", resolved="webp")

        warnings = [c.args[0] for c in presenter.show_warning.call_args_list]
        assert any("WebP" in w and "AVIF" in w for w in warnings)
        me_kwargs = mock_services["media_extractor"].extract_media_batch.call_args.kwargs
        assert me_kwargs["animated_format"] == "webp"

    def test_no_animated_encoder_warns_unavailable(self, test_config, mock_services, tmp_path):
        """Resolver returns None → 'unavailable' warning + thread animated_format=None."""
        presenter = self._run_phase3(test_config, mock_services, tmp_path, fmt="avif", resolved=None)

        warnings = [c.args[0] for c in presenter.show_warning.call_args_list]
        assert any("unavailable" in w.lower() for w in warnings)
        me_kwargs = mock_services["media_extractor"].extract_media_batch.call_args.kwargs
        assert me_kwargs["animated_format"] is None

    def test_webp_configured_missing_warns_unavailable_not_fallback(self, test_config, mock_services, tmp_path):
        """WebP-primary config + resolver None → generic 'unavailable', NOT the AVIF→WebP line."""
        presenter = self._run_phase3(test_config, mock_services, tmp_path, fmt="webp", resolved=None)

        warnings = [c.args[0] for c in presenter.show_warning.call_args_list]
        assert any("unavailable" in w.lower() for w in warnings)
        assert not any("Using WebP" in w for w in warnings)

    def test_usable_format_does_not_warn(self, test_config, mock_services, tmp_path):
        """AVIF configured + resolver returns avif → no fallback warning."""
        presenter = self._run_phase3(test_config, mock_services, tmp_path, fmt="avif", resolved="avif")

        warnings = [c.args[0] for c in presenter.show_warning.call_args_list]
        assert not any("animated screenshot" in w.lower() for w in warnings)
        me_kwargs = mock_services["media_extractor"].extract_media_batch.call_args.kwargs
        assert me_kwargs["animated_format"] == "avif"

    def test_audio_only_skips_resolve_and_warning(self, test_config, mock_services, tmp_path):
        """audio_only=True → resolver never consulted, no fallback warning, no animated_format kwarg."""
        presenter = self._run_phase3(test_config, mock_services, tmp_path, fmt="avif", resolved="webp", audio_only=True)

        mock_services["media_extractor"].resolve_animated_format.assert_not_called()
        warnings = [c.args[0] for c in presenter.show_warning.call_args_list]
        assert not any("WebP" in w for w in warnings)
        me_kwargs = mock_services["media_extractor"].extract_media_batch.call_args.kwargs
        assert "animated_format" not in me_kwargs

    def test_fallback_warning_once_per_episode(self, test_config, mock_services, tmp_path):
        """Each episode (each process_episode call) emits exactly one fallback warning."""
        self._run_phase3(test_config, mock_services, tmp_path, fmt="avif", resolved="webp")
        presenter = self._run_phase3(test_config, mock_services, tmp_path, fmt="avif", resolved="webp")

        # The second run's presenter saw exactly one AVIF→WebP warning.
        webp_warnings = [c.args[0] for c in presenter.show_warning.call_args_list if "Using WebP" in c.args[0]]
        assert len(webp_warnings) == 1

    def test_subtitle_parse_error_handling(self, processor, mock_services, tmp_path):
        """SubtitleParseError should be caught and returned as error."""
        mock_services["subtitle_parser"].parse_subtitle_file.side_effect = SubtitleParseError("parse failed")

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.success is False
        assert any("parse failed" in e for e in result.errors)
        assert result.elapsed_time > 0

    def test_unexpected_exception_handling(self, processor, mock_services, tmp_path):
        """Unexpected exceptions should be caught and returned as error."""
        mock_services["subtitle_parser"].parse_subtitle_file.side_effect = RuntimeError("unexpected")

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.success is False
        assert any("unexpected" in e.lower() for e in result.errors)

    def test_elapsed_time_positive(self, processor, mock_services, tmp_path):
        """Elapsed time should always be > 0."""
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = []

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.elapsed_time > 0

    def test_partial_media_extraction(self, processor, mock_services, tmp_path):
        """When only some words get media, only those should get definitions/cards."""
        words = [_make_word("食べる"), _make_word("走る", 5.0), _make_word("泳ぐ", 10.0)]
        media1 = _make_media("taberu")
        # Only first word gets media

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = [
            (words[0], media1),
        ]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # Only 1 definition fetched (for the word with media)
        ds_args = mock_services["definition_service"].get_definitions_batch.call_args
        assert ds_args[0][0] == [("食べる", "たべる")]

        assert result.cards_created == 1

    def test_mid_batch_failure_preserves_partial_card_ids(self, test_config, mock_services, tmp_path):
        """OVH-008: mid-batch AnkiConnectionError → partial IDs from completed
        batches are harvested from last_created_note_ids and returned in the
        result so the Undo button can appear.
        """
        from anki_miner.exceptions import AnkiConnectionError

        batch1_ids = [101, 102, 103]
        words = [_make_word("食べる")]
        media = _make_media("taberu")

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = [(words[0], media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]

        # Simulate: create_cards_batch raises mid-run but the AnkiService finally
        # has already stashed batch-1 IDs in last_created_note_ids.
        def _raise_mid_batch(card_data, progress_callback=None):
            mock_services["anki_service"].last_created_note_ids = batch1_ids
            raise AnkiConnectionError("Anki connection lost on batch 2")

        mock_services["anki_service"].create_cards_batch.side_effect = _raise_mid_batch

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            **mock_services,
        )

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.card_ids == batch1_ids
        assert result.cards_created == len(batch1_ids)
        assert result.success is False
        assert any("3 card" in e for e in result.errors)

    def test_early_phase_failure_does_not_attribute_prior_episode_ids(self, test_config, mock_services, tmp_path):
        """OVH-008 correctness guard: a failure in phase 1-4 (before
        create_cards_batch runs) must NOT attribute the PREVIOUS episode's IDs.

        The processor resets last_created_note_ids at the START of
        process_episode so stale IDs from an earlier run are cleared before
        the except handler reads them.

        Guard strength: stale IDs are injected directly onto anki_service
        IMMEDIATELY BEFORE the crashing call — the reset at the top of
        process_episode is the ONLY thing that can clear them, so removing
        that line makes this test fail.
        """
        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            **mock_services,
        )

        # Phase 1 crashes before create_cards_batch ever runs.
        mock_services["subtitle_parser"].parse_subtitle_file.side_effect = RuntimeError("parse crash")

        # Plant stale IDs directly on anki_service right before the call.
        # No prior process_episode run touches them — only the reset inside
        # process_episode can clear them before the except handler reads the
        # attribute.
        mock_services["anki_service"].last_created_note_ids = [201, 202]

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.card_ids == []
        assert result.cards_created == 0
        assert result.success is False


class TestOptionalServices:
    """Tests for EpisodeProcessor with optional pitch accent and frequency services."""

    @pytest.fixture
    def mock_services(self):
        """Create a set of mock services for the episode processor."""
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda words: words
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    def test_frequency_service_attaches_ranks(self, test_config, mock_services, tmp_path):
        """Frequency attaches min + harmonic + per-source breakdown from ONE fetch."""
        word = _make_word("食べる")
        mock_frequency = MagicMock()
        mock_frequency.is_available.return_value = True
        # Two sources so min (200) and harmonic differ, proving both are derived.
        mock_frequency.lookup_all.return_value = [("BCCWJ", 400, None), ("JPDB", 200, None)]

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, _make_media())]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            frequency_service=mock_frequency,
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # A single per-source fetch, reading-scoped (word.reading "タベル" →
        # hiragana-normalized "たべる"); min + harmonic are derived locally, so the
        # service's lookup_min/lookup_harmonic are never re-queried.
        mock_frequency.lookup_all.assert_called_once_with(word.lemma, "たべる")
        mock_frequency.lookup_min.assert_not_called()
        mock_frequency.lookup_harmonic.assert_not_called()
        # Derived from the fetched breakdown: min = 200 (drives filtering),
        # harmonic = floor(2 / (1/400 + 1/200)) = 266 (drives the sort field).
        assert word.frequency_rank == 200
        assert word.frequency_harmonic_rank == 266
        assert word.frequency_sources == [("BCCWJ", 400, None), ("JPDB", 200, None)]

    def test_categorical_only_word_gets_no_numeric_rank_but_keeps_label(self, test_config, mock_services, tmp_path):
        """A word ranked ONLY by a word-based (categorical) source has no numeric
        rank/sort, yet its level label still reaches the card breakdown."""
        from anki_miner.services.frequency.render import render_frequency_html
        from anki_miner.services.frequency.storage import CATEGORICAL_RANK

        word = _make_word("食べる")
        mock_frequency = MagicMock()
        mock_frequency.is_available.return_value = True
        mock_frequency.lookup_all.return_value = [("JLPT", CATEGORICAL_RANK, "N5")]

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, _make_media())]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            frequency_service=mock_frequency,
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # Sentinel excluded from both scalar aggregates -> no numeric rank/sort.
        assert word.frequency_rank is None
        assert word.frequency_harmonic_rank is None
        # But the breakdown keeps the source, and it renders the label (not the sentinel).
        assert word.frequency_sources == [("JLPT", CATEGORICAL_RANK, "N5")]
        html = render_frequency_html(word.frequency_sources)
        assert "N5" in html
        assert str(CATEGORICAL_RANK) not in html

    def test_mixed_numeric_and_categorical_uses_numeric_only(self, test_config, mock_services, tmp_path):
        """A word ranked by a real numeric source AND tagged by a categorical one
        keeps the numeric rank/sort — the categorical sentinel must not collapse
        the rank nor inflate the harmonic count n (45000, not 90000)."""
        from anki_miner.services.frequency.storage import CATEGORICAL_RANK

        word = _make_word("食べる")
        mock_frequency = MagicMock()
        mock_frequency.is_available.return_value = True
        mock_frequency.lookup_all.return_value = [("Freq", 45000, None), ("JLPT", CATEGORICAL_RANK, "N5")]

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, _make_media())]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            frequency_service=mock_frequency,
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert word.frequency_rank == 45000
        assert word.frequency_harmonic_rank == 45000  # NOT 90000 (no phantom n)
        assert word.frequency_sources == [("Freq", 45000, None), ("JLPT", CATEGORICAL_RANK, "N5")]

    def test_frequency_filter_removes_words(self, test_config, mock_services, tmp_path):
        """Frequency filter should remove words outside the threshold."""
        config = replace(test_config, max_frequency_rank=1000)

        word1 = _make_word("食べる")
        word1.frequency_rank = 500
        word2 = _make_word("走る", 5.0)
        word2.frequency_rank = 5000

        mock_frequency = MagicMock()
        mock_frequency.is_available.return_value = True
        mock_frequency.lookup_all.side_effect = [[("BCCWJ", 500, None)], [("BCCWJ", 5000, None)]]

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1, word2]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word1, word2]
        # word_filter.filter_by_frequency should be called; make it filter to just word1
        mock_services["word_filter"].filter_by_frequency.return_value = [word1]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word1, _make_media())]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            frequency_service=mock_frequency,
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # Verify filter_by_frequency was called with the max_rank
        mock_services["word_filter"].filter_by_frequency.assert_called_once_with([word1, word2], 1000)

    def test_bypass_optional_filters_skips_frequency(self, test_config, mock_services, tmp_path):
        """Deck Builder: bypass_optional_filters=True skips the frequency cutoff."""
        config = replace(test_config, max_frequency_rank=1000, bypass_optional_filters=True)

        word1 = _make_word("食べる")
        mock_frequency = MagicMock()
        mock_frequency.is_available.return_value = True
        mock_frequency.lookup_all.return_value = [("BCCWJ", 500, None)]

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word1]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word1, _make_media())]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            frequency_service=mock_frequency,
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_services["word_filter"].filter_by_frequency.assert_not_called()

    def test_pitch_accent_populates_extra_fields(self, test_config, mock_services, tmp_path):
        """Pitch accent service should populate extra_fields in card data."""
        word = _make_word("食べる")
        media = _make_media()

        mock_pitch = MagicMock()
        mock_pitch.is_available.return_value = True
        mock_pitch.lookup_batch_detailed.return_value = [("0", "平板")]

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            pitch_accent_service=mock_pitch,
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # Verify card data includes pitch fields in extra_fields
        card_data = mock_services["anki_service"].create_cards_batch.call_args[0][0]
        assert len(card_data) == 1
        extra_fields = card_data[0].extra_fields
        assert extra_fields is not None
        assert extra_fields["pitch_position"] == "0"
        assert extra_fields["pitch_category"] == "平板"

    def test_pitch_graph_text_unmapped_by_default(self, test_config, mock_services, tmp_path):
        """Default config: pitch_graph/pitch_text unmapped → not populated, no lookup_entry."""
        word = _make_word("食べる")
        media = _make_media()

        mock_pitch = MagicMock()
        mock_pitch.is_available.return_value = True
        mock_pitch.lookup_batch_detailed.return_value = [("0", "平板")]

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            pitch_accent_service=mock_pitch,
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        extra_fields = mock_services["anki_service"].create_cards_batch.call_args[0][0][0].extra_fields
        assert "pitch_graph" not in extra_fields
        assert "pitch_text" not in extra_fields
        # The extra per-word entry lookup is skipped entirely when both are off.
        mock_pitch.lookup_entry.assert_not_called()

    def test_pitch_graph_text_mapped_renders_inline_markup(self, test_config, mock_services, tmp_path):
        """Mapped pitch_graph/pitch_text → extra_fields carries rendered SVG/overline."""
        word = _make_word("食べる")  # reading タベル → 3 morae
        media = _make_media()

        mock_pitch = MagicMock()
        mock_pitch.is_available.return_value = True
        mock_pitch.lookup_batch_detailed.return_value = [("0", "平板")]
        mock_pitch.lookup_entry.return_value = PitchEntry("0", nasal=(), devoice=())

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        config = replace(
            test_config,
            anki_fields={**test_config.anki_fields, "pitch_graph": "PitchGraph", "pitch_text": "PitchText"},
        )
        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            pitch_accent_service=mock_pitch,
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        extra_fields = mock_services["anki_service"].create_cards_batch.call_args[0][0][0].extra_fields
        assert 'class="pronunciation-graph"' in extra_fields["pitch_graph"]
        assert 'viewBox="0 0 200 100"' in extra_fields["pitch_graph"]  # 50 * (3 + 1)
        assert 'class="pronunciation-text"' in extra_fields["pitch_text"]
        # The entry lookup uses the SAME reading the pitch batch lookup used.
        mock_pitch.lookup_entry.assert_called_once_with(word.lemma, "タベル")

    def test_both_services_full_pipeline(self, test_config, mock_services, tmp_path):
        """Both services active should produce card data with both extra fields."""
        word = _make_word("食べる")
        media = _make_media()

        mock_pitch = MagicMock()
        mock_pitch.is_available.return_value = True
        mock_pitch.lookup_batch_detailed.return_value = [("0", "平板")]

        mock_frequency = MagicMock()
        mock_frequency.is_available.return_value = True
        mock_frequency.lookup_all.return_value = [("BCCWJ", 500, None), ("JPDB", 612, "612/9M")]
        # min = 500, harmonic = floor(2 / (1/500 + 1/612)) = 550 — both derived
        # locally from the single lookup_all fetch, not re-queried on the service.

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        # Map the optional frequency_sort field so the sort column is emitted.
        config = replace(test_config, anki_fields={**test_config.anki_fields, "frequency_sort": "FrequencySort"})
        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            pitch_accent_service=mock_pitch,
            frequency_service=mock_frequency,
            **mock_services,
        )

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.cards_created == 1
        card_data = mock_services["anki_service"].create_cards_batch.call_args[0][0]
        extra_fields = card_data[0].extra_fields
        assert extra_fields is not None
        assert extra_fields["pitch_position"] == "0"
        assert extra_fields["pitch_category"] == "平板"
        # frequency is the rendered per-source bullet list — JPDB carries a
        # display_value ("612/9M") that wins over its bare rank on the card, while
        # frequency_sort keeps the numeric harmonic-mean rank (not the display,
        # not the bare MIN) for Anki's numeric sort column.
        assert extra_fields["frequency"] == "<ul><li>BCCWJ: 500</li><li>JPDB: 612/9M</li></ul>"
        assert extra_fields["frequency_sort"] == "550"

    def test_word_absent_from_all_sources_gets_no_frequency_fields(self, test_config, mock_services, tmp_path):
        """Unranked word + unmapped frequency_sort field: neither field is written."""
        word = _make_word("食べる")
        media = _make_media()

        mock_frequency = MagicMock()
        mock_frequency.is_available.return_value = True
        # No source ranks this word — min + harmonic derive to None.
        mock_frequency.lookup_all.return_value = []

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        # test_config leaves frequency_sort unmapped, so the sentinel is suppressed
        # and the default-config wire stays byte-identical to pre-harmonic behavior.
        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            frequency_service=mock_frequency,
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # frequency_rank stays None so frequency filtering still works.
        assert word.frequency_rank is None
        assert word.frequency_sources == []
        card_data = mock_services["anki_service"].create_cards_batch.call_args[0][0]
        extra_fields = card_data[0].extra_fields or {}
        assert "frequency" not in extra_fields
        assert "frequency_sort" not in extra_fields

    def test_unranked_word_writes_missing_sentinel_when_field_mapped(self, test_config, mock_services, tmp_path):
        """Unranked word + mapped frequency_sort field: writes the 9999999 sentinel.

        The sentinel sorts no-data cards *last* in Anki's browser instead of
        before rank 1 (an omitted field reads as the empty string).
        """
        word = _make_word("食べる")
        media = _make_media()

        mock_frequency = MagicMock()
        mock_frequency.is_available.return_value = True
        # No source ranks this word — min + harmonic derive to None.
        mock_frequency.lookup_all.return_value = []

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        config = replace(test_config, anki_fields={**test_config.anki_fields, "frequency_sort": "FrequencySort"})
        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            frequency_service=mock_frequency,
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        card_data = mock_services["anki_service"].create_cards_batch.call_args[0][0]
        extra_fields = card_data[0].extra_fields or {}
        # No per-source breakdown, but the sort field carries the missing sentinel.
        assert "frequency" not in extra_fields
        assert extra_fields["frequency_sort"] == "9999999"


class TestPitchLemmaReading:
    """Regression tests for OVH-025: pitch lookup should use lemma_reading, not surface reading.

    When a word's surface form has a different reading from its lemma (e.g. a
    conjugated verb), the pitch mora-count / category must be derived from the
    lemma reading, not the surface reading.
    """

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda w: w
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    def test_lemma_reading_passed_to_pitch_service(self, test_config, mock_services, tmp_path):
        """When lemma_reading is set, pitch lookup receives lemma_reading, not surface reading."""
        # Construct a word where surface reading differs from lemma reading.
        # e.g. surface "食べた" (surface reading "タベタ") but lemma "食べる"
        # with lemma_reading "タベル".
        word = TokenizedWord(
            surface="食べた",
            lemma="食べる",
            reading="タベタ",  # surface kana — must NOT reach pitch service
            sentence="食べたのテスト",
            start_time=1.0,
            end_time=3.0,
            duration=2.0,
            pos="動詞",
            lemma_reading="タベル",  # lemma kana — must be passed to pitch service
        )

        mock_pitch = MagicMock()
        mock_pitch.is_available.return_value = True
        mock_pitch.lookup_batch_detailed.return_value = [("0", "平板")]

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, _make_media())]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            pitch_accent_service=mock_pitch,
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # Verify the pitch service received the lemma reading, not the surface reading.
        call_args = mock_pitch.lookup_batch_detailed.call_args
        words_arg = call_args[0][0]  # First positional arg is the list of tuples.
        assert len(words_arg) == 1
        lemma, reading, pos = words_arg[0]
        assert lemma == "食べる"
        assert reading == "タベル", (
            f"Expected lemma_reading 'タベル', got '{reading}' — " "surface reading must not reach pitch lookup"
        )
        assert pos == "動詞"

    def test_falls_back_to_surface_reading_when_lemma_reading_empty(self, test_config, mock_services, tmp_path):
        """When lemma_reading is empty, surface reading is used as fallback (common case)."""
        word = TokenizedWord(
            surface="食べる",
            lemma="食べる",
            reading="タベル",  # surface reading
            sentence="食べるのテスト",
            start_time=1.0,
            end_time=3.0,
            duration=2.0,
            pos="動詞",
            lemma_reading="",  # empty — common case, should fall back to reading
        )

        mock_pitch = MagicMock()
        mock_pitch.is_available.return_value = True
        mock_pitch.lookup_batch_detailed.return_value = [("0", "平板")]

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, _make_media())]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            pitch_accent_service=mock_pitch,
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        call_args = mock_pitch.lookup_batch_detailed.call_args
        words_arg = call_args[0][0]
        lemma, reading, pos = words_arg[0]
        assert lemma == "食べる"
        assert reading == "タベル", f"Expected surface reading 'タベル' as fallback, got '{reading}'"


class TestKnownWordDBIntegration:
    """Tests for EpisodeProcessor with known_word_db."""

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda w: w
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    def test_known_word_db_syncs_and_refilters(self, test_config, mock_services, tmp_path):
        """Known word DB should sync with Anki and filter against the merged set in one pass.

        Performance contract: ``get_known_words`` is invoked exactly once per
        episode; the post-sync state is reconstructed in-memory by unioning
        ``anki_vocab`` with the pre-fetched set rather than re-reading SQLite.
        ``filter_unknown`` therefore runs exactly once with the merged set.
        """
        word1 = _make_word("食べる")
        word2 = _make_word("走る", start_time=5.0)
        media1 = _make_media("taberu")

        mock_known_db = MagicMock()
        mock_known_db.is_available.return_value = True
        mock_known_db.get_known_words.return_value = {"走る"}
        mock_known_db.get_words_by_source.return_value = set()
        mock_known_db.sync_with_anki.return_value = (1, 10)  # 1 added, 10 total

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1, word2]
        mock_services["anki_service"].get_existing_vocabulary.return_value = {"走る", "泳ぐ"}
        mock_services["word_filter"].filter_unknown.return_value = [word1]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word1, media1)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=replace(test_config, use_known_words_db=True),
            presenter=NullPresenter(),
            known_word_db=mock_known_db,
            **mock_services,
        )

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_known_db.sync_with_anki.assert_called_once()
        # One scan, not three.
        assert mock_known_db.get_known_words.call_count == 1
        # sync_with_anki must be called with the pre-fetched set as ``existing=``.
        sync_kwargs = mock_known_db.sync_with_anki.call_args.kwargs
        assert sync_kwargs.get("existing") == {"走る"}
        # Filter runs once against the merged set.
        assert mock_services["word_filter"].filter_unknown.call_count == 1
        merged_known = mock_services["word_filter"].filter_unknown.call_args[0][1]
        assert merged_known == {"走る", "泳ぐ"}
        assert result.cards_created == 1

    def test_known_word_db_records_mined_words(self, test_config, mock_services, tmp_path):
        """After creating cards, mined words should be added to the known word DB."""
        word = _make_word("食べる")
        media = _make_media()

        mock_known_db = MagicMock()
        mock_known_db.is_available.return_value = True
        mock_known_db.get_known_words.return_value = set()
        mock_known_db.get_words_by_source.return_value = set()
        mock_known_db.sync_with_anki.return_value = (0, 0)

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=replace(test_config, use_known_words_db=True),
            presenter=NullPresenter(),
            known_word_db=mock_known_db,
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_known_db.add_words.assert_called_once_with({"食べる"}, source="mined")

    def test_locked_db_on_post_create_add_words_keeps_successful_result(self, test_config, mock_services, tmp_path):
        """A locked known_words.db during the post-create add_words must NOT
        discard a successful run's result (T-19).

        Anki (or a parallel run) can hold the SQLite file, raising
        ``OperationalError('database is locked')``. The cards were already
        created in Anki; swallowing that into the generic except path reports
        ``cards_created=0`` with no note IDs — a successful run as a failure.
        """
        import sqlite3

        word = _make_word("食べる")
        media = _make_media()

        mock_known_db = MagicMock()
        mock_known_db.is_available.return_value = True
        mock_known_db.get_known_words.return_value = set()
        mock_known_db.get_words_by_source.return_value = set()
        mock_known_db.sync_with_anki.return_value = (0, 0)
        mock_known_db.add_words.side_effect = sqlite3.OperationalError("database is locked")

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]

        # Simulate what the real create_cards_batch does: set last_created_note_ids
        # as a side effect (the process_episode early reset clears any pre-call value).
        def _create_batch(card_data, progress_callback=None):
            mock_services["anki_service"].last_created_note_ids = [12345]
            return 1

        mock_services["anki_service"].create_cards_batch.side_effect = _create_batch

        processor = EpisodeProcessor(
            config=replace(test_config, use_known_words_db=True),
            presenter=NullPresenter(),
            known_word_db=mock_known_db,
            **mock_services,
        )

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # The lock was hit, but the successful run is preserved.
        mock_known_db.add_words.assert_called_once()
        assert result.cards_created == 1
        assert result.card_ids == [12345]
        assert not result.errors

    def test_user_ignore_list_applied_when_cache_disabled(self, test_config, mock_services, tmp_path):
        """source='user' words filter the candidate set even when use_known_words_db is off (Issue #42).

        The sync path must NOT run (cache disabled), but the user ignore list is
        still unioned into the set passed to ``filter_unknown``.
        """
        word1 = _make_word("食べる")
        word2 = _make_word("ラーメン", pos="名詞", start_time=5.0)

        mock_known_db = MagicMock()
        mock_known_db.is_available.return_value = True
        mock_known_db.get_words_by_source.return_value = {"ラーメン"}

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1, word2]
        mock_services["anki_service"].get_existing_vocabulary.return_value = {"泳ぐ"}
        mock_services["word_filter"].filter_unknown.return_value = [word1]
        mock_services["media_extractor"].extract_media_batch.return_value = []
        mock_services["anki_service"].create_cards_batch.return_value = 0

        processor = EpisodeProcessor(
            config=replace(test_config, use_known_words_db=False),
            presenter=NullPresenter(),
            known_word_db=mock_known_db,
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # Cache disabled → no sync, query Anki directly.
        mock_known_db.sync_with_anki.assert_not_called()
        mock_known_db.get_known_words.assert_not_called()
        mock_known_db.get_words_by_source.assert_called_once_with("user")
        # filter_unknown receives Anki vocab UNIONED with the user ignore list.
        merged_known = mock_services["word_filter"].filter_unknown.call_args[0][1]
        assert merged_known == {"泳ぐ", "ラーメン"}


class TestIncludeKnownWordsFlag:
    """Tests for the include_known_words config flag (Deck Builder bypass)."""

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda w: w
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    def test_include_known_words_true_bypasses_subtraction(self, test_config, mock_services, tmp_path):
        """With include_known_words=True, filter_unknown is not called and all words pass through Phase 2."""
        config = replace(test_config, include_known_words=True)

        # Both words would normally be "known" — filter_unknown would drop them.
        word1 = _make_word("食べる")
        word2 = _make_word("走る", start_time=5.0)
        media1, media2 = _make_media("taberu"), _make_media("hashiru")

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1, word2]
        # Anki reports both words as already known.
        mock_services["anki_service"].get_existing_vocabulary.return_value = {"食べる", "走る"}
        mock_services["media_extractor"].extract_media_batch.return_value = [(word1, media1), (word2, media2)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat", "1. to run"]
        mock_services["anki_service"].create_cards_batch.return_value = 2

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            **mock_services,
        )

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # filter_unknown must NOT have been called — known-words subtraction is bypassed.
        mock_services["word_filter"].filter_unknown.assert_not_called()
        # Both words reached Phase 3 (both got media extracted).
        extract_call_args = mock_services["media_extractor"].extract_media_batch.call_args
        words_sent_to_extract = extract_call_args[0][1]
        assert len(words_sent_to_extract) == 2
        assert result.new_words_found == 2
        assert result.cards_created == 2

    def test_include_known_words_true_with_known_db_bypasses_subtraction(self, test_config, mock_services, tmp_path):
        """include_known_words=True also bypasses the known_word_db path (not just the bare Anki path)."""
        config = replace(test_config, include_known_words=True)

        word1 = _make_word("食べる")
        word2 = _make_word("走る", start_time=5.0)
        media1, media2 = _make_media("taberu"), _make_media("hashiru")

        mock_known_db = MagicMock()
        mock_known_db.is_available.return_value = True
        mock_known_db.get_known_words.return_value = {"食べる", "走る"}
        mock_known_db.get_words_by_source.return_value = set()
        mock_known_db.sync_with_anki.return_value = (0, 2)

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1, word2]
        mock_services["anki_service"].get_existing_vocabulary.return_value = {"食べる", "走る"}
        mock_services["media_extractor"].extract_media_batch.return_value = [(word1, media1), (word2, media2)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat", "1. to run"]
        mock_services["anki_service"].create_cards_batch.return_value = 2

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            known_word_db=mock_known_db,
            **mock_services,
        )

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # Neither the DB read nor filter_unknown should be called.
        mock_known_db.get_known_words.assert_not_called()
        mock_known_db.sync_with_anki.assert_not_called()
        mock_services["word_filter"].filter_unknown.assert_not_called()
        assert result.new_words_found == 2
        assert result.cards_created == 2

    def test_include_known_words_false_default_subtracts_known(self, test_config, mock_services, tmp_path):
        """Default config (include_known_words=False) preserves the standard known-words filter."""
        # test_config has include_known_words=False by default.
        assert test_config.include_known_words is False

        word1 = _make_word("食べる")
        word2 = _make_word("走る", start_time=5.0)
        media1 = _make_media("taberu")

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1, word2]
        # Anki reports word2 as known; filter_unknown returns only word1.
        mock_services["anki_service"].get_existing_vocabulary.return_value = {"走る"}
        mock_services["word_filter"].filter_unknown.return_value = [word1]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word1, media1)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            **mock_services,
        )

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # filter_unknown must have been called (standard path runs).
        mock_services["word_filter"].filter_unknown.assert_called_once()
        # Only word1 (unknown) reaches Phase 3.
        words_sent_to_extract = mock_services["media_extractor"].extract_media_batch.call_args[0][1]
        assert words_sent_to_extract == [word1]
        assert result.new_words_found == 1
        assert result.cards_created == 1


class TestWordListServiceIntegration:
    """Tests for EpisodeProcessor with word_list_service."""

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda w: w
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    def test_word_list_service_filters_words(self, test_config, mock_services, tmp_path):
        """Word list service should apply blacklist/whitelist filtering."""
        word1 = _make_word("食べる")
        word2 = _make_word("走る", start_time=5.0)
        media = _make_media()

        mock_wls = MagicMock()
        mock_wls.is_available.return_value = True

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1, word2]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word1, word2]
        # filter_by_word_lists removes word2
        mock_services["word_filter"].filter_by_word_lists.return_value = [word1]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word1, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            word_list_service=mock_wls,
            **mock_services,
        )

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_services["word_filter"].filter_by_word_lists.assert_called_once_with([word1, word2], mock_wls)
        assert result.cards_created == 1


class TestWordsetServiceIntegration:
    """Tests for EpisodeProcessor with wordset_service (Issue #59)."""

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda w: w
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    def test_wordset_service_filters_words(self, test_config, mock_services, tmp_path):
        """Wordset service should drop matched proper nouns via filter_by_wordsets."""
        word1 = _make_word("食べる")
        word2 = _make_word("田中", start_time=5.0)
        media = _make_media()

        mock_ws = MagicMock()
        mock_ws.is_available.return_value = True

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1, word2]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word1, word2]
        # filter_by_wordsets removes word2 (the surname)
        mock_services["word_filter"].filter_by_wordsets.return_value = [word1]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word1, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            wordset_service=mock_ws,
            **mock_services,
        )

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # filter_by_wordsets called with both words + the wordset service + word_list_service (None)
        mock_services["word_filter"].filter_by_wordsets.assert_called_once_with([word1, word2], mock_ws, None)
        assert result.cards_created == 1

    def test_bypass_optional_filters_skips_wordset_filter(self, test_config, mock_services, tmp_path):
        """Deck Builder bypass_optional_filters=True must skip the wordset filter."""
        config = replace(test_config, bypass_optional_filters=True)

        word1 = _make_word("食べる")
        mock_ws = MagicMock()
        mock_ws.is_available.return_value = True

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word1]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word1, _make_media())]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            wordset_service=mock_ws,
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_services["word_filter"].filter_by_wordsets.assert_not_called()

    def test_wordset_filter_passes_word_list_service_for_whitelist(self, test_config, mock_services, tmp_path):
        """filter_by_wordsets receives the word_list_service so whitelist can rescue words."""
        word1 = _make_word("田中")
        media = _make_media()

        mock_ws = MagicMock()
        mock_ws.is_available.return_value = True

        mock_wls = MagicMock()
        mock_wls.is_available.return_value = True

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word1]
        mock_services["word_filter"].filter_by_word_lists.return_value = [word1]
        mock_services["word_filter"].filter_by_wordsets.return_value = [word1]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word1, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["surname def"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            wordset_service=mock_ws,
            word_list_service=mock_wls,
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # Verify filter_by_wordsets was called with the word_list_service (for whitelist rescue)
        mock_services["word_filter"].filter_by_wordsets.assert_called_once_with([word1], mock_ws, mock_wls)


class TestCrossEpisodeFiltering:
    """Tests for cross-episode frequency filtering."""

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda w: w
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    def test_cross_episode_counts_filters_words(self, test_config, mock_services, tmp_path):
        """Words below min_episode_appearances should be filtered out."""
        config = replace(test_config, min_episode_appearances=3)

        word1 = _make_word("食べる")
        word2 = _make_word("走る", start_time=5.0)
        media = _make_media()

        cross_counts = {"食べる": 5, "走る": 1}  # word2 appears in only 1 episode

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1, word2]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word1, word2]
        # filter_by_episode_count removes word2
        mock_services["word_filter"].filter_by_episode_count.return_value = [word1]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word1, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            **mock_services,
        )

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass", cross_episode_counts=cross_counts)

        mock_services["word_filter"].filter_by_episode_count.assert_called_once_with([word1, word2], cross_counts, 3)
        assert result.cards_created == 1


class TestDefinitionSkipping:
    """Tests for skipping words without definitions."""

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda w: w
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    def test_skips_words_without_definitions(self, test_config, mock_services, tmp_path):
        """Words with None definitions should be skipped when creating cards."""
        word1 = _make_word("食べる")
        word2 = _make_word("走る", start_time=5.0)
        media1, media2 = _make_media("taberu"), _make_media("hashiru")

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1, word2]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word1, word2]
        mock_services["media_extractor"].extract_media_batch.return_value = [
            (word1, media1),
            (word2, media2),
        ]
        # word1 has a definition, word2 does not
        mock_services["definition_service"].get_definitions_batch.return_value = [
            "1. to eat",
            None,
        ]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # Only 1 card should be created (word2 skipped)
        card_data = mock_services["anki_service"].create_cards_batch.call_args[0][0]
        assert len(card_data) == 1
        assert card_data[0].word == word1


class TestStatsServiceIntegration:
    """Tests for EpisodeProcessor with stats_service."""

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda w: w
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    def test_records_session_on_success(self, test_config, mock_services, tmp_path):
        """Stats service should record a session after successful processing."""
        mock_stats = MagicMock()
        mock_stats.is_available.return_value = True

        word = _make_word("食べる")
        media = _make_media()

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            stats_service=mock_stats,
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_stats.record_session.assert_called_once()
        mock_stats.record_difficulty.assert_called_once()

    def test_records_difficulty_after_phase2(self, test_config, mock_services, tmp_path):
        """Difficulty should be recorded with correct word counts."""
        mock_stats = MagicMock()
        mock_stats.is_available.return_value = True

        words = [_make_word("食べる"), _make_word("走る", 5.0)]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [words[0]]  # 1 unknown
        mock_services["media_extractor"].extract_media_batch.return_value = [(words[0], _make_media())]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            stats_service=mock_stats,
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # Verify difficulty was recorded with correct counts.
        # With no optional filters active, all_unknown_lemmas == unknown_words (1).
        call_args = mock_stats.record_difficulty.call_args
        assert call_args.kwargs["total_words"] == 2  # len(all_words)
        assert call_args.kwargs["unknown_words"] == 1  # len(all_unknown_lemmas) == 1

    def test_difficulty_uses_pre_filter_unknown_count(self, test_config, mock_services, tmp_path):
        """OVH-024: record_difficulty must use the pre-filter comprehension-unknown
        count (all_unknown_lemmas), not the post-filter mineable count.

        With i+1 or frequency filters active the mineable set can collapse to a
        handful; difficulty_score would then report near-zero for a hard episode.
        """
        # Enable frequency filter so it shrinks unknown_words from 2 → 1.
        config = replace(test_config, max_frequency_rank=100)

        common = _make_word("食べる")  # stays after frequency filter
        rare = _make_word("拝謁", start_time=5.0)  # dropped by frequency filter

        mock_stats = MagicMock()
        mock_stats.is_available.return_value = True

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [common, rare]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        # Both words are unknown (pre-filter set = 2).
        mock_services["word_filter"].filter_unknown.return_value = [common, rare]
        # Frequency filter drops the rare word → mineable set = 1.
        mock_services["word_filter"].filter_by_frequency.return_value = [common]
        mock_services["media_extractor"].extract_media_batch.return_value = [(common, _make_media())]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            stats_service=mock_stats,
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        call_kwargs = mock_stats.record_difficulty.call_args.kwargs
        # Pre-filter unknown count (2) must be used, not the post-filter count (1).
        assert (
            call_kwargs["unknown_words"] == 2
        ), "record_difficulty must use all_unknown_lemmas (pre-filter), not unknown_words (post-filter)"
        assert call_kwargs["total_words"] == 2  # len(all_words) unchanged

    def test_no_crash_without_stats_service(self, test_config, mock_services, tmp_path):
        """Processing should work fine without stats_service."""
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = []

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            **mock_services,
        )

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")
        assert result.total_words_found == 0

    def test_no_session_recorded_on_error(self, test_config, mock_services, tmp_path):
        """Stats service should NOT record a session if processing fails."""
        mock_stats = MagicMock()
        mock_stats.is_available.return_value = True

        mock_services["subtitle_parser"].parse_subtitle_file.side_effect = RuntimeError("fail")

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            stats_service=mock_stats,
            **mock_services,
        )

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")
        assert result.success is False
        mock_stats.record_session.assert_not_called()
        mock_stats.record_difficulty.assert_not_called()

    def test_locked_stats_db_on_record_session_keeps_successful_result(self, test_config, mock_services, tmp_path):
        """A locked stats.db during the post-create session record must NOT
        discard a successful run's result (T-19 follow-up).

        Anki (or a parallel run) can hold the SQLite file, raising
        ``OperationalError('database is locked')``. The cards were already
        created in Anki; letting it bubble into the generic except path
        reports ``cards_created=0`` with no note IDs — a successful run as a
        failure. Same exposure as the known_words.db write one line above.
        """
        import sqlite3

        mock_stats = MagicMock()
        mock_stats.is_available.return_value = True
        mock_stats.record_session.side_effect = sqlite3.OperationalError("database is locked")

        word = _make_word("食べる")
        media = _make_media()

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]

        # Simulate what the real create_cards_batch does: set last_created_note_ids
        # as a side effect (the process_episode early reset clears any pre-call value).
        def _create_batch(card_data, progress_callback=None):
            mock_services["anki_service"].last_created_note_ids = [12345]
            return 1

        mock_services["anki_service"].create_cards_batch.side_effect = _create_batch

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            stats_service=mock_stats,
            **mock_services,
        )

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # The lock was hit, but the successful run is preserved.
        mock_stats.record_session.assert_called_once()
        assert result.cards_created == 1
        assert result.card_ids == [12345]
        assert not result.errors

    def test_oserror_on_record_session_keeps_successful_result(self, test_config, mock_services, tmp_path):
        """A non-sqlite OSError (e.g. WAL write to a full/RO disk) during the
        post-create session record must also be swallowed, not reported as a
        failed run with cards_created=0 (F6)."""
        mock_stats = MagicMock()
        mock_stats.is_available.return_value = True
        mock_stats.record_session.side_effect = OSError("No space left on device")

        word = _make_word("食べる")
        media = _make_media()

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]

        def _create_batch(card_data, progress_callback=None):
            mock_services["anki_service"].last_created_note_ids = [12345]
            return 1

        mock_services["anki_service"].create_cards_batch.side_effect = _create_batch

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            stats_service=mock_stats,
            **mock_services,
        )

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_stats.record_session.assert_called_once()
        assert result.cards_created == 1
        assert result.card_ids == [12345]
        assert not result.errors


class TestPerRunTempFolder:
    """Isolate temp media per run instead of sharing one folder across calls."""

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda words: words
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    def test_extract_media_batch_receives_unique_temp_folder_per_run(self, test_config, mock_services, tmp_path):
        words = [_make_word("食べる")]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = [(words[0], _make_media("a"))]
        mock_services["definition_service"].get_definitions_batch.return_value = ["def"]

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        calls = mock_services["media_extractor"].extract_media_batch.call_args_list
        assert len(calls) == 2
        first_folder = calls[0].kwargs["temp_folder"]
        second_folder = calls[1].kwargs["temp_folder"]
        assert first_folder is not None
        assert second_folder is not None
        assert first_folder != second_folder
        # Both folders removed on cleanup.
        assert not first_folder.exists()
        assert not second_folder.exists()

    def test_keep_temp_env_var_preserves_folder(self, test_config, mock_services, tmp_path, monkeypatch):
        monkeypatch.setenv("ANKI_MINER_KEEP_TEMP", "1")

        config = replace(test_config, media_temp_folder=tmp_path / "persisted")
        words = [_make_word("食べる")]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = [(words[0], _make_media("a"))]
        mock_services["definition_service"].get_definitions_batch.return_value = ["def"]

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        folder = mock_services["media_extractor"].extract_media_batch.call_args.kwargs["temp_folder"]
        assert folder is not None
        assert folder.exists()
        # Lives under the configured base, not a random system temp dir.
        assert config.media_temp_folder in folder.parents


class TestProcessYoutubeUrl:
    """Tests for EpisodeProcessor.process_youtube_url."""

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda w: w
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    def _happy_pipeline(self, mock_services, word, media):
        sp = mock_services["subtitle_parser"]
        # Curation builds the line index; mirror the plain parse result through
        # the with-index path (no sentence candidates).
        sp.parse_subtitle_file_with_index.side_effect = lambda f: (sp.parse_subtitle_file.return_value, [])
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

    def test_missing_fetcher_raises_runtime_error(self, test_config, mock_services, tmp_path):
        """process_youtube_url on a processor without a fetcher raises RuntimeError."""
        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            **mock_services,
        )

        with pytest.raises(RuntimeError, match="YouTubeFetcherService not injected"):
            processor.process_youtube_url(
                url="https://youtu.be/abc",
                video_id="abc",
                workspace=tmp_path,
                sub_mode="manual_only",
                cancel_event=threading.Event(),
            )

    def test_happy_path_calls_fetch_then_process_episode(self, test_config, mock_services, tmp_path):
        """process_youtube_url should call fetch_video then run the mining pipeline."""
        video_file = tmp_path / "abc123.mp4"
        subtitle_file = tmp_path / "abc123.ja.srt"
        video_file.touch()
        subtitle_file.touch()

        word = _make_word("食べる")
        media = _make_media()
        self._happy_pipeline(mock_services, word, media)

        mock_fetcher = MagicMock()
        mock_fetcher.fetch_video.return_value = FetchedMedia(
            video_file=video_file,
            subtitle_file=subtitle_file,
            sub_source="manual",
        )

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            youtube_fetcher=mock_fetcher,
            **mock_services,
        )

        cancel_event = threading.Event()
        result = processor.process_youtube_url(
            url="https://youtu.be/abc123",
            video_id="abc123",
            workspace=tmp_path,
            sub_mode="manual_only",
            cancel_event=cancel_event,
        )

        # Fetcher was called with expected args
        mock_fetcher.fetch_video.assert_called_once()
        call = mock_fetcher.fetch_video.call_args
        assert call.args[0] == "https://youtu.be/abc123"
        assert call.args[1] == "abc123"
        assert call.args[2] == tmp_path
        assert call.args[3] == "manual_only"
        assert call.kwargs["cancel_event"] is cancel_event

        # Mining pipeline ran and produced a card
        mock_services["subtitle_parser"].parse_subtitle_file.assert_called_once_with(subtitle_file)
        assert result.cards_created == 1
        assert result.total_words_found == 1

    def test_cancel_at_entry_does_not_invoke_fetcher(self, test_config, mock_services, tmp_path):
        """Cancellation set before entry should short-circuit without calling fetch_video."""
        mock_fetcher = MagicMock()

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            youtube_fetcher=mock_fetcher,
            **mock_services,
        )

        cancel_event = threading.Event()
        cancel_event.set()

        result = processor.process_youtube_url(
            url="https://youtu.be/abc",
            video_id="abc",
            workspace=tmp_path,
            sub_mode="manual_only",
            cancel_event=cancel_event,
        )

        mock_fetcher.fetch_video.assert_not_called()
        assert result.success is False
        assert any("cancel" in e.lower() for e in result.errors)

    def test_fetcher_exception_propagates(self, test_config, mock_services, tmp_path):
        """Exceptions from the fetcher propagate; orchestrator does not swallow or cleanup."""
        mock_fetcher = MagicMock()
        mock_fetcher.fetch_video.side_effect = RuntimeError("boom")

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            youtube_fetcher=mock_fetcher,
            **mock_services,
        )

        with pytest.raises(RuntimeError, match="boom"):
            processor.process_youtube_url(
                url="https://youtu.be/abc",
                video_id="abc",
                workspace=tmp_path,
                sub_mode="manual_only",
                cancel_event=threading.Event(),
            )

        # Mining pipeline must not have run after the fetch failed.
        mock_services["subtitle_parser"].parse_subtitle_file.assert_not_called()

    def test_episode_identity_overridden_to_yt_video_id(self, test_config, mock_services, tmp_path):
        """Stats service should receive YT:<video_id> as episode name, not video_file.stem."""
        video_file = tmp_path / "abc123.mp4"
        subtitle_file = tmp_path / "abc123.ja.srt"
        video_file.touch()
        subtitle_file.touch()

        word = _make_word("食べる")
        media = _make_media()
        self._happy_pipeline(mock_services, word, media)

        mock_fetcher = MagicMock()
        mock_fetcher.fetch_video.return_value = FetchedMedia(
            video_file=video_file,
            subtitle_file=subtitle_file,
            sub_source="manual",
        )

        mock_stats = MagicMock()
        mock_stats.is_available.return_value = True

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            youtube_fetcher=mock_fetcher,
            stats_service=mock_stats,
            **mock_services,
        )

        processor.process_youtube_url(
            url="https://youtu.be/abc123",
            video_id="abc123",
            workspace=tmp_path,
            sub_mode="manual_only",
            cancel_event=threading.Event(),
        )

        # Difficulty recorded with YT identity
        diff_kwargs = mock_stats.record_difficulty.call_args.kwargs
        assert diff_kwargs["episode_name"] == "YT:abc123"
        assert diff_kwargs["series_name"] == "YouTube"

        # Session recorded with YT identity
        mock_stats.record_session.assert_called_once()
        session = mock_stats.record_session.call_args.args[0]
        assert session.episode_name == "YT:abc123"
        assert session.series_name == "YouTube"

    def test_episode_name_override_preserves_default_when_none(self, test_config, mock_services, tmp_path):
        """process_episode with no override still derives identity from video_file paths."""
        mock_stats = MagicMock()
        mock_stats.is_available.return_value = True

        word = _make_word("食べる")
        media = _make_media()
        self._happy_pipeline(mock_services, word, media)

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            stats_service=mock_stats,
            **mock_services,
        )

        series_dir = tmp_path / "MySeries"
        series_dir.mkdir()
        video_file = series_dir / "ep01.mkv"
        subtitle_file = series_dir / "ep01.ass"

        processor.process_episode(video_file, subtitle_file)

        diff_kwargs = mock_stats.record_difficulty.call_args.kwargs
        assert diff_kwargs["series_name"] == "MySeries"
        assert diff_kwargs["episode_name"] == "ep01"

    def _make_processor_with_fetcher(self, test_config, mock_services, tmp_path):
        """Build a processor wired to a fetcher that returns test media."""
        video_file = tmp_path / "abc123.mp4"
        subtitle_file = tmp_path / "abc123.ja.srt"
        video_file.touch()
        subtitle_file.touch()

        word = _make_word("食べる")
        media = _make_media()
        self._happy_pipeline(mock_services, word, media)

        mock_fetcher = MagicMock()
        mock_fetcher.fetch_video.return_value = FetchedMedia(
            video_file=video_file,
            subtitle_file=subtitle_file,
            sub_source="manual",
        )

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            youtube_fetcher=mock_fetcher,
            **mock_services,
        )
        return processor

    def test_curation_callback_forwarded_to_process_episode(self, test_config, mock_services, tmp_path):
        """Supplied curation_callback reaches process_episode and gets invoked."""
        processor = self._make_processor_with_fetcher(test_config, mock_services, tmp_path)

        seen: list = []

        def _curate(words):
            seen.append(list(words))
            # Returning the same list keeps the rest of the pipeline running.
            return words

        processor.process_youtube_url(
            url="https://youtu.be/abc123",
            video_id="abc123",
            workspace=tmp_path,
            sub_mode="manual_only",
            cancel_event=threading.Event(),
            curation_callback=_curate,
        )

        # Callback was invoked exactly once with the post-filter word list.
        assert len(seen) == 1
        assert [w.lemma for w in seen[0]] == ["食べる"]

    def test_curation_returning_none_is_cancelled(self, test_config, mock_services, tmp_path):
        """Curation callback returning None ⇒ cancelled result, no cards."""
        processor = self._make_processor_with_fetcher(test_config, mock_services, tmp_path)

        result = processor.process_youtube_url(
            url="https://youtu.be/abc123",
            video_id="abc123",
            workspace=tmp_path,
            sub_mode="manual_only",
            cancel_event=threading.Event(),
            curation_callback=lambda words: None,
        )

        mock_services["anki_service"].create_cards_batch.assert_not_called()
        assert result.cards_created == 0
        assert "Processing cancelled by user" in result.errors

    def test_curation_returning_empty_list_is_completed_zero_cards(self, test_config, mock_services, tmp_path):
        """Curation callback returning [] (confirmed, nothing selected) ⇒
        completed run with zero cards — NOT a cancellation."""
        processor = self._make_processor_with_fetcher(test_config, mock_services, tmp_path)

        result = processor.process_youtube_url(
            url="https://youtu.be/abc123",
            video_id="abc123",
            workspace=tmp_path,
            sub_mode="manual_only",
            cancel_event=threading.Event(),
            curation_callback=lambda words: [],
        )

        mock_services["anki_service"].create_cards_batch.assert_not_called()
        assert result.cards_created == 0
        assert result.new_words_found == 0
        assert "Processing cancelled by user" not in result.errors

    def test_preview_mode_true_forwarded_to_process_episode(self, test_config, mock_services, tmp_path):
        """preview_mode=True short-circuits before card creation."""
        processor = self._make_processor_with_fetcher(test_config, mock_services, tmp_path)

        result = processor.process_youtube_url(
            url="https://youtu.be/abc123",
            video_id="abc123",
            workspace=tmp_path,
            sub_mode="manual_only",
            cancel_event=threading.Event(),
            preview_mode=True,
        )

        # Preview mode never hits anki_service.create_cards_batch.
        mock_services["anki_service"].create_cards_batch.assert_not_called()
        # And no media extraction is done either.
        mock_services["media_extractor"].extract_media_batch.assert_not_called()
        assert result.cards_created == 0

    def test_curation_and_preview_default_to_none_and_false(self, test_config, mock_services, tmp_path):
        """Omitting the new kwargs preserves pre-C3 behaviour: curation off, cards created."""
        processor = self._make_processor_with_fetcher(test_config, mock_services, tmp_path)

        result = processor.process_youtube_url(
            url="https://youtu.be/abc123",
            video_id="abc123",
            workspace=tmp_path,
            sub_mode="manual_only",
            cancel_event=threading.Event(),
        )

        # Default behaviour: cards are created (no preview), and the pipeline
        # did not attempt to run a curation callback (we didn't pass one).
        mock_services["anki_service"].create_cards_batch.assert_called_once()
        assert result.cards_created == 1

    def test_on_fetched_callback_fires_with_fetched_media(self, test_config, mock_services, tmp_path):
        """on_fetched is called with the FetchedMedia returned by fetch_video."""
        processor = self._make_processor_with_fetcher(test_config, mock_services, tmp_path)

        received: list[FetchedMedia] = []

        processor.process_youtube_url(
            url="https://youtu.be/abc123",
            video_id="abc123",
            workspace=tmp_path,
            sub_mode="manual_only",
            cancel_event=threading.Event(),
            on_fetched=received.append,
        )

        assert len(received) == 1
        assert isinstance(received[0], FetchedMedia)

    def test_on_fetched_callback_fires_before_process_episode(self, test_config, mock_services, tmp_path):
        """on_fetched must be invoked before the mining pipeline starts."""
        video_file = tmp_path / "abc123.mp4"
        subtitle_file = tmp_path / "abc123.ja.srt"
        video_file.touch()
        subtitle_file.touch()

        word = _make_word("食べる")
        media = _make_media()
        self._happy_pipeline(mock_services, word, media)

        fetched_media = FetchedMedia(
            video_file=video_file,
            subtitle_file=subtitle_file,
            sub_source="manual",
        )
        mock_fetcher = MagicMock()
        mock_fetcher.fetch_video.return_value = fetched_media

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            youtube_fetcher=mock_fetcher,
            **mock_services,
        )

        call_order: list[str] = []

        def _on_fetched(fm):
            call_order.append("on_fetched")

        original_process_episode = processor.process_episode

        def _process_episode_spy(*args, **kwargs):
            call_order.append("process_episode")
            return original_process_episode(*args, **kwargs)

        processor.process_episode = _process_episode_spy  # type: ignore[method-assign]

        processor.process_youtube_url(
            url="https://youtu.be/abc123",
            video_id="abc123",
            workspace=tmp_path,
            sub_mode="manual_only",
            cancel_event=threading.Event(),
            on_fetched=_on_fetched,
        )

        assert call_order == ["on_fetched", "process_episode"]

    def test_on_fetched_none_by_default_no_error(self, test_config, mock_services, tmp_path):
        """Omitting on_fetched (default None) runs without error."""
        processor = self._make_processor_with_fetcher(test_config, mock_services, tmp_path)

        # No exception should be raised when on_fetched is not supplied.
        result = processor.process_youtube_url(
            url="https://youtu.be/abc123",
            video_id="abc123",
            workspace=tmp_path,
            sub_mode="manual_only",
            cancel_event=threading.Event(),
        )

        assert result.cards_created == 1


class TestProcessYoutubeUrlCancelPropagation:
    """The worker's cancel_event must reach process_episode's checkpoints (T-01).

    Historically process_youtube_url consulted cancel_event once pre-fetch and
    forwarded it only to fetch_video; the subsequent process_episode polled
    self._cancelled, which nothing set on the YouTube path — Stop All was
    ignored mid-mine and a curation dialog could pop after Stop.
    """

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda w: w
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    def _build(self, test_config, mock_services, tmp_path):
        """Processor wired to a happy fetcher + happy 5-phase mocks."""
        video_file = tmp_path / "abc123.mp4"
        subtitle_file = tmp_path / "abc123.ja.srt"
        video_file.touch()
        subtitle_file.touch()

        word = _make_word("食べる")
        media = _make_media()
        sp = mock_services["subtitle_parser"]
        # Curation builds the line index; mirror the plain parse result through
        # the with-index path (no sentence candidates).
        sp.parse_subtitle_file_with_index.side_effect = lambda f: (sp.parse_subtitle_file.return_value, [])
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        mock_fetcher = MagicMock()
        mock_fetcher.fetch_video.return_value = FetchedMedia(
            video_file=video_file,
            subtitle_file=subtitle_file,
            sub_source="manual",
        )

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            youtube_fetcher=mock_fetcher,
            **mock_services,
        )
        return processor, mock_fetcher

    def _run(self, processor, tmp_path, cancel_event, **kwargs):
        return processor.process_youtube_url(
            url="https://youtu.be/abc123",
            video_id="abc123",
            workspace=tmp_path,
            sub_mode="manual_only",
            cancel_event=cancel_event,
            **kwargs,
        )

    def test_cancel_event_during_parse_stops_pipeline(self, test_config, mock_services, tmp_path):
        """Stop during phase 1 must end the run before media extraction."""
        processor, _ = self._build(test_config, mock_services, tmp_path)
        cancel_event = threading.Event()

        word = _make_word("食べる")

        def _parse_then_cancel(sub_file):
            cancel_event.set()  # user pressed Stop All mid-parse
            return [word]

        mock_services["subtitle_parser"].parse_subtitle_file.side_effect = _parse_then_cancel

        result = self._run(processor, tmp_path, cancel_event)

        assert any("cancel" in e.lower() for e in result.errors)
        assert result.cards_created == 0
        mock_services["media_extractor"].extract_media_batch.assert_not_called()
        mock_services["anki_service"].create_cards_batch.assert_not_called()

    def test_cancel_event_during_filter_skips_curation_dialog(self, test_config, mock_services, tmp_path):
        """Stop during phase 2 must not invoke the curation callback afterwards."""
        processor, _ = self._build(test_config, mock_services, tmp_path)
        cancel_event = threading.Event()

        word = _make_word("食べる")

        def _filter_then_cancel(all_words, existing):
            cancel_event.set()  # Stop lands while filtering, before curation
            return [word]

        mock_services["word_filter"].filter_unknown.side_effect = _filter_then_cancel
        curation = MagicMock(name="curation_callback")

        result = self._run(processor, tmp_path, cancel_event, curation_callback=curation)

        curation.assert_not_called()
        assert any("cancel" in e.lower() for e in result.errors)
        mock_services["media_extractor"].extract_media_batch.assert_not_called()

    def test_cancel_event_during_definitions_stops_before_card_creation(self, test_config, mock_services, tmp_path):
        """Stop during phase 4 must not create cards."""
        processor, _ = self._build(test_config, mock_services, tmp_path)
        cancel_event = threading.Event()

        def _define_then_cancel(lemmas, cb, fallback_context=None):
            cancel_event.set()
            return ["1. to eat"]

        mock_services["definition_service"].get_definitions_batch.side_effect = _define_then_cancel

        result = self._run(processor, tmp_path, cancel_event)

        assert any("cancel" in e.lower() for e in result.errors)
        assert result.cards_created == 0
        mock_services["anki_service"].create_cards_batch.assert_not_called()

    def test_cancel_event_drives_media_extractor_cancelled_check(self, test_config, mock_services, tmp_path):
        """The cancelled_check handed to extract_media_batch must reflect cancel_event live."""
        processor, _ = self._build(test_config, mock_services, tmp_path)
        cancel_event = threading.Event()
        observed: dict[str, bool] = {}

        def _extract(video, words, cb, cancelled_check=None, temp_folder=None, **kwargs):
            observed["before"] = cancelled_check()
            cancel_event.set()  # Stop lands mid-extraction (the long ffmpeg loop)
            observed["after"] = cancelled_check()
            return []

        mock_services["media_extractor"].extract_media_batch.side_effect = _extract

        self._run(processor, tmp_path, cancel_event)

        assert observed == {"before": False, "after": True}

    def test_cancel_event_set_during_fetch_skips_mining(self, test_config, mock_services, tmp_path):
        """A cancel that lands as the fetch completes must not start the pipeline."""
        processor, mock_fetcher = self._build(test_config, mock_services, tmp_path)
        cancel_event = threading.Event()

        fetched = mock_fetcher.fetch_video.return_value

        def _fetch_then_cancel(*args, **kwargs):
            cancel_event.set()  # cancel arrives right as yt-dlp finishes
            return fetched

        mock_fetcher.fetch_video.side_effect = _fetch_then_cancel

        result = self._run(processor, tmp_path, cancel_event)

        assert any("cancel" in e.lower() for e in result.errors)
        mock_services["subtitle_parser"].parse_subtitle_file.assert_not_called()

    def test_cancelled_run_does_not_poison_next_run(self, test_config, mock_services, tmp_path):
        """Per-run reset: the bridge from run 1's cancel_event must not leak into run 2.

        YouTubeTab reuses ONE EpisodeProcessor across runs and _cancelled is only
        reset in __init__ — a sticky flag (or a leaked event reference) set on
        run 1 would cancel every later run.
        """
        processor, _ = self._build(test_config, mock_services, tmp_path)

        run1_event = threading.Event()

        word = _make_word("食べる")

        def _parse_then_cancel(sub_file):
            run1_event.set()
            return [word]

        mock_services["subtitle_parser"].parse_subtitle_file.side_effect = _parse_then_cancel
        result1 = self._run(processor, tmp_path, run1_event)
        assert any("cancel" in e.lower() for e in result1.errors)

        # Run 2: same processor, fresh event; run 1's event stays set.
        mock_services["subtitle_parser"].parse_subtitle_file.side_effect = None
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        result2 = self._run(processor, tmp_path, threading.Event())

        assert processor.cancelled is False
        assert result2.cards_created == 1
        assert not result2.errors


def _make_line_lemmas(text="新しい単語", lemmas=("新しい",), start=1.0, end=3.0):
    return LineLemmas(
        line_text=text,
        lemmas=frozenset(lemmas),
        start_time=start,
        end_time=end,
        duration=end - start,
    )


class TestIPlusOneFilter:
    """Tests for the use_i_plus_one_filter wiring in EpisodeProcessor."""

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda w: w
        word_filter.filter_i_plus_one.side_effect = lambda words, idx, all_unknown_lemmas=None: words
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    def _config_with_flag(self, test_config, *, flag: bool, dedup: bool = True):
        return replace(
            test_config,
            use_i_plus_one_filter=flag,
            deduplicate_sentences=dedup,
        )

    def _wire_happy_pipeline(self, mock_services, word, media):
        """Set up media/definitions/cards so the pipeline reaches the end."""
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. def"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

    def test_calls_parse_with_index_when_flag_on(self, test_config, mock_services, tmp_path):
        """Flag on routes Phase 1 through parse_subtitle_file_with_index."""
        config = self._config_with_flag(test_config, flag=True)
        word = _make_word("食べる")
        line = _make_line_lemmas(lemmas=("食べる",))

        mock_services["subtitle_parser"].parse_subtitle_file_with_index.return_value = (
            [word],
            [line],
        )
        self._wire_happy_pipeline(mock_services, word, _make_media())

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_services["subtitle_parser"].parse_subtitle_file_with_index.assert_called_once_with(tmp_path / "s.ass")
        mock_services["subtitle_parser"].parse_subtitle_file.assert_not_called()

    def test_calls_legacy_parse_when_flag_off(self, test_config, mock_services, tmp_path):
        """Flag off preserves the legacy parse_subtitle_file call."""
        config = self._config_with_flag(test_config, flag=False)
        word = _make_word("食べる")

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        self._wire_happy_pipeline(mock_services, word, _make_media())

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_services["subtitle_parser"].parse_subtitle_file.assert_called_once_with(tmp_path / "s.ass")
        mock_services["subtitle_parser"].parse_subtitle_file_with_index.assert_not_called()

    def test_skips_dedup_when_flag_on(self, test_config, mock_services, tmp_path):
        """With flag on, dedup is bypassed even if deduplicate_sentences=True."""
        config = self._config_with_flag(test_config, flag=True, dedup=True)
        word = _make_word("食べる")
        line = _make_line_lemmas(lemmas=("食べる",))

        mock_services["subtitle_parser"].parse_subtitle_file_with_index.return_value = (
            [word],
            [line],
        )
        self._wire_happy_pipeline(mock_services, word, _make_media())

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_services["word_filter"].deduplicate_by_sentence.assert_not_called()
        mock_services["word_filter"].filter_i_plus_one.assert_called_once()
        # Filter receives the unknown words, the line_index from the parser,
        # and the full unknown-lemma snapshot (Issue #74).
        call_args = mock_services["word_filter"].filter_i_plus_one.call_args
        assert call_args[0][0] == [word]
        assert call_args[0][1] == [line]
        assert call_args.kwargs["all_unknown_lemmas"] == {"食べる"}

    def test_i_plus_one_sees_lemmas_dropped_by_frequency_filter(self, test_config, mock_services, tmp_path):
        """Issue #74: the all_unknown_lemmas snapshot is taken BEFORE the
        frequency filter, so an unknown word outside max_frequency_rank stays
        visible to the i+1 check even though it is no longer mineable."""
        config = self._config_with_flag(test_config, flag=True)
        config = replace(config, max_frequency_rank=100)
        common = _make_word("食べる")
        rare = _make_word("拝謁", start_time=5.0)
        line = _make_line_lemmas(lemmas=("食べる",))

        mock_services["subtitle_parser"].parse_subtitle_file_with_index.return_value = (
            [common, rare],
            [line],
        )
        self._wire_happy_pipeline(mock_services, common, _make_media())
        mock_services["word_filter"].filter_unknown.return_value = [common, rare]
        # Frequency filter drops the rare word before i+1 runs.
        mock_services["word_filter"].filter_by_frequency.return_value = [common]

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        call_args = mock_services["word_filter"].filter_i_plus_one.call_args
        assert call_args[0][0] == [common]
        assert call_args.kwargs["all_unknown_lemmas"] == {"食べる", "拝謁"}

    def test_runs_dedup_when_flag_off(self, test_config, mock_services, tmp_path):
        """With flag off, dedup runs and filter_i_plus_one does not."""
        config = self._config_with_flag(test_config, flag=False, dedup=True)
        word = _make_word("食べる")

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        self._wire_happy_pipeline(mock_services, word, _make_media())

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_services["word_filter"].deduplicate_by_sentence.assert_called_once()
        mock_services["word_filter"].filter_i_plus_one.assert_not_called()

    def test_presenter_message_when_filter_runs(self, test_config, mock_services, tmp_path):
        """Filter run emits 'i+1 filter: kept N/M words (P%)' via show_info."""
        config = self._config_with_flag(test_config, flag=True)
        word1 = _make_word("食べる")
        word2 = _make_word("走る", start_time=5.0)
        line1 = _make_line_lemmas(text="食べる", lemmas=("食べる",))
        line2 = _make_line_lemmas(text="走る", lemmas=("走る", "速い"), start=5.0, end=7.0)

        mock_services["subtitle_parser"].parse_subtitle_file_with_index.return_value = (
            [word1, word2],
            [line1, line2],
        )
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word1, word2]
        # Pretend i+1 keeps only word1.
        mock_services["word_filter"].filter_i_plus_one.side_effect = lambda words, idx, all_unknown_lemmas=None: [word1]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word1, _make_media())]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. def"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        spy_presenter = MagicMock(spec=NullPresenter())

        processor = EpisodeProcessor(
            config=config,
            presenter=spy_presenter,
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        pattern = re.compile(r"i\+1 filter: kept \d+/\d+ words \(\d+%\)")
        matched = [
            call.args[0]
            for call in spy_presenter.show_info.call_args_list
            if call.args and isinstance(call.args[0], str) and pattern.search(call.args[0])
        ]
        assert matched, (
            "Expected an i+1 filter show_info message; got: "
            f"{[c.args for c in spy_presenter.show_info.call_args_list]}"
        )
        # Specifically: kept 1/2 (50%).
        assert "kept 1/2 words (50%)" in matched[0]

    def test_bypass_optional_filters_skips_i_plus_one(self, test_config, mock_services, tmp_path):
        """Deck Builder: bypass_optional_filters=True skips i+1 even when its flag is on."""
        config = replace(test_config, use_i_plus_one_filter=True, bypass_optional_filters=True)
        word = _make_word("食べる")
        line = _make_line_lemmas(lemmas=("食べる",))

        mock_services["subtitle_parser"].parse_subtitle_file_with_index.return_value = ([word], [line])
        self._wire_happy_pipeline(mock_services, word, _make_media())

        processor = EpisodeProcessor(config=config, presenter=NullPresenter(), **mock_services)
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_services["word_filter"].filter_i_plus_one.assert_not_called()


class TestGlossaryFetch:
    """Tests for optional multi-dict glossary fetch in process_episode."""

    def _build_processor(self, cfg, mock_services):
        return EpisodeProcessor(
            config=cfg,
            subtitle_parser=mock_services["subtitle_parser"],
            word_filter=mock_services["word_filter"],
            media_extractor=mock_services["media_extractor"],
            definition_service=mock_services["definition_service"],
            anki_service=mock_services["anki_service"],
            presenter=NullPresenter(),
        )

    def _seed_happy_path(self, mock_services, tmp_path):
        words = [_make_word("食べる")]
        media = _make_media("taberu")
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = [(words[0], media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1
        return tmp_path / "v.mkv", tmp_path / "s.ass"

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda words: words
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    def test_glossary_fetched_when_field_mapped(self, test_config, mock_services, tmp_path, monkeypatch):
        cfg = replace(test_config, anki_fields={**test_config.anki_fields, "glossary": "Glossary"})
        processor = self._build_processor(cfg, mock_services)
        video, sub = self._seed_happy_path(mock_services, tmp_path)

        glossary_html = '<div class="yomitan-glossary"><ol><li data-dictionary="X">X def</li></ol></div>'
        mock_services["definition_service"].get_glossaries_batch.return_value = [glossary_html]

        # Avoid real dictionary-registry / SQLite I/O at the per-card <style> seam.
        collect = MagicMock(return_value='.yomitan-glossary [data-dictionary="X"]{color:red}')
        monkeypatch.setattr("anki_miner.orchestration.episode_processor.collect_dictionary_css", collect)

        processor.process_episode(video, sub)

        mock_services["definition_service"].get_glossaries_batch.assert_called_once()
        collect.assert_called_once()  # per-card block assembled once per episode, not per card
        call_args = mock_services["anki_service"].create_cards_batch.call_args
        card_data = call_args[0][0]
        assert len(card_data) == 1
        payload = card_data[0]
        assert payload.extra_fields is not None
        field = payload.extra_fields["glossary"]
        # Self-contained: a per-card <style> block (base glossary.css + scoped dict
        # CSS) is prepended, and the glossary HTML follows verbatim.
        assert field.startswith("<style>")
        assert field.endswith(glossary_html)
        assert '[data-dictionary="X"]{color:red}' in field  # scoped dict CSS embedded
        assert ".yomitan-glossary" in field  # base sheet embedded
        # Glossary is the styling carrier here, so the definition field stays
        # style-free (the card-wide <style> only needs to appear once).
        assert not payload.definition.startswith("<style>")

    def test_glossary_skipped_when_field_unmapped(self, test_config, mock_services, tmp_path, monkeypatch):
        # Default test_config has anki_fields["glossary"] == "" but definition
        # mapped, so the style block is still built (it rides the definition
        # field). Mock the CSS collection to avoid real registry / SQLite I/O.
        processor = self._build_processor(test_config, mock_services)
        video, sub = self._seed_happy_path(mock_services, tmp_path)

        collect = MagicMock(return_value="")
        monkeypatch.setattr("anki_miner.orchestration.episode_processor.collect_dictionary_css", collect)

        processor.process_episode(video, sub)

        mock_services["definition_service"].get_glossaries_batch.assert_not_called()
        call_args = mock_services["anki_service"].create_cards_batch.call_args
        card_data = call_args[0][0]
        payload = card_data[0]
        # extra_fields may be None or a dict — but must NOT contain glossary.
        if payload.extra_fields is not None:
            assert "glossary" not in payload.extra_fields

    def test_style_block_prepended_to_definition_when_glossary_unmapped(
        self, test_config, mock_services, tmp_path, monkeypatch
    ):
        # Default config maps definition but not glossary: the per-card <style>
        # block (base glossary.css + scoped dict CSS) must ride the DEFINITION
        # field so default-config cards still carry the base sheet — and it must
        # appear exactly once, with the original definition preserved.
        processor = self._build_processor(test_config, mock_services)
        video, sub = self._seed_happy_path(mock_services, tmp_path)

        collect = MagicMock(return_value='.yomitan-glossary [data-dictionary="X"]{color:red}')
        monkeypatch.setattr("anki_miner.orchestration.episode_processor.collect_dictionary_css", collect)

        processor.process_episode(video, sub)

        collect.assert_called_once()  # assembled once per episode, not per card
        card_data = mock_services["anki_service"].create_cards_batch.call_args[0][0]
        assert len(card_data) == 1
        definition = card_data[0].definition
        assert definition.startswith("<style>")
        assert definition.count("<style>") == 1  # exactly once
        assert definition.endswith("1. to eat")  # original definition preserved
        assert ".yomitan-glossary" in definition  # base sheet embedded
        assert '[data-dictionary="X"]{color:red}' in definition  # scoped dict CSS embedded


class TestAudioTrackOverrideForwarding:
    """Verify process_episode forwards audio_track_override to extract_media_batch."""

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda words: words
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    @pytest.fixture
    def processor(self, test_config, mock_services):
        return EpisodeProcessor(
            config=test_config,
            subtitle_parser=mock_services["subtitle_parser"],
            word_filter=mock_services["word_filter"],
            media_extractor=mock_services["media_extractor"],
            definition_service=mock_services["definition_service"],
            anki_service=mock_services["anki_service"],
            presenter=NullPresenter(),
        )

    def test_audio_track_override_forwarded_to_extract_media_batch(self, processor, mock_services, tmp_path):
        """process_episode must pass audio_track_override to extract_media_batch."""
        word = _make_word()
        media = _make_media()

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        video = tmp_path / "ep01.mkv"
        sub = tmp_path / "ep01.ass"

        processor.process_episode(video, sub, audio_track_override=3)

        call_kwargs = mock_services["media_extractor"].extract_media_batch.call_args[1]
        assert call_kwargs.get("audio_track_override") == 3

    def test_audio_track_override_none_by_default(self, processor, mock_services, tmp_path):
        """process_episode must default audio_track_override to None."""
        word = _make_word()
        media = _make_media()

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        video = tmp_path / "ep01.mkv"
        sub = tmp_path / "ep01.ass"

        processor.process_episode(video, sub)

        call_kwargs = mock_services["media_extractor"].extract_media_batch.call_args[1]
        assert call_kwargs.get("audio_track_override") is None

    def test_process_episode_invalidates_audio_stream_cache(self, processor, mock_services, tmp_path):
        """process_episode must invalidate the per-file audio stream cache at run start.

        Prevents cross-run staleness: if the user replaces a video file on
        disk between runs, the resolver must re-probe rather than match
        against stale ffprobe output cached from the previous run.
        """
        word = _make_word()
        media = _make_media()

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        video = tmp_path / "ep01.mkv"
        sub = tmp_path / "ep01.ass"

        processor.process_episode(video, sub)

        mock_services["media_extractor"].invalidate_audio_stream_cache.assert_called_once_with(video)


class TestFormatTimestamp:
    """Tests for the _format_timestamp module helper (Issue #69)."""

    @pytest.mark.parametrize(
        "seconds,expected",
        [
            (0, "00:00:00"),
            (59, "00:00:59"),
            (3661, "01:01:01"),
            (-5, "00:00:00"),
            (62.9, "00:01:02"),
        ],
    )
    def test_format(self, seconds, expected):
        from anki_miner.orchestration.episode_processor import _format_timestamp

        assert _format_timestamp(seconds) == expected


class TestSourceField:
    """Tests for the card "source" extra field (Issue #69)."""

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda words: words
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    @pytest.fixture
    def processor(self, test_config, mock_services):
        return EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            **mock_services,
        )

    def _wire_single_word(self, mock_services, word, media):
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

    def test_default_source_label_from_video_path(self, processor, mock_services, tmp_path):
        """Without an override, source_label is '<folder> — <stem>' plus timestamp."""
        word = _make_word("食べる", start_time=62.0)
        media = _make_media()
        self._wire_single_word(mock_services, word, media)

        folder = tmp_path / "My Show"
        folder.mkdir()
        video = folder / "Episode 01.mkv"
        sub = folder / "Episode 01.ass"

        processor.process_episode(video, sub)

        card_data = mock_services["anki_service"].create_cards_batch.call_args[0][0]
        assert card_data[0].extra_fields["source"] == "My Show — Episode 01 @ 00:01:02"

    def test_source_label_override_wins(self, processor, mock_services, tmp_path):
        """source_label_override replaces the derived '<folder> — <stem>' origin."""
        word = _make_word("食べる", start_time=3661.0)
        media = _make_media()
        self._wire_single_word(mock_services, word, media)

        processor.process_episode(
            tmp_path / "ep01.mkv",
            tmp_path / "ep01.ass",
            source_label_override="A Cool Video Title",
        )

        card_data = mock_services["anki_service"].create_cards_batch.call_args[0][0]
        assert card_data[0].extra_fields["source"] == "A Cool Video Title @ 01:01:01"


class TestPreflightCardTarget:
    """Tests for Issue #52: pre-flight Anki target check before the mining pipeline."""

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda w: w
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock(spec=AnkiService)
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    @pytest.fixture
    def processor(self, test_config, mock_services):
        return EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            **mock_services,
        )

    def test_setup_error_propagates_and_aborts_pipeline(self, processor, mock_services, tmp_path):
        """SetupError from verify_card_target raises out of process_episode; Phase 1 never starts."""
        mock_services["anki_service"].verify_card_target.side_effect = SetupError("bad note type")

        with patch.object(processor, "_allocate_run_temp_folder") as mock_alloc:
            with pytest.raises(SetupError, match="bad note type"):
                processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")
            mock_alloc.assert_not_called()

        mock_services["subtitle_parser"].parse_subtitle_file.assert_not_called()
        mock_services["media_extractor"].extract_media_batch.assert_not_called()
        mock_services["anki_service"].create_cards_batch.assert_not_called()

    def test_anki_connection_error_propagates(self, processor, mock_services, tmp_path):
        """AnkiConnectionError from verify_card_target raises out of process_episode."""
        mock_services["anki_service"].verify_card_target.side_effect = AnkiConnectionError("unreachable")

        with pytest.raises(AnkiConnectionError):
            processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_services["subtitle_parser"].parse_subtitle_file.assert_not_called()

    def test_preflight_called_before_subtitle_parsing(self, test_config, mock_services, tmp_path):
        """verify_card_target is called exactly once and before parse_subtitle_file."""
        word = _make_word("食べる")
        media = _make_media()

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        parent = MagicMock()
        parent.attach_mock(mock_services["anki_service"], "anki_service")
        parent.attach_mock(mock_services["subtitle_parser"], "subtitle_parser")

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        call_names = [c[0] for c in parent.mock_calls]
        assert "anki_service.verify_card_target" in call_names
        assert "subtitle_parser.parse_subtitle_file" in call_names
        preflight_idx = call_names.index("anki_service.verify_card_target")
        parse_idx = call_names.index("subtitle_parser.parse_subtitle_file")
        assert preflight_idx < parse_idx

        mock_services["anki_service"].verify_card_target.assert_called_once()

    def test_preview_mode_skips_preflight(self, processor, mock_services, tmp_path):
        """preview_mode=True must not call verify_card_target."""
        word = _make_word("食べる")
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass", preview_mode=True)

        mock_services["anki_service"].verify_card_target.assert_not_called()

    # --- process_youtube_url pre-flight tests ---

    def _make_youtube_processor(self, test_config, mock_services, mock_fetcher):
        return EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            youtube_fetcher=mock_fetcher,
            **mock_services,
        )

    def test_youtube_setup_error_aborts_before_fetch(self, test_config, mock_services, tmp_path):
        """SetupError raised before fetch_video is called for YouTube URLs."""
        mock_services["anki_service"].verify_card_target.side_effect = SetupError("bad note type")
        mock_fetcher = MagicMock()

        processor = self._make_youtube_processor(test_config, mock_services, mock_fetcher)

        with pytest.raises(SetupError, match="bad note type"):
            processor.process_youtube_url(
                url="https://youtu.be/abc",
                video_id="abc",
                workspace=tmp_path,
                sub_mode="manual_only",
                cancel_event=threading.Event(),
            )

        mock_fetcher.fetch_video.assert_not_called()

    def test_youtube_anki_connection_error_aborts_before_fetch(self, test_config, mock_services, tmp_path):
        """AnkiConnectionError from verify_card_target propagates before fetch_video is called."""
        mock_services["anki_service"].verify_card_target.side_effect = AnkiConnectionError("unreachable")
        mock_fetcher = MagicMock()

        processor = self._make_youtube_processor(test_config, mock_services, mock_fetcher)

        with pytest.raises(AnkiConnectionError):
            processor.process_youtube_url(
                url="https://youtu.be/abc",
                video_id="abc",
                workspace=tmp_path,
                sub_mode="manual_only",
                cancel_event=threading.Event(),
            )

        mock_fetcher.fetch_video.assert_not_called()

    def test_youtube_preflight_called_before_fetch(self, test_config, mock_services, tmp_path):
        """verify_card_target is called before fetch_video in process_youtube_url."""
        video_file = tmp_path / "abc.mp4"
        subtitle_file = tmp_path / "abc.ja.srt"
        video_file.touch()
        subtitle_file.touch()

        word = _make_word("食べる")
        media = _make_media()
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. def"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        mock_fetcher = MagicMock()
        mock_fetcher.fetch_video.return_value = FetchedMedia(
            video_file=video_file,
            subtitle_file=subtitle_file,
            sub_source="manual",
        )

        parent = MagicMock()
        parent.attach_mock(mock_services["anki_service"], "anki_service")
        parent.attach_mock(mock_fetcher, "fetcher")

        processor = self._make_youtube_processor(test_config, mock_services, mock_fetcher)

        processor.process_youtube_url(
            url="https://youtu.be/abc",
            video_id="abc",
            workspace=tmp_path,
            sub_mode="manual_only",
            cancel_event=threading.Event(),
        )

        call_names = [c[0] for c in parent.mock_calls]
        assert "anki_service.verify_card_target" in call_names
        assert "fetcher.fetch_video" in call_names
        preflight_idx = call_names.index("anki_service.verify_card_target")
        fetch_idx = call_names.index("fetcher.fetch_video")
        assert preflight_idx < fetch_idx

    def test_youtube_preview_mode_skips_preflight(self, test_config, mock_services, tmp_path):
        """process_youtube_url with preview_mode=True must not call verify_card_target."""
        video_file = tmp_path / "abc.mp4"
        subtitle_file = tmp_path / "abc.ja.srt"
        video_file.touch()
        subtitle_file.touch()

        word = _make_word("食べる")
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]

        mock_fetcher = MagicMock()
        mock_fetcher.fetch_video.return_value = FetchedMedia(
            video_file=video_file,
            subtitle_file=subtitle_file,
            sub_source="manual",
        )

        processor = self._make_youtube_processor(test_config, mock_services, mock_fetcher)

        processor.process_youtube_url(
            url="https://youtu.be/abc",
            video_id="abc",
            workspace=tmp_path,
            sub_mode="manual_only",
            cancel_event=threading.Event(),
            preview_mode=True,
        )

        mock_services["anki_service"].verify_card_target.assert_not_called()


class TestDictionaryResourceFacade:
    """Dictionary-resource facade (T-60): GUI callers stay out of definition_service internals."""

    @pytest.fixture
    def processor(self, test_config):
        return EpisodeProcessor(
            config=test_config,
            subtitle_parser=MagicMock(),
            word_filter=MagicMock(),
            media_extractor=MagicMock(),
            definition_service=MagicMock(),
            anki_service=MagicMock(),
            presenter=NullPresenter(),
        )

    def test_release_dictionary_resources_closes_definition_service(self, processor):
        processor.release_dictionary_resources()
        processor.definition_service.close.assert_called_once_with()

    def test_release_dictionary_resources_idempotent(self, processor):
        processor.release_dictionary_resources()
        processor.release_dictionary_resources()
        assert processor.definition_service.close.call_count == 2

    def test_release_dictionary_resources_closes_frequency_service(self, test_config):
        freq = MagicMock()
        proc = EpisodeProcessor(
            config=test_config,
            subtitle_parser=MagicMock(),
            word_filter=MagicMock(),
            media_extractor=MagicMock(),
            definition_service=MagicMock(),
            anki_service=MagicMock(),
            presenter=NullPresenter(),
            frequency_service=freq,
        )
        proc.release_dictionary_resources()
        freq.close.assert_called_once_with()

    def test_release_dictionary_resources_no_frequency_service_is_safe(self, processor):
        # frequency_service defaults to None; releasing must not raise.
        processor.release_dictionary_resources()
        processor.definition_service.close.assert_called_once_with()

    def test_offline_lookup_fn_is_definition_service_offline_lookup(self, processor):
        assert processor.offline_lookup_fn is processor.definition_service.lookup_all_offline


class TestProcessorClose:
    """close() releases all per-run resources (Windows back-to-back-mining freeze)."""

    def _make(self, test_config, audio_fetcher=None, frequency_service=None):
        return EpisodeProcessor(
            config=test_config,
            subtitle_parser=MagicMock(),
            word_filter=MagicMock(),
            media_extractor=MagicMock(),
            definition_service=MagicMock(),
            anki_service=MagicMock(),
            presenter=NullPresenter(),
            expression_audio_fetcher=audio_fetcher,
            frequency_service=frequency_service,
        )

    def test_close_closes_definition_service(self, test_config):
        proc = self._make(test_config)
        proc.close()
        proc.definition_service.close.assert_called_once_with()

    def test_close_closes_frequency_service(self, test_config):
        freq = MagicMock()
        proc = self._make(test_config, frequency_service=freq)
        proc.close()
        freq.close.assert_called_once_with()

    def test_close_with_no_frequency_service_is_safe(self, test_config):
        proc = self._make(test_config, frequency_service=None)
        proc.close()  # must not raise
        proc.definition_service.close.assert_called_once_with()

    def test_close_closes_audio_fetcher(self, test_config):
        fetcher = MagicMock()
        proc = self._make(test_config, audio_fetcher=fetcher)
        proc.close()
        proc.definition_service.close.assert_called_once_with()
        fetcher.close.assert_called_once_with()

    def test_close_with_no_audio_fetcher_is_safe(self, test_config):
        proc = self._make(test_config, audio_fetcher=None)
        proc.close()  # must not raise
        proc.definition_service.close.assert_called_once_with()

    def test_close_tolerates_audio_fetcher_without_close(self, test_config):
        class _NoClose:
            def fetch(self, *a, **k):
                return None

        proc = self._make(test_config, audio_fetcher=_NoClose())
        proc.close()  # must not raise
        proc.definition_service.close.assert_called_once_with()

    def test_close_suppresses_audio_fetcher_close_exception(self, test_config):
        fetcher = MagicMock()
        fetcher.close.side_effect = RuntimeError("boom")
        proc = self._make(test_config, audio_fetcher=fetcher)
        proc.close()  # must not raise
        proc.definition_service.close.assert_called_once_with()


class TestPhase2FilterOrdering:
    """Pin the order of the Phase-2 optional filters.

    The i+1 filter MUST run before the sentence-length filter: ``filter_i_plus_one``
    swaps each word's example sentence (and duration) to its chosen i+1 line, so a
    length cap applied before the swap would be silently bypassed by the swap
    target (documented invariant near episode_processor.py). The script-type
    filter runs before i+1. These use ``attach_mock`` + ``mock_calls`` so a future
    reorder trips the test.
    """

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        # All Phase-2 filters pass through so each one actually fires and the
        # pipeline reaches the next; signatures differ (positional vs kwargs).
        word_filter.deduplicate_by_sentence.side_effect = lambda w: w
        word_filter.filter_i_plus_one.side_effect = lambda words, idx, all_unknown_lemmas=None: words
        word_filter.filter_by_sentence_length.side_effect = lambda words, **kw: words
        word_filter.filter_by_script_type.side_effect = lambda words, **kw: words
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    def _wire_pipeline_with_index(self, mock_services, word, line, media):
        mock_services["subtitle_parser"].parse_subtitle_file_with_index.return_value = ([word], [line])
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. def"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

    def test_i_plus_one_runs_before_sentence_length(self, test_config, mock_services, tmp_path):
        config = replace(
            test_config,
            use_i_plus_one_filter=True,
            use_sentence_length_filter=True,
            max_sentence_chars=40,
        )
        word = _make_word("食べる")
        line = _make_line_lemmas(lemmas=("食べる",))
        self._wire_pipeline_with_index(mock_services, word, line, _make_media())

        parent = MagicMock()
        parent.attach_mock(mock_services["word_filter"], "word_filter")

        processor = EpisodeProcessor(config=config, presenter=NullPresenter(), **mock_services)
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        call_names = [c[0] for c in parent.mock_calls]
        assert "word_filter.filter_i_plus_one" in call_names
        assert "word_filter.filter_by_sentence_length" in call_names
        assert call_names.index("word_filter.filter_i_plus_one") < call_names.index(
            "word_filter.filter_by_sentence_length"
        )

    def test_script_type_runs_before_i_plus_one(self, test_config, mock_services, tmp_path):
        config = replace(
            test_config,
            use_i_plus_one_filter=True,
            exclude_hiragana_only_words=True,
        )
        word = _make_word("食べる")
        line = _make_line_lemmas(lemmas=("食べる",))
        self._wire_pipeline_with_index(mock_services, word, line, _make_media())

        parent = MagicMock()
        parent.attach_mock(mock_services["word_filter"], "word_filter")

        processor = EpisodeProcessor(config=config, presenter=NullPresenter(), **mock_services)
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        call_names = [c[0] for c in parent.mock_calls]
        assert "word_filter.filter_by_script_type" in call_names
        assert "word_filter.filter_i_plus_one" in call_names
        assert call_names.index("word_filter.filter_by_script_type") < call_names.index("word_filter.filter_i_plus_one")

    def test_script_type_filter_wiring(self, test_config, mock_services, tmp_path):
        """filter_by_script_type is called with the configured exclude flags and
        its output drives the rest of the pipeline."""
        config = replace(
            test_config,
            exclude_hiragana_only_words=True,
            exclude_katakana_only_words=True,
        )
        word1 = _make_word("食べる")
        word2 = _make_word("ラーメン", pos="名詞", start_time=5.0)
        media = _make_media()

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1, word2]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word1, word2]
        # script-type filter drops the katakana-only word2 (override the
        # fixture's pass-through side_effect so return_value takes effect).
        mock_services["word_filter"].filter_by_script_type.side_effect = None
        mock_services["word_filter"].filter_by_script_type.return_value = [word1]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word1, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(config=config, presenter=NullPresenter(), **mock_services)
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_services["word_filter"].filter_by_script_type.assert_called_once_with(
            [word1, word2],
            exclude_hiragana_only=True,
            exclude_katakana_only=True,
        )
        # Only word1 (survivor) reached media extraction.
        assert mock_services["media_extractor"].extract_media_batch.call_args[0][1] == [word1]

    def test_script_type_filter_bypassed_by_optional_filters_flag(self, test_config, mock_services, tmp_path):
        """Deck Builder bypass_optional_filters=True must skip the script-type filter."""
        config = replace(
            test_config,
            exclude_hiragana_only_words=True,
            bypass_optional_filters=True,
        )
        word = _make_word("食べる")
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, _make_media())]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(config=config, presenter=NullPresenter(), **mock_services)
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_services["word_filter"].filter_by_script_type.assert_not_called()

    def test_sentence_length_filter_bypassed_by_optional_filters_flag(self, test_config, mock_services, tmp_path):
        """bypass_optional_filters=True must skip the sentence-length filter too."""
        config = replace(
            test_config,
            use_sentence_length_filter=True,
            max_sentence_chars=40,
            bypass_optional_filters=True,
        )
        word = _make_word("食べる")
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, _make_media())]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(config=config, presenter=NullPresenter(), **mock_services)
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_services["word_filter"].filter_by_sentence_length.assert_not_called()


class TestExpressionAudio:
    """Phase-3 expression (pronunciation) audio fetching (Issue #73)."""

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda words: words
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    @staticmethod
    def _enabled_config(test_config):
        """test_config with the expression_audio field mapped (the on switch)."""
        return replace(
            test_config,
            anki_fields={**test_config.anki_fields, "expression_audio": "ExpressionAudio"},
        )

    @staticmethod
    def _word(lemma, reading, start_time=1.0):
        word = _make_word(lemma, start_time=start_time)
        word.expression_reading = reading
        return word

    @staticmethod
    def _wire_pipeline(mock_services, pairs):
        words = [word for word, _ in pairs]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = pairs
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. def"] * len(words)
        mock_services["anki_service"].create_cards_batch.return_value = len(words)

    def test_enabled_fetches_per_word_and_fills_media(self, test_config, mock_services, tmp_path):
        """Fetcher called with each word's candidate ladder; hits fill MediaData, misses stay None."""
        config = self._enabled_config(test_config)
        pairs = [
            (self._word("食べる", "たべる"), _make_media("taberu")),
            (self._word("走る", "はしる", 5.0), _make_media("hashiru")),
        ]
        self._wire_pipeline(mock_services, pairs)

        audio_path = tmp_path / "jpod101_食べる_たべる.mp3"
        fetcher = MagicMock()
        fetcher.fetch_candidates.side_effect = [audio_path, None]

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.cards_created == 2
        assert fetcher.fetch_candidates.call_count == 2
        # The processor hands each word's full candidate ladder to the fetcher;
        # source/candidate nesting (and first-hit selection) is the fetcher's job.
        candidate_lists = [c.args[0] for c in fetcher.fetch_candidates.call_args_list]
        assert [("食べる", "たべる")] in candidate_lists
        assert [("走る", "はしる")] in candidate_lists
        hit_media = pairs[0][1]
        assert hit_media.expression_audio_path == audio_path
        assert hit_media.expression_audio_filename == audio_path.name
        miss_media = pairs[1][1]
        assert miss_media.expression_audio_path is None
        assert miss_media.expression_audio_filename is None

    def test_miss_retries_with_lemma_for_variant_kanji_noun(self, test_config, mock_services, tmp_path):
        """Surface-form miss ⇒ retry with the unidic lemma (canonical orthography).

        Subtitle surface 噓 (variant kanji) is what JPod101 misses; the lemma
        嘘 is what it indexes. mined_form == surface for nouns, so the retry
        swaps the kanji while keeping the (unchanged) reading.
        """
        config = self._enabled_config(test_config)
        word = _make_word(lemma="嘘", surface="噓", pos="名詞")
        word.expression_reading = "うそ"
        word.lemma_reading = "うそ"
        media = _make_media("uso")
        pairs = [(word, media)]
        self._wire_pipeline(mock_services, pairs)

        # Surface 噓 misses, lemma 嘘 hits — that selection is now internal to
        # the fetcher; the processor only owns building the ladder.
        audio_path = tmp_path / "jpod101_嘘_うそ.mp3"
        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = audio_path

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.cards_created == 1
        assert fetcher.fetch_candidates.call_count == 1
        candidates = fetcher.fetch_candidates.call_args.args[0]
        assert candidates == [("噓", "うそ"), ("嘘", "うそ")]  # surface then lemma
        assert media.expression_audio_path == audio_path
        assert media.expression_audio_filename == audio_path.name

    def test_katakana_loanword_retries_with_katakana_reading(self, test_config, mock_services, tmp_path):
        """Loanword hiragana-reading miss ⇒ retry with the katakana reading.

        ``expression_reading`` is folded to hiragana for card display (ちっぷ),
        but JPod101 indexes loanword audio under the katakana reading (チップ).
        """
        config = self._enabled_config(test_config)
        word = _make_word(lemma="チップ", surface="チップ", pos="名詞")
        word.expression_reading = "ちっぷ"
        word.lemma_reading = "ちっぷ"
        media = _make_media("chip")
        pairs = [(word, media)]
        self._wire_pipeline(mock_services, pairs)

        audio_path = tmp_path / "jpod101_チップ_チップ.mp3"
        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = audio_path

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.cards_created == 1
        assert fetcher.fetch_candidates.call_count == 1
        candidates = fetcher.fetch_candidates.call_args.args[0]
        assert candidates == [("チップ", "ちっぷ"), ("チップ", "チップ")]  # hiragana then katakana
        assert media.expression_audio_path == audio_path

    def test_surface_mined_noun_retries_with_lemma_reading(self, test_config, mock_services, tmp_path):
        """Surface miss ⇒ lemma retry uses the lemma's OWN reading, not the surface reading.

        Surface 探し/さがし misses; the canonical lemma is 探す/さがす. The retry
        must swap BOTH kanji and reading — keeping さがし would still miss.
        """
        config = self._enabled_config(test_config)
        word = _make_word(lemma="探す", surface="探し", pos="名詞")
        word.expression_reading = "さがし"
        word.lemma_reading = "さがす"
        media = _make_media("sagasu")
        pairs = [(word, media)]
        self._wire_pipeline(mock_services, pairs)

        audio_path = tmp_path / "jpod101_探す_さがす.mp3"
        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = audio_path

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.cards_created == 1
        assert fetcher.fetch_candidates.call_count == 1
        candidates = fetcher.fetch_candidates.call_args.args[0]
        # Lemma retry swaps BOTH kanji and reading (探す/さがす, not 探す/さがし).
        assert candidates == [("探し", "さがし"), ("探す", "さがす")]
        assert media.expression_audio_path == audio_path

    def test_empty_reading_yields_empty_candidate_ladder(self, test_config, mock_services, tmp_path):
        """A word with no usable reading yields an empty candidate ladder.

        ``fetch_candidates([])`` is a cheap no-op that returns None without
        touching the network (the leaf's homograph guard handles the actual
        skip — see test_expression_audio_fetcher)."""
        config = self._enabled_config(test_config)
        word = _make_word(lemma="々", surface="々", pos="記号")
        word.expression_reading = ""
        word.lemma_reading = ""
        media = _make_media("sym")
        pairs = [(word, media)]
        self._wire_pipeline(mock_services, pairs)
        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = None

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        fetcher.fetch_candidates.assert_called_once()
        assert fetcher.fetch_candidates.call_args.args[0] == []
        assert media.expression_audio_path is None

    def test_miss_no_lemma_retry_when_mined_form_equals_lemma(self, test_config, mock_services, tmp_path):
        """Verbs mine as lemma (mined_form == lemma) ⇒ single-form candidate ladder."""
        config = self._enabled_config(test_config)
        # Default pos=動詞 ⇒ mined_form == lemma == 食べる.
        pairs = [(self._word("食べる", "たべる"), _make_media("taberu"))]
        self._wire_pipeline(mock_services, pairs)

        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = None

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert fetcher.fetch_candidates.call_count == 1
        # No redundant lemma duplicate when mined_form == lemma.
        assert fetcher.fetch_candidates.call_args.args[0] == [("食べる", "たべる")]
        assert pairs[0][1].expression_audio_path is None

    def test_blank_field_mapping_does_not_fetch(self, test_config, mock_services, tmp_path):
        """Blank anki_fields['expression_audio'] ⇒ fetcher never called (the
        field name is the sole on/off switch)."""
        config = replace(
            test_config,
            anki_fields={**test_config.anki_fields, "expression_audio": ""},
        )
        pairs = [(self._word("食べる", "たべる"), _make_media("taberu"))]
        self._wire_pipeline(mock_services, pairs)
        fetcher = MagicMock()

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        fetcher.fetch_candidates.assert_not_called()

    def test_no_fetcher_injected_no_crash(self, test_config, mock_services, tmp_path):
        """Enabled + field mapped but fetcher=None ⇒ pipeline completes, no fetch."""
        config = self._enabled_config(test_config)
        pairs = [(self._word("食べる", "たべる"), _make_media("taberu"))]
        self._wire_pipeline(mock_services, pairs)

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            **mock_services,
        )
        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.cards_created == 1
        assert pairs[0][1].expression_audio_path is None

    def test_cancel_mid_loop_stops_fetching(self, test_config, mock_services, tmp_path):
        """Cancellation between fetches stops the loop and yields a cancelled result."""
        config = self._enabled_config(test_config)
        pairs = [
            (self._word("食べる", "たべる"), _make_media("taberu")),
            (self._word("走る", "はしる", 5.0), _make_media("hashiru")),
        ]
        self._wire_pipeline(mock_services, pairs)

        fetcher = MagicMock()
        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )

        def _fetch_then_cancel(candidates, cancelled_check=None):
            processor.cancel()
            return tmp_path / "a.mp3"

        fetcher.fetch_candidates.side_effect = _fetch_then_cancel

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert fetcher.fetch_candidates.call_count == 1
        assert "Processing cancelled by user" in result.errors
        mock_services["anki_service"].create_cards_batch.assert_not_called()

    def test_presenter_receives_summary_line(self, test_config, mock_services, tmp_path):
        """Presenter gets the 'Expression audio: X/Y available' info line."""
        config = self._enabled_config(test_config)
        pairs = [
            (self._word("食べる", "たべる"), _make_media("taberu")),
            (self._word("走る", "はしる", 5.0), _make_media("hashiru")),
        ]
        self._wire_pipeline(mock_services, pairs)

        fetcher = MagicMock()
        fetcher.fetch_candidates.side_effect = [tmp_path / "a.mp3", None]
        presenter = MagicMock()

        processor = EpisodeProcessor(
            config=config,
            presenter=presenter,
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        presenter.show_info.assert_any_call("Expression audio: 1/2 available")

    def test_fetcher_receives_cancelled_check_kwarg(self, test_config, mock_services, tmp_path):
        """fetch() is called with cancelled_check= that reflects processor.cancelled."""
        config = self._enabled_config(test_config)
        pairs = [(self._word("食べる", "たべる"), _make_media("taberu"))]
        self._wire_pipeline(mock_services, pairs)

        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = None

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert fetcher.fetch_candidates.call_count == 1
        call_kwargs = fetcher.fetch_candidates.call_args.kwargs
        assert "cancelled_check" in call_kwargs
        # The callable should return False (processor not cancelled) and be callable.
        check_fn = call_kwargs["cancelled_check"]
        assert callable(check_fn)
        assert check_fn() is False

    def test_progress_emitted_per_word(self, test_config, mock_services, tmp_path):
        """progress_callback.on_progress is called once per word during the expression audio loop."""
        config = self._enabled_config(test_config)
        pairs = [
            (self._word("食べる", "たべる"), _make_media("taberu")),
            (self._word("走る", "はしる", 5.0), _make_media("hashiru")),
            (self._word("飲む", "のむ", 9.0), _make_media("nomu")),
        ]
        self._wire_pipeline(mock_services, pairs)

        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = None

        progress_callback = MagicMock()

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass", progress_callback=progress_callback)

        # The raw callback is wrapped by StageWeightedProgress, which forwards
        # on_progress for every word in the expression-audio loop with the
        # item_description "Expression audio: <mined_form>".  Filter to only
        # those calls and assert exactly 3 (one per word) — other on_progress
        # calls (e.g. the finish() snap to 100 with "") belong to different
        # stages.
        expr_audio_calls = [
            c for c in progress_callback.on_progress.call_args_list if c.args[1].startswith("Expression audio:")
        ]
        assert len(expr_audio_calls) == 3


class TestExpressionAudioProgressBand:
    """Progress-accounting tests for the expression-audio stage (Issue #73 fix).

    Verifies that _phase3_extract correctly consumes the dedicated progress band
    registered by process_episode — no band theft from definitions or later stages.
    """

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda words: words
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    @staticmethod
    def _enabled_config(test_config):
        return replace(
            test_config,
            anki_fields={**test_config.anki_fields, "expression_audio": "ExpressionAudio"},
        )

    @staticmethod
    def _wire_pipeline(mock_services, pairs):
        words = [word for word, _ in pairs]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = pairs
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. def"] * len(words)
        mock_services["anki_service"].create_cards_batch.return_value = len(words)

    @staticmethod
    def _word(lemma, reading="よみ", start_time=1.0):
        word = _make_word(lemma, start_time=start_time)
        word.expression_reading = reading
        return word

    def test_feature_on_stage_count_matches_bands(self, test_config, mock_services, tmp_path):
        """Feature ON: on_start call count equals number of registered bands.

        With expression_audio active the bands are: extract, expression_audio,
        definitions, cards = 4.  StageWeightedProgress forwards on_start only
        once to the inner callback (the global on_start), so we check on_start
        descriptions to count stage entries instead.
        """
        config = self._enabled_config(test_config)
        pairs = [(self._word("食べる", "たべる"), _make_media("taberu"))]
        self._wire_pipeline(mock_services, pairs)

        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = None

        # Use a recording callback that counts on_start calls by description
        class _RecordingCallback:
            def __init__(self):
                self.starts = []
                self.completes = 0

            def on_start(self, total, description):
                self.starts.append(description)

            def on_progress(self, current, item_description):
                pass

            def on_complete(self):
                self.completes += 1

            def on_error(self, item_description, error_message):
                pass

        cb = _RecordingCallback()

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass", progress_callback=cb)

        # StageWeightedProgress only forwards on_start to the inner callback
        # once — on the very first stage (extract). All subsequent on_start
        # calls from later stages (expression audio, definitions, cards) only
        # advance the internal band counter and never reach the inner callback.
        # Therefore cb.starts has exactly 1 entry regardless of band count.
        # The expression-audio band being registered is verified indirectly:
        # fetch_candidates was called (feature ran) AND finish() emitted one
        # on_complete, confirming the full 4-band sweep completed without
        # band-accounting errors.
        assert len(cb.starts) == 1
        assert cb.completes == 1  # from StageWeightedProgress.finish()

        # Cross-check: fetcher was called (expression-audio band ran)
        assert fetcher.fetch_candidates.call_count == 1

    def test_feature_on_on_start_description_includes_expression_audio(self, test_config, mock_services, tmp_path):
        """The expression-audio on_start description is passed to the inner callback.

        Because StageWeightedProgress only forwards on_start once (first stage),
        we pass the raw callback directly to _phase3_extract to inspect all
        on_start calls without the wrapper.
        """
        config = self._enabled_config(test_config)
        pairs = [(self._word("食べる", "たべる"), _make_media("taberu"))]
        self._wire_pipeline(mock_services, pairs)

        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = None

        # Pass a raw MagicMock as progress_callback so we can inspect all calls.
        raw_cb = MagicMock()

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass", progress_callback=raw_cb)

        # Check that on_start was called with "Fetching expression audio" description
        on_start_descriptions = [c.args[1] for c in raw_cb.on_start.call_args_list]
        assert any("expression audio" in d.lower() for d in on_start_descriptions)

    def test_feature_on_zero_media_results_band_still_consumed(self, test_config, mock_services, tmp_path):
        """Feature ON + empty media_results: band consumed (on_start(0) + on_complete called).

        The gate in _phase3_extract must NOT include `media_results` non-empty —
        otherwise the band is silently skipped and the next stage steals it.
        We call _phase3_extract directly with a raw callback (bypassing
        StageWeightedProgress) so every on_start/on_complete lands on our mock.
        """
        config = self._enabled_config(test_config)

        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = None

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )

        # extract_media_batch returns empty — simulates total extraction failure
        mock_services["media_extractor"].extract_media_batch.return_value = []

        raw_cb = MagicMock()

        ctx = _make_episode_context(tmp_path)
        # Call _phase3_extract directly with the raw callback (no wrapper)
        result = processor._phase3_extract(
            ctx=ctx,
            video_file=tmp_path / "v.mkv",
            unknown_words=[self._word("食べる", "たべる")],
            progress_callback=raw_cb,
            run_temp_folder=tmp_path,
        )

        # Band must be consumed: on_start(0, "Fetching expression audio") + on_complete
        assert raw_cb.on_start.call_count == 1
        on_start_args = raw_cb.on_start.call_args
        assert on_start_args.args[0] == 0  # total = 0 (empty media_results)
        assert "expression audio" in on_start_args.args[1].lower()
        assert raw_cb.on_complete.call_count == 1
        # Fetcher never called — no words to iterate
        fetcher.fetch_candidates.assert_not_called()
        # Returns empty list unchanged
        assert result == []

    def test_feature_off_no_expression_audio_on_start(self, test_config, mock_services, tmp_path):
        """Feature OFF: no expression-audio on_start; baseline stage count unchanged."""
        # Feature disabled via blank expression_audio field (the on/off switch).
        config = replace(
            test_config,
            anki_fields={**test_config.anki_fields, "expression_audio": ""},
        )
        pairs = [(self._word("食べる", "たべる"), _make_media("taberu"))]
        self._wire_pipeline(mock_services, pairs)

        fetcher = MagicMock()
        raw_cb = MagicMock()

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass", progress_callback=raw_cb)

        on_start_descriptions = [c.args[1] for c in raw_cb.on_start.call_args_list]
        assert not any("expression audio" in d.lower() for d in on_start_descriptions)
        fetcher.fetch_candidates.assert_not_called()

    def test_feature_off_no_fetcher_no_expression_audio_on_start(self, test_config, mock_services, tmp_path):
        """Feature enabled but no fetcher injected: no expression-audio band."""
        config = self._enabled_config(test_config)
        pairs = [(self._word("食べる", "たべる"), _make_media("taberu"))]
        self._wire_pipeline(mock_services, pairs)

        raw_cb = MagicMock()

        # No fetcher injected
        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass", progress_callback=raw_cb)

        on_start_descriptions = [c.args[1] for c in raw_cb.on_start.call_args_list]
        assert not any("expression audio" in d.lower() for d in on_start_descriptions)


# ---------------------------------------------------------------------------
# OVH-023 / OVH-038 — guard record_difficulty against locked stats.db
# ---------------------------------------------------------------------------


class TestRecordDifficultyGuard:
    """A locked stats.db during _phase2_filter must not abort process_episode (OVH-023/038)."""

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda words: words
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    def _make_stats_service(self, tmp_path):
        from anki_miner.services.stats_service import StatsService

        svc = StatsService(tmp_path / "stats.db")
        svc.load()
        return svc

    def test_locked_stats_db_does_not_abort_run(self, test_config, mock_services, tmp_path):
        """record_difficulty raising OperationalError must be caught in _phase2_filter;
        process_episode still returns a valid result instead of raising."""
        import sqlite3

        stats = self._make_stats_service(tmp_path)
        word = _make_word("食べる")
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, _make_media())]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            stats_service=stats,
            **mock_services,
        )

        with patch.object(
            stats,
            "record_difficulty",
            side_effect=sqlite3.OperationalError("database is locked"),
        ):
            result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # The run must succeed — cards were created despite the locked stats.db.
        assert result.cards_created == 1
        assert result.success is True

    def test_non_operational_sqlite_error_also_caught(self, test_config, mock_services, tmp_path):
        """A non-OperationalError sqlite failure (e.g. DatabaseError) must also be
        caught, not just OperationalError, so a corrupt/disk-IO stats.db can't turn
        a successful run into an apparent failure (F11)."""
        import sqlite3

        stats = self._make_stats_service(tmp_path)
        word = _make_word("食べる")
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, _make_media())]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            stats_service=stats,
            **mock_services,
        )

        with patch.object(
            stats,
            "record_difficulty",
            side_effect=sqlite3.DatabaseError("database disk image is malformed"),
        ):
            result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.cards_created == 1
        assert result.success is True

    def test_difficulty_recorded_when_db_available(self, test_config, mock_services, tmp_path):
        """Sanity: record_difficulty is called when the stats service is healthy."""
        stats = self._make_stats_service(tmp_path)
        word = _make_word("食べる")
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, _make_media())]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            stats_service=stats,
            **mock_services,
        )

        with patch.object(stats, "record_difficulty", wraps=stats.record_difficulty) as spy:
            processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        spy.assert_called_once()


# ---------------------------------------------------------------------------
# OVH-030 — mined_forms populated on ProcessingResult
# ---------------------------------------------------------------------------


class TestMinedFormsOnResult:
    """ProcessingResult.mined_forms is populated on a successful run (OVH-030)."""

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda words: words
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    def test_mined_forms_populated_on_success(self, test_config, mock_services, tmp_path):
        """mined_forms on the result must contain the mined_form of every created card."""
        # Verb: mined_form == lemma; noun: mined_form == surface.
        verb = TokenizedWord(
            surface="食べた",
            lemma="食べる",
            reading="タベル",
            sentence="食べた",
            start_time=1.0,
            end_time=3.0,
            duration=2.0,
            pos="動詞",
        )
        noun = TokenizedWord(
            surface="猫",
            lemma="猫",
            reading="ネコ",
            sentence="猫だ",
            start_time=4.0,
            end_time=6.0,
            duration=2.0,
            pos="名詞",
        )

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [verb, noun]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [verb, noun]
        mock_services["media_extractor"].extract_media_batch.return_value = [
            (verb, _make_media("taberu")),
            (noun, _make_media("neko")),
        ]
        mock_services["definition_service"].get_definitions_batch.return_value = [
            "1. to eat",
            "1. cat",
        ]
        mock_services["anki_service"].create_cards_batch.return_value = 2

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            **mock_services,
        )
        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.cards_created == 2
        # verb mined_form == lemma; noun mined_form == surface
        assert set(result.mined_forms) == {"食べる", "猫"}

    def test_mined_forms_empty_when_no_cards_created(self, test_config, mock_services, tmp_path):
        """mined_forms must be empty when no words are found."""
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = []

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            **mock_services,
        )
        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.mined_forms == []

    def test_mined_forms_excludes_prior_session_mined_rows(self, test_config, mock_services, tmp_path):
        """Undo set excludes a 'mined' row a prior session created and this run
        merely re-encountered — only THIS session's new rows are revertable."""
        prior = _make_word("食べる")  # verb → mined_form == lemma 食べる
        fresh = _make_word("猫", surface="猫", pos="名詞", start_time=4.0)  # noun → mined_form == surface 猫

        mock_known_db = MagicMock()
        mock_known_db.is_available.return_value = True
        mock_known_db.get_known_words.return_value = set()
        mock_known_db.sync_with_anki.return_value = (0, 0)
        # The earlier run already recorded 食べる as 'mined'.
        mock_known_db.get_words_by_source.return_value = {"食べる"}

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [prior, fresh]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [prior, fresh]
        mock_services["media_extractor"].extract_media_batch.return_value = [
            (prior, _make_media("taberu")),
            (fresh, _make_media("neko")),
        ]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat", "1. cat"]
        mock_services["anki_service"].create_cards_batch.return_value = 2

        processor = EpisodeProcessor(
            config=replace(test_config, use_known_words_db=True),
            presenter=NullPresenter(),
            known_word_db=mock_known_db,
            **mock_services,
        )
        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.cards_created == 2
        # Both were mined, but 食べる predates this session → only 猫 is revertable.
        assert result.mined_forms == ["猫"]
        # The full set is still recorded in the DB.
        mock_known_db.add_words.assert_called_once_with({"食べる", "猫"}, source="mined")


class TestOfflineDefinitionPreFilter:
    """Pre-curator offline definition filter — words with no offline definition
    are dropped before the curation dialog (and preview/batch) sees them."""

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda words: words
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    def _build(self, config, mock_services):
        return EpisodeProcessor(
            config=config,
            subtitle_parser=mock_services["subtitle_parser"],
            word_filter=mock_services["word_filter"],
            media_extractor=mock_services["media_extractor"],
            definition_service=mock_services["definition_service"],
            anki_service=mock_services["anki_service"],
            presenter=NullPresenter(),
        )

    def _prime(self, mock_services, words):
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["subtitle_parser"].parse_subtitle_file_with_index.side_effect = lambda f: (
            mock_services["subtitle_parser"].parse_subtitle_file.return_value,
            [],
        )
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = list(words)

    def test_no_definition_words_dropped_before_curation(self, test_config, mock_services, tmp_path):
        keep, drop = _make_word("食べる"), _make_word("走る", 5.0)
        self._prime(mock_services, [keep, drop])
        mock_services["definition_service"].has_offline_definitions.return_value = {
            "食べる": True,
            "走る": False,
        }
        mock_services["media_extractor"].extract_media_batch.return_value = [(keep, _make_media("k"))]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        captured: dict = {}

        def cb(words):
            captured["lemmas"] = [w.lemma for w in words]
            return words

        proc = self._build(test_config, mock_services)
        proc.process_episode(tmp_path / "ep.mkv", tmp_path / "ep.ass", curation_callback=cb)

        assert captured["lemmas"] == ["食べる"]

    def test_words_with_definition_retained(self, test_config, mock_services, tmp_path):
        w1, w2 = _make_word("食べる"), _make_word("走る", 5.0)
        self._prime(mock_services, [w1, w2])
        mock_services["definition_service"].has_offline_definitions.return_value = {
            "食べる": True,
            "走る": True,
        }

        captured: dict = {}

        def cb(words):
            captured["lemmas"] = [w.lemma for w in words]
            return None  # cancel after curation; we only assert the input

        proc = self._build(test_config, mock_services)
        proc.process_episode(tmp_path / "ep.mkv", tmp_path / "ep.ass", curation_callback=cb)

        assert captured["lemmas"] == ["食べる", "走る"]

    def test_filter_skipped_when_bypass_optional_filters(self, test_config, mock_services, tmp_path):
        config = replace(test_config, bypass_optional_filters=True)
        w1, w2 = _make_word("食べる"), _make_word("走る", 5.0)
        self._prime(mock_services, [w1, w2])

        captured: dict = {}

        def cb(words):
            captured["lemmas"] = [w.lemma for w in words]
            return None

        proc = self._build(config, mock_services)
        proc.process_episode(tmp_path / "ep.mkv", tmp_path / "ep.ass", curation_callback=cb)

        mock_services["definition_service"].has_offline_definitions.assert_not_called()
        assert captured["lemmas"] == ["食べる", "走る"]


class TestWithinRunDuplicateCollapse:
    """Words colliding on mined_form within one run are collapsed before the
    curator, so it never offers a word Anki will silently skip as a duplicate."""

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda words: words
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    def _build(self, config, mock_services):
        return EpisodeProcessor(
            config=config,
            subtitle_parser=mock_services["subtitle_parser"],
            word_filter=mock_services["word_filter"],
            media_extractor=mock_services["media_extractor"],
            definition_service=mock_services["definition_service"],
            anki_service=mock_services["anki_service"],
            presenter=NullPresenter(),
        )

    def _prime(self, mock_services, words):
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["subtitle_parser"].parse_subtitle_file_with_index.side_effect = lambda f: (
            mock_services["subtitle_parser"].parse_subtitle_file.return_value,
            [],
        )
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = list(words)
        # Every word survives the offline-definition pre-filter so the collapse
        # is the only thing acting on the list the curator receives.
        mock_services["definition_service"].has_offline_definitions.side_effect = lambda lemmas: dict.fromkeys(
            lemmas, True
        )

    def test_duplicate_mined_forms_collapsed_before_curation(self, test_config, mock_services, tmp_path):
        # Two verbs share lemma 食べる ⇒ identical mined_form (mined_form == lemma
        # for verbs). The curator must see exactly one.
        dup_a, dup_b = _make_word("食べる", start_time=1.0), _make_word("食べる", start_time=9.0)
        self._prime(mock_services, [dup_a, dup_b])

        captured: dict = {}

        def cb(words):
            captured["mined_forms"] = [w.mined_form for w in words]
            return None

        proc = self._build(test_config, mock_services)
        proc.process_episode(tmp_path / "ep.mkv", tmp_path / "ep.ass", curation_callback=cb)

        assert captured["mined_forms"] == ["食べる"]

    def test_distinct_mined_forms_not_collapsed(self, test_config, mock_services, tmp_path):
        w1, w2 = _make_word("食べる"), _make_word("走る", start_time=5.0)
        self._prime(mock_services, [w1, w2])

        captured: dict = {}

        def cb(words):
            captured["mined_forms"] = [w.mined_form for w in words]
            return None

        proc = self._build(test_config, mock_services)
        proc.process_episode(tmp_path / "ep.mkv", tmp_path / "ep.ass", curation_callback=cb)

        assert captured["mined_forms"] == ["食べる", "走る"]

    def test_duplicates_preserved_when_allow_duplicate_cards(self, test_config, mock_services, tmp_path):
        # Deck Builder parity: allow_duplicate_cards=True ⇒ Anki creates both, so
        # showing both is correct and the collapse must be skipped.
        config = replace(test_config, allow_duplicate_cards=True)
        dup_a, dup_b = _make_word("食べる", start_time=1.0), _make_word("食べる", start_time=9.0)
        self._prime(mock_services, [dup_a, dup_b])

        captured: dict = {}

        def cb(words):
            captured["mined_forms"] = [w.mined_form for w in words]
            return None

        proc = self._build(config, mock_services)
        proc.process_episode(tmp_path / "ep.mkv", tmp_path / "ep.ass", curation_callback=cb)

        assert captured["mined_forms"] == ["食べる", "食べる"]


def _counts(**kw):
    """Build a full failure-cause counts dict, defaulting unset buckets to 0."""
    from anki_miner.services.expression_audio_fetcher import FAILURE_KEYS

    base = dict.fromkeys(FAILURE_KEYS, 0)
    base.update(kw)
    return base


class TestAudioFailureDiagnosis:
    """_audio_failure_diagnosis: name the dominant cause only when it matters."""

    def test_no_failures_returns_none(self):
        assert _audio_failure_diagnosis(_counts(), attempts=10) is None

    def test_zero_attempts_returns_none(self):
        assert _audio_failure_diagnosis(_counts(ssl=5), attempts=0) is None

    def test_scattered_failures_below_half_stay_quiet(self):
        # 2 failures out of 10 attempts — noise beside real hits/misses.
        assert _audio_failure_diagnosis(_counts(ssl=2), attempts=10) is None

    def test_ssl_dominant_reports_certificate_connection_message(self):
        msg = _audio_failure_diagnosis(_counts(ssl=8), attempts=10)
        assert msg is not None
        assert "connection/certificate failure" in msg

    def test_connection_dominant_reports_certificate_connection_message(self):
        msg = _audio_failure_diagnosis(_counts(connection=6), attempts=10)
        assert "connection/certificate failure" in msg

    def test_timeout_dominant_reports_certificate_connection_message(self):
        msg = _audio_failure_diagnosis(_counts(timeout=6), attempts=10)
        assert "connection/certificate failure" in msg

    def test_http_status_dominant_reports_server_errors(self):
        msg = _audio_failure_diagnosis(_counts(http_status=6), attempts=10)
        assert "server errors" in msg

    def test_non_audio_dominant_reports_rate_limited(self):
        msg = _audio_failure_diagnosis(_counts(non_audio=6), attempts=10)
        assert "rate-limited" in msg

    def test_tie_resolves_to_ssl_first(self):
        # ssl and http_status tie at 3 each; ssl wins on stable key order.
        msg = _audio_failure_diagnosis(_counts(ssl=3, http_status=3), attempts=10)
        assert "connection/certificate failure" in msg

    def test_exactly_half_triggers(self):
        # total * 2 >= attempts boundary: 5 failures / 10 attempts fires.
        assert _audio_failure_diagnosis(_counts(ssl=5), attempts=10) is not None


class TestProcessorAudioFailureSummary:
    """Phase-3 summary surfaces the dominant audio-failure cause."""

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda words: words
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    @staticmethod
    def _enabled_config(test_config):
        return replace(
            test_config,
            anki_fields={**test_config.anki_fields, "expression_audio": "ExpressionAudio"},
        )

    @staticmethod
    def _wire(mock_services, pairs):
        words = [word for word, _ in pairs]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = pairs
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. def"] * len(words)
        mock_services["anki_service"].create_cards_batch.return_value = len(words)

    def test_dominant_ssl_failure_warns(self, test_config, mock_services, tmp_path):
        config = self._enabled_config(test_config)
        pairs = [(_make_word("食べる"), _make_media("taberu"))]
        self._wire(mock_services, pairs)

        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = None
        fetcher.stats.return_value = _counts(ssl=1)

        presenter = MagicMock()
        processor = EpisodeProcessor(
            config=config,
            presenter=presenter,
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        warnings = [c.args[0] for c in presenter.show_warning.call_args_list]
        assert any("connection/certificate failure" in w for w in warnings)

    def test_no_failures_emits_no_warning(self, test_config, mock_services, tmp_path):
        config = self._enabled_config(test_config)
        pairs = [(_make_word("食べる"), _make_media("taberu"))]
        self._wire(mock_services, pairs)

        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = tmp_path / "hit.mp3"
        fetcher.stats.return_value = _counts()

        presenter = MagicMock()
        processor = EpisodeProcessor(
            config=config,
            presenter=presenter,
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        warnings = [c.args[0] for c in presenter.show_warning.call_args_list]
        assert not any("skipped this run" in w for w in warnings)

    def test_fetcher_without_stats_is_safe(self, test_config, mock_services, tmp_path):
        """A fetcher whose stats() returns a non-dict never crashes the run."""
        config = self._enabled_config(test_config)
        pairs = [(_make_word("食べる"), _make_media("taberu"))]
        self._wire(mock_services, pairs)

        # MagicMock's auto-stubbed stats() returns a MagicMock (not a dict).
        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = None

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.cards_created == 1


class TestDictionaryStalenessGate:
    """4.0 backstop: process_episode refuses to start when an enabled indexed
    dict slot is schema-stale, rather than silently emitting zero cards."""

    def _make_meta(self, tmp_path):
        from anki_miner.services.dictionary.registry import DictMeta

        return DictMeta(
            dict_id="old-dict",
            source_name="Old Dict",
            format="yomitan",
            entry_count=0,
            schema_ok=False,
            db_path=tmp_path / "old-dict" / "index.sqlite",
        )

    def _make_processor(self, test_config, registry):
        subtitle_parser = MagicMock()
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return EpisodeProcessor(
            config=test_config,
            subtitle_parser=subtitle_parser,
            word_filter=MagicMock(),
            media_extractor=media_extractor,
            definition_service=definition_service,
            anki_service=anki_service,
            presenter=NullPresenter(),
            dictionary_registry=registry,
        )

    def test_stale_enabled_slot_raises_actionable_error(self, test_config, tmp_path):
        registry = MagicMock()
        registry.stale_enabled.return_value = [self._make_meta(tmp_path)]
        proc = self._make_processor(test_config, registry)

        with pytest.raises(SetupError, match="Reimport All"):
            proc.process_episode(tmp_path / "ep01.mkv", tmp_path / "ep01.ass")
        # Aborted before any Anki work / parsing.
        proc.anki_service.verify_card_target.assert_not_called()
        proc.subtitle_parser.parse_subtitle_file.assert_not_called()

    def test_no_stale_slot_proceeds(self, test_config, tmp_path):
        registry = MagicMock()
        registry.stale_enabled.return_value = []
        proc = self._make_processor(test_config, registry)

        proc.subtitle_parser.parse_subtitle_file.return_value = []
        # No words → early return, but the point is it did NOT raise.
        result = proc.process_episode(tmp_path / "ep01.mkv", tmp_path / "ep01.ass")
        assert result is not None
        proc.subtitle_parser.parse_subtitle_file.assert_called_once()

    def test_preview_mode_skips_gate(self, test_config, tmp_path):
        """Preview creates no cards, so the gate (like the card-target preflight)
        does not fire."""
        registry = MagicMock()
        registry.stale_enabled.return_value = [self._make_meta(tmp_path)]
        proc = self._make_processor(test_config, registry)
        proc.subtitle_parser.parse_subtitle_file.return_value = []
        # Should not raise in preview mode.
        proc.process_episode(tmp_path / "ep01.mkv", tmp_path / "ep01.ass", preview_mode=True)

    def test_no_registry_is_noop(self, test_config, tmp_path):
        proc = self._make_processor(test_config, None)
        proc.subtitle_parser.parse_subtitle_file.return_value = []
        proc.process_episode(tmp_path / "ep01.mkv", tmp_path / "ep01.ass")
        proc.subtitle_parser.parse_subtitle_file.assert_called_once()
