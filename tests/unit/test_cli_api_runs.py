"""--api prepare and commit over mocked services: run folders, verdicts, refusals."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.cli import runner
from anki_miner.cli.api import files, runs
from anki_miner.cli.api.contract import ApiError
from anki_miner.models import CANCELLED_ERROR, AnkiWriteState, ProcessingResult
from tests.unit.test_cli_api_lines import ENTRIES, _with_variants, _word


def _ok_result(new: int = 0) -> ProcessingResult:
    return ProcessingResult(
        total_words_found=4, new_words_found=new, cards_created=0, anki_write_state=AnkiWriteState.NO_NOTE_WRITE
    )


@pytest.fixture
def video(tmp_path: Path) -> Path:
    (tmp_path / "v.mkv").write_bytes(b"video")
    (tmp_path / "s.srt").write_text("1\n00:00:12,480 --> 00:00:14,900\n約束したでしょう\n", encoding="utf-8")
    return tmp_path / "v.mkv"


def _run_file(tmp_path: Path, video: Path, *, tags: str = "", second: bool = False) -> files.RunFile:
    episode = {"run_id": "ep-01", "video_file": str(video), "subtitle_file": str(tmp_path / "s.srt"), "tags": tags}
    episodes = [episode, {**episode, "run_id": "ep-02"}] if second else [episode]
    return files.parse_run_file(
        {"schema": 1, "run_dir": str(tmp_path), "language": "ja", "config": {}, "episodes": episodes}
    )


@pytest.fixture
def services(test_config):
    """Every service the runs build, mocked; the processor feeds the callback two words."""
    ns = SimpleNamespace(words=lambda: [_with_variants("約束", 0, 3), _word("今日", 1)], calls=0)
    processor = MagicMock()
    processor.subtitle_parser.parse_raw_entries.return_value = ENTRIES
    processor.anki_service.last_created_mined_forms = []
    processor.anki_service.last_created_note_ids = []
    processor.anki_service.last_media_store_failures = 0
    processor.last_media_missing = {}

    def process(*_args, **kwargs):
        kwargs["curation_callback"](ns.words())
        return _ok_result()

    processor.process_episode.side_effect = process
    shared = MagicMock()
    shared.dictionary_registry.usable_enabled.return_value = []
    shared.frequency_registry = None
    with (
        patch.object(runs.settings, "resolve_run_config", return_value=test_config),
        patch.object(runs, "check_environment"),
        patch.object(runs, "check_card_target") as check_card_target,
        patch.object(runs, "create_shared_lookup_services", return_value=shared),
        patch.object(runs, "AnkiService"),
        patch.object(runs, "binary_available", return_value=True),
        patch.object(runs, "get_media_duration_seconds", return_value=1.0) as duration,
        patch.object(runs, "create_episode_processor", return_value=processor) as factory,
    ):
        ns.processor = processor
        ns.factory = factory
        ns.duration = duration
        ns.check_card_target = check_card_target
        yield ns


def test_prepare_writes_candidates_and_saved_run(services, tmp_path, video) -> None:
    verdicts = runs.prepare_runs(_run_file(tmp_path, video), threading.Event())
    assert verdicts == [{"run_id": "ep-01", "ok": True, "error": None, "message": None, "file": "candidates.json"}]
    folder = tmp_path / "ep-01"
    doc = json.loads((folder / "candidates.json").read_text(encoding="utf-8"))
    assert [c["mined_form"] for c in doc["candidates"]] == ["約束", "今日"]
    assert doc["candidates"][0]["sentence_candidates"] == [0, 3]
    assert (folder / "prepared.json").exists() and not (folder / "media").exists()


def test_mine_builds_processor_without_db_or_stats(services, tmp_path, video) -> None:
    runs.prepare_runs(_run_file(tmp_path, video), threading.Event())
    args, kwargs = services.factory.call_args
    assert args[2] is None  # no stats service
    assert kwargs["with_known_words_db"] is False
    assert kwargs["run_temp_root"] == tmp_path.resolve() / "ep-01" / "media"
    assert args[0].media_temp_folder == tmp_path.resolve() / "ep-01" / "media"


def test_prepare_episode_tags_add_to_the_profile_tags(services, tmp_path, video, test_config) -> None:
    runs.prepare_runs(_run_file(tmp_path, video, tags="job::1"), threading.Event())
    assert services.factory.call_args.args[0].anki_tags == f"{test_config.anki_tags} job::1".strip()


def test_prepare_unreadable_video_fails_that_run_only(services, tmp_path, video) -> None:
    services.duration.side_effect = [None, 1.0]
    verdicts = runs.prepare_runs(_run_file(tmp_path, video, second=True), threading.Event())
    assert [v["error"] for v in verdicts] == ["VIDEO_UNREADABLE", None]


def test_prepare_card_target_failure_is_call_level(services, tmp_path, video) -> None:
    services.check_card_target.side_effect = runner.SetupFailure("Deck 'X' is not in Anki")
    with pytest.raises(ApiError) as err:
        runs.prepare_runs(_run_file(tmp_path, video), threading.Event())
    assert err.value.code == "SETUP_ERROR"


def test_prepare_missing_ffmpeg_is_setup_error(services, tmp_path, video) -> None:
    with patch.object(runs, "binary_available", return_value=False), pytest.raises(ApiError) as err:
        runs.prepare_runs(_run_file(tmp_path, video), threading.Event())
    assert err.value.code == "SETUP_ERROR" and "ffmpeg" in err.value.message


def test_commit_round_trip(services, tmp_path, video) -> None:
    runs.prepare_runs(_run_file(tmp_path, video), threading.Event())
    services.processor.anki_service.last_created_mined_forms = ["約束"]
    services.processor.anki_service.last_created_note_ids = [1727000000001]
    services.processor.last_media_missing = {"約束": ["audio"]}
    commit = files.CommitFile(
        tmp_path.resolve(), (files.CommitRun("ep-01", (files.WordPick("約束", 3), files.WordPick("無い"))),)
    )
    [verdict] = runs.commit_runs(commit, threading.Event())
    assert verdict == {"run_id": "ep-01", "ok": True, "error": None, "message": None, "file": "result-1.json"}
    result = json.loads((tmp_path / "ep-01" / "result-1.json").read_text(encoding="utf-8"))
    assert result["outcome"] == "success" and result["error"] is None and result["media_store_failures"] == 0
    assert result["anki_write_state"] == "no_note_write" and result["failure_is_transient"] is False
    assert result["words"][0]["media_missing"] == ["audio"]
    assert [(w["mined_form"], w["status"]) for w in result["words"]] == [("約束", "created"), ("無い", "not_found")]
    assert result["words"][0]["line_range"] == [3, 3]
    assert runs.commit_runs(commit, threading.Event())[0]["file"] == "result-2.json"


def test_commit_unknown_run(services, tmp_path) -> None:
    commit = files.CommitFile(tmp_path, (files.CommitRun("nope", (files.WordPick("x"),)),))
    assert runs.commit_runs(commit, threading.Event())[0]["error"] == "UNKNOWN_RUN"


def test_commit_bad_line_refuses_before_mining(services, tmp_path, video) -> None:
    runs.prepare_runs(_run_file(tmp_path, video), threading.Event())
    services.processor.process_episode.reset_mock()
    commit = files.CommitFile(tmp_path.resolve(), (files.CommitRun("ep-01", (files.WordPick("約束", 2),)),))
    assert runs.commit_runs(commit, threading.Event())[0] == {
        "run_id": "ep-01",
        "ok": False,
        "error": "BAD_LINE",
        "message": "約束: line 2 is not one of its sentence_candidates.",
        "file": None,
    }
    services.processor.process_episode.assert_not_called()


def test_commit_stale_subtitle(services, tmp_path, video) -> None:
    runs.prepare_runs(_run_file(tmp_path, video), threading.Event())
    (tmp_path / "s.srt").write_text("changed", encoding="utf-8")
    commit = files.CommitFile(tmp_path.resolve(), (files.CommitRun("ep-01", (files.WordPick("約束"),)),))
    verdict = runs.commit_runs(commit, threading.Event())[0]
    assert verdict["error"] == "RUN_STALE" and "s.srt" in verdict["message"] and verdict["file"] is None


def test_commit_cancel_file_stops_one_run(services, tmp_path, video) -> None:
    runs.prepare_runs(_run_file(tmp_path, video, second=True), threading.Event())

    def cancelled_first(*_a, **kw):
        if services.calls == 0:
            services.calls += 1
            (tmp_path / "ep-01" / "cancel").touch()
            assert kw["cancel_event"].wait(2)
            return ProcessingResult(
                total_words_found=0,
                new_words_found=0,
                cards_created=0,
                errors=[CANCELLED_ERROR],
                anki_write_state=AnkiWriteState.NO_NOTE_WRITE,
            )
        kw["curation_callback"](services.words())
        return _ok_result(new=1)

    services.processor.process_episode.side_effect = cancelled_first
    commit = files.CommitFile(
        tmp_path.resolve(),
        (files.CommitRun("ep-01", (files.WordPick("約束"),)), files.CommitRun("ep-02", (files.WordPick("約束"),))),
    )
    first, second = runs.commit_runs(commit, threading.Event())
    assert first["error"] == "CANCELLED" and first["file"] == "result-1.json"
    assert second["ok"] is True
    assert not (tmp_path / "ep-01" / "cancel").exists()


def _honours_cancel(services):
    """A processor that stops when its run's cancel event is set, as the real checkpoints do."""

    def process(*_a, **kw):
        if kw["cancel_event"].wait(0.5):
            return ProcessingResult(
                total_words_found=0,
                new_words_found=0,
                cards_created=0,
                errors=[CANCELLED_ERROR],
                anki_write_state=AnkiWriteState.NO_NOTE_WRITE,
            )
        kw["curation_callback"](services.words())
        return _ok_result()

    return process


def test_prepare_cancel_file_already_there_cancels_that_run(services, tmp_path, video) -> None:
    # A Windows caller has no signals: it stops queued episodes by creating their cancel files first.
    (tmp_path / "ep-01").mkdir()
    (tmp_path / "ep-01" / "cancel").touch()
    services.processor.process_episode.side_effect = _honours_cancel(services)
    first, second = runs.prepare_runs(_run_file(tmp_path, video, second=True), threading.Event())
    assert first["error"] == "CANCELLED" and second["ok"] is True
    assert not (tmp_path / "ep-01" / "cancel").exists()
    assert not (tmp_path / "ep-01" / "candidates.json").exists()


def test_relative_episode_paths_survive_a_commit_from_another_folder(services, tmp_path, video, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    job = files.parse_run_file(
        {
            "schema": 1,
            "run_dir": ".",
            "language": "ja",
            "episodes": [{"run_id": "ep-01", "video_file": "v.mkv", "subtitle_file": "s.srt"}],
        }
    )
    assert runs.prepare_runs(job, threading.Event())[0]["ok"] is True
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    commit = files.CommitFile(tmp_path.resolve(), (files.CommitRun("ep-01", (files.WordPick("約束"),)),))
    verdict = runs.commit_runs(commit, threading.Event())[0]
    assert verdict["ok"] is True, verdict
