"""BatchProcessingTab word-curation wiring (Issue #60)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.presenters import GUIPresenter, GUIProgressCallback
from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab


@pytest.fixture
def tab(qapp, qtbot):
    widget = BatchProcessingTab(AnkiMinerConfig(), GUIPresenter(), GUIProgressCallback())
    qtbot.addWidget(widget)
    return widget


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


def test_pairs_worker_built_with_factory_not_prebuilt(tab):
    """The manual-pairs path defers EpisodeProcessor construction to the worker
    via a processor_factory; create_episode_processor is NOT called on the GUI
    thread (that build moved off the GUI thread to stop the freeze)."""
    pairs = [SimpleNamespace(video="v", subtitle="s")]
    with (
        patch("anki_miner.gui.widgets.batch_processing_tab.create_episode_processor") as mock_create,
        patch("anki_miner.gui.workers.manual_pair_worker.ManualPairWorkerThread") as worker_cls,
    ):
        tab._start_processing_with_pairs(pairs)

        # No GUI-thread build.
        mock_create.assert_not_called()
        # Worker got the factory + a None processor (positional arg 0).
        assert worker_cls.call_args.args[0] is None
        factory = worker_cls.call_args.kwargs["processor_factory"]
        assert factory is not None

        # Invoking the factory (as the worker's run() would) builds via the
        # service factory.
        factory()
        mock_create.assert_called_once()


def test_queue_worker_callback_follows_checkbox(tab):
    with patch("anki_miner.gui.workers.batch_queue_worker.BatchQueueWorkerThread") as worker_cls:
        tab.review_words_checkbox.setChecked(True)
        tab._start_queue_worker()
        assert worker_cls.call_args.kwargs["curation_callback"] == tab._curation_bridge

        tab.review_words_checkbox.setChecked(False)
        tab._start_queue_worker()
        assert worker_cls.call_args.kwargs["curation_callback"] is None


def test_build_curation_context_reads_worker_attrs(tab, facade_processor, tmp_path):
    subs = tmp_path / "ep1.ass"
    subs.touch()
    lookup = MagicMock(name="lookup_all_offline")
    facade_processor.definition_service.lookup_all_offline = lookup
    tab.worker_thread = SimpleNamespace(
        curation_processor=facade_processor,
        _curation_video=tmp_path / "ep1.mkv",
        _curation_subtitle=subs,
        _curation_offset=4.0,
    )
    mock_parser = MagicMock()
    mock_parser.return_value.parse_raw_entries.return_value = [(0.0, 1.0, "テスト")]
    with patch("anki_miner.gui.widgets._mining_tab_base.SubtitleParserService", mock_parser):
        media_context, lookup_fn = tab._build_curation_context()
    assert lookup_fn is lookup
    assert media_context is not None
    assert media_context.offset == 4.0
    assert media_context.subtitle_entries == [(0.0, 1.0, "テスト")]


def test_build_curation_context_parse_error_returns_none_context(tab, facade_processor, tmp_path):
    subs = tmp_path / "ep1.ass"
    subs.touch()
    lookup = MagicMock(name="lookup_all_offline")
    facade_processor.definition_service.lookup_all_offline = lookup
    tab.worker_thread = SimpleNamespace(
        curation_processor=facade_processor,
        _curation_video=tmp_path / "ep1.mkv",
        _curation_subtitle=subs,
        _curation_offset=0.0,
    )
    mock_parser = MagicMock()
    mock_parser.return_value.parse_raw_entries.side_effect = RuntimeError("bad subs")
    with patch("anki_miner.gui.widgets._mining_tab_base.SubtitleParserService", mock_parser):
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


def test_build_curation_context_routes_through_shared_helpers(tab, facade_processor, tmp_path):
    """_build_curation_context delegates to the shared MiningTabBase helpers
    (T-60): _make_curation_media_context gets the worker's _curation_* attrs
    (no audio-track override on batch), _lookup_fn_from_processor resolves the
    typed curation_processor through the offline_lookup_fn facade."""
    from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab

    video = tmp_path / "ep1.mkv"
    subs = tmp_path / "ep1.ass"
    tab.worker_thread = SimpleNamespace(
        curation_processor=facade_processor,
        _curation_video=video,
        _curation_subtitle=subs,
        _curation_offset=4.0,
    )

    sentinel_ctx = object()
    with patch.object(BatchProcessingTab, "_make_curation_media_context", return_value=sentinel_ctx) as helper:
        media_context, lookup_fn = tab._build_curation_context()

    helper.assert_called_once_with(tab.config, video, subs, offset=4.0)
    assert media_context is sentinel_ctx
    assert lookup_fn is facade_processor.definition_service.lookup_all_offline
