"""Tests for DeckBuilderTab: its inputs, page contract and run lifecycle.

The inputs half covers the folder/settings controls, their seeding and
persistence, ``_build_request``'s validation, and the pinned action bar / log
wiring every mining screen shares. The lifecycle half drives Preview, Build and
Cancel against a fake worker with ``DeckBuilderWorker``'s signal surface, so
every state change is observed without a thread or a real pipeline.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QObject, pyqtSignal

from anki_miner.gui.controllers.task_registry import TaskOutcome, TaskRegistry
from anki_miner.gui.presenters import GUIPresenter
from anki_miner.gui.utils import queue_state_store as store
from anki_miner.gui.utils.queue_state_store import QueueItemSnapshot, QueueSnapshot
from anki_miner.gui.widgets import deck_builder_tab
from anki_miner.gui.widgets.base import ScreenIssue
from anki_miner.gui.widgets.deck_builder_tab import DeckBuilderTab
from anki_miner.models.deck_build import DeckCorpus, DeckSelectionMode
from anki_miner.models.processing import TerminalOutcome

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _tab(qtbot, config) -> DeckBuilderTab:
    widget = DeckBuilderTab(
        config=config,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def tab(qtbot, test_config) -> DeckBuilderTab:
    return _tab(qtbot, test_config)


# ---------------------------------------------------------------------------
# Deck-name auto-fill from video folder
# ---------------------------------------------------------------------------


def test_deck_name_autofilled_from_video_folder(tab, tmp_path):
    folder = tmp_path / "Jujutsu Kaisen"
    folder.mkdir()
    tab.video_folder_selector.set_path(str(folder))
    # path_changed fires on set_path -> _on_video_folder_changed
    assert tab.deck_name_edit.text() == "Jujutsu Kaisen"
    assert tab._last_auto_deck_name == "Jujutsu Kaisen"


def test_auto_fill_updates_when_still_auto(tab, tmp_path):
    folder1 = tmp_path / "Show A"
    folder2 = tmp_path / "Show B"
    folder1.mkdir()
    folder2.mkdir()

    tab.video_folder_selector.set_path(str(folder1))
    assert tab.deck_name_edit.text() == "Show A"

    # Change folder while name still equals auto value -> should update.
    tab.video_folder_selector.set_path(str(folder2))
    assert tab.deck_name_edit.text() == "Show B"


def test_manual_deck_name_not_overwritten(tab, tmp_path):
    folder1 = tmp_path / "Show A"
    folder2 = tmp_path / "Show B"
    folder1.mkdir()
    folder2.mkdir()

    tab.video_folder_selector.set_path(str(folder1))
    # Simulate a manual edit by the user.
    tab.deck_name_edit.setText("My Custom Deck")
    # _last_auto_deck_name is still "Show A"; current text != auto -> no overwrite.
    tab.video_folder_selector.set_path(str(folder2))
    assert tab.deck_name_edit.text() == "My Custom Deck"


# ---------------------------------------------------------------------------
# Mode <-> spinbox visibility
# ---------------------------------------------------------------------------


def test_mode_all_hides_both_spinboxes(tab):
    # Set to something else first, then back to ALL.
    tab.mode_combo.setCurrentIndex(1)  # TOP_N
    tab.mode_combo.setCurrentIndex(0)  # ALL
    # isHidden(), not isVisible(): the latter requires a fully shown hierarchy.
    assert tab.top_n_spinbox.isHidden()
    assert tab.coverage_spinbox.isHidden()


def test_mode_top_n_shows_n_spinbox(tab):
    tab.mode_combo.setCurrentIndex(1)  # TOP_N
    assert not tab.top_n_spinbox.isHidden()
    assert tab.coverage_spinbox.isHidden()


def test_mode_coverage_pct_shows_coverage_spinbox(tab):
    tab.mode_combo.setCurrentIndex(2)  # COVERAGE_PCT
    assert tab.top_n_spinbox.isHidden()
    assert not tab.coverage_spinbox.isHidden()


# ---------------------------------------------------------------------------
# Seeding from config
# ---------------------------------------------------------------------------


def test_seeds_defaults_from_config(tab, test_config):
    assert tab.mode_combo.currentData() == DeckSelectionMode.ALL
    assert tab.top_n_spinbox.value() == test_config.deck_builder_top_n
    assert tab.coverage_spinbox.value() == test_config.deck_builder_coverage_pct
    assert tab.skip_known_checkbox.isChecked() == test_config.deck_builder_skip_known
    assert tab.top_n_spinbox.isHidden()
    assert tab.coverage_spinbox.isHidden()


def test_a_restored_top_n_mode_opens_with_its_spinbox_shown(qtbot, test_config):
    """Regression: a restored non-default mode must not open with a hidden control."""
    config = replace(test_config, deck_builder_mode="top_n", deck_builder_top_n=250)
    tab = _tab(qtbot, config)

    assert tab.mode_combo.currentData() == DeckSelectionMode.TOP_N
    assert not tab.top_n_spinbox.isHidden()
    assert tab.coverage_spinbox.isHidden()
    assert tab.top_n_spinbox.value() == 250


# ---------------------------------------------------------------------------
# persist_run_options on each control
# ---------------------------------------------------------------------------


def test_mode_change_persists(tab):
    tab.mode_combo.setCurrentIndex(1)  # TOP_N
    assert tab.config.deck_builder_mode == "top_n"


def test_top_n_change_persists(tab):
    tab.top_n_spinbox.setValue(42)
    assert tab.config.deck_builder_top_n == 42


def test_coverage_change_persists(tab):
    tab.mode_combo.setCurrentIndex(2)  # COVERAGE_PCT
    tab.coverage_spinbox.setValue(77.5)
    assert tab.config.deck_builder_coverage_pct == 77.5


def test_skip_known_change_persists(tab):
    original = tab.skip_known_checkbox.isChecked()
    tab.skip_known_checkbox.setChecked(not original)
    assert tab.config.deck_builder_skip_known == (not original)


def test_review_words_change_persists(tab):
    original = tab.review_words_checkbox.isChecked()
    tab.review_words_checkbox.setChecked(not original)
    assert tab.config.review_words_before_mining == (not original)


def test_seeding_a_restored_mode_does_not_re_persist(qtbot, test_config):
    """Constructing from a non-default config must not immediately re-save it."""
    config = replace(test_config, deck_builder_mode="top_n", deck_builder_top_n=250)
    seen = []
    tab = DeckBuilderTab(config=config, presenter=MagicMock(), progress_callback=MagicMock())
    qtbot.addWidget(tab)
    tab.run_options_changed.connect(seen.append)

    tab.update_config(config)

    assert seen == []


# ---------------------------------------------------------------------------
# Translation folder gated on the shared setting (F7)
# ---------------------------------------------------------------------------


def test_translation_rows_follow_secondary_subtitle_enabled(tab, test_config):
    assert not tab.secondary_folder_selector.isVisibleTo(tab)
    assert not tab.secondary_offset_row.isVisibleTo(tab)

    tab.update_config(replace(test_config, secondary_subtitle_enabled=True))
    assert tab.secondary_folder_selector.isVisibleTo(tab)
    assert tab.secondary_offset_row.isVisibleTo(tab)

    tab.update_config(test_config)
    assert not tab.secondary_folder_selector.isVisibleTo(tab)
    assert not tab.secondary_offset_row.isVisibleTo(tab)


# ---------------------------------------------------------------------------
# Folder history key (D7)
# ---------------------------------------------------------------------------


def test_every_folder_selector_uses_the_deckbuilder_history_key(tab):
    for selector in (
        tab.video_folder_selector,
        tab.subtitle_folder_selector,
        tab.secondary_folder_selector,
    ):
        assert selector._history_key == "video.deckbuilder.inputs"


# ---------------------------------------------------------------------------
# _build_request validation
# ---------------------------------------------------------------------------


def _setup_no_folders(tab: DeckBuilderTab, tmp_path: Path) -> None:
    pass


def _setup_missing_folder(tab: DeckBuilderTab, tmp_path: Path) -> None:
    tab.video_folder_selector.set_path(str(tmp_path / "gone"))
    tab.subtitle_folder_selector.set_path(str(tmp_path))


def _setup_empty_deck_name(tab: DeckBuilderTab, tmp_path: Path) -> None:
    tab.video_folder_selector.set_path(str(tmp_path))
    tab.subtitle_folder_selector.set_path(str(tmp_path))
    tab.deck_name_edit.setText("")


def _setup_bad_translation_folder(tab: DeckBuilderTab, tmp_path: Path) -> None:
    tab.update_config(replace(tab.config, secondary_subtitle_enabled=True))
    tab.video_folder_selector.set_path(str(tmp_path))
    tab.subtitle_folder_selector.set_path(str(tmp_path))
    tab.deck_name_edit.setText("Deck")
    tab.secondary_folder_selector.set_path(str(tmp_path / "gone"))


@pytest.mark.parametrize(
    "setup",
    [_setup_no_folders, _setup_missing_folder, _setup_empty_deck_name, _setup_bad_translation_folder],
    ids=["no_folders", "missing_folder", "empty_deck_name", "bad_translation_folder"],
)
def test_build_request_refusals_raise_screen_issue(tab, tmp_path, setup):
    setup(tab, tmp_path)
    shown: list = []
    tab.show_screen_issue = lambda issue, **_kw: shown.append(issue)  # type: ignore[method-assign]

    assert tab._build_request() is None
    assert shown


def test_build_request_returns_a_complete_request_on_success(tab, tmp_path):
    video_folder = tmp_path / "video"
    subtitle_folder = tmp_path / "subs"
    video_folder.mkdir()
    subtitle_folder.mkdir()

    tab.video_folder_selector.set_path(str(video_folder))
    tab.subtitle_folder_selector.set_path(str(subtitle_folder))
    tab.deck_name_edit.setText("  My Deck  ")
    tab.skip_known_checkbox.setChecked(True)
    tab.review_words_checkbox.setChecked(True)
    tab.offset_spinbox.setValue(1.5)

    request = tab._build_request()

    assert request is not None
    assert request.video_folder == video_folder
    assert request.subtitle_folder == subtitle_folder
    # Stripped, not the raw field text.
    assert request.deck_name == "My Deck"
    assert request.skip_known is True
    assert request.review is True
    assert request.subtitle_offset == 1.5
    assert request.secondary_folder is None


# ---------------------------------------------------------------------------
# Pinned action bar (D6)
# ---------------------------------------------------------------------------


def test_action_bar_primary_is_build_and_secondaries_are_preview_cancel(tab):
    assert tab.action_bar is not None
    assert tab.action_bar.current_primary() is tab.build_button
    assert tab.action_bar._secondary == [tab.preview_button, tab.cancel_button]


def test_idle_screen_offers_preview_and_build_but_not_cancel(tab):
    # Build needs no preview first: from idle it scans and builds in one go.
    assert tab._run_state == "idle"
    assert tab.build_button.isEnabled()
    assert tab.preview_button.isEnabled()
    assert not tab.cancel_button.isEnabled()


# ---------------------------------------------------------------------------
# Presenter -> log wiring
# ---------------------------------------------------------------------------


def test_presenter_messages_reach_the_log(qtbot, test_config):
    presenter = GUIPresenter()
    widget = DeckBuilderTab(config=test_config, presenter=presenter, progress_callback=MagicMock())
    qtbot.addWidget(widget)

    presenter.info_signal.emit("info line")
    presenter.success_signal.emit("success line")
    presenter.warning_signal.emit("warning line")
    presenter.error_signal.emit("error line")

    messages = [entry.message for entry in widget.log_widget._entries]
    assert messages == ["info line", "success line", "warning line", "error line"]


# ---------------------------------------------------------------------------
# Config update
# ---------------------------------------------------------------------------


def test_update_config_stores_new_config(tab, test_config):
    new_config = replace(test_config, anki_deck_name="updated_deck")
    tab.update_config(new_config)
    assert tab.config is new_config


# ---------------------------------------------------------------------------
# Run lifecycle: a fake worker with DeckBuilderWorker's signal surface
# ---------------------------------------------------------------------------


class _FakeWorker(QObject):
    """Stands in for ``DeckBuilderWorker``: real signals, no thread, no pipeline.

    A QObject, so ``sender()`` inside the tab's slots names this worker exactly
    as it would name the real one.
    """

    preview_ready = pyqtSignal(object)
    item_started = pyqtSignal(str, str)
    item_pairs_progress = pyqtSignal(str, int, int)
    item_completed = pyqtSignal(str, int)
    item_failed = pyqtSignal(str, str, int)
    queue_finished = pyqtSignal(int, object)
    error = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(
        self,
        request,
        config,
        presenter,
        progress_callback=None,
        stats_service=None,
        curation_callback=None,
        parent=None,
    ):
        super().__init__()
        self.request = request
        self.config = config
        self.presenter = presenter
        self.progress_callback = progress_callback
        self.stats_service = stats_service
        self.curation_callback = curation_callback
        self.curation_processor = None
        self.calls: list[tuple] = []
        self.running = False

    def confirm(self, mode, value):
        self.calls.append(("confirm", mode, value))

    def cancel(self):
        self.calls.append(("cancel",))

    def start(self):
        self.calls.append(("start",))
        self.running = True

    def isRunning(self):
        return self.running

    def wait(self, *_args):
        return True

    def end(self, total_cards: int = 0, whitelist=None) -> None:
        """Finish the way the real run does: queue_finished, then finished."""
        self.running = False
        self.queue_finished.emit(total_cards, whitelist)
        self.finished.emit()


@pytest.fixture
def workers(monkeypatch) -> list[_FakeWorker]:
    """Every fake worker the tab constructs, in construction order."""
    created: list[_FakeWorker] = []

    def _construct(*args, **kwargs):
        worker = _FakeWorker(*args, **kwargs)
        created.append(worker)
        return worker

    monkeypatch.setattr(deck_builder_tab, "DeckBuilderWorker", _construct)
    return created


@pytest.fixture
def ready_tab(tab, tmp_path) -> DeckBuilderTab:
    """A tab whose folders hold one matching episode, named 'My Show'."""
    video = tmp_path / "My Show"
    subs = tmp_path / "subs"
    video.mkdir()
    subs.mkdir()
    (video / "Show_01.mkv").touch()
    (subs / "Show_01.srt").touch()
    tab.video_folder_selector.set_path(str(video))
    tab.subtitle_folder_selector.set_path(str(subs))
    return tab


@pytest.fixture
def registry(qapp):
    reg = TaskRegistry()
    yield reg
    reg.shutdown()


def _corpus() -> DeckCorpus:
    # 10 tokens: a=5, b=3, c=2; one pool row per lemma.
    return DeckCorpus(
        counts={"a": 5, "b": 3, "c": 2},
        row_lemmas=(frozenset({"a"}), frozenset({"b"}), frozenset({"c"})),
        episodes=2,
    )


def _scan_inputs(tab: DeckBuilderTab) -> list:
    return [
        tab.video_folder_selector,
        tab.subtitle_folder_selector,
        tab.secondary_folder_selector,
        tab.offset_spinbox,
        tab.secondary_offset_spinbox,
        tab.deck_name_edit,
        tab.skip_known_checkbox,
        tab.review_words_checkbox,
    ]


def _selection_controls(tab: DeckBuilderTab) -> list:
    return [tab.mode_combo, tab.top_n_spinbox, tab.coverage_spinbox]


def _results(tab: DeckBuilderTab) -> dict[str, str]:
    return {key: label.text() for key, label in tab._result_labels.items()}


def _select(tab: DeckBuilderTab, mode: DeckSelectionMode) -> None:
    tab.mode_combo.setCurrentIndex(tab.mode_combo.findData(mode))


def test_preview_starts_worker_unconfirmed_and_locks_inputs_including_review(ready_tab, workers):
    ready_tab.review_words_checkbox.setChecked(True)

    ready_tab.preview_button.click()

    assert len(workers) == 1
    worker = workers[0]
    assert worker.calls == [("start",)]
    assert worker.request.deck_name == "My Show"
    assert worker.request.review is True
    assert worker.curation_callback == ready_tab._curation_bridge
    assert ready_tab.worker_thread is worker
    assert ready_tab._run_state == "scanning"
    assert not any(control.isEnabled() for control in _scan_inputs(ready_tab))
    assert all(control.isEnabled() for control in _selection_controls(ready_tab))
    assert not ready_tab.preview_button.isEnabled()
    assert ready_tab.build_button.isEnabled()
    assert ready_tab.cancel_button.isEnabled()


def test_deck_name_is_stripped_into_the_request(ready_tab, workers):
    # Spaces and "::" subdecks pass through to the worker verbatim, once stripped.
    ready_tab.deck_name_edit.setText("  Show :: Season 1  ")

    ready_tab.preview_button.click()

    assert workers[0].request.deck_name == "Show :: Season 1"


def test_build_without_preview_starts_preconfirmed(ready_tab, workers):
    _select(ready_tab, DeckSelectionMode.TOP_N)
    ready_tab.top_n_spinbox.setValue(2)

    ready_tab.build_button.click()

    assert len(workers) == 1
    # confirm lands before start(): the worker never waits at the gate.
    assert workers[0].calls == [("confirm", DeckSelectionMode.TOP_N, 2.0), ("start",)]
    assert ready_tab._confirmed_selection == (DeckSelectionMode.TOP_N, 2.0)
    assert ready_tab._run_state == "building"
    assert not ready_tab.build_button.isEnabled()
    assert not ready_tab.preview_button.isEnabled()
    assert ready_tab.cancel_button.isEnabled()
    assert not any(control.isEnabled() for control in _selection_controls(ready_tab))


def test_build_during_scan_preconfirms(ready_tab, workers):
    ready_tab.preview_button.click()

    ready_tab.build_button.click()

    assert len(workers) == 1
    assert workers[0].calls == [("start",), ("confirm", DeckSelectionMode.ALL, 0.0)]
    assert ready_tab._run_state == "building"


def test_preconfirmed_run_stays_building_when_preview_arrives(ready_tab, workers):
    ready_tab.build_button.click()

    workers[0].preview_ready.emit(_corpus())

    assert ready_tab._run_state == "building"
    assert ready_tab._corpus is not None
    assert _results(ready_tab)["card_count"] == "3"
    assert not ready_tab.build_button.isEnabled()


def test_preview_ready_fills_numbers_and_mode_change_recomputes_without_worker(ready_tab, workers):
    ready_tab.preview_button.click()

    workers[0].preview_ready.emit(_corpus())

    assert ready_tab._run_state == "preview_ready"
    assert _results(ready_tab) == {
        "total_tokens": "10",
        "unique_lemmas": "3",
        "candidate_count": "3",
        "projected_coverage_pct": "100.0%",
        "known_skipped": "0",
        "card_count": "3",
    }

    # The recompute is the tab's own maths over the cached corpus.
    ready_tab.worker_thread = None
    _select(ready_tab, DeckSelectionMode.TOP_N)
    ready_tab.top_n_spinbox.setValue(1)

    results = _results(ready_tab)
    assert results["candidate_count"] == "1"
    assert results["projected_coverage_pct"] == "50.0%"
    assert results["card_count"] == "1"
    assert workers[0].calls == [("start",)]


def test_build_after_preview_confirms_current_mode_and_value(ready_tab, workers):
    ready_tab.preview_button.click()
    workers[0].preview_ready.emit(_corpus())
    _select(ready_tab, DeckSelectionMode.COVERAGE_PCT)
    ready_tab.coverage_spinbox.setValue(50.0)

    ready_tab.build_button.click()

    assert workers[0].calls == [("start",), ("confirm", DeckSelectionMode.COVERAGE_PCT, 50.0)]
    assert ready_tab._confirmed_selection == (DeckSelectionMode.COVERAGE_PCT, 50.0)
    assert ready_tab._run_state == "building"
    assert not any(control.isEnabled() for control in _selection_controls(ready_tab))


def test_cancel_sets_cancel_requested_and_marks_receipt_cancelled(ready_tab, workers):
    ready_tab.preview_button.click()
    worker = workers[0]

    ready_tab.cancel_button.click()

    assert ready_tab._cancel_requested is True
    assert ("cancel",) in worker.calls
    # A cancelled run cannot be confirmed into a build on its way out.
    assert not ready_tab.build_button.isEnabled()

    worker.end()

    assert ready_tab._receipt_widget.receipt.outcome is TerminalOutcome.CANCELLED
    assert ready_tab.progress_widget.status_label.text() == "Cancelled"
    assert ready_tab._run_state == "idle"
    assert ready_tab.preview_button.isEnabled()


def test_late_preview_from_a_cancelled_worker_is_ignored(ready_tab, workers):
    ready_tab.preview_button.click()
    ready_tab.cancel_button.click()

    workers[0].preview_ready.emit(_corpus())

    assert ready_tab._run_state == "scanning"
    assert ready_tab._corpus is None
    assert not ready_tab.build_button.isEnabled()


def test_signals_from_a_superseded_worker_are_ignored(ready_tab, workers):
    ready_tab.preview_button.click()
    old = workers[0]
    old.end()
    ready_tab.preview_button.click()
    assert len(workers) == 2

    # Queued signals the finished run left behind land under the new run.
    old.preview_ready.emit(_corpus())
    old.item_failed.emit("id", "stale failure", 0)
    old.error.emit("stale error")
    old.end(total_cards=7)

    assert ready_tab.worker_thread is workers[1]
    assert ready_tab._run_state == "scanning"
    assert ready_tab._corpus is None
    assert ready_tab._run_failed is False
    assert ready_tab._run_had_item_failures is False
    assert ready_tab.issue_banner().current_issue() is None


def test_no_pairs_raises_screen_issue_and_starts_nothing(tab, workers, tmp_path):
    video = tmp_path / "My Show"
    subs = tmp_path / "subs"
    video.mkdir()
    subs.mkdir()
    (video / "Show_01.mkv").touch()
    tab.video_folder_selector.set_path(str(video))
    tab.subtitle_folder_selector.set_path(str(subs))

    tab.preview_button.click()
    tab.build_button.click()

    assert workers == []
    issue = tab.issue_banner().current_issue()
    assert issue is not None
    assert issue.summary == "No video/subtitle pairs found. Check the folders."
    assert tab._run_state == "idle"


def test_real_curation_bridge_reaches_the_dialog_signal(ready_tab, workers, qtbot):
    ready_tab.review_words_checkbox.setChecked(True)
    ready_tab.preview_button.click()
    bridge = workers[0].curation_callback
    # Opening the curator is its own suite's business; here only the hand-off matters.
    ready_tab._curation_requested.disconnect(ready_tab._on_curation_requested)
    results: list = []

    with qtbot.waitSignal(ready_tab._curation_requested, timeout=2000) as blocker:
        thread = threading.Thread(target=lambda: results.append(bridge(["w1"])), daemon=True)
        thread.start()

    assert blocker.args == [["w1"]]
    ready_tab._curation_result = ["w1"]
    ready_tab._curation_event.set()
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert results == [["w1"]]


def test_task_published_and_cancel_request_routes_to_cancel(ready_tab, workers, registry):
    ready_tab.bind_task_registry(registry)

    ready_tab.preview_button.click()

    snapshot = registry.snapshot("run.deckbuilder")
    assert snapshot is not None
    assert snapshot.is_running
    assert snapshot.title == "Deck Builder"

    workers[0].preview_ready.emit(_corpus())
    assert registry.snapshot("run.deckbuilder").detail == "Preview ready. Press Build Deck to create the cards."

    registry.request_cancel("run.deckbuilder")

    assert ready_tab._cancel_requested is True
    assert ("cancel",) in workers[0].calls
    assert registry.snapshot("run.deckbuilder").cancelling

    workers[0].end()
    assert registry.snapshot("run.deckbuilder").outcome is TaskOutcome.CANCELLED


def test_receipt_records_counts_and_logs_closing_line(ready_tab, workers, registry):
    ready_tab.bind_task_registry(registry)
    ready_tab.preview_button.click()
    worker = workers[0]
    worker.preview_ready.emit(_corpus())
    _select(ready_tab, DeckSelectionMode.TOP_N)
    ready_tab.top_n_spinbox.setValue(2)
    ready_tab.build_button.click()

    worker.item_completed.emit("id", 2)
    worker.end(total_cards=2)

    receipt = ready_tab._receipt_widget.receipt
    assert receipt.outcome is TerminalOutcome.SUCCESS
    assert (receipt.items_total, receipt.items_completed, receipt.notes_added) == (1, 1, 2)
    # Top 2 of a=5, b=3, c=2 covers 8 of 10 tokens.
    ready_tab.presenter.show_success.assert_called_with(
        "Created 2 cards in deck 'My Show'; the candidate words cover ~80.0% of tokens."
    )
    assert registry.snapshot("run.deckbuilder").outcome is TaskOutcome.SUCCEEDED
    assert ready_tab._run_state == "idle"


def test_item_pairs_progress_moves_the_bar_and_the_published_count(ready_tab, workers, registry):
    ready_tab.bind_task_registry(registry)
    ready_tab.build_button.click()

    workers[0].item_pairs_progress.emit("id", 1, 4)

    assert ready_tab.progress_widget._last_percent == 25
    assert ready_tab.progress_widget.status_label.text() == "1 of 4 episodes mined"
    snapshot = registry.snapshot("run.deckbuilder")
    assert (snapshot.current, snapshot.total) == (1, 4)


def test_item_failed_yields_failed_task_outcome(ready_tab, workers, registry):
    ready_tab.bind_task_registry(registry)
    ready_tab.build_button.click()
    worker = workers[0]

    worker.item_failed.emit("id", "1 of 2 episodes failed, starting with Show_01.mkv.", 1)
    worker.end(total_cards=1)

    assert ready_tab._run_had_item_failures is True
    receipt = ready_tab._receipt_widget.receipt
    assert (receipt.items_failed, receipt.notes_added) == (1, 1)
    issue = ready_tab.issue_banner().current_issue()
    assert issue is not None
    assert issue.details == "1 of 2 episodes failed, starting with Show_01.mkv."
    assert registry.snapshot("run.deckbuilder").outcome is TaskOutcome.FAILED


def test_all_prepass_failures_fail_without_preview(ready_tab, workers, registry):
    ready_tab.bind_task_registry(registry)
    ready_tab.preview_button.click()
    worker = workers[0]

    # Every episode failed the pre-pass: no preview_ready, one item failure.
    worker.item_failed.emit("id", "1 of 1 episodes failed, starting with Show_01.mkv.", 0)
    worker.end(total_cards=0)

    assert ready_tab._corpus is None
    presenter = ready_tab.presenter
    lines = [c.args[0] for c in (*presenter.show_info.call_args_list, *presenter.show_success.call_args_list)]
    assert "Created 0 cards in deck 'My Show'." in lines
    assert not any("cover" in line for line in lines)
    assert registry.snapshot("run.deckbuilder").outcome is TaskOutcome.FAILED
    assert ready_tab.progress_widget.status_label.text() == "Finished with errors — see log"


def test_worker_error_fails_the_run_and_raises_a_screen_issue(ready_tab, workers, registry):
    ready_tab.bind_task_registry(registry)
    ready_tab.preview_button.click()
    worker = workers[0]

    worker.error.emit("AnkiConnect is not reachable.")
    worker.end()

    assert ready_tab._run_failed is True
    issue = ready_tab.issue_banner().current_issue()
    assert issue is not None
    assert issue.details == "AnkiConnect is not reachable."
    ready_tab.presenter.show_error.assert_called_with("AnkiConnect is not reachable.")
    assert ready_tab._receipt_widget.receipt.outcome is TerminalOutcome.FAILED
    assert registry.snapshot("run.deckbuilder").outcome is TaskOutcome.FAILED


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("cancelled", "Cancelled"),
        ("failed", "Failed — see log"),
        ("item_failed", "Finished with errors — see log"),
        ("success", "Complete — 3 cards created"),
    ],
)
def test_terminal_progress_state_per_outcome(ready_tab, workers, outcome, expected):
    ready_tab.build_button.click()
    worker = workers[0]
    if outcome == "cancelled":
        ready_tab.cancel_button.click()
    elif outcome == "failed":
        worker.error.emit("boom")
    elif outcome == "item_failed":
        worker.item_failed.emit("id", "boom", 3)
    else:
        worker.item_completed.emit("id", 3)

    worker.end(total_cards=3)

    assert ready_tab.progress_widget.status_label.text() == expected


def test_start_failure_rolls_back_to_idle(ready_tab, monkeypatch):
    def _explode(*_args, **_kwargs):
        raise RuntimeError("could not construct")

    monkeypatch.setattr(deck_builder_tab, "DeckBuilderWorker", _explode)

    ready_tab.build_button.click()

    assert ready_tab.worker_thread is None
    assert ready_tab._run_failed is True
    assert ready_tab._run_state == "idle"
    assert ready_tab.preview_button.isEnabled()
    assert ready_tab._receipt_widget.receipt.outcome is TerminalOutcome.FAILED


def test_a_second_start_while_running_is_refused(ready_tab, workers):
    ready_tab.preview_button.click()

    ready_tab._start(confirm_now=False)

    assert len(workers) == 1


def test_release_refused_while_preview_pending(ready_tab, workers):
    ready_tab.preview_button.click()
    workers[0].preview_ready.emit(_corpus())
    processor = MagicMock(name="EpisodeProcessor")
    workers[0].curation_processor = processor

    # Parked at the Build gate still counts as running: its processor is in use.
    assert ready_tab.release_dictionary_resources() is False
    processor.release_dictionary_resources.assert_not_called()

    workers[0].end()
    assert ready_tab.release_dictionary_resources() is True
    processor.release_dictionary_resources.assert_called_once_with()


# ---------------------------------------------------------------------------
# Durable recovery (D16-C): a snapshot row only while a build is running
# ---------------------------------------------------------------------------


def test_snapshot_has_a_row_only_while_building(ready_tab, workers):
    def _assert_empty_and_discarded() -> None:
        snapshot = ready_tab.queue_snapshot()
        assert snapshot.items == ()
        store.save(snapshot)
        assert not store.snapshot_path(ready_tab.QUEUE_STATE_KEY).exists()

    _assert_empty_and_discarded()  # idle

    ready_tab.preview_button.click()
    _assert_empty_and_discarded()  # scanning

    workers[0].preview_ready.emit(_corpus())
    _assert_empty_and_discarded()  # preview_ready

    ready_tab.build_button.click()
    worker = workers[0]
    worker.item_completed.emit("id", 1)
    worker.end(total_cards=1)
    _assert_empty_and_discarded()  # completed -> idle

    ready_tab.build_button.click()
    workers[1].error.emit("boom")
    workers[1].end()
    _assert_empty_and_discarded()  # failed -> idle


def test_building_snapshot_is_one_interrupted_row(ready_tab, workers, tmp_path):
    ready_tab.update_config(replace(ready_tab.config, secondary_subtitle_enabled=True))
    secondary = tmp_path / "trans"
    secondary.mkdir()
    ready_tab.secondary_folder_selector.set_path(str(secondary))
    ready_tab.secondary_offset_spinbox.setValue(-1.5)
    ready_tab.offset_spinbox.setValue(2.5)

    ready_tab.build_button.click()
    request = workers[0].request

    snapshot = ready_tab.queue_snapshot()
    assert [item.item_id for item in snapshot.items] == ["build"]
    row = snapshot.items[0]
    assert row.source == store.folder_pair_source(
        request.video_folder,
        request.subtitle_folder,
        offset=request.subtitle_offset,
        secondary=request.secondary_folder,
        secondary_offset=request.secondary_offset,
    )
    assert row.title == request.deck_name
    assert row.status == store.status_from_run_state("processing")


def test_cancel_during_building_still_snapshots_one_row_before_finished(ready_tab, workers):
    """Task 7 carry-over: Cancel leaves ``_run_state`` at "building" until
    ``finished`` arrives, and cards may already be in Anki -- that IS an
    interrupted build, so it still snapshots one row while the cancel drains."""
    ready_tab.build_button.click()

    ready_tab.cancel_button.click()

    assert ready_tab._run_state == "building"
    snapshot = ready_tab.queue_snapshot()
    assert len(snapshot.items) == 1
    assert snapshot.items[0].status == store.STATUS_INTERRUPTED


def test_restore_refills_form_and_shows_interrupted_banner(tab, tmp_path):
    video = tmp_path / "My Show"
    subtitle = tmp_path / "subs"
    video.mkdir()
    subtitle.mkdir()
    snapshot = QueueSnapshot(
        key=tab.QUEUE_STATE_KEY,
        items=(
            QueueItemSnapshot(
                item_id="build",
                source=store.folder_pair_source(video, subtitle, offset=1.5),
                title="My Show",
                status=store.STATUS_INTERRUPTED,
            ),
        ),
    )

    assert tab.restore_queue_snapshot(snapshot) == 1

    assert tab.video_folder_selector.get_path() == str(video)
    assert tab.subtitle_folder_selector.get_path() == str(subtitle)
    assert tab.offset_spinbox.value() == 1.5
    assert tab.deck_name_edit.text() == "My Show"
    issue = tab.issue_banner().current_issue()
    assert issue is not None
    assert issue.summary == (
        "The build into deck 'My Show' was interrupted when Anki Miner closed. "
        "Build Deck again to finish it; words already in the deck are skipped."
    )


def test_restore_with_missing_folder_shows_folder_not_found(tab, tmp_path):
    video = tmp_path / "gone"
    subtitle = tmp_path / "subs"
    subtitle.mkdir()
    snapshot = QueueSnapshot(
        key=tab.QUEUE_STATE_KEY,
        items=(
            QueueItemSnapshot(
                item_id="build",
                source=store.folder_pair_source(video, subtitle),
                title="My Show",
                status=store.STATUS_INTERRUPTED,
            ),
        ),
    )

    assert tab.restore_queue_snapshot(snapshot) == 1

    issue = tab.issue_banner().current_issue()
    assert issue is not None
    assert issue.summary == f"Folder not found: {video}"


def test_restore_refused_while_running(ready_tab, workers):
    ready_tab.preview_button.click()  # -> scanning, not idle

    snapshot = QueueSnapshot(
        key=ready_tab.QUEUE_STATE_KEY,
        items=(
            QueueItemSnapshot(
                item_id="build",
                source=store.folder_pair_source(Path("/a"), Path("/b")),
                title="X",
                status=store.STATUS_INTERRUPTED,
            ),
        ),
    )

    assert ready_tab.restore_queue_snapshot(snapshot) == 0


def test_clear_queue_resets_form(ready_tab):
    ready_tab.deck_name_edit.setText("Custom")
    ready_tab.offset_spinbox.setValue(3.0)
    ready_tab.secondary_offset_spinbox.setValue(-2.0)
    ready_tab.show_screen_issue(ScreenIssue(summary="stale issue"))

    ready_tab.clear_queue()

    assert ready_tab.video_folder_selector.get_path() == ""
    assert ready_tab.subtitle_folder_selector.get_path() == ""
    assert ready_tab.secondary_folder_selector.get_path() == ""
    assert ready_tab.offset_spinbox.value() == ready_tab.config.subtitle_offset
    assert ready_tab.secondary_offset_spinbox.value() == 0.0
    assert ready_tab.deck_name_edit.text() == ""
    assert ready_tab.issue_banner().current_issue() is None
