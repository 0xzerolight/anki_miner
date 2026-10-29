"""Per-file sync policy (services/book_sync/pipeline.py): load the book, transcribe, align, write.

The reading loader, the long-audio transcriber and the aligner are patched at
their canonical modules; the SRT writer runs for real into tmp_path.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SetupError
from anki_miner.models.reading import ReadingDocument, ReadingSourceRef, ReadingUnit
from anki_miner.services.book_sync.aligner import BookCursor, BookText, SentenceTiming
from anki_miner.services.book_sync.pipeline import BookSyncStatus, load_book, sync_one
from anki_miner.services.book_sync.report import UnheardGap

_SENTENCES = ("吾輩は猫である。", "名前はまだ無い。", "どこで生れたかとんと見当がつかぬ。")


def _make_config(tmp_path: Path) -> AnkiMinerConfig:
    return AnkiMinerConfig(asr_models_root=tmp_path / "models", media_temp_folder=tmp_path / "temp")


def _doc(units=_SENTENCES, title="猫") -> ReadingDocument:
    doc = ReadingDocument(title=title, kind="book", series="Books", episode=title)
    doc.units = [ReadingUnit(text=t, index=i, location_label="ch.0") for i, t in enumerate(units)]
    return doc


def _book() -> BookText:
    from anki_miner.services.book_sync.normalize import normalize_for_alignment

    return BookText(title="猫", sentences=_SENTENCES, keys=tuple(normalize_for_alignment(s) for s in _SENTENCES))


# --- load_book -----------------------------------------------------------------


def test_load_book_epub_yields_sentences_and_keys(tmp_path, monkeypatch):
    import anki_miner.services.reading.detector as det

    book_path = tmp_path / "neko.epub"
    seen: dict = {}
    monkeypatch.setattr(det, "detect", lambda p: [ReadingSourceRef(kind="epub", path=p, title="neko")])

    def fake_load(ref, **kwargs):
        seen.update(kwargs)
        return _doc()

    monkeypatch.setattr(det, "load", fake_load)
    rules = object()

    book = load_book(book_path, rules=rules, encodings=("utf-8",), cancel_check=lambda: False)  # type: ignore[arg-type]

    assert book.title == "猫"
    assert book.sentences == _SENTENCES
    assert book.keys[0] == "吾輩わ猫である"
    assert seen["rules"] is rules and seen["encodings"] == ("utf-8",)


def test_load_book_rejects_non_book_sources(tmp_path, monkeypatch):
    import anki_miner.services.reading.detector as det

    monkeypatch.setattr(det, "detect", lambda p: [ReadingSourceRef(kind="subtitle", path=p)])
    with pytest.raises(SetupError, match="not a book"):
        load_book(tmp_path / "x.srt", rules=None, encodings=None)


def test_load_book_rejects_empty_document(tmp_path, monkeypatch):
    import anki_miner.services.reading.detector as det

    monkeypatch.setattr(det, "detect", lambda p: [ReadingSourceRef(kind="txt", path=p)])
    monkeypatch.setattr(det, "load", lambda ref, **kw: _doc(units=()))
    with pytest.raises(SetupError, match="no text"):
        load_book(tmp_path / "empty.txt", rules=None, encodings=None)


# --- sync_one ------------------------------------------------------------------


def _patch_transcribe(monkeypatch, status="OK", segments=None):
    import anki_miner.services.asr.long_audio as la

    segs = [
        (0.0, 2.0, "吾輩は猫である"),
        (2.0, 4.0, "名前はまだない"),
        (4.0, 7.0, "どこで生まれたかとんと見当がつかぬ"),
    ]

    def fake(config, extractor, media, **kwargs):
        if kwargs.get("on_extract_start"):
            kwargs["on_extract_start"]()
        if kwargs.get("on_transcribe_start"):
            kwargs["on_transcribe_start"]()
        if kwargs.get("progress_cb"):
            kwargs["progress_cb"](1.0)
        return la.LongAudioResult(getattr(la.LongAudioStatus, status), segs if segments is None else segments)

    monkeypatch.setattr(la, "transcribe_media", fake)


def test_sync_one_writes_one_cue_per_matched_sentence(tmp_path, monkeypatch):
    _patch_transcribe(monkeypatch)
    out = tmp_path / "neko.srt"
    stages: list[str] = []
    cursor = BookCursor()

    result = sync_one(
        _make_config(tmp_path),
        object(),
        tmp_path / "neko.m4b",
        _book(),
        cursor,
        out,
        on_extract_start=lambda: stages.append("extract"),
        on_transcribe_start=lambda: stages.append("transcribe"),
        on_align_start=lambda: stages.append("align"),
    )

    assert result.status is BookSyncStatus.SUCCESS and result.out_srt == out
    assert result.cues == 3 and result.unmatched_sentences == 0
    assert stages == ["extract", "transcribe", "align"]
    text = out.read_text(encoding="utf-8")
    for sentence in _SENTENCES:
        assert sentence in text  # the book's own sentence, punctuation intact
    assert cursor.sentence == 3


def test_sync_one_counts_untimed_sentences_between_timed_ones(tmp_path, monkeypatch):
    _patch_transcribe(monkeypatch)
    import anki_miner.services.book_sync.pipeline as pl

    monkeypatch.setattr(
        pl, "align_to_book", lambda segs, book, cursor, **kw: [SentenceTiming(0, 0.0, 2.0), SentenceTiming(2, 4.0, 7.0)]
    )
    result = sync_one(_make_config(tmp_path), object(), tmp_path / "a.mp3", _book(), BookCursor(), tmp_path / "a.srt")
    assert result.cues == 2 and result.unmatched_sentences == 1


@pytest.mark.parametrize(
    ("status", "expected"),
    [("EXTRACTION_FAILED", BookSyncStatus.EXTRACTION_FAILED), ("CANCELLED", BookSyncStatus.CANCELLED)],
)
def test_sync_one_maps_transcription_statuses(tmp_path, monkeypatch, status, expected):
    _patch_transcribe(monkeypatch, status=status, segments=[])
    result = sync_one(_make_config(tmp_path), object(), tmp_path / "a.mp3", _book(), BookCursor(), tmp_path / "a.srt")
    assert result.status is expected
    assert not (tmp_path / "a.srt").exists()


def test_sync_one_reports_no_speech(tmp_path, monkeypatch):
    _patch_transcribe(monkeypatch, segments=[])
    result = sync_one(_make_config(tmp_path), object(), tmp_path / "a.mp3", _book(), BookCursor(), tmp_path / "a.srt")
    assert result.status is BookSyncStatus.NO_SPEECH


def test_sync_one_reports_no_match(tmp_path, monkeypatch):
    _patch_transcribe(monkeypatch, segments=[(0.0, 5.0, "全然違う話をしている")])
    result = sync_one(_make_config(tmp_path), object(), tmp_path / "a.mp3", _book(), BookCursor(), tmp_path / "a.srt")
    assert result.status is BookSyncStatus.NO_MATCH
    assert not (tmp_path / "a.srt").exists()


def test_sync_one_cancel_during_alignment(tmp_path, monkeypatch):
    _patch_transcribe(monkeypatch)
    event = threading.Event()
    import anki_miner.services.book_sync.pipeline as pl

    def fake_align(segs, book, cursor, *, cancel_check=None, log=None, on_out_of_order=None):
        event.set()
        return [SentenceTiming(0, 0.0, 2.0)]

    monkeypatch.setattr(pl, "align_to_book", fake_align)
    result = sync_one(
        _make_config(tmp_path),
        object(),
        tmp_path / "a.mp3",
        _book(),
        BookCursor(),
        tmp_path / "a.srt",
        cancel_event=event,
    )
    assert result.status is BookSyncStatus.CANCELLED
    assert not (tmp_path / "a.srt").exists()


def test_sync_one_forwards_language_and_session(tmp_path, monkeypatch):
    import anki_miner.services.asr.long_audio as la

    seen: dict = {}

    def fake(config, extractor, media, **kwargs):
        seen.update(kwargs)
        return la.LongAudioResult(la.LongAudioStatus.OK, [(0.0, 2.0, "吾輩は猫である")])

    monkeypatch.setattr(la, "transcribe_media", fake)
    session = object()
    sync_one(
        _make_config(tmp_path),
        object(),
        tmp_path / "a.mp3",
        _book(),
        BookCursor(),
        tmp_path / "a.srt",
        language="ko",
        ct2_model_session=session,  # type: ignore[arg-type]
    )
    assert seen["language"] == "ko" and seen["ct2_model_session"] is session


def test_sync_one_shares_the_transcript_cache_and_the_mining_languages_sentence_rules(tmp_path, monkeypatch):
    """F-12 + MS-21: the same cached, re-broken transcript Generate uses; a reuse is announced."""
    import dataclasses

    import anki_miner.services.asr.long_audio as la
    from anki_miner.languages.registry import get_profile

    seen: dict = {}

    def fake(config, extractor, media, **kwargs):
        seen.update(kwargs)
        kwargs["on_cache_hit"]()
        return la.LongAudioResult(la.LongAudioStatus.OK, [(0.0, 2.0, "吾輩は猫である")])

    monkeypatch.setattr(la, "transcribe_media", fake)
    reused: list[str] = []
    config = dataclasses.replace(_make_config(tmp_path), language="es")

    sync_one(
        config,
        object(),
        tmp_path / "a.mp3",
        _book(),
        BookCursor(),
        tmp_path / "a.srt",
        on_transcript_reused=lambda: reused.append("hit"),
    )

    assert seen["use_cache"] is True
    assert seen["sentence_rules"] is get_profile("es").sentence_rules
    assert reused == ["hit"]


def test_sync_one_writes_out_of_order_cues_in_audio_order_and_counts_by_index(tmp_path, monkeypatch):
    """MS-24: the aligner returns a detour's cues where the audio has them; the SRT keeps that order."""
    _patch_transcribe(monkeypatch)
    import anki_miner.services.book_sync.pipeline as pl

    monkeypatch.setattr(
        pl, "align_to_book", lambda segs, book, cursor, **kw: [SentenceTiming(2, 0.0, 2.0), SentenceTiming(0, 4.0, 7.0)]
    )
    out = tmp_path / "a.srt"

    result = sync_one(_make_config(tmp_path), object(), tmp_path / "a.mp3", _book(), BookCursor(), out)

    assert result.cues == 2 and result.unmatched_sentences == 1
    text = out.read_text(encoding="utf-8")
    assert text.index(_SENTENCES[2]) < text.index(_SENTENCES[0])


def test_sync_one_does_not_count_a_sentence_an_earlier_file_timed_as_unheard(tmp_path, monkeypatch):
    """MS-24: this file's span covers sentence 1, which an earlier file of the book already timed."""
    _patch_transcribe(monkeypatch)
    import anki_miner.services.book_sync.pipeline as pl

    monkeypatch.setattr(
        pl, "align_to_book", lambda segs, book, cursor, **kw: [SentenceTiming(0, 0.0, 2.0), SentenceTiming(2, 4.0, 7.0)]
    )

    result = sync_one(
        _make_config(tmp_path), object(), tmp_path / "a.mp3", _book(), BookCursor(timed={1}), tmp_path / "a.srt"
    )

    assert result.cues == 2 and result.unmatched_sentences == 0


# --- MS-25 speech coverage, MS-24 unheard sentences ---------------------------


def _two_of_three(monkeypatch) -> None:
    """Sentences 0 and 2 timed, sentence 1 not heard (between 2.0 s and 4.0 s)."""
    import anki_miner.services.book_sync.pipeline as pl

    monkeypatch.setattr(
        pl, "align_to_book", lambda segs, book, cursor, **kw: [SentenceTiming(0, 0.0, 2.0), SentenceTiming(2, 4.0, 7.0)]
    )


def test_sync_one_keeps_its_cues_timings_in_srt_order_and_its_unheard_gaps(tmp_path, monkeypatch):
    """What --api sync reports: cue n is timings[n - 1], and the gaps the log names."""
    _patch_transcribe(monkeypatch)
    import anki_miner.services.book_sync.pipeline as pl

    timings = [SentenceTiming(2, 0.0, 2.0), SentenceTiming(0, 4.0, 5.0), SentenceTiming(4, 9.0, 10.0)]
    monkeypatch.setattr(pl, "align_to_book", lambda segs, book, cursor, **kw: timings)
    book = _book()
    book = BookText(book.title, (*book.sentences, "四。", "五。"), (*book.keys, "4", "5"))

    result = sync_one(_make_config(tmp_path), object(), tmp_path / "a.mp3", book, BookCursor(), tmp_path / "a.srt")

    assert result.timings == tuple(timings)
    assert result.unheard == (UnheardGap(3, 3, 2.0, 9.0),)  # 1 sits between 0 and 2, read backwards: no gap


def test_a_file_with_no_cues_keeps_no_timings(tmp_path, monkeypatch):
    _patch_transcribe(monkeypatch, segments=[(0.0, 5.0, "全然違う話をしている")])
    result = sync_one(_make_config(tmp_path), object(), tmp_path / "a.mp3", _book(), BookCursor(), tmp_path / "a.srt")
    assert result.status is BookSyncStatus.NO_MATCH and result.timings == () and result.unheard == ()


def test_sync_one_reports_speech_and_matched_seconds(tmp_path, monkeypatch):
    _patch_transcribe(monkeypatch)  # speech 0-2, 2-4, 4-7 s
    _two_of_three(monkeypatch)

    result = sync_one(_make_config(tmp_path), object(), tmp_path / "a.mp3", _book(), BookCursor(), tmp_path / "a.srt")

    assert result.speech_seconds == pytest.approx(7.0)
    assert result.matched_seconds == pytest.approx(5.0)


def test_an_unheard_sentence_gets_no_cue_and_its_guessed_place_goes_to_the_log(tmp_path, monkeypatch, caplog):
    _patch_transcribe(monkeypatch)
    _two_of_three(monkeypatch)
    caplog.set_level(logging.INFO, logger="anki_miner.services.book_sync.pipeline")
    out = tmp_path / "a.srt"

    sync_one(_make_config(tmp_path), object(), tmp_path / "a.mp3", _book(), BookCursor(), out)

    text = out.read_text(encoding="utf-8")
    assert _SENTENCES[1] not in text  # never mined: no cue for a guessed time
    assert "Book sync unheard" in caplog.text and "2-2@2.0-4.0s" in caplog.text


def test_a_sentence_an_earlier_file_timed_is_not_logged_as_unheard(tmp_path, monkeypatch, caplog):
    """MS-24: sentence 1 sits in this file's span but an earlier file of the book timed it."""
    _patch_transcribe(monkeypatch)
    _two_of_three(monkeypatch)
    caplog.set_level(logging.INFO, logger="anki_miner.services.book_sync.pipeline")

    result = sync_one(
        _make_config(tmp_path), object(), tmp_path / "a.mp3", _book(), BookCursor(timed={1}), tmp_path / "a.srt"
    )

    assert result.unmatched_sentences == 0
    assert "Book sync unheard" not in caplog.text


def test_sync_one_forwards_the_out_of_order_event_to_the_aligner(tmp_path, monkeypatch):
    """MS-24: the aligner reports a detour as an event; the worker words it (Z.2 WB3 I-3)."""
    _patch_transcribe(monkeypatch)
    import anki_miner.services.book_sync.pipeline as pl

    def fake_align(segs, book, cursor, **kw):
        kw["on_out_of_order"](2)
        return [SentenceTiming(0, 0.0, 2.0)]

    monkeypatch.setattr(pl, "align_to_book", fake_align)
    found: list[int] = []

    sync_one(
        _make_config(tmp_path),
        object(),
        tmp_path / "a.mp3",
        _book(),
        BookCursor(),
        tmp_path / "a.srt",
        on_out_of_order=found.append,
    )

    assert found == [2]
