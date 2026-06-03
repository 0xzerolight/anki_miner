"""BatchProcessingTab word-curation wiring (Issue #60)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QApplication

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.presenters import GUIPresenter, GUIProgressCallback
from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def tab(qapp):
    return BatchProcessingTab(AnkiMinerConfig(), GUIPresenter(), GUIProgressCallback())


def test_checkbox_default_unchecked(tab):
    assert tab.review_words_checkbox.isChecked() is False


def test_pairs_worker_callback_follows_checkbox(tab):
    pairs = [SimpleNamespace(video="v", subtitle="s")]
    with (
        patch("anki_miner.gui.widgets.batch_processing_tab.create_episode_processor"),
        patch("anki_miner.gui.workers.manual_pair_worker.ManualPairWorkerThread") as worker_cls,
    ):
        tab.review_words_checkbox.setChecked(True)
        tab._start_processing_with_pairs(pairs)
        # Bound methods compare by ``==`` (each attribute access makes a fresh wrapper),
        # matching the existing youtube_tab curation test convention.
        assert worker_cls.call_args.kwargs["curation_callback"] == tab._curation_bridge

        tab._is_processing = False
        tab.review_words_checkbox.setChecked(False)
        tab._start_processing_with_pairs(pairs)
        assert worker_cls.call_args.kwargs["curation_callback"] is None


def test_queue_worker_callback_follows_checkbox(tab):
    with patch("anki_miner.gui.workers.batch_queue_worker.BatchQueueWorkerThread") as worker_cls:
        tab.review_words_checkbox.setChecked(True)
        tab._start_queue_worker()
        assert worker_cls.call_args.kwargs["curation_callback"] == tab._curation_bridge

        tab.review_words_checkbox.setChecked(False)
        tab._start_queue_worker()
        assert worker_cls.call_args.kwargs["curation_callback"] is None


def test_build_curation_context_reads_worker_attrs(tab, tmp_path):
    subs = tmp_path / "ep1.ass"
    subs.touch()
    lookup = MagicMock(name="lookup_all_offline")
    proc = SimpleNamespace(definition_service=SimpleNamespace(lookup_all_offline=lookup))
    tab.worker_thread = SimpleNamespace(
        _curation_processor=proc,
        _curation_video=tmp_path / "ep1.mkv",
        _curation_subtitle=subs,
        _curation_offset=4.0,
    )
    mock_parser = MagicMock()
    mock_parser.return_value.parse_raw_entries.return_value = [(0.0, 1.0, "テスト")]
    with patch("anki_miner.gui.widgets.batch_processing_tab.SubtitleParserService", mock_parser):
        media_context, lookup_fn = tab._build_curation_context()
    assert lookup_fn is lookup
    assert media_context is not None
    assert media_context.offset == 4.0
    assert media_context.subtitle_entries == [(0.0, 1.0, "テスト")]


def test_build_curation_context_parse_error_returns_none_context(tab, tmp_path):
    subs = tmp_path / "ep1.ass"
    subs.touch()
    lookup = MagicMock(name="lookup_all_offline")
    proc = SimpleNamespace(definition_service=SimpleNamespace(lookup_all_offline=lookup))
    tab.worker_thread = SimpleNamespace(
        _curation_processor=proc,
        _curation_video=tmp_path / "ep1.mkv",
        _curation_subtitle=subs,
        _curation_offset=0.0,
    )
    mock_parser = MagicMock()
    mock_parser.return_value.parse_raw_entries.side_effect = RuntimeError("bad subs")
    with patch("anki_miner.gui.widgets.batch_processing_tab.SubtitleParserService", mock_parser):
        media_context, lookup_fn = tab._build_curation_context()
    assert media_context is None
    assert lookup_fn is lookup


def test_build_curation_context_no_worker_returns_none(tab):
    tab.worker_thread = None
    assert tab._build_curation_context() == (None, None)


def test_cancel_rejects_active_curation_dialog(tab):
    dialog = MagicMock()
    tab._active_curation_dialog = dialog
    tab.worker_thread = MagicMock()
    tab._on_cancel_clicked()
    dialog.reject.assert_called_once()
