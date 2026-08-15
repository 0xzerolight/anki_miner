"""Tests for MainWindow's schema-staleness migration prompt (4.0).

On startup, when an enabled indexed slot in any of the three indexed families
is schema-stale, the window offers a one-click Reimport All so the user never
hits a silent zero-card run (dictionary), an unfiltered flood of rare words
(frequency), or a blank pitch field.

The sidecar scan runs off the GUI thread (``run_off_thread``) and the prompt is
shown from the ``_on_stale_resources_scanned`` continuation; the prompt-logic
tests drive that continuation directly, and a separate test verifies the
off-thread dispatch wiring. QMessageBox is monkeypatched so no real Qt modal
runs.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def main_window(qtbot, monkeypatch, patch_heavy_init, test_config):
    # first_run_setup_done=True so the deferred first-run wizard never fires.
    construction_config = replace(test_config, first_run_setup_done=True)
    # stub_first_run_setup=False mirrors the original: the wizard is already inert
    # (flag set above), so _maybe_offer_first_run_setup is left real.
    patch_heavy_init(construction_config, stub_first_run_setup=False)
    from anki_miner.gui import main_window as mw_module

    # Run any off-thread dispatch inline (no real QThread) so the startup
    # stale-resource singleShot can't leak a worker into a test that never spins
    # a loop. Individual tests re-patch run_off_thread when they assert on it.
    monkeypatch.setattr(mw_module, "run_off_thread", lambda parent, work, on_done, *a, **kw: on_done(work()))
    from anki_miner.gui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    yield window
    window.deleteLater()


def _patch_stale(monkeypatch, *, dicts=(), freqs=(), pitches=()):
    """Point all three registry scan seams at fixed metas."""
    import anki_miner.services.dictionary.registry as dict_reg
    import anki_miner.services.frequency.registry as freq_reg
    import anki_miner.services.pitch_accent.registry as pitch_reg

    monkeypatch.setattr(dict_reg, "stale_enabled_dicts", lambda config: list(dicts))
    monkeypatch.setattr(freq_reg, "stale_enabled_freq_sources", lambda config: list(freqs))
    monkeypatch.setattr(pitch_reg, "stale_enabled_pitch_sources", lambda config: list(pitches))


def _stub_settings_trigger(qtbot, window) -> MagicMock:
    """Install a minimal fake Settings tab so ``_settings_tab_index`` resolves.

    A bare MainWindow has no tabs (app.py adds them), so the prompt's Settings
    navigation needs a stand-in carrying the ``open_ui_subtab`` marker the
    index lookup keys on, plus a capturing ``trigger_reimport_all``.
    """
    from PyQt6.QtWidgets import QWidget

    fake = QWidget()
    qtbot.addWidget(fake)
    fake.open_ui_subtab = lambda: None  # marker used by _settings_tab_index
    fake.trigger_reimport_all = MagicMock(name="trigger_reimport_all")
    window.tabs.addTab(fake, "Settings")
    return fake.trigger_reimport_all


def test_stale_prompt_yes_triggers_reimport(main_window, monkeypatch, qtbot):
    from PyQt6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes)
    trigger = _stub_settings_trigger(qtbot, main_window)

    main_window._stale_resource_prompt_handled = False
    main_window._on_stale_resources_scanned(
        {"dictionary": [("old-dict", "Old Dict"), ("other-old-dict", "Other Old Dict")]}
    )

    trigger.assert_called_once()
    args, kwargs = trigger.call_args
    assert args[0] == frozenset({"old-dict", "other-old-dict"})
    assert kwargs["kind"] == "dictionary"


def test_stale_prompt_later_does_not_reimport(main_window, monkeypatch, qtbot):
    from PyQt6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.No)
    trigger = _stub_settings_trigger(qtbot, main_window)

    main_window._stale_resource_prompt_handled = False
    main_window._on_stale_resources_scanned({"dictionary": [("old-dict", "Old Dict")]})

    trigger.assert_not_called()


def test_no_stale_resource_no_prompt(main_window, monkeypatch, qtbot):
    from PyQt6.QtWidgets import QMessageBox

    called = MagicMock()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: called() or QMessageBox.StandardButton.Yes)
    trigger = _stub_settings_trigger(qtbot, main_window)

    main_window._stale_resource_prompt_handled = False
    # Every family scanned clean — including the two that are simply not
    # configured, which is the common case and must stay silent.
    main_window._on_stale_resources_scanned({"dictionary": [], "frequency": [], "pitch": []})

    called.assert_not_called()  # no dialog shown
    trigger.assert_not_called()
    # Guard stays down so a later launch re-offers if still stale.
    assert main_window._stale_resource_prompt_handled is False


def test_prompt_handled_once_per_session(main_window, monkeypatch, qtbot):
    from PyQt6.QtWidgets import QMessageBox

    q = MagicMock(return_value=QMessageBox.StandardButton.No)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: q())
    _stub_settings_trigger(qtbot, main_window)

    stale = {"dictionary": [("old-dict", "Old Dict")]}
    main_window._stale_resource_prompt_handled = False
    main_window._on_stale_resources_scanned(stale)
    main_window._on_stale_resources_scanned(stale)  # second call is a no-op (guard set)

    assert q.call_count == 1


def test_families_run_one_after_another(main_window, monkeypatch, qtbot):
    """Three stale families produce one dialog and three *sequenced* batches.

    Firing them together would stack three ApplicationModal progress dialogs,
    so each batch starts only when the previous one reports completion.
    """
    from PyQt6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes)
    trigger = _stub_settings_trigger(qtbot, main_window)

    completions: list = []

    def fake_trigger(only_ids, *, kind, on_complete=None):
        completions.append((kind, on_complete))

    trigger.side_effect = fake_trigger

    main_window._stale_resource_prompt_handled = False
    main_window._on_stale_resources_scanned(
        {
            "dictionary": [("d", "Dict")],
            "frequency": [("f", "Freq")],
            "pitch": [("p", "Pitch")],
        }
    )

    # Only the first family started; the other two wait on its callback.
    assert [kind for kind, _cb in completions] == ["dictionary"]
    completions[-1][1]()
    assert [kind for kind, _cb in completions] == ["dictionary", "frequency"]
    completions[-1][1]()
    assert [kind for kind, _cb in completions] == ["dictionary", "frequency", "pitch"]
    # The last family's completion must not loop back round.
    completions[-1][1]()
    assert len(completions) == 3


def test_scan_dispatched_off_thread(main_window, monkeypatch):
    # _maybe_prompt_stale_resources offloads the sidecar scan to run_off_thread
    # and wires _on_stale_resources_scanned as the GUI-thread continuation.
    from anki_miner.gui import main_window as mw_module

    _patch_stale(
        monkeypatch,
        dicts=[SimpleNamespace(dict_id="old-dict", source_name="Old Dict")],
        freqs=[SimpleNamespace(source_id="old-freq", source_name="Old Freq")],
        pitches=[],
    )

    captured: dict = {}

    def fake_run_off_thread(parent, work, on_done, *a, **kw):
        captured["parent"] = parent
        captured["work_result"] = work()  # the offloaded scan
        captured["on_done"] = on_done
        return MagicMock()

    monkeypatch.setattr(mw_module, "run_off_thread", fake_run_off_thread)

    main_window._stale_resource_prompt_handled = False
    main_window._maybe_prompt_stale_resources()

    assert captured["parent"] is main_window
    # The offloaded work runs all three scans and returns (id, name) pairs.
    assert captured["work_result"] == {
        "dictionary": [("old-dict", "Old Dict")],
        "frequency": [("old-freq", "Old Freq")],
        "pitch": [],
    }
    # The continuation is the GUI-thread prompt handler.
    assert captured["on_done"] == main_window._on_stale_resources_scanned
