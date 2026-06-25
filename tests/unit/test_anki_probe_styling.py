"""Tests for AnkiProbeController's card-styling auto-sync (Issue #44).

The controller's decision logic — sync-on-Save gating, the one-time migration
reseed, deferred-change push, and live/pending status — is exercised directly
with a mocked panel and a stubbed ``_start_styling_write`` so no real worker
threads or AnkiConnect calls are involved.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QWidget

from anki_miner.config import create_default_config
from anki_miner.gui.controllers.anki_probe_controller import AnkiProbeController


def _make(*, migrated, preset="minimal", touched=False, custom_css="", note_type="NT"):
    """Build a controller with a mocked panel and a stubbed write path."""
    panel = MagicMock()
    panel.get_card_style_preset.return_value = preset
    panel.is_styling_user_touched.return_value = touched
    panel.get_custom_css.return_value = custom_css
    panel.get_note_type.return_value = note_type
    panel.get_ankiconnect_url.return_value = ""
    cfg = replace(create_default_config(), card_style_migrated=migrated, card_style_preset=preset)
    persist = MagicMock()
    ctrl = AnkiProbeController(
        parent=MagicMock(spec=QWidget),
        anki_panel=panel,
        filtering_panel=MagicMock(),
        get_config=lambda: cfg,
        persist_styling=persist,
    )
    # Replace the real worker-spawning write with a spy so decisions are testable
    # without threads.
    ctrl._start_styling_write = MagicMock()  # type: ignore[method-assign]
    return ctrl, panel, persist


# --- sync_styling (Save) gating ------------------------------------------------


def test_sync_skips_when_migration_pending_and_untouched():
    ctrl, panel, persist = _make(migrated=False, touched=False)
    ctrl.sync_styling()
    ctrl._start_styling_write.assert_not_called()
    persist.assert_not_called()
    panel.set_styling_status.assert_called_once()  # a "will sync later" notice


def test_sync_writes_when_touched_even_if_pending():
    ctrl, _panel, _persist = _make(migrated=False, touched=True, preset="minimal")
    ctrl.sync_styling()
    ctrl._start_styling_write.assert_called_once_with("minimal")


def test_sync_writes_when_migrated():
    ctrl, _panel, _persist = _make(migrated=True, touched=False, preset="off")
    ctrl.sync_styling()
    ctrl._start_styling_write.assert_called_once_with("off")


# --- migration reseed (probe result, not yet migrated) -------------------------


def test_reseed_absent_block_sets_off():
    ctrl, panel, persist = _make(migrated=False, touched=False, preset="default")
    ctrl._on_styling_probe_result(present=False, preset_id="")
    panel.set_card_style_preset.assert_called_once_with("off")
    persist.assert_called_once_with("off", True)
    ctrl._start_styling_write.assert_not_called()  # already matches reality


def test_reseed_present_block_uses_detected_preset():
    ctrl, panel, persist = _make(migrated=False, touched=False, preset="default")
    ctrl._on_styling_probe_result(present=True, preset_id="minimal")
    panel.set_card_style_preset.assert_called_once_with("minimal")
    persist.assert_called_once_with("minimal", True)


def test_reseed_legacy_block_keeps_saved_preset():
    # Legacy block (preset_id="") → fall back to the saved preference.
    ctrl, panel, persist = _make(migrated=False, touched=False, preset="minimal")
    ctrl._on_styling_probe_result(present=True, preset_id="")
    panel.set_card_style_preset.assert_called_once_with("minimal")
    persist.assert_called_once_with("minimal", True)


def test_reseed_retired_live_id_adopts_replacement():
    # A live block written by an old version records the retired ``yomitan-classic``
    # id; the reseed must adopt its surviving replacement (``default``) so the
    # dropdown re-selects a real entry instead of falling through to Off.
    ctrl, panel, persist = _make(migrated=False, touched=False, preset="off")
    ctrl._on_styling_probe_result(present=True, preset_id="yomitan-classic")
    panel.set_card_style_preset.assert_called_once_with("default")
    persist.assert_called_once_with("default", True)


def test_reseed_respects_in_session_user_choice():
    ctrl, panel, persist = _make(migrated=False, touched=True, preset="minimal")
    ctrl._on_styling_probe_result(present=True, preset_id="default")
    # The user's deliberate pick wins: apply it, don't overwrite from Anki.
    ctrl._start_styling_write.assert_called_once_with("minimal")
    panel.set_card_style_preset.assert_not_called()
    persist.assert_not_called()


# --- reconcile when already migrated ------------------------------------------


def test_migrated_in_sync_reports_live_no_write():
    ctrl, panel, _persist = _make(migrated=True, preset="minimal")
    ctrl._on_styling_probe_result(present=True, preset_id="minimal")
    ctrl._start_styling_write.assert_not_called()
    panel.set_styling_status.assert_called_once()


def test_migrated_diverged_pushes_desired():
    # Desired minimal but Anki has no block → push the deferred change.
    ctrl, _panel, _persist = _make(migrated=True, preset="minimal")
    ctrl._on_styling_probe_result(present=False, preset_id="")
    ctrl._start_styling_write.assert_called_once_with("minimal")


# --- write outcome -------------------------------------------------------------


def test_finished_marks_migrated_when_pending():
    ctrl, panel, persist = _make(migrated=False, preset="minimal")
    ctrl._on_styling_finished("done")
    persist.assert_called_once_with("minimal", True)
    panel.reset_styling_user_touched.assert_called_once()
    panel.set_styling_status.assert_called_once()


def test_finished_does_not_repersist_when_already_migrated():
    ctrl, panel, persist = _make(migrated=True, preset="minimal")
    ctrl._on_styling_finished("done")
    persist.assert_not_called()
    panel.reset_styling_user_touched.assert_called_once()


# --- pure helpers --------------------------------------------------------------


def test_normalize_applied():
    norm = AnkiProbeController._normalize_applied
    assert norm(False, "", "minimal") == "off"
    assert norm(True, "minimal", "default") == "minimal"
    # Legacy block reads as matching desired (no churn)…
    assert norm(True, "", "minimal") == "minimal"
    # …unless desired is Off, which is a real "strip it" divergence.
    assert norm(True, "", "off") == ""
    # A retired live id is mapped onto its replacement before comparison.
    assert norm(True, "yomitan-classic", "default") == "default"


def test_live_status_text_distinguishes_off():
    off = AnkiProbeController._live_status_text("off")
    minimal = AnkiProbeController._live_status_text("minimal")
    assert "Off" in off
    assert "Minimal" in minimal and "Live" in minimal
