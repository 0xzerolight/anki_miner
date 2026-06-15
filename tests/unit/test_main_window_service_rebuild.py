"""Tests for :class:`MainWindow` service rebuild on config change (T-14).

Window-owned services bound to the startup config — ``AnkiService`` (used by the
Undo delete callback) and ``ValidationService`` — must be rebuilt on every
``update_config`` so an AnkiConnect URL/port change actually reaches those code
paths instead of hitting the stale startup endpoint.

Builds a real ``MainWindow`` with heavy startup side effects patched out, like
``test_main_window_menu``.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from PyQt6.QtWidgets import QApplication

from anki_miner.config import AnkiMinerConfig

# QApplication required for any Qt widget test.
_app = QApplication.instance() or QApplication([])


def _patch_heavy_init(monkeypatch, test_config: AnkiMinerConfig) -> None:
    """Replace config persistence, validation service, and auto-check calls."""
    from anki_miner.gui import main_window as mw_module

    monkeypatch.setattr(mw_module.GUIConfigManager, "load_config", lambda: test_config)
    monkeypatch.setattr(mw_module.GUIConfigManager, "save_config", lambda cfg: None)
    monkeypatch.setattr(mw_module.ValidationService, "__init__", lambda self, *a, **kw: None)
    monkeypatch.setattr(mw_module.MainWindow, "_run_validation", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_check_for_updates", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_maybe_create_shortcut_on_first_run", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_maybe_offer_first_run_setup", lambda self: None)


@pytest.fixture
def main_window(monkeypatch, test_config):
    """Build a MainWindow without side-effect-heavy startup behaviour."""
    _patch_heavy_init(monkeypatch, test_config)
    from anki_miner.gui.main_window import MainWindow

    window = MainWindow()
    yield window
    window.deleteLater()


def test_update_config_rebuilds_anki_service(main_window, monkeypatch):
    """AnkiService must be reconstructed with the new config on update_config.

    Pins the Undo-hits-stale-URL bug: the undo callback uses
    ``self._anki_service``, which must track AnkiConnect URL changes.
    """
    from anki_miner.gui import main_window as mw_module

    constructed_with: list[AnkiMinerConfig] = []

    def _fake_anki_init(self, config):
        constructed_with.append(config)

    monkeypatch.setattr(mw_module.AnkiService, "__init__", _fake_anki_init)

    old_service = main_window._anki_service
    new_config = replace(main_window.config, ankiconnect_url="http://example:9999")
    main_window.update_config(new_config)

    assert main_window._anki_service is not old_service
    assert constructed_with[-1] is new_config


def test_update_config_rebuilds_validation_service(main_window, monkeypatch):
    """ValidationService must be reconstructed with the new config too."""
    from anki_miner.gui import main_window as mw_module

    constructed_with: list[AnkiMinerConfig] = []

    def _fake_validation_init(self, config):
        constructed_with.append(config)

    monkeypatch.setattr(mw_module.ValidationService, "__init__", _fake_validation_init)

    old_service = main_window.validation_service
    new_config = replace(main_window.config, ankiconnect_url="http://example:9999")
    main_window.update_config(new_config)

    assert main_window.validation_service is not old_service
    assert constructed_with[-1] is new_config


def test_undo_callback_uses_rebuilt_anki_service(main_window, monkeypatch):
    """The Undo delete path must target the rebuilt (current-config) service.

    Drives _on_processing_result so the actual undo_callback is exercised,
    proving no stale captured reference survives a config change.
    """
    from anki_miner.gui import main_window as mw_module
    from anki_miner.models import ProcessingResult

    delete_calls: list[tuple[str, list[int]]] = []

    class _FakeAnki:
        def __init__(self, config):
            self.url = config.ankiconnect_url

        def delete_notes(self, note_ids):
            delete_calls.append((self.url, note_ids))
            return len(note_ids)

    monkeypatch.setattr(mw_module, "AnkiService", _FakeAnki)

    # Capture the undo_callback handed to the dialog, and skip exec().
    captured = {}

    class _FakeDialog:
        undo_completed = False

        def __init__(self, result, parent, undo_callback=None):
            captured["cb"] = undo_callback

        def exec(self):
            return 0

    monkeypatch.setattr(mw_module, "ResultsDialog", _FakeDialog)

    # Change config -> service rebuilds against the new URL.
    new_config = replace(main_window.config, ankiconnect_url="http://new:1234", enable_history=False)
    main_window.update_config(new_config)

    result = ProcessingResult(
        total_words_found=1,
        new_words_found=1,
        cards_created=1,
        card_ids=[42],
    )
    main_window._on_processing_result(result)

    captured["cb"]([42])

    assert delete_calls == [("http://new:1234", [42])]
