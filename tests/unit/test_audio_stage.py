"""Tests for the expression- and sentence-audio stage (orchestration.audio_stage).

Split from ``test_episode_processor.py`` (ARC-036) to track the ARC-021
AudioStage extraction. These stay behavior-pinned: they drive
``process_episode`` end-to-end over MagicMock services (:func:`build_processor`)
and assert on the fetch loops the stage now owns. The pure diagnosis helper is
imported straight from ``orchestration.audio_stage``.
"""

from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anki_miner.models import MediaData, TokenizedWord
from anki_miner.orchestration.audio_stage import _audio_failure_diagnosis
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
        _wire_pipeline(mock_services, pairs)

        # Surface 噓 misses, lemma 嘘 hits — that selection is now internal to
        # the fetcher; the processor only owns building the ladder.
        audio_path = tmp_path / "jpod101_嘘_うそ.mp3"
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
        _wire_pipeline(mock_services, pairs)

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

        processor = build_processor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass", progress_callback=cb)

        # StageWeightedProgress forwards inner.on_start exactly once — on the
        # very first stage (extract). Later stages (expression audio,
        # definitions, cards) advance the band counter and refresh the label via
        # inner.on_progress (never a second on_start), so cb.starts has exactly
        # 1 entry regardless of band count. The expression-audio band being
        # registered is verified indirectly: fetch_candidates was called
        # (feature ran) AND finish() emitted one on_complete, confirming the
        # full 4-band sweep completed without band-accounting errors.
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
        otherwise the band is silently skipped and the next stage steals it.
        We call _phase3_extract directly with a raw callback (bypassing
        StageWeightedProgress) so every on_start/on_complete lands on our mock.
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
