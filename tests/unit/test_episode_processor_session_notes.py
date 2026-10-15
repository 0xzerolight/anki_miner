"""AI-16: the stats row of a finished run carries the run's note ids and mined forms."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from anki_miner.models.processing import ProcessingResult
from anki_miner.services.stats_service import StatsService
from tests.conftest import build_processor


def _result(**overrides) -> ProcessingResult:
    values: dict = {"total_words_found": 10, "new_words_found": 3, "cards_created": 2}
    values.update(overrides)
    return ProcessingResult(**values)


def test_the_session_row_carries_the_runs_note_ids_and_forms(test_config):
    stats = MagicMock()
    processor = build_processor(test_config, stats_service=stats)

    processor._record_session(
        SimpleNamespace(series_name="Show", episode_name="ep01"),
        _result(card_ids=[11, 12], mined_forms=["猫", "犬"]),
    )

    session = stats.record_session.call_args.args[0]
    assert session.note_ids == [11, 12]
    assert session.mined_forms == ["猫", "犬"]
    assert session.cards_created == 2


def test_the_receipt_reaches_the_database(test_config, tmp_path):
    stats = StatsService(tmp_path / "stats.db")
    assert stats.load()
    processor = build_processor(test_config, stats_service=stats)

    processor._record_session(
        SimpleNamespace(series_name="Show", episode_name="ep01"),
        _result(card_ids=[7, 8], mined_forms=["猫"]),
    )

    [row] = stats.get_recent_sessions()
    assert row.id is not None
    receipt = stats.get_run_receipt(row.id)
    assert receipt is not None
    assert (receipt.note_ids, receipt.mined_forms) == ((7, 8), ("猫",))
