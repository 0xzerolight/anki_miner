"""Tests for MainWindow's schema-staleness migration prompt (4.0).

On startup, when an enabled indexed slot in any of the four indexed families is
schema-stale, the window offers a one-click Reimport All so the user never hits
a silent zero-card run (dictionary), an unfiltered flood of rare words
(frequency), a blank pitch field, or missing expression audio.

The sidecar scan runs off the GUI thread (``run_off_thread``) and the prompt is
shown from the ``_on_stale_resources_scanned`` continuation; the prompt-logic
tests drive that continuation directly, and a separate test verifies the
off-thread dispatch wiring. The dialog's ``exec`` is monkeypatched to click a
real button, so no modal runs and ``clickedButton()`` stays honest.

Startup prewarm is here too: it holds the indexes a repair rebuilds, and
``release_dictionary_resources`` refuses outright while it runs, so it starts
from this continuation rather than beside it.
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


def _patch_stale(monkeypatch, *, dicts=(), freqs=(), pitches=(), packs=()):
    """Point all four registry scan seams at fixed metas."""
    import anki_miner.services.audio_packs.registry as audio_reg
    import anki_miner.services.dictionary.registry as dict_reg
    import anki_miner.services.frequency.registry as freq_reg
    import anki_miner.services.pitch_accent.registry as pitch_reg

    monkeypatch.setattr(dict_reg, "stale_enabled_dicts", lambda config: list(dicts))
    monkeypatch.setattr(freq_reg, "stale_enabled_freq_sources", lambda config: list(freqs))
    monkeypatch.setattr(pitch_reg, "stale_enabled_pitch_sources", lambda config: list(pitches))
    monkeypatch.setattr(audio_reg, "stale_enabled_audio_packs", lambda config: list(packs))


def _answer_prompt(monkeypatch, *, accept: bool) -> SimpleNamespace:
    """Answer the stale-resource prompt by clicking a real button, as a user does.

    ``exec`` is the seam (same one ``test_recovery_controller`` uses) so
    ``clickedButton()`` reports a genuine click rather than a stubbed enum.
    Returns a record of what the prompt put on screen.
    """
    from PyQt6.QtWidgets import QMessageBox

    from anki_miner.gui import main_window as mw_module

    role = QMessageBox.ButtonRole.AcceptRole if accept else QMessageBox.ButtonRole.RejectRole
    seen = SimpleNamespace(bodies=[], buttons=[])

    def _click(box) -> int:
        seen.bodies.append(box.text())
        seen.buttons.append([b.text() for b in box.buttons()])
        for button in box.buttons():
            if box.buttonRole(button) == role:
                button.click()
                return 0
        raise AssertionError(f"no button with role {role}")

    monkeypatch.setattr(mw_module.QMessageBox, "exec", _click)
    return seen


def _stub_prewarm(monkeypatch, window) -> list[str]:
    """Record prewarm starts instead of spinning a real PrewarmWorker."""
    started: list[str] = []
    monkeypatch.setattr(window, "_start_prewarm", lambda: started.append("prewarm"))
    return started


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


def test_prompt_offers_a_named_reimport_button(main_window, monkeypatch, qtbot):
    """The accept button runs every family's Reimport All, so it says so.

    'Yes' named the answer to a question the dialog then had to spell out;
    the button names the action instead.
    """
    seen = _answer_prompt(monkeypatch, accept=True)
    _stub_settings_trigger(qtbot, main_window)

    main_window._stale_resource_prompt_handled = False
    main_window._on_stale_resources_scanned({"dictionary": [("old-dict", "Old Dict")]})

    assert seen.buttons == [["Reimport All", "Later"]]
    assert "Re-import them now?" not in seen.bodies[0]


def test_stale_prompt_yes_triggers_reimport(main_window, monkeypatch, qtbot):
    _answer_prompt(monkeypatch, accept=True)
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
    _answer_prompt(monkeypatch, accept=False)
    trigger = _stub_settings_trigger(qtbot, main_window)

    main_window._stale_resource_prompt_handled = False
    main_window._on_stale_resources_scanned({"dictionary": [("old-dict", "Old Dict")]})

    trigger.assert_not_called()


def test_no_stale_resource_no_prompt(main_window, monkeypatch, qtbot):
    seen = _answer_prompt(monkeypatch, accept=True)
    trigger = _stub_settings_trigger(qtbot, main_window)

    main_window._stale_resource_prompt_handled = False
    # Every family scanned clean — including the two that are simply not
    # configured, which is the common case and must stay silent.
    main_window._on_stale_resources_scanned({"dictionary": [], "frequency": [], "pitch": []})

    assert seen.bodies == []  # no dialog shown
    trigger.assert_not_called()
    # Guard stays down so a later launch re-offers if still stale.
    assert main_window._stale_resource_prompt_handled is False


def test_prompt_handled_once_per_session(main_window, monkeypatch, qtbot):
    seen = _answer_prompt(monkeypatch, accept=False)
    _stub_settings_trigger(qtbot, main_window)

    stale = {"dictionary": [("old-dict", "Old Dict")]}
    main_window._stale_resource_prompt_handled = False
    main_window._on_stale_resources_scanned(stale)
    main_window._on_stale_resources_scanned(stale)  # second call is a no-op (guard set)

    assert len(seen.bodies) == 1


def test_families_run_one_after_another(main_window, monkeypatch, qtbot):
    """Three stale families produce one dialog and three *sequenced* batches.

    Firing them together would stack three ApplicationModal progress dialogs,
    so each batch starts only when the previous one reports completion.
    """
    _answer_prompt(monkeypatch, accept=True)
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
        packs=[SimpleNamespace(pack_id="old-pack", source="Old Pack")],
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
    # The offloaded work runs all four scans and returns (id, name) pairs.
    assert captured["work_result"] == {
        "dictionary": [("old-dict", "Old Dict")],
        "frequency": [("old-freq", "Old Freq")],
        "pitch": [],
        "audio": [("old-pack", "Old Pack")],
    }
    # The continuation is the GUI-thread prompt handler.
    assert captured["on_done"] == main_window._on_stale_resources_scanned


# ---------------------------------------------------------------------------
# Prewarm ordering: prewarm holds the indexes a repair has to rebuild, and
# release_dictionary_resources refuses outright while it runs — so starting it
# before the prompt is answered made every family's Reimport All fail.
# ---------------------------------------------------------------------------


def test_boot_does_not_start_prewarm_before_the_prompt(main_window, monkeypatch):
    from anki_miner.gui import main_window as mw_module

    scheduled: list = []
    monkeypatch.setattr(mw_module.QTimer, "singleShot", staticmethod(lambda _ms, fn: scheduled.append(fn)))
    monkeypatch.setattr(main_window, "_start_prewarm", MagicMock(name="_start_prewarm"))

    main_window._post_setup_boot_started = False
    main_window._start_post_setup_boot_once()

    assert main_window._start_prewarm not in scheduled
    main_window._start_prewarm.assert_not_called()


def test_prewarm_starts_when_nothing_is_stale(main_window, monkeypatch):
    started = _stub_prewarm(monkeypatch, main_window)

    main_window._stale_resource_prompt_handled = False
    main_window._on_stale_resources_scanned({"dictionary": [], "frequency": [], "pitch": []})

    assert started == ["prewarm"]


def test_prewarm_starts_after_the_user_declines(main_window, monkeypatch, qtbot):
    _answer_prompt(monkeypatch, accept=False)
    _stub_settings_trigger(qtbot, main_window)
    started = _stub_prewarm(monkeypatch, main_window)

    main_window._stale_resource_prompt_handled = False
    main_window._on_stale_resources_scanned({"dictionary": [("old-dict", "Old Dict")]})

    assert started == ["prewarm"]


def test_prewarm_waits_for_the_repair_chain_to_drain(main_window, monkeypatch, qtbot):
    _answer_prompt(monkeypatch, accept=True)
    trigger = _stub_settings_trigger(qtbot, main_window)
    started = _stub_prewarm(monkeypatch, main_window)

    completions: list = []
    trigger.side_effect = lambda only_ids, *, kind, on_complete=None: completions.append((kind, on_complete))

    main_window._stale_resource_prompt_handled = False
    main_window._on_stale_resources_scanned({"dictionary": [("d", "Dict")], "frequency": [("f", "Freq")]})

    assert started == []  # first batch in flight
    completions[-1][1]()
    assert started == []  # second batch in flight
    completions[-1][1]()
    assert started == ["prewarm"]


def test_prewarm_starts_when_there_is_no_settings_tab(main_window, monkeypatch):
    _answer_prompt(monkeypatch, accept=True)
    started = _stub_prewarm(monkeypatch, main_window)

    main_window._stale_resource_prompt_handled = False
    main_window._on_stale_resources_scanned({"dictionary": [("old-dict", "Old Dict")]})

    assert started == ["prewarm"]


def test_prewarm_starts_when_the_scan_itself_fails(main_window, monkeypatch):
    started = _stub_prewarm(monkeypatch, main_window)

    main_window._on_stale_scan_failed("freqs root unreadable")

    assert started == ["prewarm"]


def test_prewarm_starts_when_the_prompt_was_already_handled(main_window, monkeypatch):
    started = _stub_prewarm(monkeypatch, main_window)

    main_window._stale_resource_prompt_handled = True
    main_window._maybe_prompt_stale_resources()
    main_window._on_stale_resources_scanned({"dictionary": [("old-dict", "Old Dict")]})

    assert started == ["prewarm", "prewarm"]


def test_start_prewarm_is_idempotent(main_window, monkeypatch):
    import anki_miner.gui.workers.prewarm_worker as prewarm_module

    made: list = []
    monkeypatch.setattr(prewarm_module, "PrewarmWorker", lambda config: made.append(config) or MagicMock())
    monkeypatch.setattr(main_window.background_tasks, "set_prewarm", lambda worker: None)

    main_window._prewarm_started = False
    main_window._start_prewarm()
    main_window._start_prewarm()

    assert len(made) == 1


# ---------------------------------------------------------------------------
# Audio packs: repaired by the prompt, but never a reason mining is blocked.
# ---------------------------------------------------------------------------


def test_audio_family_is_repaired_with_the_others(main_window, monkeypatch, qtbot):
    _answer_prompt(monkeypatch, accept=True)
    trigger = _stub_settings_trigger(qtbot, main_window)
    completions: list = []
    trigger.side_effect = lambda only_ids, *, kind, on_complete=None: completions.append((kind, on_complete))

    main_window._stale_resource_prompt_handled = False
    main_window._on_stale_resources_scanned({"dictionary": [("d", "Dict")], "audio": [("kore", "Kore Audio")]})

    assert [kind for kind, _cb in completions] == ["dictionary"]
    completions[-1][1]()
    assert [kind for kind, _cb in completions] == ["dictionary", "audio"]


def test_audio_only_staleness_also_claims_mining_is_blocked(main_window, monkeypatch, qtbot):
    """Audio packs gate mining, so stale audio claims mining is blocked."""
    seen = _answer_prompt(monkeypatch, accept=False)
    _stub_settings_trigger(qtbot, main_window)

    main_window._stale_resource_prompt_handled = False
    main_window._on_stale_resources_scanned({"audio": [("kore", "Kore Audio")]})

    assert "Kore Audio" in seen.bodies[0]
    assert "Mining is blocked" in seen.bodies[0]


def test_a_gating_family_still_says_mining_is_blocked(main_window, monkeypatch, qtbot):
    seen = _answer_prompt(monkeypatch, accept=False)
    _stub_settings_trigger(qtbot, main_window)

    main_window._stale_resource_prompt_handled = False
    main_window._on_stale_resources_scanned({"pitch": [("p1", "Kanjium")], "audio": [("kore", "Kore Audio")]})

    assert "Mining is blocked" in seen.bodies[0]
    assert "Audio packs:" in seen.bodies[0]


def test_audio_packs_are_in_the_pre_run_gate(test_config):
    """resource_staleness feeds the abort that stops a run; audio is in it.

    Reverses an earlier call to leave audio out on the grounds that a stale
    pack costs only expression audio. Cost is not the test the other three are
    held to — frequency and pitch are optional too, and they gate. What the
    gate prevents is a *silent* wrong result, and a dropped pack is one: the
    run reports success while cards fall back to the online sources or get no
    audio, with nothing but a log line saying so.
    """
    from anki_miner.services.resource_staleness import _FAMILY_LABELS

    assert "audio" in _FAMILY_LABELS
    assert set(_FAMILY_LABELS) == {"dictionary", "frequency", "pitch", "audio"}
