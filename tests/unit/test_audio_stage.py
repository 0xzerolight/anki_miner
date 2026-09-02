"""Tests for the expression- and sentence-audio stage (orchestration.audio_stage).

Split from ``test_episode_processor.py`` (ARC-036) to track the ARC-021
AudioStage extraction. These stay behavior-pinned: they drive
``process_episode`` end-to-end over MagicMock services (:func:`build_processor`)
and assert on the fetch loops the stage now owns. The pure diagnosis helper is
imported straight from ``orchestration.audio_stage``.
"""

import logging
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anki_miner.models import MediaData, TokenizedWord
from anki_miner.orchestration.audio_stage import AudioStage, _audio_failure_diagnosis
from anki_miner.orchestration.episode_processor import _EpisodeContext
from anki_miner.presenters import NullPresenter
from tests.conftest import build_processor


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


def _counts(**kw):
    """Build a full failure-cause counts dict, defaulting unset buckets to 0."""
    from anki_miner.services.expression_audio_fetcher import FAILURE_KEYS

    base = dict.fromkeys(FAILURE_KEYS, 0)
    base.update(kw)
    return base


def _wire_pipeline(mock_services, pairs):
    """Wire the pipeline mocks so process_episode reaches Phase-3 audio.

    Unified (ARC-036) from the byte-identical per-class ``_wire_pipeline`` /
    ``_wire`` staticmethods the split collapsed together.
    """
    words = [word for word, _ in pairs]
    mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
    mock_services["anki_service"].get_existing_vocabulary.return_value = set()
    mock_services["word_filter"].filter_unknown.return_value = words
    mock_services["media_extractor"].extract_media_batch.return_value = pairs
    mock_services["definition_service"].get_definitions_batch.return_value = ["1. def"] * len(words)
    mock_services["anki_service"].create_cards_batch.return_value = list(range(len(words)))


class TestAudioStageLogging:
    """Bounded operation receipts for the hot expression-audio loop."""

    @staticmethod
    def _stage(config, fetcher, presenter=None):
        return AudioStage(
            config=config,
            presenter=presenter or NullPresenter(),
            cancelled=lambda: False,
            expression_audio_fetcher=fetcher,
            sentence_audio_fetcher=None,
        )

    @staticmethod
    def _enabled_config(test_config):
        return replace(
            test_config,
            anki_fields={**test_config.anki_fields, "expression_audio": "ExpressionAudio"},
        )

    def test_stage_summary_includes_failure_counter(self, test_config, caplog):
        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = None
        fetcher.stats.return_value = _counts(ssl=1)
        stage = self._stage(self._enabled_config(test_config), fetcher)

        with caplog.at_level(logging.INFO, logger="anki_miner.orchestration.audio_stage"):
            stage.fetch_expression_audio([(_make_word(), _make_media())], None)

        record = next(record for record in caplog.records if record.getMessage().startswith("Audio stage done:"))
        assert "ssl=1" in record.getMessage()
        assert record.levelno == logging.INFO
        assert record.name == "anki_miner.orchestration.audio_stage"

    def test_inactive_stage_logs_field_not_mapped_reason(self, test_config, caplog):
        config = replace(
            test_config,
            anki_fields={**test_config.anki_fields, "expression_audio": ""},
        )
        stage = self._stage(config, MagicMock())

        with caplog.at_level(logging.INFO, logger="anki_miner.orchestration.audio_stage"):
            stage.fetch_expression_audio([(_make_word(), _make_media())], None)

        record = next(record for record in caplog.records if record.getMessage().startswith("Expression audio gate:"))
        assert "reason=field_not_mapped" in record.getMessage()

    def test_stage_logging_does_not_scale_per_word(self, test_config, caplog):
        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = None
        fetcher.stats.return_value = _counts()
        stage = self._stage(self._enabled_config(test_config), fetcher)
        pairs = [(_make_word(lemma=f"word-{index}"), _make_media(str(index))) for index in range(50)]

        with caplog.at_level(logging.INFO, logger="anki_miner.orchestration.audio_stage"):
            stage.fetch_expression_audio(pairs, None)

        records = [record for record in caplog.records if record.name == "anki_miner.orchestration.audio_stage"]
        assert any(record.getMessage().startswith("Audio stage done:") for record in records)
        assert len(records) < 10

    def test_diagnosis_is_logged_at_warning(self, test_config, caplog):
        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = None
        fetcher.stats.return_value = _counts(timeout=1)
        stage = self._stage(self._enabled_config(test_config), fetcher, MagicMock())

        with caplog.at_level(logging.WARNING, logger="anki_miner.orchestration.audio_stage"):
            stage.fetch_expression_audio([(_make_word(), _make_media())], None)

        record = next(record for record in caplog.records if record.getMessage().startswith("Audio stage diagnosis:"))
        assert record.levelno == logging.WARNING
        assert record.name == "anki_miner.orchestration.audio_stage"

    def test_previous_failure_does_not_warn_after_later_hit(self, test_config, tmp_path):
        counts = _counts()
        outcomes = iter([None, tmp_path / "recovered.mp3"])

        def _fetch_candidates(*_args, **_kwargs):
            result = next(outcomes)
            if result is None:
                counts["connection"] += 1
            return result

        fetcher = MagicMock()
        fetcher.fetch_candidates.side_effect = _fetch_candidates
        fetcher.stats.side_effect = lambda: dict(counts)
        presenter = MagicMock()
        stage = self._stage(self._enabled_config(test_config), fetcher, presenter)

        stage.fetch_expression_audio([(_make_word(), _make_media("failed"))], None)
        presenter.show_warning.reset_mock()
        recovered_media = _make_media("recovered")

        stage.fetch_expression_audio([(_make_word(), recovered_media)], None)

        assert recovered_media.expression_audio_filename == "recovered.mp3"
        presenter.show_warning.assert_not_called()


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

    def test_enabled_fetches_per_word_and_fills_media(self, test_config, mock_services, tmp_path):
        """Fetcher called with each word's candidate ladder; hits fill MediaData, misses stay None."""
        config = self._enabled_config(test_config)
        pairs = [
            (self._word("食べる", "たべる"), _make_media("taberu")),
            (self._word("走る", "はしる", 5.0), _make_media("hashiru")),
        ]
        _wire_pipeline(mock_services, pairs)

        audio_path = tmp_path / "jpod101_食べる_たべる.mp3"
        fetcher = MagicMock()
        fetcher.fetch_candidates.side_effect = [audio_path, None]

        processor = build_processor(
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

    def test_unsafe_lemma_is_not_an_audio_candidate(self, test_config, mock_services, tmp_path):
        config = self._enabled_config(test_config)
        word = _make_word(lemma="返る", surface="帰れ", pos="動詞")
        word.orth_base = "帰れる"
        word.expression_reading = "かえれる"
        word.lemma_reading = "かえる"
        media = _make_media("kaereru")
        pairs = [(word, media)]
        _wire_pipeline(mock_services, pairs)

        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = None

        processor = build_processor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.cards_created == 1
        assert fetcher.fetch_candidates.call_count == 1
        candidates = fetcher.fetch_candidates.call_args.args[0]
        assert candidates == [("帰れる", "かえれる")]
        assert media.expression_audio_path is None
        assert media.expression_audio_filename is None

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
        _wire_pipeline(mock_services, pairs)

        audio_path = tmp_path / "jpod101_チップ_チップ.mp3"
        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = audio_path

        processor = build_processor(
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
        _wire_pipeline(mock_services, pairs)

        audio_path = tmp_path / "jpod101_探す_さがす.mp3"
        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = audio_path

        processor = build_processor(
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
        _wire_pipeline(mock_services, pairs)
        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = None

        processor = build_processor(
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
        _wire_pipeline(mock_services, pairs)

        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = None

        processor = build_processor(
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
        _wire_pipeline(mock_services, pairs)
        fetcher = MagicMock()

        processor = build_processor(
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
        _wire_pipeline(mock_services, pairs)

        processor = build_processor(
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
        _wire_pipeline(mock_services, pairs)

        fetcher = MagicMock()
        processor = build_processor(
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
        _wire_pipeline(mock_services, pairs)

        fetcher = MagicMock()
        fetcher.fetch_candidates.side_effect = [tmp_path / "a.mp3", None]
        presenter = MagicMock()

        processor = build_processor(
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
        _wire_pipeline(mock_services, pairs)

        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = None

        processor = build_processor(
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
        _wire_pipeline(mock_services, pairs)

        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = None

        progress_callback = MagicMock()

        processor = build_processor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass", progress_callback=progress_callback)

        # The expression-audio loop forwards on_progress for every word with the
        # item_description "Expression audio: <mined_form>". Filter to only those
        # calls and assert exactly 3 (one per word) — other on_progress calls
        # belong to different stages.
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
    def _word(lemma, reading="よみ", start_time=1.0):
        word = _make_word(lemma, start_time=start_time)
        word.expression_reading = reading
        return word

    def test_expression_audio_reports_inside_the_media_stage(self, test_config, mock_services, tmp_path):
        """Feature ON: expression audio is a sub-operation of stage 3, not a stage.

        Whether it runs is a field-mapping decision, so letting it add a stage
        would change the denominator the user sees between two runs of the same
        pipeline. It reports its own true count under stage 3 instead.
        """
        config = self._enabled_config(test_config)
        pairs = [(self._word("食べる", "たべる"), _make_media("taberu"))]
        _wire_pipeline(mock_services, pairs)

        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = None

        class _RecordingCallback:
            def __init__(self):
                self.stages = []
                self.starts = []

            def on_stage(self, index, total, name):
                self.stages.append((index, total, name))

            def on_start(self, total, description):
                self.starts.append(description)

            def on_progress(self, current, item_description):
                pass

            def on_complete(self):
                pass

            def on_error(self, item_description, error_message):
                pass

        cb = _RecordingCallback()

        processor = build_processor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass", progress_callback=cb)

        assert [i for i, _, _ in cb.stages] == [1, 2, 3, 4, 5]
        assert all(total == 5 for _, total, _ in cb.stages)
        assert any("expression audio" in d.lower() for d in cb.starts)

        # Cross-check: the fetcher really ran under that stage.
        assert fetcher.fetch_candidates.call_count == 1

    def test_feature_on_on_start_description_includes_expression_audio(self, test_config, mock_services, tmp_path):
        """The expression-audio on_start description reaches the callback."""
        config = self._enabled_config(test_config)
        pairs = [(self._word("食べる", "たべる"), _make_media("taberu"))]
        _wire_pipeline(mock_services, pairs)

        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = None

        # Pass a raw MagicMock as progress_callback so we can inspect all calls.
        raw_cb = MagicMock()

        processor = build_processor(
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
        otherwise the sub-operation goes silent instead of declaring a real
        total of zero. We call _phase3_extract directly with a raw callback.
        """
        config = self._enabled_config(test_config)

        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = None

        processor = build_processor(
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
        _wire_pipeline(mock_services, pairs)

        fetcher = MagicMock()
        raw_cb = MagicMock()

        processor = build_processor(
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
        _wire_pipeline(mock_services, pairs)

        raw_cb = MagicMock()

        # No fetcher injected
        processor = build_processor(
            config=config,
            presenter=NullPresenter(),
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass", progress_callback=raw_cb)

        on_start_descriptions = [c.args[1] for c in raw_cb.on_start.call_args_list]
        assert not any("expression audio" in d.lower() for d in on_start_descriptions)


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

    def test_dominant_ssl_failure_warns(self, test_config, mock_services, tmp_path):
        config = self._enabled_config(test_config)
        pairs = [(_make_word("食べる"), _make_media("taberu"))]
        _wire_pipeline(mock_services, pairs)

        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = None
        fetcher.stats.return_value = _counts(ssl=1)

        presenter = MagicMock()
        processor = build_processor(
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
        _wire_pipeline(mock_services, pairs)

        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = tmp_path / "hit.mp3"
        fetcher.stats.return_value = _counts()

        presenter = MagicMock()
        processor = build_processor(
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
        _wire_pipeline(mock_services, pairs)

        # MagicMock's auto-stubbed stats() returns a MagicMock (not a dict).
        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = None

        processor = build_processor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.cards_created == 1


class TestSlowPackDiagnosis:
    """A dominant "slow" bucket names the pack and the pack-specific remedy.

    The generic "responding too slowly" advice (reorder or disable the source)
    is right for an online source and wrong for a local pack, whose fix is to
    move the folder onto a local drive and re-import. The chain knows which
    member expired; the stage has to say so.
    """

    def test_slow_dominant_with_pack_names_the_pack_and_the_remedy(self):
        msg = _audio_failure_diagnosis(_counts(slow=6), attempts=10, slow_pack="forvo")
        assert msg is not None
        assert "'forvo'" in msg
        assert "Settings -> Audio" in msg
        assert "re-import" in msg.lower()
        assert "local drive" in msg

    def test_slow_dominant_without_pack_keeps_the_generic_message(self):
        msg = _audio_failure_diagnosis(_counts(slow=6), attempts=10)
        assert msg is not None
        assert "responding too slowly" in msg
        assert "Reorder or disable" in msg

    def test_pack_name_is_ignored_when_slow_is_not_dominant(self):
        msg = _audio_failure_diagnosis(_counts(ssl=8, slow=1), attempts=10, slow_pack="forvo")
        assert msg is not None
        assert "forvo" not in msg
        assert "connection/certificate failure" in msg

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda words: words
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": MagicMock(),
            "definition_service": MagicMock(),
            "anki_service": MagicMock(),
        }

    @staticmethod
    def _enabled_config(test_config):
        return replace(
            test_config,
            anki_fields={**test_config.anki_fields, "expression_audio": "ExpressionAudio"},
        )

    def _run(self, test_config, mock_services, tmp_path, fetcher):
        config = self._enabled_config(test_config)
        pairs = [(_make_word("食べる"), _make_media("taberu"))]
        _wire_pipeline(mock_services, pairs)
        presenter = MagicMock()
        processor = build_processor(
            config=config,
            presenter=presenter,
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")
        return [c.args[0] for c in presenter.show_warning.call_args_list]

    def test_stage_warning_names_the_slow_pack(self, test_config, mock_services, tmp_path):
        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = None
        fetcher.stats.return_value = _counts(slow=1)
        fetcher.slowest_pack_id.return_value = "forvo"

        warnings = self._run(test_config, mock_services, tmp_path, fetcher)
        assert any("'forvo'" in w and "Settings -> Audio" in w for w in warnings), warnings

    def test_stage_warning_stays_generic_without_a_slow_pack(self, test_config, mock_services, tmp_path):
        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = None
        fetcher.stats.return_value = _counts(slow=1)
        fetcher.slowest_pack_id.return_value = None

        warnings = self._run(test_config, mock_services, tmp_path, fetcher)
        assert any("responding too slowly" in w for w in warnings), warnings

    def test_non_string_slowest_pack_id_is_treated_as_absent(self, test_config, mock_services, tmp_path):
        """A MagicMock fetcher auto-stubs slowest_pack_id(); the stage must not crash on it."""
        fetcher = MagicMock()
        fetcher.fetch_candidates.return_value = None
        fetcher.stats.return_value = _counts(slow=1)

        warnings = self._run(test_config, mock_services, tmp_path, fetcher)
        assert any("responding too slowly" in w for w in warnings), warnings
