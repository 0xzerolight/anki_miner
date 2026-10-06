"""--api mine over mocked services: run folders, result files, verdicts, refusals."""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.cli import runner
from anki_miner.cli.api import files, runs
from anki_miner.cli.api.contract import ApiError
from anki_miner.exceptions import SubtitleParseError
from anki_miner.models import CANCELLED_ERROR, AnkiWriteState, ProcessingResult
from tests.unit.test_cli_api_lines import ENTRIES, _on_lines, _word

WORDS = [{"word": "約束", "line_start": 30.0}, {"word": "無い"}]


def _result(*, cancelled: bool = False) -> ProcessingResult:
    return ProcessingResult(
        total_words_found=4,
        new_words_found=0,
        cards_created=0,
        errors=[CANCELLED_ERROR] if cancelled else [],
        anki_write_state=AnkiWriteState.NO_NOTE_WRITE,
    )


@pytest.fixture
def video(tmp_path: Path) -> Path:
    (tmp_path / "v.mkv").write_bytes(b"video")
    (tmp_path / "s.srt").write_text("1\n00:00:12,480 --> 00:00:14,900\n約束したでしょう\n", encoding="utf-8")
    return tmp_path / "v.mkv"


def _run_file(tmp_path: Path, video: Path, *, second: bool = False, words=WORDS, **episode) -> files.RunFile:
    first = {"run_id": "ep-01", "video_file": str(video), "subtitle_file": str(tmp_path / "s.srt"), "words": words}
    first.update(episode)
    episodes = [first, {**first, "run_id": "ep-02"}] if second else [first]
    return files.parse_run_file(
        {"schema": 1, "run_dir": str(tmp_path), "language": "ja", "config": {}, "episodes": episodes}
    )


def _result_file(tmp_path: Path, name: str = "result-1.json") -> dict:
    return json.loads((tmp_path / "ep-01" / name).read_text(encoding="utf-8"))


@pytest.fixture
def services(test_config):
    """Every service a run builds, mocked; the processor feeds the callback two words."""
    ns = SimpleNamespace(words=lambda: [_on_lines("約束", 0, 3), _word("今日", 1)], calls=0)
    processor = MagicMock()
    processor.subtitle_parser.parse_raw_entries.return_value = ENTRIES
    anki = processor.anki_service
    anki.last_created_mined_forms, anki.last_created_note_ids = [], []
    anki.last_not_created, anki.last_media_store_failures = {}, 0
    processor.last_word_drops, processor.last_definition_rejects, processor.last_media_missing = {}, [], {}
    processor.last_collapsed = []

    def process(*_args, **kwargs):
        kwargs["curation_callback"](ns.words())
        return _result()

    processor.process_episode.side_effect = process
    with (
        patch.object(runs.settings, "resolve_run_config", return_value=test_config),
        patch.object(runs, "check_environment"),
        patch.object(runs, "check_card_target") as check_card_target,
        patch.object(runs, "create_shared_lookup_services", return_value=MagicMock()),
        patch.object(runs, "AnkiService"),
        patch.object(runs, "binary_available", return_value=True),
        patch.object(runs, "get_media_duration_seconds", return_value=1.0) as duration,
        patch.object(runs, "get_primary_video_codec", return_value=None),
        patch.object(runs, "create_episode_processor", return_value=processor) as factory,
    ):
        ns.processor = processor
        ns.factory = factory
        ns.duration = duration
        ns.check_card_target = check_card_target
        yield ns


def test_mine_writes_a_result_file_per_run(services, tmp_path, video) -> None:
    anki = services.processor.anki_service
    anki.last_created_mined_forms, anki.last_created_note_ids = ["約束"], [1727000000001]
    services.processor.last_media_missing = {"約束": ["audio"]}
    [verdict] = runs.mine_runs(_run_file(tmp_path, video), threading.Event())
    assert verdict == {"run_id": "ep-01", "ok": True, "error": None, "message": None, "file": "result-1.json"}
    result = _result_file(tmp_path)
    assert result["outcome"] == "success" and result["error"] is None and result["media_store_failures"] == 0
    assert result["anki_write_state"] == "no_note_write" and result["failure_is_transient"] is False
    made, missing = result["words"]
    assert (made["status"], made["note_id"], made["media_missing"]) == ("created", 1727000000001, ["audio"])
    assert (made["line_start"], made["sentence"]) == (30.0, "約束だよ")
    assert (missing["word"], missing["mined_form"], missing["status"]) == ("無い", None, "not_found")
    assert not (tmp_path / "ep-01" / "media").exists()
    # mining the run again starts over and keeps the earlier result
    assert runs.mine_runs(_run_file(tmp_path, video), threading.Event())[0]["file"] == "result-2.json"
    assert (tmp_path / "ep-01" / "result-1.json").exists()


def test_statuses_come_from_the_processor_and_its_anki_service(services, tmp_path, video) -> None:
    services.words = lambda: [_on_lines("約束", 0, 3), _word("今日", 1), _word("別", 2)]
    services.processor.anki_service.last_not_created = {"今日": "uncertain"}
    services.processor.last_word_drops = {"別": "no_definition"}
    services.processor.last_definition_rejects = [_word("走る", 3)]
    words = [{"word": w} for w in ("約束", "今日", "別", "走る")]
    runs.mine_runs(_run_file(tmp_path, video, words=words), threading.Event())
    statuses = [w["status"] for w in _result_file(tmp_path)["words"]]
    assert statuses == ["not_attempted", "uncertain", "no_definition", "no_definition"]


def test_a_run_that_finished_before_curation_reports_not_found(services, tmp_path, video) -> None:
    # nothing parsed, or every word filtered out: a success that never opened the curation step
    services.processor.process_episode.side_effect = lambda *_a, **_kw: _result()
    runs.mine_runs(_run_file(tmp_path, video), threading.Event())
    result = _result_file(tmp_path)
    assert result["outcome"] == "success" and {w["status"] for w in result["words"]} == {"not_found"}


def test_line_text_goes_through_the_parsers_own_cleaner(services, tmp_path, video) -> None:
    services.processor.subtitle_parser._clean_line_text.side_effect = lambda text: text.replace("（男性）", "")
    words = [{"word": "約束", "line_text": "（男性）約束だよ"}]
    runs.mine_runs(_run_file(tmp_path, video, words=words), threading.Event())
    assert _result_file(tmp_path)["words"][0]["line_start"] == 30.0


def test_mine_builds_processor_without_db_or_stats(services, tmp_path, video) -> None:
    runs.mine_runs(_run_file(tmp_path, video), threading.Event())
    args, kwargs = services.factory.call_args
    assert args[2] is None  # no stats service
    assert kwargs["with_known_words_db"] is False
    assert kwargs["run_temp_root"] == tmp_path.resolve() / "ep-01" / "media"
    assert args[0].media_temp_folder == tmp_path.resolve() / "ep-01" / "media"


def test_episode_tags_add_to_the_profile_tags(services, tmp_path, video, test_config) -> None:
    runs.mine_runs(_run_file(tmp_path, video, tags="job::1"), threading.Event())
    assert services.factory.call_args.args[0].anki_tags == f"{test_config.anki_tags} job::1".strip()


def test_subtitle_offset_left_out_is_zero_not_the_profiles(services, tmp_path, video, test_config) -> None:
    with patch.object(runs.settings, "resolve_run_config", return_value=replace(test_config, subtitle_offset=2.5)):
        runs.mine_runs(_run_file(tmp_path, video), threading.Event())
    parse = services.processor.subtitle_parser.parse_raw_entries
    assert [c.args[1] for c in parse.call_args_list] == [0.0, 0.0]
    assert services.processor.process_episode.call_args.kwargs["subtitle_offset"] == 0.0


def test_subtitle_offset_shifts_the_lines_and_a_second_parse_keeps_the_files_times(services, tmp_path, video) -> None:
    runs.mine_runs(_run_file(tmp_path, video, subtitle_offset=-1.5), threading.Event())
    parse = services.processor.subtitle_parser.parse_raw_entries
    assert [c.args[1] for c in parse.call_args_list] == [-1.5, 0.0]


def test_unreadable_video_fails_that_run_only(services, tmp_path, video) -> None:
    services.duration.side_effect = [None, 1.0]
    verdicts = runs.mine_runs(_run_file(tmp_path, video, second=True), threading.Event())
    assert [v["error"] for v in verdicts] == ["VIDEO_UNREADABLE", None]
    assert verdicts[0]["file"] is None


def test_unreadable_subtitle_fails_that_run_without_a_file(services, tmp_path, video) -> None:
    services.processor.subtitle_parser.parse_raw_entries.side_effect = SubtitleParseError("No suitable formats")
    [verdict] = runs.mine_runs(_run_file(tmp_path, video), threading.Event())
    assert verdict["error"] == "SUBTITLE_UNREADABLE" and verdict["file"] is None


def test_card_target_failure_is_call_level(services, tmp_path, video) -> None:
    services.check_card_target.side_effect = runner.SetupFailure("Deck 'X' is not in Anki")
    with pytest.raises(ApiError) as err:
        runs.mine_runs(_run_file(tmp_path, video), threading.Event())
    assert err.value.code == "SETUP_ERROR"


def test_missing_ffmpeg_is_setup_error(services, tmp_path, video) -> None:
    with patch.object(runs, "binary_available", return_value=False), pytest.raises(ApiError) as err:
        runs.mine_runs(_run_file(tmp_path, video), threading.Event())
    assert err.value.code == "SETUP_ERROR" and "ffmpeg" in err.value.message


def test_cancel_file_stops_one_run(services, tmp_path, video) -> None:
    def cancelled_first(*_a, **kw):
        if services.calls == 0:
            services.calls += 1
            (tmp_path / "ep-01" / "cancel").touch()
            assert kw["cancel_event"].wait(2)
            return _result(cancelled=True)
        kw["curation_callback"](services.words())
        return _result()

    services.processor.process_episode.side_effect = cancelled_first
    first, second = runs.mine_runs(_run_file(tmp_path, video, second=True), threading.Event())
    assert first["error"] == "CANCELLED" and first["file"] == "result-1.json"
    assert second["ok"] is True
    assert not (tmp_path / "ep-01" / "cancel").exists()
    result = _result_file(tmp_path)
    assert result["outcome"] == "cancelled" and {w["status"] for w in result["words"]} == {"not_attempted"}


def _honours_cancel(services):
    """A processor that stops when its run's cancel event is set, as the real checkpoints do."""

    def process(*_a, **kw):
        if kw["cancel_event"].wait(0.5):
            return _result(cancelled=True)
        kw["curation_callback"](services.words())
        return _result()

    return process


def test_cancel_file_already_there_cancels_that_run(services, tmp_path, video) -> None:
    # A Windows caller has no signals: it stops queued episodes by creating their cancel files first.
    (tmp_path / "ep-01").mkdir()
    (tmp_path / "ep-01" / "cancel").touch()
    services.processor.process_episode.side_effect = _honours_cancel(services)
    first, second = runs.mine_runs(_run_file(tmp_path, video, second=True), threading.Event())
    assert first["error"] == "CANCELLED" and second["ok"] is True
    assert not (tmp_path / "ep-01" / "cancel").exists()


def test_signal_before_a_run_cancels_it_without_a_file(services, tmp_path, video) -> None:
    everything = threading.Event()
    everything.set()
    [verdict] = runs.mine_runs(_run_file(tmp_path, video), everything)
    assert verdict["error"] == "CANCELLED" and verdict["file"] is None
    services.processor.process_episode.assert_not_called()


def test_relative_paths_are_taken_from_the_callers_folder(services, tmp_path, video, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    episode = {"run_id": "ep-01", "video_file": "v.mkv", "subtitle_file": "s.srt", "words": WORDS}
    job = files.parse_run_file({"schema": 1, "run_dir": ".", "language": "ja", "episodes": [episode]})
    assert runs.mine_runs(job, threading.Event())[0]["ok"] is True
    assert services.processor.process_episode.call_args.args[0] == tmp_path.resolve() / "v.mkv"


def test_a_video_without_a_container_duration_is_readable(tmp_path, test_config) -> None:
    """A live-mode MKV (stream dump, interrupted recording) plays and mines but
    carries no format.duration; "duration unknown" is not "cannot be opened"."""
    import subprocess

    video = tmp_path / "live.mkv"
    video.write_bytes(b"x")
    probe = json.dumps({"format": {}, "streams": [{"index": 0, "codec_type": "video", "codec_name": "h264"}]})
    episode = SimpleNamespace(video_file=video)
    with patch(
        "anki_miner.utils.audio_track_detector.subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, probe, ""),
    ):
        runs._check_video(test_config, episode)  # must not raise VIDEO_UNREADABLE


def test_each_episodes_words_are_its_runs_whitelist(services, tmp_path, video) -> None:
    runs.mine_runs(_run_file(tmp_path, video), threading.Event())
    assert services.factory.call_args.kwargs["extra_whitelist"] == frozenset({"約束", "無い"})


def test_the_collapse_record_reaches_the_result(services, tmp_path, video) -> None:
    services.processor.last_collapsed = [(_word("無い", 2), "ない")]
    runs.mine_runs(_run_file(tmp_path, video), threading.Event())
    row = _result_file(tmp_path)["words"][1]
    assert (row["word"], row["status"], row["mined_form"], row["filter"]) == (
        "無い",
        "not_found",
        "ない",
        "duplicate-expression",
    )


def test_the_languages_fold_reaches_the_selection(services, tmp_path, video) -> None:
    services.words = lambda: [_word("May", 1)]
    with patch.object(runs, "get_profile", return_value=SimpleNamespace(dedup_fold=str.casefold)):
        runs.mine_runs(_run_file(tmp_path, video, words=[{"word": "MAY"}]), threading.Event())
    assert _result_file(tmp_path)["words"][0]["mined_form"] == "May"  # neither front nor lemma is "MAY"


def test_allowed_duplicate_cards_keep_fold_equal_words_apart(services, tmp_path, video, test_config) -> None:
    services.words = lambda: [_word("May", 1), _word("may", 3)]
    allowed = replace(test_config, allow_duplicate_cards=True)
    with (
        patch.object(runs.settings, "resolve_run_config", return_value=allowed),
        patch.object(runs, "get_profile", return_value=SimpleNamespace(dedup_fold=str.casefold)),
    ):
        runs.mine_runs(_run_file(tmp_path, video, words=[{"word": "May"}, {"word": "may"}]), threading.Event())
    assert [w["mined_form"] for w in _result_file(tmp_path)["words"]] == ["May", "may"]
    assert "duplicate" not in [w["status"] for w in _result_file(tmp_path)["words"]]


def test_a_run_binds_its_line_words_to_the_processor(services, tmp_path, video) -> None:
    services.words = lambda: []
    services.processor.word_on_line.side_effect = lambda word, line, span, **_kw: replace(
        word, sentence=line[2], start_time=line[0], end_time=line[1]
    )
    services.processor.parse_sentence_fn.return_value = []
    services.processor.definition_service.offline_term_readings.return_value = {}
    words = [{"word": "約束", "line_start": 30.0}]
    runs.mine_runs(_run_file(tmp_path, video, words=words), threading.Event())
    row = _result_file(tmp_path)["words"][0]
    assert (row["from_line"], row["line_start"], row["sentence"]) == (True, 30.0, "約束だよ")


def test_a_merged_word_is_never_made_from_its_line(services, tmp_path, video) -> None:
    services.words = lambda: []
    services.processor.last_collapsed = [(_word("約束", 0), "約")]
    words = [{"word": "約束", "line_start": 30.0}]  # line 30.0 holds 約束: only the merge record stops it
    runs.mine_runs(_run_file(tmp_path, video, words=words), threading.Event())
    row = _result_file(tmp_path)["words"][0]
    assert (row["status"], row["filter"], row["from_line"]) == ("not_found", "duplicate-expression", False)
    services.processor.word_on_line.assert_not_called()
