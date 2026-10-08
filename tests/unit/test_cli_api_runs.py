"""--api mine over mocked services: run folders, result files, verdicts, refusals."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests

from anki_miner.cli import runner
from anki_miner.cli.api import files, render, runfolder, runs
from anki_miner.cli.api.contract import ApiError
from anki_miner.exceptions import SubtitleParseError
from anki_miner.models import CANCELLED_ERROR, AnkiWriteState, ProcessingResult
from anki_miner.services import _ankiconnect, anki_service
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.validation_service import ValidationService
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
        patch.object(runs, "AnkiService") as anki_cls,
        patch.object(runs, "ValidationService") as validation,  # Anki answers unless a test says not
        patch.object(runs, "binary_available", return_value=True),
        patch.object(runs, "get_media_duration_seconds", return_value=1.0) as duration,
        patch.object(runs, "get_primary_video_codec", return_value=None),
        patch.object(runs, "create_episode_processor", return_value=processor) as factory,
    ):
        validation.return_value.check_ankiconnect.return_value = (True, "AnkiConnect is running")
        ns.processor = processor
        ns.factory = factory
        ns.duration = duration
        ns.check_card_target = check_card_target
        ns.anki_cls = anki_cls
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


def test_an_unreadable_secondary_subtitle_fails_that_run_without_a_file(services, tmp_path, video) -> None:
    (tmp_path / "en.srt").write_bytes(b"\x00\xff")
    services.processor._load_secondary_entries.side_effect = SubtitleParseError(
        "Secondary subtitle file en.srt: No suitable formats"
    )
    job = _run_file(tmp_path, video, secondary_subtitle_file=str(tmp_path / "en.srt"))
    [verdict] = runs.mine_runs(job, threading.Event())
    assert verdict["error"] == "SUBTITLE_UNREADABLE" and verdict["file"] is None and "en.srt" in verdict["message"]
    services.processor.process_episode.assert_not_called()
    services.processor._load_secondary_entries.assert_called_once_with(tmp_path.resolve() / "en.srt")


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


def test_a_cancel_file_left_by_an_earlier_call_is_cleared(services, tmp_path, video) -> None:
    (tmp_path / "ep-01").mkdir()
    (tmp_path / "ep-01" / "cancel").touch()
    services.processor.process_episode.side_effect = _honours_cancel(services)
    [verdict] = runs.mine_runs(_run_file(tmp_path, video), threading.Event())
    assert verdict["ok"] is True and not (tmp_path / "ep-01" / "cancel").exists()


def test_a_cancel_file_for_a_queued_run_cancels_it_when_it_starts(services, tmp_path, video) -> None:
    # A Windows caller has no signals: it stops queued episodes by creating their cancel files.
    def first_queues_a_cancel(*_a, **kw):
        if services.calls == 0:
            services.calls += 1
            (tmp_path / "ep-02").mkdir()
            (tmp_path / "ep-02" / "cancel").touch()
            kw["curation_callback"](services.words())
            return _result()
        return _honours_cancel(services)(*_a, **kw)

    services.processor.process_episode.side_effect = first_queues_a_cancel
    first, second = runs.mine_runs(_run_file(tmp_path, video, second=True), threading.Event())
    assert first["ok"] is True and second["error"] == "CANCELLED"
    assert not (tmp_path / "ep-02" / "cancel").exists()


def test_an_earlier_calls_progress_file_is_gone_before_the_run(services, tmp_path, video) -> None:
    (tmp_path / "ep-01").mkdir()
    stale = tmp_path / "ep-01" / "progress.json"
    stale.write_text('{"schema": 1, "run_id": "ep-01", "stage": 5, "stages": 5, "done": 3, "total": 3}', "utf-8")
    seen: list[bool] = []

    def process(*_a, **kw):
        seen.append(stale.exists())
        kw["curation_callback"](services.words())
        return _result()

    services.processor.process_episode.side_effect = process
    runs.mine_runs(_run_file(tmp_path, video), threading.Event())
    assert seen == [False]


def test_a_cancel_after_the_last_step_leaves_the_result_and_says_so(services, tmp_path, video, caplog) -> None:
    def finishes_then_a_cancel_lands(*_a, **kw):
        kw["curation_callback"](services.words())
        (tmp_path / "ep-01" / "cancel").touch()
        assert kw["cancel_event"].wait(2)
        return _result()

    services.processor.process_episode.side_effect = finishes_then_a_cancel_lands
    with caplog.at_level("WARNING", logger=runs.logger.name):
        [verdict] = runs.mine_runs(_run_file(tmp_path, video), threading.Event())
    assert verdict["ok"] is True and _result_file(tmp_path)["outcome"] == "success"
    assert not (tmp_path / "ep-01" / "cancel").exists()
    assert any("ep-01" in r.getMessage() and "after its last step" in r.getMessage() for r in caplog.records)


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
    with patch(
        "anki_miner.utils.audio_track_detector.subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, probe, ""),
    ):
        runs.check_video(test_config, video)  # must not raise VIDEO_UNREADABLE


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


def _dry(tmp_path, video, **episode) -> files.RunFile:
    return replace(_run_file(tmp_path, video, **episode), dry_run=True)


def test_a_dry_run_needs_no_anki_nor_ffmpeg_and_cuts_nothing(services, tmp_path, video, caplog) -> None:
    """Review Focus 2: Anki closed."""
    services.anki_cls.return_value.duplicate_fronts.side_effect = runs.AnkiConnectionError("down")
    with patch.object(runs, "binary_available", return_value=False):
        [verdict] = runs.mine_runs(_dry(tmp_path, video), threading.Event(), runs.Kind.DRY_RUN)
    assert verdict["ok"] is True
    assert "duplicate check failed" in caplog.text  # the log explains a ready the probe never confirmed
    result = _result_file(tmp_path)
    assert result["dry_run"] is True and result["anki_write_state"] == "no_note_write"
    assert [w["status"] for w in result["words"]] == ["ready", "not_found"]
    anki = services.factory.call_args.kwargs["anki_service"]
    assert isinstance(anki, runs._OfflineAnki)
    assert isinstance(services.check_card_target.call_args.args[1], runs._OfflineAnki)  # no card-target check
    services.duration.assert_not_called()  # no video check


def test_a_dry_run_reports_what_anki_already_has(services, tmp_path, video) -> None:
    services.anki_cls.return_value.duplicate_fronts.return_value = {"約束"}
    runs.mine_runs(_dry(tmp_path, video), threading.Event(), runs.Kind.DRY_RUN)
    assert _result_file(tmp_path)["words"][0]["status"] == "duplicate"
    [cancelled] = services.anki_cls.return_value.set_cancelled_check.call_args.args
    assert cancelled() is False  # the run's cancel event, so a cancel cuts a retry wait short


def test_a_dry_run_cancelled_during_its_duplicate_check_is_cancelled(services, tmp_path, video) -> None:
    anki = services.anki_cls.return_value

    def cancel_mid_check(_payloads):
        (tmp_path / "ep-01" / "cancel").touch()
        [cancelled] = anki.set_cancelled_check.call_args.args
        deadline = time.monotonic() + 2
        while not cancelled() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert cancelled()
        return set()  # the check cut short: nothing confirmed

    anki.duplicate_fronts.side_effect = cancel_mid_check
    [verdict] = runs.mine_runs(_dry(tmp_path, video), threading.Event(), runs.Kind.DRY_RUN)
    assert verdict["error"] == "CANCELLED" and verdict["file"] == "result-1.json", verdict
    assert not (tmp_path / "ep-01" / "cancel").exists()
    result = _result_file(tmp_path)
    assert (result["outcome"], result["error"]) == ("cancelled", "CANCELLED")
    assert [w["status"] for w in result["words"]] == ["not_attempted", "not_found"]


def _dry_made_row(services, tmp_path, video, *, in_anki: set[str]) -> dict:
    """A dry run's row for 約束 made from line 30.0, which no offline dictionary defines."""
    services.words = lambda: []
    services.processor.word_on_line.side_effect = lambda word, line, span, **_kw: replace(
        word, sentence=line[2], start_time=line[0], end_time=line[1]
    )
    services.processor.parse_sentence_fn.return_value = []
    services.processor.definition_service.offline_term_readings.return_value = {}
    services.processor.definition_viable.return_value = [False]
    services.anki_cls.return_value.duplicate_fronts.return_value = in_anki
    words = [{"word": "約束", "line_start": 30.0}]
    runs.mine_runs(_dry(tmp_path, video, words=words), threading.Event(), runs.Kind.DRY_RUN)
    return _result_file(tmp_path)["words"][0]


def test_a_dry_run_checks_a_made_words_definition(services, tmp_path, video) -> None:
    row = _dry_made_row(services, tmp_path, video, in_anki=set())
    assert (row["status"], row["from_line"]) == ("no_definition", True)


def test_in_a_dry_run_no_definition_beats_duplicate(services, tmp_path, video) -> None:
    """mine never reaches Anki's check for a word phase 4 drops."""
    assert _dry_made_row(services, tmp_path, video, in_anki={"約束"})["status"] == "no_definition"


def test_a_dry_run_with_anki_closed_asks_once_and_never_waits(services, tmp_path, video) -> None:
    """Review Focus 2: the probe's retries (3 tries, 8 s apart) would hold every episode ~16 s, past a cancel."""

    def no_wait(_seconds):
        raise AssertionError("the dry run waited to retry a closed Anki")

    with (
        patch.object(runs, "AnkiService", AnkiService),
        patch.object(runs, "ValidationService", ValidationService),
        patch.object(_ankiconnect, "_post", side_effect=requests.exceptions.ConnectionError("refused")) as post,
        patch.object(anki_service.time, "sleep", side_effect=no_wait),
    ):
        [verdict] = runs.mine_runs(_dry(tmp_path, video), threading.Event(), runs.Kind.DRY_RUN)
    assert verdict["ok"] is True, verdict
    assert [w["status"] for w in _result_file(tmp_path)["words"]] == ["ready", "not_found"]
    assert post.call_count == 1  # one refused connection for the episode


def test_a_real_mine_says_dry_run_false(services, tmp_path, video) -> None:
    runs.mine_runs(_run_file(tmp_path, video), threading.Event())
    assert _result_file(tmp_path)["dry_run"] is False


def test_render_writes_render_n_beside_the_results_and_no_result_file(services, tmp_path, video) -> None:
    def process(*_args, **kwargs):
        kwargs["curation_callback"](services.words())
        anki = services.factory.call_args.kwargs["anki_service"]
        anki.rendered = {"約束": render.Rendered(fields={"Word": "約束"}, files=["約束_x.jpg"])}
        return _result()

    services.processor.process_episode.side_effect = process
    [verdict] = runs.mine_runs(_run_file(tmp_path, video), threading.Event(), runs.Kind.RENDER)
    assert verdict["file"] == "render-1.json"
    assert not (tmp_path / "ep-01" / "result-1.json").exists()
    report = json.loads((tmp_path / "ep-01" / "render-1.json").read_text(encoding="utf-8"))
    assert "dry_run" not in report and "anki_write_state" not in report
    made, missing = report["words"]
    assert (made["status"], made["fields"], made["files"]) == ("rendered", {"Word": "約束"}, ["render-1/約束_x.jpg"])
    assert (missing["status"], missing["fields"], missing["files"]) == ("not_found", None, [])
    anki = services.factory.call_args.kwargs["anki_service"]
    assert isinstance(anki, render.RenderService)
    services.check_card_target.assert_called_once()  # the note type is checked as for mine


def test_a_failed_calls_leftover_folder_takes_its_number(tmp_path) -> None:
    """Review Focus 4: a render or media call that died leaves <stem>-<n>/ and no json behind."""
    (tmp_path / "render-1").mkdir()
    (tmp_path / "result-3.json").write_text("{}", encoding="utf-8")
    assert runfolder.next_numbered(tmp_path, "render").name == "render-2.json"
    assert runfolder.next_numbered(tmp_path, "result").name == "result-4.json"
    assert runfolder.next_numbered(tmp_path, "media").name == "media-1.json"
