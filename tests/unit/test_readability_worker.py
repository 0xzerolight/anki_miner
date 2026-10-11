"""ReadabilityWorker: scores subtitle files against known words (Utilities → Readability).

Same shape as tests/unit/test_booksync_worker.py: the service factories are
patched at the worker's import site and ``run()`` is driven on the test thread.
The parser is a fake; ``measure`` and the known-forms recipe run for real.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import AnkiConnectionError, OperationCancelled, SetupError, SubtitleParseError
from anki_miner.gui.utils.service_factory import ServiceLoadResult
from anki_miner.gui.workers.readability_worker import ReadabilityWorker
from anki_miner.models import LineLemmas, TerminalOutcome, TokenizedWord
from anki_miner.models.readability import ReadabilityStats
from anki_miner.models.reading import ReadingDocument, ReadingUnit
from anki_miner.services.word_filter import WordFilterService

_SHARED = "anki_miner.gui.workers.readability_worker.create_shared_lookup_services"
_SERVICES = "anki_miner.gui.workers.readability_worker.create_services"
_LOAD = "anki_miner.gui.workers.readability_worker.load_reading_source"
_SPLIT = "anki_miner.gui.workers.readability_worker.split_oversize"


def _word(form: str) -> TokenizedWord:
    return TokenizedWord(
        surface=form, lemma=form, reading="", sentence=form, start_time=0.0, end_time=1.0, duration=1.0
    )


def _parsed(*lines: tuple[str, ...]) -> tuple[list[TokenizedWord], list[LineLemmas], Counter[str]]:
    """One file's parse: every lemma of every line, occurrences counted per line."""
    counts: Counter[str] = Counter(lemma for line in lines for lemma in line)
    index = [
        LineLemmas(line_text="".join(line), lemmas=frozenset(line), start_time=0.0, end_time=1.0, duration=1.0)
        for line in lines
    ]
    return [_word(form) for form in counts], index, counts


class _FakeParser:
    def __init__(self, files: dict[str, object]) -> None:
        self.files = files
        self.calls: list[str] = []
        self.unit_calls: list[tuple[str, bool]] = []

    def parse_subtitle_file_with_index(self, path: Path):
        self.calls.append(path.name)
        entry = self.files[path.name]
        if isinstance(entry, Exception):
            raise entry
        words, index, _counts = entry  # type: ignore[misc]
        return words, index

    def count_lemmas(self, path: Path) -> Counter[str]:
        return self.files[path.name][2]  # type: ignore[index]

    def parse_text_units(self, units, want_line_index, *, count_by_lemma=False):
        """Keyed by the first unit's text (the fake loader sets it to the file name)."""
        key = units[0].text
        self.unit_calls.append((key, count_by_lemma))
        entry = self.files[key]
        if isinstance(entry, Exception):
            raise entry
        return entry


def _services(config: AnkiMinerConfig, parser: _FakeParser, *, vocabulary=frozenset({"猫"}), db=None):
    return SimpleNamespace(
        subtitle_parser=parser,
        word_filter=WordFilterService(config),
        known_word_db=db,
        anki_service=MagicMock(get_existing_vocabulary=MagicMock(return_value=set(vocabulary))),
        expression_audio_fetcher=MagicMock(),
        sentence_audio_fetcher=MagicMock(),
        load_result=ServiceLoadResult(),
    )


def _capture(worker: ReadabilityWorker) -> dict[str, list]:
    cap: dict[str, list] = {"measured": [], "finished": [], "skipped": [], "queue": [], "error": []}
    worker.file_measured.connect(lambda idx, stats: cap["measured"].append((idx, stats)))
    worker.file_finished.connect(lambda idx, out, err: cap["finished"].append((idx, out, err)))
    worker.file_skipped.connect(lambda idx, out, reason: cap["skipped"].append((idx, out, reason)))
    worker.queue_finished.connect(lambda outcome: cap["queue"].append(outcome))
    worker.error.connect(lambda msg: cap["error"].append(msg))
    return cap


def _files(tmp_path: Path, *names: str) -> list[Path]:
    return [tmp_path / name for name in names]


def _run(config, files, services, shared=None) -> tuple[dict[str, list], ReadabilityWorker, MagicMock]:
    shared = shared or MagicMock()
    worker = ReadabilityWorker(config, files)
    cap = _capture(worker)
    with patch(_SHARED, return_value=shared), patch(_SERVICES, return_value=services):
        worker.run()
    return cap, worker, shared


def test_services_are_built_without_the_whitelist(qapp, tmp_path):
    """Readability measures text: a mining preference list must not move its numbers (R1 rescue)."""
    config = AnkiMinerConfig(use_whitelist=True)
    services = _services(config, _FakeParser({"a.srt": _parsed(("猫",))}))
    worker = ReadabilityWorker(config, _files(tmp_path, "a.srt"))
    with patch(_SHARED, return_value=MagicMock()), patch(_SERVICES, return_value=services) as create:
        worker.run()

    built_with = create.call_args.args[0]
    assert built_with.use_whitelist is False


def test_services_are_built_without_initializing_the_known_words_db(qapp, tmp_path):
    """initialize() writes (CREATE TABLE, migration); the report only reads (Readability review, minor 2)."""
    config = AnkiMinerConfig(use_known_words_db=True)
    db = MagicMock()
    db.is_available.return_value = True
    db.get_words_by_source.return_value = set()
    db.get_known_words.return_value = {"犬"}
    services = _services(config, _FakeParser({"a.srt": _parsed(("猫", "犬"))}), db=db)
    worker = ReadabilityWorker(config, _files(tmp_path, "a.srt"))
    cap = _capture(worker)
    with patch(_SHARED, return_value=MagicMock()), patch(_SERVICES, return_value=services) as create:
        worker.run()

    assert create.call_args.args[0].use_known_words_db is False
    assert cap["measured"][0][1].unknown_count == 0  # the DB's words still count: the user's setting is on


def test_load_warnings_reach_the_tool(qapp, tmp_path):
    """A dictionary or word list that failed to load changes the counts; say so (Readability review, minor 2)."""
    config = AnkiMinerConfig()
    services = _services(config, _FakeParser({"a.srt": _parsed(("猫",))}))
    services.load_result.warnings.append("Couldn't load name wordsets: boom")
    shared = MagicMock()
    shared.load_result = ServiceLoadResult(warnings=["Couldn't load frequency data: gone"])
    worker = ReadabilityWorker(config, _files(tmp_path, "a.srt"))
    warnings: list[str] = []
    worker.load_warning.connect(warnings.append)
    with patch(_SHARED, return_value=shared), patch(_SERVICES, return_value=services):
        worker.run()

    assert warnings == ["Couldn't load frequency data: gone", "Couldn't load name wordsets: boom"]


def test_measures_each_file_in_order(qapp, tmp_path):
    config = AnkiMinerConfig()
    parser = _FakeParser({"1.srt": _parsed(("猫", "犬")), "2.srt": _parsed(("猫",), ("猫",))})
    services = _services(config, parser)
    files = _files(tmp_path, "1.srt", "2.srt")

    cap, _worker, _shared = _run(config, files, services)

    assert [idx for idx, _ in cap["measured"]] == [0, 1]
    first, second = (stats for _, stats in cap["measured"])
    assert isinstance(first, ReadabilityStats)
    assert (first.word_count, first.unknown_count, first.new_words) == (2, 1, frozenset({"犬"}))
    assert second.known_pct == 100.0
    assert cap["finished"] == [(0, files[0], None), (1, files[1], None)]
    assert cap["queue"] == [TerminalOutcome.SUCCESS]
    services.anki_service.get_existing_vocabulary.assert_called_once_with(allow_degraded=False)


def test_anki_unreachable_fails_the_run_with_a_typed_fault(qapp, tmp_path):
    config = AnkiMinerConfig()
    parser = _FakeParser({"1.srt": _parsed(("猫",))})
    services = _services(config, parser)
    services.anki_service.get_existing_vocabulary.side_effect = AnkiConnectionError("refused")

    cap, worker, shared = _run(config, _files(tmp_path, "1.srt"), services)

    assert isinstance(worker.fatal_exception, AnkiConnectionError)
    assert cap["error"] == ["refused"]
    assert cap["queue"] == [TerminalOutcome.FAILED]
    assert cap["measured"] == [] and parser.calls == []
    shared.close.assert_called_once()
    services.expression_audio_fetcher.close.assert_called_once()
    services.sentence_audio_fetcher.close.assert_called_once()


def test_anki_unreachable_logs_one_warning_not_a_traceback(qapp, tmp_path, caplog):
    """Anki simply being closed is expected: report_failure's rule, one WARNING line."""
    config = AnkiMinerConfig()
    services = _services(config, _FakeParser({"1.srt": _parsed(("猫",))}))
    services.anki_service.get_existing_vocabulary.side_effect = AnkiConnectionError("refused")

    with caplog.at_level(logging.WARNING):
        _run(config, _files(tmp_path, "1.srt"), services)

    assert [r for r in caplog.records if r.levelno >= logging.ERROR or r.exc_info] == []
    assert any("refused" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)


def test_one_unreadable_file_is_partial(qapp, tmp_path):
    config = AnkiMinerConfig()
    parser = _FakeParser({"1.srt": _parsed(("猫",)), "2.srt": SubtitleParseError("garbled")})

    cap, _worker, _shared = _run(config, _files(tmp_path, "1.srt", "2.srt"), _services(config, parser))

    assert [idx for idx, _ in cap["measured"]] == [0]
    assert cap["finished"][1][0] == 1 and cap["finished"][1][2]
    assert cap["queue"] == [TerminalOutcome.PARTIAL]


def test_file_without_mining_language_words_is_skipped(qapp, tmp_path):
    config = AnkiMinerConfig()
    parser = _FakeParser({"ep.ja.srt": _parsed(("猫",)), "ep.en.srt": ([], [], Counter())})
    files = _files(tmp_path, "ep.ja.srt", "ep.en.srt")

    cap, _worker, _shared = _run(config, files, _services(config, parser))

    assert [idx for idx, _ in cap["measured"]] == [0]
    assert [(idx, out) for idx, out, _reason in cap["skipped"]] == [(1, files[1])]
    assert cap["skipped"][0][2]  # a skip always says why
    assert cap["queue"] == [TerminalOutcome.SUCCESS]


def test_user_ignore_list_counts_as_known(qapp, tmp_path):
    config = AnkiMinerConfig(use_known_words_db=False)
    db = MagicMock()
    db.is_available.return_value = True
    db.get_words_by_source.return_value = {"犬"}
    parser = _FakeParser({"1.srt": _parsed(("猫", "犬"))})

    cap, _worker, _shared = _run(config, _files(tmp_path, "1.srt"), _services(config, parser, db=db))

    assert cap["measured"][0][1].unknown_count == 0


def test_cancel_between_files_still_tears_down(qapp, tmp_path):
    config = AnkiMinerConfig()
    parser = _FakeParser({"1.srt": _parsed(("猫",)), "2.srt": _parsed(("猫",))})
    services = _services(config, parser)
    shared = MagicMock()
    worker = ReadabilityWorker(config, _files(tmp_path, "1.srt", "2.srt"))
    cap = _capture(worker)
    worker.file_measured.connect(lambda *_: worker.cancel())

    with patch(_SHARED, return_value=shared), patch(_SERVICES, return_value=services):
        worker.run()

    assert parser.calls == ["1.srt"]
    assert cap["queue"] == [TerminalOutcome.CANCELLED]
    shared.close.assert_called_once()


# --- Books and manga volumes: the Reading loaders, then parse_text_units ---


def _doc(name: str) -> ReadingDocument:
    return ReadingDocument(
        title=name,
        kind="book",
        series="Books",
        episode=name,
        units=[ReadingUnit(text=name, index=0, location_label="¶1")],
    )


def _book_files(tmp_path: Path, *names: str) -> list[Path]:
    """Real files: detect() reads books by extension, and split_oversize stats them."""
    paths = _files(tmp_path, *names)
    for path in paths:
        path.write_text("x", encoding="utf-8")
    return paths


def _load_by_name(failures: dict[str, BaseException] | None = None):
    def load(_services, _config, ref, *, cancel_check):
        name = ref.path.name
        if failures and name in failures:
            raise failures[name]
        return _doc(name)

    return load


def test_a_book_is_measured_from_its_text_units_by_lemma(qapp, tmp_path):
    config = AnkiMinerConfig()
    parser = _FakeParser({"novel.txt": _parsed(("猫", "犬"), ("犬",))})
    services = _services(config, parser)
    files = _book_files(tmp_path, "novel.txt")

    with patch(_LOAD, side_effect=_load_by_name()) as load:
        cap, _worker, _shared = _run(config, files, services)

    assert parser.unit_calls == [("novel.txt", True)]
    ((_idx, stats),) = cap["measured"]
    assert (stats.word_count, stats.unknown_count, stats.new_words) == (3, 2, frozenset({"犬"}))
    assert cap["finished"] == [(0, files[0], None)]
    assert load.call_args.args[0] is services and load.call_args.args[2].kind == "txt"
    assert cap["queue"] == [TerminalOutcome.SUCCESS]


def test_a_real_txt_book_goes_through_the_reading_loader(qapp, tmp_path):
    """The Services → load_reading_source seam unpatched: detect, split_oversize and the novel loader run."""
    config = AnkiMinerConfig()
    book = tmp_path / "novel.txt"
    book.write_text("猫が寝る。", encoding="utf-8")
    parser = _FakeParser({"猫が寝る。": _parsed(("猫", "寝る"))})

    cap, _worker, _shared = _run(config, [book], _services(config, parser))

    assert parser.unit_calls == [("猫が寝る。", True)]
    assert [idx for idx, _ in cap["measured"]] == [0]


def test_a_mokuro_volume_loads_as_a_manga_ref(qapp, tmp_path):
    config = AnkiMinerConfig()
    volume = tmp_path / "Vol1.mokuro"
    volume.write_text(
        json.dumps(
            {
                "version": "0.1.0",
                "title": "T",
                "title_uuid": "t",
                "volume": "V",
                "volume_uuid": "v",
                "pages": [],
            }
        ),
        encoding="utf-8",
    )
    parser = _FakeParser({"Vol1.mokuro": _parsed(("猫",))})

    with patch(_LOAD, side_effect=_load_by_name()) as load:
        cap, _worker, _shared = _run(config, [volume], _services(config, parser))

    assert load.call_args.args[2].kind == "mokuro"
    assert [idx for idx, _ in cap["measured"]] == [0]


def test_a_book_that_cannot_load_fails_only_its_row(qapp, tmp_path, caplog):
    """Review Focus 3: SetupError is fatal in the base, but a DRM book must not stop the others."""
    config = AnkiMinerConfig()
    parser = _FakeParser({"good.txt": _parsed(("猫",))})
    files = _book_files(tmp_path, "bad.epub", "good.txt")
    failure = SetupError("'bad.epub' is DRM-protected and cannot be mined.")

    with caplog.at_level(logging.WARNING), patch(_LOAD, side_effect=_load_by_name({"bad.epub": failure})):
        cap, worker, _shared = _run(config, files, _services(config, parser))

    assert cap["finished"] == [(0, None, str(failure)), (1, files[1], None)]
    assert [idx for idx, _ in cap["measured"]] == [1]
    assert worker.fatal_exception is None
    assert cap["queue"] == [TerminalOutcome.PARTIAL]
    assert [r for r in caplog.records if r.exc_info] == []
    assert any("DRM" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)


def test_an_archive_without_ocr_data_fails_its_row(qapp, tmp_path):
    """A picked .zip that is not a mokuro volume: detect() says what is missing."""
    import zipfile

    config = AnkiMinerConfig()
    archive = tmp_path / "subs.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("ep01.srt", "x")

    with patch(_LOAD) as load:
        cap, _worker, _shared = _run(config, [archive], _services(config, _FakeParser({})))

    load.assert_not_called()
    ((idx, out, error),) = cap["finished"]
    assert (idx, out) == (0, None) and ".mokuro" in error
    assert cap["queue"] == [TerminalOutcome.FAILED]


def test_a_missing_engine_still_stops_the_run(qapp, tmp_path):
    config = AnkiMinerConfig()
    parser = _FakeParser({"a.txt": SetupError("no engine"), "b.txt": _parsed(("猫",))})
    files = _book_files(tmp_path, "a.txt", "b.txt")

    with patch(_LOAD, side_effect=_load_by_name()):
        cap, worker, _shared = _run(config, files, _services(config, parser))

    assert isinstance(worker.fatal_exception, SetupError)
    assert [key for key, _ in parser.unit_calls] == ["a.txt"]
    assert cap["queue"] == [TerminalOutcome.FAILED]


def test_cancel_during_a_load_ends_without_a_failure(qapp, tmp_path):
    """Review Focus 5: the loader's own cancel is not a broken book."""
    config = AnkiMinerConfig()
    files = _book_files(tmp_path, "big.epub", "next.epub")
    worker = ReadabilityWorker(config, files)
    cap = _capture(worker)

    def load(_services, _config, _ref, *, cancel_check):
        worker.cancel()
        assert cancel_check()
        raise OperationCancelled("Reading load cancelled")

    with (
        patch(_SHARED, return_value=MagicMock()),
        patch(_SERVICES, return_value=_services(config, _FakeParser({}))),
        patch(_LOAD, side_effect=load),
    ):
        worker.run()

    assert cap["finished"] == [] and cap["measured"] == []
    assert cap["queue"] == [TerminalOutcome.CANCELLED]


def test_an_oversized_txt_is_one_row_from_its_parts(qapp, tmp_path):
    config = AnkiMinerConfig()
    parser = _FakeParser({"part1": _parsed(("猫", "犬")), "part2": _parsed(("犬", "鳥"))})
    files = _book_files(tmp_path, "huge.txt")
    parts = [SimpleNamespace(path=Path("part1"), kind="txt"), SimpleNamespace(path=Path("part2"), kind="txt")]

    with patch(_SPLIT, return_value=parts), patch(_LOAD, side_effect=_load_by_name()):
        cap, _worker, _shared = _run(config, files, _services(config, parser))

    ((idx, stats),) = cap["measured"]
    assert idx == 0
    assert (stats.word_count, stats.unknown_count, stats.line_count) == (4, 3, 2)
    assert stats.new_words == frozenset({"犬", "鳥"})
