"""sync: a book timed to its audiobook (API.md, "sync").

Per episode, what Utilities -> Audiobook Sync does for one book: each audio
file, in the order given, is transcribed and aligned to the book's own
sentences from where the previous file stopped (one ``BookCursor``, one model
session), into ``<run_dir>/<run_id>/sync-<n>/<audio stem>.srt``; then the
book's report. The book is read as Reading reads it (``open_reading``), so a
cue's ``index`` is the unit index ``mine`` and ``inspect`` use. Nothing goes to
Anki and no lock is held, as for ``transcribe``.
"""

from __future__ import annotations

import threading
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path

from anki_miner.cli.api import files
from anki_miner.cli.api.contract import AUDIO_UNREADABLE, CANCELLED, SOURCE_UNREADABLE, ApiError
from anki_miner.cli.api.files import JobsFile
from anki_miner.cli.api.jobs import Run, job_runs
from anki_miner.cli.api.sources import check_audio, open_reading
from anki_miner.cli.api.transcribe import CANCELLED_MESSAGE, asr_language, prepare_speech, transcribing_progress
from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.service_factory import create_line_parser
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.asr.transcriber import Ct2ModelSession
from anki_miner.services.book_sync.aligner import BookCursor, BookText
from anki_miner.services.book_sync.normalize import normalize_for_alignment
from anki_miner.services.book_sync.pipeline import BookSyncResult, BookSyncStatus, sync_one
from anki_miner.services.book_sync.report import book_unheard, build_report
from anki_miner.services.media_extractor import MediaExtractorService

_KEYS = frozenset({"run_id", "book", "audio"})
_BOOK_SUFFIXES = (".epub", ".txt")
#: A file's ``status`` in sync-<n>.json; EXTRACTION_FAILED and CANCELLED end the run instead.
_STATUS = {
    BookSyncStatus.SUCCESS: "synced",
    BookSyncStatus.NO_SPEECH: "no_speech",
    BookSyncStatus.NO_MATCH: "no_match",
}


@dataclass(frozen=True)
class SyncEpisode:
    run_id: str
    book: Path
    #: In reading order, as the caller sorted them.
    audio: tuple[Path, ...]


def _episode(raw: object, where: str) -> SyncEpisode:
    obj = files.json_object(raw, where)
    files.check_keys(obj, _KEYS, _KEYS, where)
    book = files.abs_path(files.text(obj["book"], f"{where}.book"))
    if book.suffix.lower() not in _BOOK_SUFFIXES:
        raise files.bad(f"{where}.book must be an .epub or .txt file.")
    raw_audio = obj["audio"]
    if isinstance(raw_audio, str):
        raw_audio = [raw_audio]
    if not isinstance(raw_audio, list) or not raw_audio:
        raise files.bad(f"{where}.audio must be a path or a non-empty list of paths.")
    audio = tuple(files.abs_path(files.text(path, f"{where}.audio[{j}]")) for j, path in enumerate(raw_audio))
    return SyncEpisode(files.valid_run_id(obj["run_id"], f"{where}.run_id"), book, audio)


def call(sync_file: Path, cancel_all: threading.Event) -> list[dict[str, object]]:
    """``sync SYNC_FILE``: the file checked, then its runs."""
    return sync_runs(files.parse_jobs_file(files.read_json_file(sync_file), "sync", _episode), cancel_all)


def sync_runs(job: JobsFile[SyncEpisode], cancel_all: threading.Event) -> list[dict[str, object]]:
    """Every episode of *job* synced in turn; a sync-<n>.json for each that ends."""
    return job_runs(job, "sync", cancel_all, partial(prepare_speech, job), _sync_book)


def _sync_book(config: AnkiMinerConfig, episode: SyncEpisode, run: Run) -> dict[str, object]:
    run_config = replace(config, media_temp_folder=run.media)
    for audio in episode.audio:  # every file before the first hour of transcription
        check_audio(run_config, audio)
    doc = open_reading(run_config, create_line_parser(run_config), episode.book, cancel_check=run.cancel.is_set)
    units = doc.units
    if not units:
        raise ApiError(SOURCE_UNREADABLE, f"{episode.book.name} contains no text to sync.")
    # load_book's BookText, from the units mine and inspect see: sentence i is units[i].
    book = BookText(
        title=doc.title,
        sentences=tuple(unit.text for unit in units),
        keys=tuple(normalize_for_alignment(unit.text) for unit in units),
    )
    extractor = MediaExtractorService(run_config)
    cursor = BookCursor()
    stages = 2 * len(episode.audio)
    results: list[BookSyncResult] = []
    session = Ct2ModelSession()
    try:
        for k, (audio, name) in enumerate(zip(episode.audio, _srt_names(episode.audio), strict=True)):
            transcribing = transcribing_progress(run.progress, 2 * k + 1, stages)
            transcribing(0.0)  # extraction is part of this file's transcribing stage
            result = sync_one(
                run_config,
                extractor,
                audio,
                book,
                cursor,
                run.out / name,
                on_align_start=partial(run.progress.on_stage, 2 * k + 2, stages, "align"),
                transcribe_progress_cb=transcribing,
                cancel_event=run.cancel,
                ct2_model_session=session,
                language=asr_language(config),
            )
            if result.status is BookSyncStatus.CANCELLED:
                raise ApiError(CANCELLED, CANCELLED_MESSAGE)
            if result.status is BookSyncStatus.EXTRACTION_FAILED:
                raise ApiError(AUDIO_UNREADABLE, f"The audio could not be extracted from {audio}")
            results.append(result)
    finally:
        session.release()
    return _record(episode, units, cursor, results)


def _srt_names(audio: Sequence[Path]) -> list[str]:
    """``<stem>.srt`` per audio file; a stem met again (two ``01.mp3`` from different folders) gets ``-2``, ``-3``…

    Compared as a case- and normalization-insensitive file system compares them.
    """
    taken: set[str] = set()
    names: list[str] = []
    for path in audio:
        name, n = f"{path.stem}.srt", 1
        while (key := unicodedata.normalize("NFC", name).casefold()) in taken:
            n += 1
            name = f"{path.stem}-{n}.srt"
        taken.add(key)
        names.append(name)
    return names


def _record(
    episode: SyncEpisode, units: Sequence[ReadingUnit], cursor: BookCursor, results: Sequence[BookSyncResult]
) -> dict[str, object]:
    """sync-<n>.json: each file's subtitle and cues, then the book's report (Book Sync's, MS-24/MS-25)."""
    report = build_report(
        episode.book.name,
        cursor,
        sum(result.speech_seconds for result in results),
        sum(result.matched_seconds for result in results),
    )
    gaps = book_unheard((gap for result in results for gap in result.unheard), cursor.timed)
    return {
        "files": [
            {
                "audio_file": str(audio),
                "subtitle_file": str(result.out_srt) if result.out_srt is not None else None,
                "status": _STATUS[result.status],
                # Cue n of the SRT is timings[n - 1].
                "cues": [{"n": n, "index": units[t.index].index} for n, t in enumerate(result.timings, start=1)],
            }
            for audio, result in zip(episode.audio, results, strict=True)
        ],
        "sentence_pct": report.sentence_pct if report is not None else None,
        "audio_pct": report.audio_pct if report is not None else None,
        "grade": report.grade if report is not None else None,
        "unheard": [{"first": units[first].index, "last": units[last].index} for first, last in gaps],
    }
