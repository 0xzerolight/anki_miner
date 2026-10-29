"""--api sync over a fake sync_one and a real book: the files, cue indices, the report, codes, refusals.

The book is a real ``.txt`` read through ``open_reading``, so a cue's ``index``
is checked against the units ``mine`` and ``inspect`` read. ``sync_one`` is
faked: it times the sentences the test names for each audio file, as the real
one would (cursor, unheard gaps, SRT).
"""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner.cli.api import sync, transcribe
from anki_miner.cli.api.contract import AUDIO_UNREADABLE, ApiError
from anki_miner.cli.api.sources import open_reading
from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.service_factory import create_line_parser
from anki_miner.services.asr import srt_writer
from anki_miner.services.book_sync.aligner import SentenceTiming
from anki_miner.services.book_sync.pipeline import BookSyncResult, BookSyncStatus
from anki_miner.services.book_sync.report import unheard_gaps

NEKO = (
    "吾輩は猫である\n\n"
    "　吾輩は猫である。名前はまだ無い。\n"
    "　どこで生れたかとんと見当がつかぬ。何でも薄暗いじめじめした所でニャーニャー泣いていた事だけは記憶している。\n"
)
NAMAE = "名前はまだ無い。"
DOKO = "どこで生れたかとんと見当がつかぬ。"
NANDEMO = "何でも薄暗いじめじめした所でニャーニャー泣いていた事だけは記憶している。"
WAGAHAI = "吾輩は猫である。"


class _Sync:
    """sync_one: times the sentences *plan* names for each audio file (by its path), 10 s of speech, 9 s in cues."""

    def __init__(self, plan: dict[Path, list[str]], *, statuses: dict[Path, BookSyncStatus] | None = None):
        # Keyed as the episode names them: made absolute.
        self.plan = {path.resolve(): texts for path, texts in plan.items()}
        self.statuses = {path.resolve(): status for path, status in (statuses or {}).items()}
        self.calls: list[dict] = []
        self.progress_seen: list[tuple[int, int]] = []

    def __call__(self, config, extractor, audio, book, cursor, out_srt, **kwargs):
        self.calls.append({"config": config, "audio": audio, "book": book, "out_srt": out_srt, **kwargs})
        self._seen(out_srt)
        kwargs["transcribe_progress_cb"](1.0)
        status = self.statuses.get(audio, BookSyncStatus.SUCCESS)
        if status is not BookSyncStatus.SUCCESS:
            return BookSyncResult(status)
        kwargs["on_align_start"]()
        self._seen(out_srt)
        already = set(cursor.timed)
        timings = [
            SentenceTiming(book.sentences.index(text), 2.0 * i, 2.0 * i + 1.5)
            for i, text in enumerate(self.plan[audio])
        ]
        cursor.timed.update(t.index for t in timings)
        cursor.sentence = max(t.index for t in timings) + 1
        out_srt.parent.mkdir(parents=True, exist_ok=True)
        srt_writer.segments_to_srt([(t.start, t.end, book.sentences[t.index]) for t in timings], out_srt)
        return BookSyncResult(
            BookSyncStatus.SUCCESS,
            out_srt=out_srt,
            cues=len(timings),
            speech_seconds=10.0,
            matched_seconds=9.0,
            timings=tuple(timings),
            unheard=tuple(unheard_gaps(timings, known=already)),
        )

    def _seen(self, out_srt: Path) -> None:
        progress = json.loads((out_srt.parent.parent / "progress.json").read_text(encoding="utf-8"))
        self.progress_seen.append((progress["stage"], progress["stages"]))


class _Session:
    made: list[_Session] = []

    def __init__(self) -> None:
        self.released = False
        _Session.made.append(self)

    def release(self) -> None:
        self.released = True


@pytest.fixture(autouse=True)
def tools(monkeypatch):
    monkeypatch.setattr(transcribe, "speech_problem", lambda config: None)
    monkeypatch.setattr(transcribe, "require_ffmpeg", lambda config: None)
    monkeypatch.setattr(sync, "MediaExtractorService", lambda config: object())
    monkeypatch.setattr(sync, "check_audio", lambda config, path: None)
    _Session.made = []
    monkeypatch.setattr(sync, "Ct2ModelSession", _Session)


@pytest.fixture
def book(tmp_path) -> Path:
    path = tmp_path / "猫.txt"
    path.write_text(NEKO, encoding="utf-8")
    return path


def _use(monkeypatch, fake: _Sync) -> _Sync:
    monkeypatch.setattr(sync, "sync_one", fake)
    return fake


def _file(tmp_path: Path, language: str = "ja", **episode) -> dict:
    return {"schema": 1, "run_dir": str(tmp_path), "language": language, "episodes": [{"run_id": "bk-1", **episode}]}


def _job(tmp_path: Path, language: str = "ja", **episode):
    return sync.files.parse_jobs_file(_file(tmp_path, language, **episode), "sync", sync._episode)


def _run(tmp_path: Path, language: str = "ja", **episode) -> dict:
    [verdict] = sync.sync_runs(_job(tmp_path, language, **episode), threading.Event())
    return verdict


def _record(tmp_path: Path, n: int = 1) -> dict:
    return json.loads((tmp_path / "bk-1" / f"sync-{n}.json").read_text(encoding="utf-8"))


def _unit_index(book: Path, text: str, language: str = "ja") -> int:
    """The unit index mine and inspect give *text*: open_reading with the line parser."""
    config = replace(AnkiMinerConfig(), language=language)
    doc = open_reading(config, create_line_parser(config), book, cancel_check=lambda: False)
    return next(unit.index for unit in doc.units if unit.text == text)


def test_two_audio_files_give_two_subtitles_with_the_books_unit_indices(monkeypatch, tmp_path, book) -> None:
    first, second = tmp_path / "a" / "01.mp3", tmp_path / "b" / "01.mp3"
    fake = _use(monkeypatch, _Sync({first: [WAGAHAI, DOKO], second: [NANDEMO]}))
    verdict = _run(tmp_path, book=str(book), audio=[str(first), str(second)])
    assert verdict == {
        "run_id": "bk-1",
        "ok": True,
        "error": None,
        "message": None,
        "file": "sync-1.json",
        "failure_is_transient": False,
    }
    out = tmp_path.resolve() / "bk-1" / "sync-1"
    record = _record(tmp_path)
    assert record["files"] == [
        {
            "audio_file": str(first.resolve()),
            "subtitle_file": str(out / "01.srt"),
            "status": "synced",
            "cues": [{"n": 1, "index": _unit_index(book, WAGAHAI)}, {"n": 2, "index": _unit_index(book, DOKO)}],
        },
        {
            "audio_file": str(second.resolve()),
            "subtitle_file": str(out / "01-2.srt"),  # the same stem from another folder
            "status": "synced",
            "cues": [{"n": 1, "index": _unit_index(book, NANDEMO)}],
        },
    ]
    assert [_unit_index(book, t) for t in (WAGAHAI, DOKO, NANDEMO)] == [1, 3, 4]  # the title line is unit 0
    # 名前はまだ無い。 sits between two timed sentences of the first file: the narrator skipped it.
    assert record["unheard"] == [{"first": _unit_index(book, NAMAE), "last": _unit_index(book, NAMAE)}]
    # Sentences 1-4 spanned, 3 timed: 75 %; 18 of 20 s of speech in cues: 90 %; the weaker gives B.
    assert (record["sentence_pct"], record["audio_pct"], record["grade"]) == (75, 90, "B")
    srt = (out / "01.srt").read_text(encoding="utf-8")
    assert srt.index(WAGAHAI) < srt.index(DOKO) and NAMAE not in srt
    assert [call["audio"] for call in fake.calls] == [first.resolve(), second.resolve()]


def test_one_cursor_and_one_model_session_cover_every_file(monkeypatch, tmp_path, book) -> None:
    first, second = tmp_path / "01.mp3", tmp_path / "02.mp3"
    fake = _use(monkeypatch, _Sync({first: [WAGAHAI], second: [NAMAE]}))
    assert _run(tmp_path, book=str(book), audio=[str(first), str(second)])["ok"]
    [session] = _Session.made
    assert [call["ct2_model_session"] for call in fake.calls] == [session, session] and session.released
    assert fake.calls[0]["book"] is fake.calls[1]["book"]
    assert {call["config"].media_temp_folder for call in fake.calls} == {tmp_path.resolve() / "bk-1" / "media"}
    assert [call["language"] for call in fake.calls] == ["ja", "ja"]
    # File k is stage 2k-1 while transcribing, 2k while aligning, of 2 per file.
    assert fake.progress_seen == [(1, 4), (2, 4), (3, 4), (4, 4)]
    assert not (tmp_path / "bk-1" / "media").exists()


def test_a_sentence_a_later_file_timed_is_not_unheard(monkeypatch, tmp_path, book) -> None:
    """A passage read out of order (MS-24): the second file times what the first skipped."""
    first, second = tmp_path / "01.mp3", tmp_path / "02.mp3"
    _use(monkeypatch, _Sync({first: [WAGAHAI, DOKO], second: [NAMAE]}))
    assert _run(tmp_path, book=str(book), audio=[str(first), str(second)])["ok"]
    assert _record(tmp_path)["unheard"] == []


def test_files_without_cues_say_why_and_a_book_nothing_timed_has_no_grade(monkeypatch, tmp_path, book) -> None:
    first, second = tmp_path / "intro.mp3", tmp_path / "music.mp3"
    statuses = {first: BookSyncStatus.NO_MATCH, second: BookSyncStatus.NO_SPEECH}
    _use(monkeypatch, _Sync({}, statuses=statuses))
    assert _run(tmp_path, book=str(book), audio=[str(first), str(second)])["ok"]
    record = _record(tmp_path)
    assert [(f["status"], f["subtitle_file"], f["cues"]) for f in record["files"]] == [
        ("no_match", None, []),
        ("no_speech", None, []),
    ]
    assert (record["sentence_pct"], record["audio_pct"], record["grade"], record["unheard"]) == (None, None, None, [])
    assert not (tmp_path / "bk-1" / "sync-1").exists()


def test_a_non_japanese_book_is_transcribed_in_its_language(monkeypatch, tmp_path) -> None:
    from anki_miner.languages.registry import get_profile

    book = tmp_path / "고양이.txt"
    book.write_text(
        "나는 고양이로소이다. 이름은 아직 없다.\n어디서 태어났는지 도무지 짐작이 가지 않는다.\n", encoding="utf-8"
    )
    audio = tmp_path / "01.mp3"
    fake = _use(monkeypatch, _Sync({audio: ["이름은 아직 없다."]}))
    assert _run(tmp_path, language="ko", book=str(book), audio=str(audio))["ok"]
    assert fake.calls[0]["language"] == get_profile("ko").asr_language == "ko"
    assert _record(tmp_path)["files"][0]["cues"] == [{"n": 1, "index": _unit_index(book, "이름은 아직 없다.", "ko")}]


def test_an_unreadable_audio_file_refuses_the_run_before_any_transcription(monkeypatch, tmp_path, book) -> None:
    first, broken = tmp_path / "01.mp3", tmp_path / "02.mp3"

    def check(config, path):
        if path == broken:
            raise ApiError(AUDIO_UNREADABLE, f"The audio file cannot be opened: {path}")

    monkeypatch.setattr(sync, "check_audio", check)
    fake = _use(monkeypatch, _Sync({first: [WAGAHAI]}))
    verdict = _run(tmp_path, book=str(book), audio=[str(first), str(broken)])
    assert verdict["error"] == "AUDIO_UNREADABLE" and "02.mp3" in verdict["message"] and verdict["file"] is None
    assert fake.calls == []


def test_audio_that_cannot_be_extracted_is_audio_unreadable(monkeypatch, tmp_path, book) -> None:
    first, second = tmp_path / "01.mp3", tmp_path / "02.mp3"
    _use(monkeypatch, _Sync({first: [WAGAHAI]}, statuses={second: BookSyncStatus.EXTRACTION_FAILED}))
    verdict = _run(tmp_path, book=str(book), audio=[str(first), str(second)])
    assert verdict["error"] == "AUDIO_UNREADABLE" and "02.mp3" in verdict["message"]
    assert not (tmp_path / "bk-1" / "sync-1").exists() and not (tmp_path / "bk-1" / "sync-1.json").exists()
    assert _Session.made[0].released


def test_a_book_that_does_not_load_is_source_unreadable(monkeypatch, tmp_path) -> None:
    fake = _use(monkeypatch, _Sync({}))
    verdict = _run(tmp_path, book=str(tmp_path / "missing.epub"), audio=str(tmp_path / "01.mp3"))
    assert verdict["error"] == "SOURCE_UNREADABLE" and fake.calls == []


def test_a_cancel_file_cancels_the_run_and_leaves_no_json(monkeypatch, tmp_path, book) -> None:
    first, second = tmp_path / "01.mp3", tmp_path / "02.mp3"

    class _Cancelled(_Sync):
        def __call__(self, config, extractor, audio, book, cursor, out_srt, **kwargs):
            if audio != second:
                return super().__call__(config, extractor, audio, book, cursor, out_srt, **kwargs)
            (tmp_path / "bk-1" / "cancel").write_text("", encoding="utf-8")  # the caller stops the second file
            kwargs["cancel_event"].wait(5.0)
            return BookSyncResult(BookSyncStatus.CANCELLED)

    _use(monkeypatch, _Cancelled({first: [WAGAHAI]}))
    verdict = _run(tmp_path, book=str(book), audio=[str(first), str(second)])
    assert verdict["error"] == "CANCELLED" and verdict["file"] is None
    assert not (tmp_path / "bk-1" / "cancel").exists()
    # The first file's subtitle goes with its folder.
    assert not (tmp_path / "bk-1" / "sync-1").exists() and not (tmp_path / "bk-1" / "sync-1.json").exists()
    assert _Session.made[0].released


def test_a_missing_speech_model_refuses_the_call_before_any_run(monkeypatch, tmp_path, book) -> None:
    monkeypatch.setattr(transcribe, "speech_problem", lambda config: "The speech model large-v3 is not installed.")
    fake = _use(monkeypatch, _Sync({}))
    with pytest.raises(ApiError) as err:
        sync.sync_runs(_job(tmp_path, book=str(book), audio=str(tmp_path / "01.mp3")), threading.Event())
    assert err.value.code == "SETUP_ERROR" and "speech model" in err.value.message
    assert fake.calls == [] and not (tmp_path / "bk-1").exists()


@pytest.mark.parametrize(
    "episode",
    [
        {"book": "猫.pdf", "audio": "01.mp3"},
        {"book": "猫.srt", "audio": "01.mp3"},
        {"book": "猫.epub", "audio": []},
        {"book": "猫.epub", "audio": ["01.mp3", 2]},
        {"book": "猫.epub", "audio": {"file": "01.mp3"}},
        {"book": "猫.epub"},
        {"book": "猫.epub", "audio": "01.mp3", "subtitle_file": "a.srt"},
    ],
)
def test_a_bad_episode_refuses_the_call(tmp_path, episode) -> None:
    with pytest.raises(ApiError) as err:
        _job(tmp_path, **episode)
    assert err.value.code == "BAD_RUN_FILE"


def test_one_audio_path_is_a_list_of_one(tmp_path) -> None:
    [episode] = _job(tmp_path, book="猫.EPUB", audio="01.mp3").episodes
    assert episode.audio == (Path("01.mp3").resolve(),)


def test_repeated_stems_get_their_own_subtitle_names() -> None:
    names = sync._srt_names([Path("a/01.mp3"), Path("b/01.MP3"), Path("c/01-2.mp3"), Path("d/02.m4a")])
    assert names == ["01.srt", "01-2.srt", "01-2-2.srt", "02.srt"]


def test_the_sync_command_runs_beside_an_open_window(capfd, monkeypatch, tmp_path) -> None:
    from anki_miner.cli import api, entry
    from anki_miner.config import paths as config_paths
    from anki_miner.gui.app import _hold_window_marker

    monkeypatch.setattr(api, "_prepare_process", lambda: None)
    monkeypatch.setattr(entry, "_install_api_log", lambda: None)
    ok = {"run_id": "bk-1", "ok": True, "error": None, "message": None, "file": "sync-1.json"}
    monkeypatch.setattr(sync, "sync_runs", lambda job, cancel: [ok])
    path = tmp_path / "sync.json"
    path.write_text(json.dumps(_file(tmp_path, book="猫.epub", audio=["01.mp3"])), encoding="utf-8")
    config_paths.ANKI_MINER_HOME.mkdir(parents=True, exist_ok=True)
    marker = _hold_window_marker(config_paths.ANKI_MINER_HOME)
    try:
        assert entry.main(["--api", "sync", str(path)]) == 0
    finally:
        marker.unlock()
    [line] = capfd.readouterr().out.splitlines()
    verdict = json.loads(line)
    assert verdict["ok"] is True and verdict["command"] == "sync" and verdict["runs"] == [ok]
