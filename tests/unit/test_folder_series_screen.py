"""FolderSeriesScreenBase: the folder-pair season scaffolding shared out of Batch.

``BatchProcessingTab`` keeps its own tests (``test_batch_processing_tab_*.py``);
these pin the SHARED base behaviour on a minimal host so a future second
subclass (Deck Builder) inherits it proven, not just hoped-for. The i18n test
in particular exercises the base on hosts named literally ``BatchProcessingTab``
and ``DeckBuilderTab``, to prove the lifted strings translate regardless of the
runtime class -- the whole point of writing them as an explicit
``QCoreApplication.translate("BatchProcessingTab", ...)`` rather than ``self.tr()``.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QMimeData, QPointF, Qt, QTranslator, QUrl
from PyQt6.QtGui import QDropEvent
from PyQt6.QtWidgets import QDoubleSpinBox, QWidget

from anki_miner.gui.widgets._folder_series_screen import FolderSeriesScreenBase
from anki_miner.gui.widgets.enhanced import FileSelector
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.gui.workers._queue_worker_base import CurationEpisode

DE_QM = Path(__file__).resolve().parents[2] / "anki_miner" / "gui" / "resources" / "translations" / "anki_miner_de.qm"


def _build_host(qtbot, config, *, name: str = "FolderSeriesScreenHost") -> FolderSeriesScreenBase:
    """A minimal ``FolderSeriesScreenBase`` subclass with the widgets it duck-types on.

    ``FolderSeriesScreenBase`` declares no ``__init__`` of its own, so a bare
    subclass falls straight through to ``QWidget.__init__`` (see
    ``MiningTabBase``) -- no need to reproduce a real screen's Add Series card.
    ``name`` lets the i18n test build hosts literally named
    ``"BatchProcessingTab"`` and ``"DeckBuilderTab"``.
    """
    cls = type(name, (FolderSeriesScreenBase,), {})
    tab = cls()
    qtbot.addWidget(tab)
    tab.config = config
    tab.video_folder_selector = FileSelector()
    tab.subtitle_folder_selector = FileSelector()
    tab.secondary_folder_selector = FileSelector()
    tab.secondary_offset_spinbox = QDoubleSpinBox()
    tab.secondary_offset_row = QWidget()
    tab.progress_widget = ProgressWidget()
    tab.worker_thread = None
    return tab


def _point(selector, path: Path, *, valid: bool = True) -> None:
    selector.path_or_none = MagicMock(return_value=str(path))
    selector.is_valid = MagicMock(return_value=valid)


def _mime(*paths: Path) -> QMimeData:
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    return data


def _drop_event(data: QMimeData) -> QDropEvent:
    """Build a drop event. The CALLER must keep ``data`` alive: the event holds
    a borrowed pointer, and letting the mime die first segfaults Qt."""
    return QDropEvent(
        QPointF(1.0, 1.0),
        Qt.DropAction.CopyAction,
        data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def test_two_dropped_folders_route_video_then_subtitle(qtbot, test_config, tmp_path):
    tab = _build_host(qtbot, test_config)
    video_folder = tmp_path / "video"
    subtitle_folder = tmp_path / "subs"
    video_folder.mkdir()
    subtitle_folder.mkdir()
    data = _mime(video_folder, subtitle_folder)

    event = _drop_event(data)
    tab.dropEvent(event)

    assert tab.video_folder_selector.get_path() == str(video_folder)
    assert tab.subtitle_folder_selector.get_path() == str(subtitle_folder)
    assert event.isAccepted()


def test_a_stage_signal_does_not_move_the_progress_bar(qtbot, test_config):
    """D18, applied to the folder-series screens: the bar counts whole items,
    so a stage signal inside one of them must move only the status line."""
    tab = _build_host(qtbot, test_config)

    tab._on_progress_stage(2, 5, "Fetching definitions")

    assert tab.progress_widget.progress_bar.value() == 0
    assert "Fetching definitions" in tab.progress_widget.status_label.text()


def test_curation_context_carries_a_resolver_from_the_season_map(qtbot, test_config, facade_processor, tmp_path):
    tab = _build_host(qtbot, test_config)
    subs1 = tmp_path / "ep1.ass"
    subs2 = tmp_path / "ep2.ass"
    subs1.touch()
    subs2.touch()
    ep1 = tmp_path / "ep1.mkv"
    ep2 = tmp_path / "ep2.mkv"
    season_map = {
        ep1: CurationEpisode(subs1, 0.0),
        ep2: CurationEpisode(subs2, 0.0),
    }
    tab.worker_thread = SimpleNamespace(
        curation_processor=facade_processor,
        _curation_video=ep1,
        _curation_subtitle=subs1,
        _curation_offset=0.0,
        _curation_secondary=None,
        _curation_secondary_offset=0.0,
        _curation_media_map=season_map,
    )
    mock_parser = MagicMock()
    mock_parser.return_value.parse_raw_entries.return_value = [(0.0, 1.0, "テスト")]
    with patch("anki_miner.gui.widgets._mining_tab_base.SubtitleParserService", mock_parser):
        media_context, _lookup_fn = tab._build_curation_context()

    assert media_context is not None
    assert media_context.context_resolver is not None


def test_a_missing_translation_folder_raises_a_screen_issue(qtbot, test_config, tmp_path):
    tab = _build_host(qtbot, replace(test_config, secondary_subtitle_enabled=True))
    _point(tab.secondary_folder_selector, tmp_path / "gone", valid=False)
    shown: list = []
    tab.show_screen_issue = lambda issue, **_kw: shown.append(issue)  # type: ignore[method-assign]

    ok, folder = tab._validated_secondary_folder(tmp_path / "subs")

    assert ok is False
    assert folder is None
    assert shown
    assert "no longer exists" in shown[0].summary


def test_lifted_strings_translate_on_any_subclass(qapp, qtbot, test_config, tmp_path):
    """Both a Batch-named and a Deck-Builder-named host read the German string.

    The lifted strings go through an explicit
    ``QCoreApplication.translate("BatchProcessingTab", ...)`` rather than
    ``self.tr()`` precisely so translation does not depend on which subclass is
    actually running -- ``self.tr()`` would resolve under the runtime class
    name and silently fall back to English on a ``DeckBuilderTab`` host.
    """
    config = replace(test_config, secondary_subtitle_enabled=True)
    translator = QTranslator()
    assert translator.load(str(DE_QM)) is True
    qapp.installTranslator(translator)
    try:
        for name in ("BatchProcessingTab", "DeckBuilderTab"):
            tab = _build_host(qtbot, config, name=name)
            _point(tab.secondary_folder_selector, tmp_path / "gone", valid=False)
            shown: list = []
            tab.show_screen_issue = lambda issue, _shown=shown, **_kw: _shown.append(issue)  # type: ignore[method-assign]

            ok, _folder = tab._validated_secondary_folder(tmp_path / "subs")

            assert ok is False
            assert shown, f"{name} raised no screen issue"
            assert shown[0].summary == "Dieser Übersetzungs-Untertitelordner existiert nicht mehr.", name
    finally:
        qapp.removeTranslator(translator)
