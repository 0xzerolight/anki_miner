"""Tests for cancellation support in EpisodeProcessor and MediaExtractorService."""

import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.models import MediaData, TokenizedWord
from anki_miner.orchestration.episode_processor import EpisodeProcessor
from anki_miner.presenters import NullPresenter
from anki_miner.services.media_extractor import MediaExtractorService


def _make_word(lemma="食べる", surface=None, start_time=1.0):
    return TokenizedWord(
        surface=surface or f"{lemma}た",
        lemma=lemma,
        reading="タベル",
        sentence=f"{lemma}のテスト",
        start_time=start_time,
        end_time=start_time + 2.0,
        duration=2.0,
    )


def _make_media(prefix="word"):
    return MediaData(
        screenshot_path=Path(f"/tmp/{prefix}.jpg"),
        audio_path=Path(f"/tmp/{prefix}.mp3"),
        screenshot_filename=f"{prefix}.jpg",
        audio_filename=f"{prefix}.mp3",
    )


class TestEpisodeProcessorCancel:
    """Tests for EpisodeProcessor cancellation between phases."""

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

    @pytest.fixture
    def processor(self, test_config, mock_services):
        return EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            **mock_services,
        )

    def test_cancel_flag_initially_false(self, processor):
        """Cancel flag should be False after construction."""
        assert processor.cancelled is False

    def test_cancel_sets_flag(self, processor):
        """cancel() should set the cancelled flag to True."""
        processor.cancel()
        assert processor.cancelled is True

    def test_cancel_after_phase1(self, processor, mock_services, tmp_path):
        """Cancel after subtitle parsing returns partial result."""
        words = [_make_word("食べる")]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words

        # Cancel after phase 1 — side_effect on subtitle_parser to trigger cancel
        def parse_and_cancel(sub_file):
            result = words
            processor.cancel()  # Cancel after phase 1
            return result

        mock_services["subtitle_parser"].parse_subtitle_file.side_effect = parse_and_cancel

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.total_words_found == 1
        assert result.cards_created == 0
        assert "cancelled" in result.errors[0].lower()
        # Phase 2 should NOT have been reached
        mock_services["anki_service"].get_existing_vocabulary.assert_not_called()

    def test_cancel_after_phase2(self, processor, mock_services, tmp_path):
        """Cancel after filtering returns partial result with word counts."""
        words = [_make_word("食べる"), _make_word("走る", start_time=5.0)]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words

        # Cancel during phase 2 — side_effect on filter_unknown
        def filter_and_cancel(all_words, existing):
            processor.cancel()
            return words

        mock_services["word_filter"].filter_unknown.side_effect = filter_and_cancel

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.total_words_found == 2
        assert result.new_words_found == 2
        assert result.cards_created == 0
        assert "cancelled" in result.errors[0].lower()
        # Phase 3 should NOT have been reached
        mock_services["media_extractor"].extract_media_batch.assert_not_called()

    def test_cancel_after_phase3(self, processor, mock_services, tmp_path):
        """Cancel after media extraction returns partial result."""
        words = [_make_word("食べる")]
        media = _make_media("taberu")
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words

        def extract_and_cancel(video, ws, cb, cancelled_check=None, temp_folder=None, **kwargs):
            processor.cancel()
            return [(words[0], media)]

        mock_services["media_extractor"].extract_media_batch.side_effect = extract_and_cancel

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.total_words_found == 1
        assert result.new_words_found == 1
        assert result.cards_created == 0
        assert "cancelled" in result.errors[0].lower()
        # Phase 4 should NOT have been reached
        mock_services["definition_service"].get_definitions_batch.assert_not_called()

    def test_cancel_after_phase4(self, processor, mock_services, tmp_path):
        """Cancel after definitions returns partial result."""
        words = [_make_word("食べる")]
        media = _make_media("taberu")
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = [(words[0], media)]

        def define_and_cancel(lemmas, cb, fallback_context=None):
            processor.cancel()
            return ["1. to eat"]

        mock_services["definition_service"].get_definitions_batch.side_effect = define_and_cancel

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.total_words_found == 1
        assert result.new_words_found == 1
        assert result.cards_created == 0
        assert "cancelled" in result.errors[0].lower()
        # Phase 5 should NOT have been reached
        mock_services["anki_service"].create_cards_batch.assert_not_called()

    def test_cancel_not_set_runs_full_pipeline(self, processor, mock_services, tmp_path):
        """When not cancelled, full pipeline runs normally."""
        words = [_make_word("食べる")]
        media = _make_media("taberu")

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = [(words[0], media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.cards_created == 1
        assert result.success is True

    def test_cancelled_check_passed_to_media_extractor(self, processor, mock_services, tmp_path):
        """Verify that cancelled_check callable is passed to extract_media_batch."""
        words = [_make_word("食べる")]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = []

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # Verify cancelled_check was passed as a keyword argument
        call_kwargs = mock_services["media_extractor"].extract_media_batch.call_args[1]
        assert "cancelled_check" in call_kwargs
        assert callable(call_kwargs["cancelled_check"])

    def test_cancelled_check_reflects_processor_state(self, processor, mock_services, tmp_path):
        """The cancelled_check callable should reflect processor._cancelled."""
        words = [_make_word("食べる")]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = []

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        cancelled_check = mock_services["media_extractor"].extract_media_batch.call_args[1]["cancelled_check"]

        assert cancelled_check() is False
        processor.cancel()
        assert cancelled_check() is True


class TestProcessEpisodeCancelEvent:
    """process_episode must bridge a caller-supplied cancel_event into its checkpoints.

    The audiobook queue worker calls process_episode directly (no
    process_youtube_url wrapper), so worker.cancel() mid-mine must reach the
    processor's phase checkpoints via the cancel_event keyword — NOT via the
    sticky processor.cancel(), which poisons cached processors across runs.
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

    @pytest.fixture
    def processor(self, test_config, mock_services):
        return EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            **mock_services,
        )

    def _wire_happy_pipeline(self, mock_services):
        words = [_make_word("食べる")]
        media = _make_media("taberu")
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = [(words[0], media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1
        return words

    def test_set_cancel_event_stops_at_first_checkpoint(self, processor, mock_services, tmp_path):
        """A pre-set cancel_event ends the run at the first phase checkpoint."""
        words = [_make_word("食べる")]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        event = threading.Event()
        event.set()

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass", cancel_event=event)

        assert result.cards_created == 0
        assert "cancelled" in result.errors[0].lower()
        # Phase 2 must NOT have been reached.
        mock_services["anki_service"].get_existing_vocabulary.assert_not_called()

    def test_cancel_event_set_mid_parse_stops_pipeline(self, processor, mock_services, tmp_path):
        """An event set mid-phase-1 (worker Stop mid-mine) stops before phase 2."""
        words = [_make_word("食べる")]
        event = threading.Event()

        def _parse_then_cancel(sub_file):
            event.set()  # user pressed Stop mid-parse
            return words

        mock_services["subtitle_parser"].parse_subtitle_file.side_effect = _parse_then_cancel

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass", cancel_event=event)

        assert result.cards_created == 0
        assert "cancelled" in result.errors[0].lower()
        mock_services["anki_service"].get_existing_vocabulary.assert_not_called()

    def test_unset_cancel_event_runs_full_pipeline(self, processor, mock_services, tmp_path):
        """An event that is never set must not perturb a normal run."""
        self._wire_happy_pipeline(mock_services)

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass", cancel_event=threading.Event())

        assert result.cards_created == 1
        assert result.success is True

    def test_bridge_reset_after_run(self, processor, mock_services, tmp_path):
        """_external_cancel is dropped after the call (shared-processor reuse)."""
        self._wire_happy_pipeline(mock_services)

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass", cancel_event=threading.Event())

        assert processor._external_cancel is None

    def test_bridge_reset_after_cancelled_run_does_not_poison_next_run(self, processor, mock_services, tmp_path):
        """Run 1's set event must not cancel run 2 on the same processor."""
        self._wire_happy_pipeline(mock_services)
        run1_event = threading.Event()
        run1_event.set()

        result1 = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass", cancel_event=run1_event)
        assert "cancelled" in result1.errors[0].lower()
        assert processor._external_cancel is None

        # Run 2: fresh event; run 1's event stays set.
        result2 = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass", cancel_event=threading.Event())

        assert processor.cancelled is False
        assert result2.cards_created == 1

    def test_bridge_reset_after_error(self, processor, mock_services, tmp_path):
        """_external_cancel is dropped even when the pipeline errors out."""
        mock_services["subtitle_parser"].parse_subtitle_file.side_effect = ValueError("boom")

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass", cancel_event=threading.Event())

        assert result.errors  # the error surfaced in the result
        assert processor._external_cancel is None


class TestMediaExtractorBatchCancel:
    """Tests for MediaExtractorService.extract_media_batch() cancellation."""

    MODULE = "anki_miner.services.media_extractor"

    @pytest.fixture
    def service(self, test_config):
        with patch(f"{self.MODULE}.ensure_directory"):
            return MediaExtractorService(test_config)

    @pytest.fixture
    def video_file(self, tmp_path):
        return tmp_path / "episode_01.mkv"

    def test_cancelled_mid_batch(self, service, video_file, make_tokenized_word, recording_progress, tmp_path):
        """Should return partial results when cancelled_check returns True mid-batch."""
        words = [
            make_tokenized_word(lemma="食べる", start_time=1.0),
            make_tokenized_word(lemma="飲む", start_time=3.0),
            make_tokenized_word(lemma="走る", start_time=5.0),
        ]

        call_count = 0

        def fake_extract(vf, word, temp_folder=None, **kwargs):
            nonlocal call_count
            call_count += 1
            ss = tmp_path / f"{word.lemma}_cancel.jpg"
            ss.write_bytes(b"\xff\xd8fake")
            return MediaData(screenshot_path=ss, screenshot_filename=ss.name)

        # Cancel after the first item is collected. Keyed on recorded progress
        # (not cancelled_check call count) so the pre-loop cancellation probe
        # and in-flight poll checks don't trip it before any work happens.
        def cancelled_check():
            return len(recording_progress.progresses) >= 1

        with patch.object(service, "extract_media", side_effect=fake_extract):
            result = service.extract_media_batch(video_file, words, recording_progress, cancelled_check=cancelled_check)

        # Should have at least 1 result but not all 3
        assert len(result) >= 1
        assert len(result) < 3
        # A cancelled run must not report completion.
        assert recording_progress.completes == 0

    def test_cancel_kills_inflight_ffmpeg_and_returns_promptly(
        self, service, video_file, make_tokenized_word, recording_progress
    ):
        """Cancelling mid-encode must kill live ffmpeg processes, not wait them out.

        Regression: shutdown(cancel_futures=True) only drops *queued* futures;
        already-running encodes used to run to completion (30-60s timeouts) and
        the executor's context exit joined them, blocking the cancelling caller.
        """
        words = [
            make_tokenized_word(lemma="食べる", start_time=1.0),
            make_tokenized_word(lemma="飲む", start_time=3.0),
        ]

        # Audio path must not probe encoders or run ffprobe for real.
        service._animated_encoder_ok["libmp3lame"] = True
        service._animated_encoder_ok["libopus"] = True

        spawned = []
        all_spawned = threading.Event()

        class FakeEncode:
            """Stands in for a long-running ffmpeg Popen; exits only when killed."""

            def __init__(self):
                self._dead = threading.Event()
                self.kill_calls = 0
                self.returncode = None

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return None

            def communicate(self, timeout=None):
                # Block like a long encode; wake immediately on kill.
                if not self._dead.wait(timeout=timeout):
                    raise subprocess.TimeoutExpired("ffmpeg", timeout)
                self.returncode = -9
                return ("", "killed")

            def kill(self):
                self.kill_calls += 1
                self._dead.set()

            def wait(self, timeout=None):
                self._dead.wait(timeout)
                return self.returncode

        def fake_popen(cmd, **kwargs):
            proc = FakeEncode()
            spawned.append(proc)
            if len(spawned) >= len(words):
                all_spawned.set()
            return proc

        with (
            patch(f"{self.MODULE}.subprocess.Popen", side_effect=fake_popen),
            patch(f"{self.MODULE}.subprocess.run") as mock_run,
            patch(f"{self.MODULE}.find_japanese_audio_stream", return_value=None),
        ):
            start = time.monotonic()
            result = service.extract_media_batch(
                video_file,
                words,
                recording_progress,
                cancelled_check=all_spawned.is_set,
            )
            elapsed = time.monotonic() - start

        assert result == []
        # Every in-flight encode was killed instead of running to completion.
        assert len(spawned) == len(words)
        assert all(proc.kill_calls >= 1 for proc in spawned)
        # Promptly: nowhere near the 30s the fake encodes would otherwise block.
        assert elapsed < 10.0
        # Cancelled runs must not report completion, and no new ffmpeg may
        # spawn after the kill (the audio stage follows the killed screenshot).
        assert recording_progress.completes == 0
        mock_run.assert_not_called()

    def test_kill_all_tolerates_already_exited_process(self):
        """Killing a process that already exited (reap race) must not raise."""
        from anki_miner.services.media_extractor import _FfmpegProcRegistry

        registry = _FfmpegProcRegistry()
        proc = MagicMock()
        proc.kill.side_effect = ProcessLookupError("No such process")
        assert registry.register(proc) is True

        registry.kill_all()  # must not raise

        assert registry.cancelled is True
        proc.kill.assert_called_once()
        # Once cancelled, new processes are refused so workers cannot spawn
        # fresh ffmpeg after the kill sweep.
        assert registry.register(MagicMock()) is False

    def test_no_new_ffmpeg_spawn_after_cancelled_registry(self, service):
        """_run_ffmpeg must refuse to spawn once the batch registry is cancelled."""
        from anki_miner.services.media_extractor import _FfmpegProcRegistry

        registry = _FfmpegProcRegistry()
        registry.kill_all()

        with patch(f"{self.MODULE}.subprocess.Popen") as mock_popen:
            ok = service._run_ffmpeg(
                ["ffmpeg", "-i", "in.mkv", "out.jpg"],
                "Test op",
                timeout=5,
                proc_registry=registry,
            )

        assert ok is False
        mock_popen.assert_not_called()

    def test_no_cancelled_check_processes_all(self, service, video_file, make_tokenized_word, tmp_path):
        """Should process all words when cancelled_check is None."""
        words = [
            make_tokenized_word(lemma="食べる", start_time=1.0),
            make_tokenized_word(lemma="飲む", start_time=3.0),
        ]

        def fake_extract(vf, word, temp_folder=None, **kwargs):
            ss = tmp_path / f"{word.lemma}_all.jpg"
            ss.write_bytes(b"\xff\xd8fake")
            return MediaData(screenshot_path=ss, screenshot_filename=ss.name)

        with patch.object(service, "extract_media", side_effect=fake_extract):
            result = service.extract_media_batch(video_file, words)

        assert len(result) == 2

    def test_cancelled_before_any_processing(self, service, video_file, make_tokenized_word, tmp_path):
        """Should return empty list when cancelled immediately."""
        words = [make_tokenized_word(lemma="食べる", start_time=1.0)]

        def fake_extract(vf, word, temp_folder=None, **kwargs):
            ss = tmp_path / f"{word.lemma}_imm.jpg"
            ss.write_bytes(b"\xff\xd8fake")
            return MediaData(screenshot_path=ss, screenshot_filename=ss.name)

        with patch.object(service, "extract_media", side_effect=fake_extract):
            result = service.extract_media_batch(video_file, words, cancelled_check=lambda: True)

        # Cancelled immediately, so no results should be collected
        assert len(result) == 0
