"""Per-file sync policy (services/book_sync/pipeline.py): load the book, transcribe, align, write.

The reading loader, the long-audio transcriber and the aligner are patched at
their canonical modules; the SRT writer runs for real into tmp_path.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SetupError
from anki_miner.models.reading import ReadingDocument, ReadingSourceRef, ReadingUnit
from anki_miner.services.book_sync.aligner import BookCursor, BookText, SentenceTiming
from anki_miner.services.book_sync.pipeline import BookSyncStatus, load_book, sync_one

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

    def fake_align(segs, book, cursor, *, cancel_check=None, log=None):
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
