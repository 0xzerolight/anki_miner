"""The modal-dialog ledger (decision D24).

Every ``QMessageBox`` in the GUI is classified here, keyed by source path plus
the symbol that encloses the call. Two directions are enforced:

* a call site that is not in the ledger fails the test — a new recoverable-error
  message box cannot be added without arguing for it here;
* a ledger entry with no call site fails the test — a migrated site cannot be
  left claiming a modal it no longer opens.

Deliberately *not* keyed on the message title (translating a title would rewrite
the ledger) and deliberately *not* counting calls per symbol (a ledger that
tracks multiplicity fails on every unrelated edit). Path plus enclosing symbol
is the whole key, per the consolidation ruling.

The permissible categories:

``blocker``
    The app itself cannot continue: a second instance over the same databases,
    an unhandled exception. These stay technical — they are read by someone with
    a broken install, not by someone mining.
``confirm``
    The user is about to lose data or write to Anki. A modal is the point: it is
    the last moment to say no.
``choice``
    A non-error question with no default answer the app could pick.
``notice``
    A success report for an action the user just took and is standing in front
    of. Not part of the error hierarchy; it interrupts nobody's unattended run
    because there is no run.
``w1-download`` / ``w5-queue``
    Owned by another workstream's surface (background download outcomes; queue
    rows and run receipts). Classified here, migrated there.
``recoverable``
    A recoverable failure still reported as a modal — a run-stopper on an
    unattended batch. **The ledger holds none, and must never hold one again**:
    :func:`test_no_recoverable_modal_remains` is the assertion that makes the
    category unusable rather than a place to park new work.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

GUI_ROOT = Path(__file__).resolve().parents[2] / "anki_miner" / "gui"
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Static ``QMessageBox`` methods that open a modal.
_MODAL_METHODS = frozenset({"information", "warning", "critical", "question", "about"})

PERMITTED_CATEGORIES = frozenset({"blocker", "confirm", "choice", "notice", "w1-download", "w5-queue", "recoverable"})

LEDGER: dict[str, str] = {
    # --- Whole-app blockers: a broken install, read once, kept technical -----
    "gui/app.py::_confirm_second_instance": "blocker",
    "gui/app.py::_install_excepthook._hook": "blocker",
    # --- Destructive confirmations and open questions -----------------------
    "gui/controllers/dictionary_import_flow.py::DictionaryImportFlow.restore_unlisted": "confirm",
    "gui/main_window.py::MainWindow._restyle_mined_cards": "confirm",
    "gui/main_window.py::MainWindow._on_stale_resources_scanned": "choice",
    "gui/widgets/analytics_tab.py::AnalyticsTab._on_reset_clicked": "confirm",
    "gui/widgets/backfill_tab.py::CardBackfillTab._start_apply": "confirm",
    "gui/widgets/deck_filter_tab.py::DeckFilterTab._start_apply": "confirm",
    "gui/widgets/dialogs/known_words_dialog.py::KnownWordsManagerDialog._on_import_parsed": "confirm",
    "gui/widgets/dialogs/known_words_dialog.py::KnownWordsManagerDialog._on_reset": "confirm",
    "gui/widgets/dialogs/profile_manager_dialog.py::ProfileManagerDialog._on_delete": "confirm",
    "gui/widgets/dialogs/results_dialog.py::ResultsDialog._on_undo_clicked": "confirm",
    "gui/widgets/panels/audio_pack_settings_panel.py::AudioPackSettingsPanel._confirm_chain_only_remove": "confirm",
    "gui/widgets/panels/audio_pack_settings_panel.py::AudioPackSettingsPanel._confirm_remove": "confirm",
    "gui/widgets/panels/dictionary_settings_panel.py::DictionarySettingsPanel._confirm_chain_only_remove": "confirm",
    "gui/widgets/panels/dictionary_settings_panel.py::DictionarySettingsPanel._confirm_remove": "confirm",
    "gui/widgets/panels/frequency_settings_panel.py::FrequencySettingsPanel._confirm_remove": "confirm",
    "gui/widgets/panels/pitch_settings_panel.py::PitchSettingsPanel._confirm_remove": "confirm",
    "gui/widgets/settings_tab.py::SettingsTab._apply_settings_import": "confirm",
    "gui/widgets/settings_tab.py::SettingsTab._on_rebuild_known_words": "confirm",
    "gui/widgets/settings_tab.py::SettingsTab._on_reset_to_defaults_clicked": "confirm",
    "gui/widgets/youtube_playlist_flow.py::PlaylistAddController._ask_playlist_choice": "choice",
    # D16-C's one startup question. Not an error: nothing failed, and neither
    # Restore nor Discard is an answer the app could pick for the user.
    "gui/controllers/recovery_controller.py::RecoveryController.offer": "choice",
    # --- Success reports for a foreground action ----------------------------
    "gui/controllers/audio_pack_import_flow.py::AudioPackImportFlow._add_android_db_picked.on_success": "notice",
    "gui/controllers/audio_pack_import_flow.py::AudioPackImportFlow.add_pack.on_finished": "notice",
    "gui/controllers/audio_pack_import_flow.py::AudioPackImportFlow._run_pack_reimport.on_success": "notice",
    "gui/controllers/dictionary_import_flow.py::DictionaryImportFlow._add_dict_picked.on_finished": "notice",
    "gui/controllers/dictionary_import_flow.py::DictionaryImportFlow.reimport_all": "notice",
    "gui/controllers/dictionary_import_flow.py::DictionaryImportFlow.reimport_all.on_finished": "notice",
    "gui/controllers/dictionary_import_flow.py::DictionaryImportFlow.reimport_dict.on_success": "notice",
    # Frequency and pitch share one flow now, so one key covers both families.
    "gui/controllers/source_chain_import_flow.py::SourceChainImportFlow._add_source_picked.on_finished": "notice",
    "gui/controllers/source_chain_import_flow.py::SourceChainImportFlow._continue_reimport.on_success": "notice",
    "gui/controllers/source_chain_import_flow.py::SourceChainImportFlow.reimport_all": "notice",
    "gui/controllers/source_chain_import_flow.py::SourceChainImportFlow.reimport_all.on_finished": "notice",
    "gui/main_window.py::MainWindow._restyle_mined_cards.on_result": "notice",
    "gui/main_window.py::MainWindow._run_shortcut_work.on_done": "notice",
    "gui/main_window.py::MainWindow.commit_boot": "notice",
    "gui/widgets/condense_tab.py::CondenseTab._on_audio_tracks_clicked._on_streams": "notice",
    "gui/widgets/condense_tab.py::CondenseTab._on_subtitle_tracks_clicked._on_streams": "notice",
    "gui/widgets/dialogs/export_dialog.py::ExportDialog._on_export_done": "notice",
    "gui/widgets/dialogs/known_words_dialog.py::KnownWordsManagerDialog._on_export._on_picked": "notice",
    "gui/widgets/settings_tab.py::SettingsTab._on_export_settings._on_picked": "notice",
    "gui/widgets/settings_tab.py::SettingsTab._on_retry_missing_audio_done": "notice",
    # The answer to a button the user is standing in front of, and after a
    # schema bump the *usual* answer. Silence here read as a dead button.
    "gui/widgets/settings_tab.py::SettingsTab._show_nothing_to_restore": "notice",
    "gui/widgets/single_episode_tab.py::SingleEpisodeTab._on_timing_clicked._on_parsed": "notice",
    "gui/widgets/single_episode_tab.py::SingleEpisodeTab._on_tracks_clicked._on_streams": "notice",
    "gui/widgets/subtitle_retime_tab.py::SubtitleRetimeTab._on_tracks_clicked._on_choices": "notice",
    # --- Owned by another workstream's surface ------------------------------
    "gui/widgets/dialogs/results_dialog.py::ResultsDialog._on_undo_error": "w5-queue",
    # The two terminal Batch boxes that used to sit here are gone: W1-T8's
    # inline run receipt replaced them, so a finished or cancelled run now
    # states its counts in place instead of raising "Batch Processing Complete"
    # at someone who had just pressed Cancel (D20).
    # `_process_queue`'s "No valid series in the queue to process." was the last
    # of the three terminal/refusal boxes here; W5-T7 made it a screen issue when
    # it froze the running queue. The category is down to the two below.
    "gui/widgets/batch_processing_tab.py::BatchProcessingTab._retry_failed_items": "w5-queue",
    "gui/widgets/panels/queue_panel.py::QueuePanel._clear_queue": "w5-queue",
    # --- No "recoverable" entry may ever be added here. See the module docstring.
}


class _ModalVisitor(ast.NodeVisitor):
    """Collect ``path::enclosing.symbol`` for every modal call in one module."""

    def __init__(self, relpath: str, sink: dict[str, set[str]]) -> None:
        self._relpath = relpath
        self._sink = sink
        self._scope: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802 (ast API)
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]  # noqa: N815 (ast API)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802 (ast API)
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 (ast API)
        func = node.func
        kind = ""
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "QMessageBox"
            and func.attr in _MODAL_METHODS
        ):
            kind = func.attr
        elif isinstance(func, ast.Name) and func.id == "QMessageBox":
            kind = "constructed"
        if kind:
            self._sink[f"{self._relpath}::{'.'.join(self._scope)}"].add(kind)
        self.generic_visit(node)


def observed_modals() -> dict[str, set[str]]:
    """Every modal call site under ``anki_miner/gui``, keyed path::symbol."""
    found: dict[str, set[str]] = defaultdict(set)
    for path in sorted(GUI_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "QMessageBox" not in source:
            continue
        relpath = path.relative_to(REPO_ROOT / "anki_miner").as_posix()
        _ModalVisitor(relpath, found).visit(ast.parse(source))
    return dict(found)


class TestLedgerCoverage:
    def test_every_modal_is_classified(self):
        undeclared = sorted(set(observed_modals()) - set(LEDGER))
        assert undeclared == [], (
            "New QMessageBox call sites must be classified in LEDGER. "
            "A recoverable failure belongs in a ScreenIssueBanner, not a modal."
        )

    def test_no_ledger_entry_is_stale(self):
        stale = sorted(set(LEDGER) - set(observed_modals()))
        assert stale == [], "These ledger entries no longer open a modal; delete them."

    def test_every_category_is_a_known_one(self):
        unknown = sorted({category for category in LEDGER.values() if category not in PERMITTED_CATEGORIES})
        assert unknown == []

    def test_no_recoverable_modal_remains(self):
        """The whole point of D24: a recoverable failure never halts a run."""
        remaining = sorted(key for key, category in LEDGER.items() if category == "recoverable")
        assert remaining == [], (
            "A recoverable failure belongs in a ScreenIssueBanner. " "The 'recoverable' category exists to be empty."
        )


class TestClassification:
    def test_a_docstring_mention_is_not_a_call_site(self):
        """``about_dialog`` documents ``QMessageBox.about``; documenting is not calling."""
        about_dialog = (GUI_ROOT / "widgets" / "dialogs" / "about_dialog.py").read_text(encoding="utf-8")
        assert "QMessageBox.about" in about_dialog
        assert not any(key.startswith("gui/widgets/dialogs/about_dialog.py") for key in observed_modals())

    def test_the_emergency_startup_diagnostics_stay_modal(self):
        """A broken install has no screen to put a banner on."""
        assert LEDGER["gui/app.py::_confirm_second_instance"] == "blocker"
        assert LEDGER["gui/app.py::_install_excepthook._hook"] == "blocker"
