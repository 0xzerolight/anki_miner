from __future__ import annotations

import json
import threading
from dataclasses import replace
from itertools import product
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.cli import runner
from anki_miner.cli.events import EventSink
from anki_miner.exceptions import AnkiConnectionError, SetupError
from anki_miner.exceptions.youtube import BotDetectionError, YouTubeFetchError
from anki_miner.models import CANCELLED_ERROR, AnkiWriteState, ProcessingResult

VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def _ok(cards: int = 2) -> ProcessingResult:
    return ProcessingResult(total_words_found=10, new_words_found=cards, cards_created=cards, card_ids=[1, 2][:cards])


def _failed(msg: str = "boom") -> ProcessingResult:
    return ProcessingResult(total_words_found=0, new_words_found=0, cards_created=0, errors=[msg])


def _sink() -> tuple[EventSink, list[bytes]]:
    lines: list[bytes] = []
    return EventSink(write=lines.append), lines


def _events(lines: list[bytes]) -> list[dict]:
    return [json.loads(x) for x in b"".join(lines).decode("ascii").splitlines()]


def _episode(tmp_path: Path, name: str = "e1") -> runner.EpisodeJob:
    return runner.EpisodeJob(video=tmp_path / f"{name}.mkv", subtitle=tmp_path / f"{name}.srt")


@pytest.fixture
def services():
    """Patch every service the run builds; yield the processor mock."""
    processor = MagicMock()
    processor.anki_service.anki_write_state = AnkiWriteState.NO_NOTE_WRITE
    shared = MagicMock()
    shared.load_result.info = ["Loaded 1 dictionary"]
    shared.load_result.warnings = []
    with (
        patch.object(runner, "stale_resource_reimport_error", return_value=None),
        patch.object(runner, "AnkiService") as anki_cls,
        patch.object(runner, "create_shared_lookup_services", return_value=shared),
        patch.object(runner, "require_usable_offline_provider"),
        patch.object(runner, "StatsService"),
        patch.object(runner, "create_episode_processor", return_value=processor) as factory,
    ):
        processor.test_anki_cls = anki_cls
        processor.test_shared = shared
        processor.test_factory = factory
        yield processor


@pytest.fixture
def youtube(services):
    """A reachable yt-dlp and a fetcher whose probe the test controls."""
    fetcher = MagicMock()
    fetcher.probe_metadata.return_value = SimpleNamespace(video_id="dQw4w9WgXcQ", title="T", has_auto_ja_subs=True)
    with (
        patch.object(runner, "ytdlp_available", return_value=True),
        patch.object(runner, "create_youtube_fetcher", return_value=fetcher),
    ):
        yield fetcher


# ---- job expansion -------------------------------------------------------


def test_pair_jobs_rejects_missing_files(tmp_path: Path) -> None:
    video = tmp_path / "第01話.mkv"
    video.touch()
    with pytest.raises(runner.InputError, match="第01話.srt"):
        runner.pair_jobs([(video, tmp_path / "第01話.srt")])


def test_pair_jobs_resolves_paths(tmp_path: Path) -> None:
    (tmp_path / "a.mkv").touch()
    (tmp_path / "a.srt").touch()
    [job] = runner.pair_jobs([(tmp_path / "a.mkv", tmp_path / "a.srt")])
    assert job.video.is_absolute()
    assert job.describe() == {"video": str(job.video), "subtitle": str(job.subtitle)}


def test_batch_jobs_uses_episode_matcher(tmp_path: Path) -> None:
    (tmp_path / "v").mkdir()
    (tmp_path / "s").mkdir()
    pair = SimpleNamespace(video=tmp_path / "v/e1.mkv", subtitle=tmp_path / "s/e1.srt", secondary=None)
    with patch.object(runner.FilePairMatcher, "find_pairs_by_episode_number", return_value=[pair]) as find:
        jobs = runner.batch_jobs(tmp_path / "v", tmp_path / "s")
    find.assert_called_once_with((tmp_path / "v").resolve(), (tmp_path / "s").resolve())
    assert [(j.video, j.subtitle) for j in jobs] == [(pair.video, pair.subtitle)]


def test_batch_jobs_without_matches_is_input_error(tmp_path: Path) -> None:
    (tmp_path / "v").mkdir()
    (tmp_path / "s").mkdir()
    with (
        patch.object(runner.FilePairMatcher, "find_pairs_by_episode_number", return_value=[]),
        pytest.raises(runner.InputError, match="No subtitle file"),
    ):
        runner.batch_jobs(tmp_path / "v", tmp_path / "s")


def test_batch_jobs_requires_directories(tmp_path: Path) -> None:
    with pytest.raises(runner.InputError, match="not a folder"):
        runner.batch_jobs(tmp_path / "nope", tmp_path)


def test_reading_jobs_expand_detected_refs(tmp_path: Path) -> None:
    srt = tmp_path / "a.srt"
    srt.touch()
    ref = SimpleNamespace(kind="subtitle", title="a", path=srt)
    with patch.object(runner.detector, "detect", return_value=[ref]):
        [job] = runner.reading_jobs([srt])
    assert job.source is ref and job.path == srt.resolve()


def test_reading_jobs_unrecognised_path_is_input_error(tmp_path: Path) -> None:
    bad = tmp_path / "a.pdf"
    bad.touch()
    with (
        patch.object(runner.detector, "detect", side_effect=SetupError("not a recognized reading source")),
        pytest.raises(runner.InputError, match="not a recognized"),
    ):
        runner.reading_jobs([bad])


@pytest.mark.parametrize("url", ["https://www.youtube.com/playlist?list=PL123", "https://example.com/video"])
def test_youtube_jobs_refuse_non_video_urls(url: str) -> None:
    with pytest.raises(runner.InputError, match="YouTube video URL"):
        runner.youtube_jobs([url])


def test_youtube_jobs_accept_video_urls() -> None:
    [job] = runner.youtube_jobs([VIDEO_URL])
    assert job.describe() == {"url": VIDEO_URL}


# ---- run -----------------------------------------------------------------


def test_run_mines_each_pair_with_one_processor(services, test_config, tmp_path: Path) -> None:
    services.process_episode.side_effect = [_ok(2), _ok(1)]
    sink, lines = _sink()
    reports = runner.MiningRun(test_config, sink, threading.Event()).run(
        [_episode(tmp_path, "e1"), _episode(tmp_path, "e2")]
    )
    assert [r.status for r in reports] == ["success", "success"]
    assert [r.cards_created for r in reports] == [2, 1]
    assert reports[0].note_ids == [1, 2]
    services.test_factory.assert_called_once()
    services.close.assert_called_once()
    services.test_shared.close.assert_called_once()
    events = _events(lines)
    kinds = [e["event"] for e in events]
    assert kinds.count("item_start") == 2 and kinds.count("item_done") == 2
    assert {"event": "message", "item": None, "level": "info", "text": "Loaded 1 dictionary"} in events


def test_run_never_passes_a_curation_callback(services, test_config, tmp_path: Path) -> None:
    # Review focus 1: a headless curator would block forever.
    services.process_episode.return_value = _ok()
    config = replace(test_config, review_words_before_mining=True)
    runner.MiningRun(config, _sink()[0], threading.Event()).run([_episode(tmp_path)])
    assert services.process_episode.call_args.kwargs.get("curation_callback") is None


def test_run_passes_cancel_event_not_sticky_cancel(services, test_config, tmp_path: Path) -> None:
    services.process_episode.return_value = _ok()
    cancel = threading.Event()
    runner.MiningRun(test_config, _sink()[0], cancel).run([_episode(tmp_path)])
    assert services.process_episode.call_args.kwargs["cancel_event"] is cancel
    services.cancel.assert_not_called()


def test_soft_failure_and_exception_are_per_item(services, test_config, tmp_path: Path) -> None:
    services.process_episode.side_effect = [_failed("no words"), RuntimeError("ffmpeg died"), _ok()]
    reports = runner.MiningRun(test_config, _sink()[0], threading.Event()).run(
        [_episode(tmp_path, "a"), _episode(tmp_path, "b"), _episode(tmp_path, "c")]
    )
    assert [r.status for r in reports] == ["failed", "failed", "success"]
    assert reports[0].errors == ["no words"]
    assert reports[1].errors == ["ffmpeg died"]
    assert reports[1].retryable is False


def test_returned_transient_failure_is_retryable(services, test_config, tmp_path: Path) -> None:
    transient = _failed("timeout")
    transient.failure_is_transient = True
    transient.anki_write_state = AnkiWriteState.NO_NOTE_WRITE
    services.process_episode.return_value = transient
    [report] = runner.MiningRun(test_config, _sink()[0], threading.Event()).run([_episode(tmp_path)])
    assert report.retryable is True


def test_raised_generic_fetch_error_is_retryable(services, youtube, test_config, tmp_path: Path) -> None:
    # A download that dropped mid-fetch: no note can have been written yet.
    services.process_youtube_url.side_effect = YouTubeFetchError("connection reset")
    with (
        patch.object(runner, "classify_probe_result", return_value=(True, None, "manual_only")),
        patch.object(runner, "allocate_youtube_workspace", return_value=tmp_path),
    ):
        [report] = runner.MiningRun(test_config, _sink()[0], threading.Event()).run([runner.YouTubeJob(VIDEO_URL)])
    assert report.status == "failed" and report.retryable is True


def test_probe_failure_is_not_retryable(services, youtube, test_config) -> None:
    # A private, deleted or region-locked video fails its probe with a generic
    # YouTubeFetchError. The GUI never retries a probe error; neither may a caller.
    youtube.probe_metadata.side_effect = YouTubeFetchError("Video unavailable")
    [report] = runner.MiningRun(test_config, _sink()[0], threading.Event()).run([runner.YouTubeJob(VIDEO_URL)])
    assert report.status == "failed" and report.errors == ["Video unavailable"]
    assert report.retryable is False


def test_raised_bot_detection_is_not_retryable(services, youtube, test_config) -> None:
    youtube.probe_metadata.side_effect = BotDetectionError("sign in to confirm")
    [report] = runner.MiningRun(test_config, _sink()[0], threading.Event()).run([runner.YouTubeJob(VIDEO_URL)])
    assert report.status == "failed" and report.retryable is False


def test_cancel_marks_remaining_items_skipped(services, test_config, tmp_path: Path) -> None:
    cancel = threading.Event()

    def first(*_a, **_k):
        cancel.set()
        return ProcessingResult(total_words_found=3, new_words_found=1, cards_created=1, errors=[CANCELLED_ERROR])

    services.process_episode.side_effect = first
    reports = runner.MiningRun(test_config, _sink()[0], cancel).run(
        [_episode(tmp_path, "a"), _episode(tmp_path, "b"), _episode(tmp_path, "c")]
    )
    assert [r.status for r in reports] == ["cancelled", "skipped", "skipped"]
    assert reports[0].cards_created == 1
    assert services.process_episode.call_count == 1
    assert runner.run_status(reports, cancelled=True) == "cancelled"


def test_stale_index_is_setup_failure_before_any_service(test_config, tmp_path: Path) -> None:
    with (
        patch.object(runner, "stale_resource_reimport_error", return_value="Re-import JMdict"),
        patch.object(runner, "create_shared_lookup_services") as shared,
        pytest.raises(runner.SetupFailure, match="Re-import JMdict"),
    ):
        runner.MiningRun(test_config, _sink()[0], threading.Event()).run([_episode(tmp_path)])
    shared.assert_not_called()


def test_missing_language_pack_is_setup_failure(services, test_config, tmp_path: Path) -> None:
    profile = SimpleNamespace(unavailable_reason=lambda: "Install the Chinese language pack in Settings.")
    with (
        patch.object(runner, "get_profile", return_value=profile),
        pytest.raises(runner.SetupFailure, match="Chinese language pack"),
    ):
        runner.MiningRun(test_config, _sink()[0], threading.Event()).run([_episode(tmp_path)])
    services.process_episode.assert_not_called()


def test_ankiconnect_down_is_setup_failure(services, test_config, tmp_path: Path) -> None:
    # Review focus 3.
    services.test_anki_cls.return_value.verify_card_target.side_effect = AnkiConnectionError(
        "Cannot connect to AnkiConnect"
    )
    with pytest.raises(runner.SetupFailure, match="Cannot connect"):
        runner.MiningRun(test_config, _sink()[0], threading.Event()).run([_episode(tmp_path)])
    services.process_episode.assert_not_called()
    services.test_shared.close.assert_called_once()


def test_card_target_setup_error_is_setup_failure(services, test_config, tmp_path: Path) -> None:
    services.test_anki_cls.return_value.verify_card_target.side_effect = SetupError("Deck 'X' does not exist")
    with pytest.raises(runner.SetupFailure, match="Deck 'X'"):
        runner.MiningRun(test_config, _sink()[0], threading.Event()).run([_episode(tmp_path)])


def test_youtube_without_ytdlp_is_setup_failure(services, test_config) -> None:
    with (
        patch.object(runner, "ytdlp_available", return_value=False),
        pytest.raises(runner.SetupFailure, match="yt-dlp"),
    ):
        runner.MiningRun(test_config, _sink()[0], threading.Event()).run([runner.YouTubeJob(VIDEO_URL)])


def test_reading_job_loads_then_mines(services, test_config, tmp_path: Path) -> None:
    services.process_reading.return_value = _ok()
    ref = SimpleNamespace(kind="subtitle", title="a", path=tmp_path / "a.srt")
    cancel = threading.Event()
    with patch.object(runner, "load_reading_source", return_value="DOC") as load:
        [report] = runner.MiningRun(test_config, _sink()[0], cancel).run([runner.ReadingJob(ref, tmp_path / "a.srt")])
    assert report.status == "success"
    assert load.call_args.args[:3] == (services, test_config, ref)
    assert load.call_args.kwargs["cancel_check"] == cancel.is_set
    assert services.process_reading.call_args.args == ("DOC",)
    assert services.process_reading.call_args.kwargs["cancel_event"] is cancel
    assert services.process_reading.call_args.kwargs.get("curation_callback") is None


def test_youtube_job_probes_classifies_and_cleans_workspace(services, youtube, test_config, tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    services.process_youtube_url.return_value = _ok()
    with (
        patch.object(runner, "classify_probe_result", return_value=(True, None, "manual_only")),
        patch.object(runner, "allocate_youtube_workspace", return_value=workspace),
    ):
        [report] = runner.MiningRun(test_config, _sink()[0], threading.Event()).run([runner.YouTubeJob(VIDEO_URL)])
    assert report.status == "success"
    kwargs = services.process_youtube_url.call_args.kwargs
    assert kwargs["video_id"] == "dQw4w9WgXcQ" and kwargs["sub_mode"] == "manual_only"
    assert kwargs["workspace"] == workspace and kwargs["fallback_allowed"] is True
    assert kwargs.get("curation_callback") is None
    assert not workspace.exists()


def test_youtube_refused_by_probe_is_failed_item(services, youtube, test_config) -> None:
    with patch.object(runner, "classify_probe_result", return_value=(False, "Live streams are not supported.", None)):
        [report] = runner.MiningRun(test_config, _sink()[0], threading.Event()).run([runner.YouTubeJob(VIDEO_URL)])
    assert report.status == "failed" and report.errors == ["Live streams are not supported."]
    services.process_youtube_url.assert_not_called()


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [(["success", "success"], "success"), (["success", "failed"], "partial"), (["failed", "failed"], "failed")],
)
def test_run_status(statuses: list[str], expected: str) -> None:
    reports = [runner.ItemReport(item=i, kind="episode", input={}, status=s) for i, s in enumerate(statuses)]
    assert runner.run_status(reports, cancelled=False) == expected


def _old_run_status(statuses: list[str], *, cancelled: bool) -> str:
    """Copy of run_status's pre-refactor body — the equivalence reference, not live code."""
    if cancelled:
        return "cancelled"
    succeeded = sum(1 for s in statuses if s == "success")
    if succeeded == len(statuses):
        return "success"
    return "partial" if succeeded else "failed"


_ALL_STATUS_COMBOS = [
    (list(combo), cancelled)
    for length in range(0, 4)
    for combo in product(["success", "failed", "cancelled", "skipped"], repeat=length)
    for cancelled in (True, False)
]


@pytest.mark.parametrize(("statuses", "cancelled"), _ALL_STATUS_COMBOS)
def test_run_status_matches_old_rule(statuses: list[str], cancelled: bool) -> None:
    """runner.run_status must match the pre-refactor rule for every status/cancelled combination.

    Exhaustive over both of run_status's inputs (every status string it
    branches on, at every list length 0-3, crossed with both cancelled
    values) — not filtered to a claimed-reachable subset. Run once against
    the unmodified runner.py (this IS the pre-refactor rule, so it passes
    trivially and proves the parametrization itself is sound) and once
    after the refactor (the actual equivalence proof).
    """
    reports = [runner.ItemReport(item=i, kind="episode", input={}, status=s) for i, s in enumerate(statuses)]
    assert runner.run_status(reports, cancelled=cancelled) == _old_run_status(statuses, cancelled=cancelled)
