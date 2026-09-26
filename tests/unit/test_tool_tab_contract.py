"""The ``_ToolTabBase`` contract, tested once across the tool tabs that share it.

Generate, Retime, Condense and Audiobook Sync (and Download, which has no mode
toggle or Overwrite box) each used to carry its own copy of these tests and of
a fake worker. Each tab's own file now keeps only what is specific to that
tool. Every translated string stays in its tab: these tests assert behaviour
and non-empty copy, never wording. Manga OCR is left out: no Output row, no
mode toggle, and its run starts behind an off-thread volume scan.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.booksync_tab import BookSyncTab
from anki_miner.gui.widgets.condense_tab import CondenseTab
from anki_miner.gui.widgets.download_tab import DownloadTab
from anki_miner.gui.widgets.subtitle_creation_tab import SubtitleCreationTab
from anki_miner.gui.widgets.subtitle_retime_tab import SubtitleRetimeTab
from tests.unit._tool_tab_harness import OS_ACCESS, FakeToolWorker, capture_slots, make_config

_ENGINE_AVAILABLE = "anki_miner.services.asr._engine.available"
_PICK_DIRECTORY = "anki_miner.gui.widgets._tool_tab_base.file_dialogs.pick_directory"


def _fill_creation(tab, tmp_path: Path) -> None:
    video = tmp_path / "episode.mp4"
    video.write_bytes(b"fake")
    tab.file_selector.set_path(str(video))


def _fill_retime(tab, tmp_path: Path) -> None:
    video = tmp_path / "episode.mp4"
    sub = tmp_path / "episode.srt"
    video.write_bytes(b"fake")
    sub.write_text("1\n")
    tab.video_file_selector.set_path(str(video))
    tab.subtitle_file_selector.set_path(str(sub))


def _fill_condense(tab, tmp_path: Path) -> None:
    media = tmp_path / "episode.mkv"
    media.write_bytes(b"fake")
    tab.media_file_selector.set_path(str(media))


def _fill_booksync(tab, tmp_path: Path) -> None:
    book = tmp_path / "neko.epub"
    audio = tmp_path / "neko.m4b"
    book.write_bytes(b"PK")
    audio.write_bytes(b"x")
    tab.file_selector.set_path(str(audio))
    tab.book_selector.set_path(str(book))


def _fill_download(tab, tmp_path: Path) -> None:
    tab.url_input.setPlainText("https://example.com/watch?v=1")


@dataclass(frozen=True)
class _Spec:
    """How to build one tool tab and start one single-file run on it."""

    tab_cls: type
    #: Attribute name of the tab's primary action button.
    primary: str
    #: Patch target of the worker class the tab constructs.
    worker_cls: str
    #: ``(target, return_value)`` patches active while the tab is constructed;
    #: the first one is the engine probe.
    construct_patches: tuple[tuple[str, object], ...]
    #: ``(target, return_value)`` patches active while the primary is clicked.
    run_patches: tuple[tuple[str, object], ...]
    #: Fill the single-file inputs for one valid run.
    fill_single: Callable[[Any, Path], None]
    #: Shown in single-file mode and hidden in folder mode; ``folder_widgets`` the reverse.
    single_widgets: tuple[str, ...] = ()
    folder_widgets: tuple[str, ...] = ()


_CREATION = _Spec(
    tab_cls=SubtitleCreationTab,
    primary="generate_button",
    worker_cls="anki_miner.gui.widgets.subtitle_creation_tab.SubtitleGenWorker",
    construct_patches=((_ENGINE_AVAILABLE, True),),
    run_patches=((_ENGINE_AVAILABLE, True), ("anki_miner.services.asr.model_manager.is_downloaded", True)),
    fill_single=_fill_creation,
    single_widgets=("file_selector",),
    folder_widgets=("folder_selector",),
)
_RETIME = _Spec(
    tab_cls=SubtitleRetimeTab,
    primary="retime_button",
    worker_cls="anki_miner.gui.widgets.subtitle_retime_tab.SubtitleRetimeWorker",
    construct_patches=(
        ("anki_miner.gui.widgets.subtitle_retime_tab.SubtitleRetimeTab._compute_alass_available", True),
    ),
    run_patches=(("anki_miner.gui.widgets.subtitle_retime_tab.SubtitleRetimeTab._alass_available", True),),
    fill_single=_fill_retime,
    single_widgets=("video_file_selector", "subtitle_file_selector", "track_row_widget"),
    folder_widgets=("video_folder_selector", "subtitle_folder_selector"),
)
_CONDENSE = _Spec(
    tab_cls=CondenseTab,
    primary="condense_button",
    worker_cls="anki_miner.gui.widgets.condense_tab.CondenseWorker",
    construct_patches=(("anki_miner.gui.widgets.condense_tab.CondenseTab._compute_ffmpeg_available", True),),
    run_patches=(("anki_miner.gui.widgets.condense_tab.CondenseTab._ffmpeg_available", True),),
    fill_single=_fill_condense,
    single_widgets=(
        "media_file_selector",
        "subtitle_file_selector",
        "audio_track_row_widget",
        "subtitle_track_row_widget",
    ),
    folder_widgets=("media_folder_selector", "subtitle_folder_selector", "subtitle_folder_hint", "merge_row_widget"),
)
_BOOKSYNC = _Spec(
    tab_cls=BookSyncTab,
    primary="sync_button",
    worker_cls="anki_miner.gui.widgets.booksync_tab.BookSyncWorker",
    construct_patches=((_ENGINE_AVAILABLE, True),),
    run_patches=((_ENGINE_AVAILABLE, True), ("anki_miner.gui.widgets.booksync_tab.usable_model_installed", True)),
    fill_single=_fill_booksync,
    single_widgets=("file_selector",),
    folder_widgets=("folder_selector",),
)
_DOWNLOAD = _Spec(
    tab_cls=DownloadTab,
    primary="download_button",
    worker_cls="anki_miner.gui.widgets.download_tab.DownloadWorker",
    construct_patches=(("anki_miner.gui.widgets.download_tab.DownloadTab._compute_ytdlp_available", True),),
    run_patches=(("anki_miner.gui.widgets.download_tab.DownloadTab._ytdlp_ready", True),),
    fill_single=_fill_download,
)

#: Tabs with a Single File / Folder toggle and an Overwrite box.
_MODE_TABS = [_CREATION, _RETIME, _CONDENSE, _BOOKSYNC]
#: Tabs with an Output row whose run is one queue worker started from the primary.
_ALL_TABS = [*_MODE_TABS, _DOWNLOAD]
#: Tabs whose engine probe is the base template (Retime keeps its own).
_PROBED_TABS = [_CREATION, _CONDENSE, _BOOKSYNC, _DOWNLOAD]


def _spec_id(spec: _Spec) -> str:
    return spec.tab_cls.__name__


def _patched(targets: tuple[tuple[str, object], ...]) -> contextlib.ExitStack:
    stack = contextlib.ExitStack()
    for target, value in targets:
        stack.enter_context(patch(target, return_value=value))
    return stack


def _make_tab(spec: _Spec, qtbot, tmp_path: Path):
    """Construct the tab with its engine probe answering "available", and wait for it."""
    with _patched(spec.construct_patches):
        tab = spec.tab_cls(make_config(tmp_path))
        qtbot.addWidget(tab)
        assert tab._availability_worker.wait(3000)
        qtbot.waitUntil(getattr(tab, spec.primary).isEnabled, timeout=3000)
    return tab


def _start_single_run(spec: _Spec, tab, tmp_path: Path, worker: FakeToolWorker) -> MagicMock:
    """Fill the single-file inputs, click the primary; return the worker-class mock."""
    spec.fill_single(tab, tmp_path)
    with (
        _patched(spec.run_patches),
        patch(OS_ACCESS, return_value=True),
        patch(spec.worker_cls, return_value=worker) as worker_cls,
    ):
        getattr(tab, spec.primary).click()
    return worker_cls


def _assert_mode(tab, spec: _Spec, *, single: bool) -> None:
    assert tab.file_mode_button.isChecked() is single
    assert tab.folder_mode_button.isChecked() is not single
    for name in spec.single_widgets:
        assert getattr(tab, name).isHidden() is not single, name
    for name in spec.folder_widgets:
        assert getattr(tab, name).isHidden() is single, name


# ---------------------------------------------------------------------------
# Construction + engine probe
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", _ALL_TABS, ids=_spec_id)
def test_construction_leaves_the_tab_idle_and_ready(spec, qtbot, tmp_path):
    tab = _make_tab(spec, qtbot, tmp_path)
    assert tab.worker_thread is None
    assert tab._primary_button is getattr(tab, spec.primary)
    assert tab.cancel_button.isHidden()


@pytest.mark.parametrize("spec", _PROBED_TABS, ids=_spec_id)
def test_a_failed_engine_probe_warns_under_the_tabs_own_logger(spec, qtbot, tmp_path, caplog):
    """The template logs through the subclass's module logger, and the tool stays off."""
    probe_target = spec.construct_patches[0][0]

    def _failed() -> list[logging.LogRecord]:
        return [r for r in caplog.records if "availability probe failed" in r.getMessage()]

    with caplog.at_level(logging.WARNING), patch(probe_target, side_effect=RuntimeError("boom")):
        tab = spec.tab_cls(make_config(tmp_path))
        qtbot.addWidget(tab)
        assert tab._availability_worker.wait(3000)
        qtbot.waitUntil(lambda: bool(_failed()), timeout=3000)

    [record] = _failed()
    assert record.name == spec.tab_cls.__module__
    assert record.getMessage() == f"{spec.tab_cls._PROBE_NAME} availability probe failed: boom"
    assert not getattr(tab, spec.primary).isEnabled()


# ---------------------------------------------------------------------------
# Single File / Folder mode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", _MODE_TABS, ids=_spec_id)
def test_single_file_mode_is_the_default(spec, qtbot, tmp_path):
    _assert_mode(_make_tab(spec, qtbot, tmp_path), spec, single=True)


@pytest.mark.parametrize("spec", _MODE_TABS, ids=_spec_id)
def test_mode_toggle_switches_to_folder_and_back(spec, qtbot, tmp_path):
    tab = _make_tab(spec, qtbot, tmp_path)
    tab.folder_mode_button.click()
    _assert_mode(tab, spec, single=False)
    tab.file_mode_button.click()
    _assert_mode(tab, spec, single=True)


@pytest.mark.parametrize("spec", _MODE_TABS, ids=_spec_id)
def test_mode_buttons_and_overwrite_explain_themselves(spec, qtbot, tmp_path):
    tab = _make_tab(spec, qtbot, tmp_path)
    assert tab.file_mode_button.toolTip().strip()
    assert tab.folder_mode_button.toolTip().strip()
    assert tab.overwrite_checkbox.toolTip().strip()


@pytest.mark.parametrize("spec", _MODE_TABS, ids=_spec_id)
def test_folder_mode_failed_collection_leaves_primary_enabled(spec, qtbot, tmp_path):
    """No folder picked: the click disables the primary before the off-thread
    scan, so the collector's synchronous bail must still hand it back."""
    tab = _make_tab(spec, qtbot, tmp_path)
    spec.fill_single(tab, tmp_path)  # Audiobook Sync refuses without a book first
    tab.folder_mode_button.click()

    getattr(tab, spec.primary).click()

    assert getattr(tab, spec.primary).isEnabled()
    assert tab.issue_banner().current_issue() is not None


# ---------------------------------------------------------------------------
# Output row
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", _ALL_TABS, ids=_spec_id)
def test_output_row_starts_on_the_tools_default(spec, qtbot, tmp_path):
    tab = _make_tab(spec, qtbot, tmp_path)
    # A value, not helper copy: helper-text is italic.
    assert tab.output_location_label.objectName() == "output-location-value"
    assert tab.output_location_label.text() == tab._strings.output_default
    assert tab.clear_output_button.isHidden()


@pytest.mark.parametrize("spec", _ALL_TABS, ids=_spec_id)
def test_choose_folder_then_reset(spec, qtbot, tmp_path):
    tab = _make_tab(spec, qtbot, tmp_path)
    out = tmp_path / "out"
    out.mkdir()

    with patch(_PICK_DIRECTORY, side_effect=lambda *a, on_done, **k: on_done(str(out))):
        tab.choose_output_button.click()

    assert tab._custom_output_dir == out
    assert tab.output_location_label.text() == str(out)
    assert not tab.clear_output_button.isHidden()

    tab.clear_output_button.click()

    assert tab._custom_output_dir is None
    assert tab.output_location_label.text() == tab._strings.output_default
    assert tab.clear_output_button.isHidden()


# ---------------------------------------------------------------------------
# Worker lifecycle + close contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", _ALL_TABS, ids=_spec_id)
def test_iter_close_workers_empty_when_idle(spec, qtbot, tmp_path):
    assert list(_make_tab(spec, qtbot, tmp_path).iter_close_workers()) == []


@pytest.mark.parametrize("spec", _ALL_TABS, ids=_spec_id)
def test_a_started_run_owns_its_worker(spec, qtbot, tmp_path):
    tab = _make_tab(spec, qtbot, tmp_path)
    worker = FakeToolWorker()

    worker_cls = _start_single_run(spec, tab, tmp_path, worker)

    assert worker_cls.call_count == 1
    assert tab.worker_thread is worker
    assert worker._started
    assert worker in list(tab.iter_close_workers())
    assert not getattr(tab, spec.primary).isEnabled()
    assert not tab.cancel_button.isHidden()


@pytest.mark.parametrize("spec", _ALL_TABS, ids=_spec_id)
def test_worker_released_on_thread_finished(spec, qtbot, tmp_path):
    """Native QThread.finished clears the handle and schedules deleteLater (M9)."""
    tab = _make_tab(spec, qtbot, tmp_path)
    worker = FakeToolWorker()
    finished = capture_slots(worker.finished)
    _start_single_run(spec, tab, tmp_path, worker)

    for slot in finished:
        slot()

    assert tab.worker_thread is None
    worker.deleteLater.assert_called_once()


@pytest.mark.parametrize("spec", _ALL_TABS, ids=_spec_id)
def test_file_skipped_logs_skipped_not_done(spec, qtbot, tmp_path):
    """file_skipped(idx, out_path, reason) logs 'Skipped: <name> — <reason>', not 'Done' (T1)."""
    tab = _make_tab(spec, qtbot, tmp_path)
    worker = FakeToolWorker()
    skipped = capture_slots(worker.file_skipped)
    _start_single_run(spec, tab, tmp_path, worker)

    for slot in skipped:
        slot(0, tmp_path / "episode.out", "Skipped, exists")

    log_text = tab.log_widget.text_edit.toPlainText()
    assert "Skipped" in log_text
    assert "episode.out" in log_text
    assert "Skipped, exists" in log_text
    assert "Done" not in log_text
