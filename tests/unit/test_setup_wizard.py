"""Tests for the guided first-run Setup Wizard (Task 3).

Detect-&-guide-only wizard: it inspects Anki state and guides the user, but
NEVER creates decks or note types via AnkiConnect. All AnkiConnect access is
monkeypatched here — no real network/Anki.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from anki_miner.config import AnkiMinerConfig


@pytest.fixture
def wiz_config(test_config):
    """A config with a fresh-install-ish AnkiConnect URL for the wizard."""
    return replace(test_config, ankiconnect_url="http://127.0.0.1:8765")


# ---------------------------------------------------------------------------
# Package surface
# ---------------------------------------------------------------------------


def test_package_exports_setup_wizard_and_runner():
    from anki_miner.gui.widgets.dialogs.setup_wizard import (  # noqa: PLC0415
        SetupWizard,
        run_setup_wizard,
    )

    assert callable(run_setup_wizard)
    assert SetupWizard is not None


# ---------------------------------------------------------------------------
# SetupWizard container
# ---------------------------------------------------------------------------


def test_wizard_starts_with_copy_of_config(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    assert wiz.working_config().ankiconnect_url == wiz_config.ankiconnect_url


def test_wizard_update_working_config_replaces(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    new = replace(wiz_config, anki_deck_name="Mining Deck")
    wiz.update_working_config(new)
    assert wiz.working_config().anki_deck_name == "Mining Deck"


def test_wizard_has_skip_setup_button_wired_to_reject(qtbot, wiz_config):
    from PyQt6.QtWidgets import QWizard  # noqa: PLC0415

    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    btn = wiz.button(QWizard.WizardButton.CustomButton1)
    assert btn is not None
    assert btn.text() == "Skip Setup"


def test_wizard_adds_five_pages(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    assert len(wiz.pageIds()) == 5


def test_wizard_done_joins_workers(qtbot, wiz_config):
    """done() must cancel + wait on every owned worker so no QThread outlives the modal."""
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)

    fake = MagicMock()
    fake.isRunning.return_value = True
    wiz.register_worker(fake)
    wiz.done(0)

    fake.cancel.assert_called_once()
    fake.wait.assert_called_once()


# ---------------------------------------------------------------------------
# AnkiConnectPage
# ---------------------------------------------------------------------------


def test_ankiconnect_page_complete_only_after_successful_recheck(qtbot, wiz_config, monkeypatch):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import pages as pages_mod  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.ankiconnect_page

    # Initially not reachable → page incomplete.
    page._reachable = False
    assert page.isComplete() is False

    # Simulate a successful recheck result landing on the main thread.
    page._on_recheck_result((True, "AnkiConnect v6 is running"))
    assert page._reachable is True
    assert page.isComplete() is True

    # And a failure flips it back.
    page._on_recheck_result((False, "Cannot connect to Anki"))
    assert page.isComplete() is False
    assert pages_mod is not None


def test_ankiconnect_page_writes_url_to_working_config(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.ankiconnect_page
    page.url_input.setText("http://localhost:9999")
    page._write_url_to_config()
    assert wiz.working_config().ankiconnect_url == "http://localhost:9999"


def test_ankiconnect_page_recheck_uses_check_ankiconnect(qtbot, wiz_config, monkeypatch):
    """Recheck must run ValidationService.check_ankiconnect off-thread, not raw network."""
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import setup_wizard as sw_mod  # noqa: PLC0415

    calls = {}

    class _FakeValidation:
        def __init__(self, cfg):
            calls["cfg"] = cfg

        def check_ankiconnect(self):
            calls["checked"] = True
            return (True, "ok")

    monkeypatch.setattr(sw_mod, "ValidationService", _FakeValidation)

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.ankiconnect_page
    # Run the work callable synchronously (the worker just wraps it).
    result = page._recheck_work()
    assert result == (True, "ok")
    assert calls["checked"] is True


# ---------------------------------------------------------------------------
# DeckPage
# ---------------------------------------------------------------------------


def test_deck_page_preselects_config_deck(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    cfg = replace(wiz_config, anki_deck_name="My Mining Deck")
    wiz = SetupWizard(cfg)
    qtbot.addWidget(wiz)
    page = wiz.deck_page
    page.initializePage()
    assert page.deck_combo.currentText() == "My Mining Deck"


def test_deck_page_writes_deck_to_config(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.deck_page
    page.deck_combo.setCurrentText("Fresh Deck")
    page._write_deck_to_config()
    assert wiz.working_config().anki_deck_name == "Fresh Deck"


def test_deck_page_is_complete_when_name_nonempty(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.deck_page
    page.deck_combo.setCurrentText("")
    assert page.isComplete() is False
    page.deck_combo.setCurrentText("Anything")
    assert page.isComplete() is True


def test_deck_page_unknown_deck_shows_autocreate_hint(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.deck_page
    page._on_decks_fetched(["Default", "Existing"])
    page.deck_combo.setCurrentText("Brand New Deck")
    page._update_deck_hint()
    assert "created automatically" in page.deck_hint.text().lower()


# ---------------------------------------------------------------------------
# NoteTypePage
# ---------------------------------------------------------------------------


def test_notetype_page_preselects_config_note_type(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    cfg = replace(wiz_config, anki_note_type="Lapis")
    wiz = SetupWizard(cfg)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    page.initializePage()
    assert page.notetype_combo.currentText() == "Lapis"


def test_notetype_page_auto_map_stages_fields(qtbot, wiz_config):
    """Auto-Map must stage the mapped anki_fields (plain dict) into the working config."""
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    page.notetype_combo.setCurrentText("Lapis")
    page._on_fields_fetched(["Expression", "Sentence", "MainDefinition", "Picture", "SentenceAudio"])
    page._on_auto_map_clicked()

    cfg = wiz.working_config()
    assert cfg.anki_note_type == "Lapis"
    assert cfg.anki_fields["word"] == "Expression"
    assert cfg.anki_fields["sentence"] == "Sentence"
    assert cfg.anki_fields["definition"] == "MainDefinition"
    # anki_word_field stays synced with anki_fields["word"].
    assert cfg.anki_word_field == "Expression"
    # anki_fields is a plain dict at stage time, re-wrapped to MappingProxy by config.
    import types as _types  # noqa: PLC0415

    assert isinstance(cfg.anki_fields, _types.MappingProxyType)


def test_notetype_page_unsuitable_fieldlist_shows_guidance(qtbot, wiz_config):
    """A field list missing a word+sentence shape triggers the import-note-type guidance."""
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    page.notetype_combo.setCurrentText("Basic")
    page._on_fields_fetched(["Front", "Back"])
    # isVisibleTo(page) reflects the explicit setVisible(True) without needing the
    # top-level wizard to be shown (offscreen Qt).
    assert page.guidance_label.isVisibleTo(page)
    assert page.guidance_label.text() != ""


def test_notetype_page_suitable_fieldlist_hides_guidance(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    page.notetype_combo.setCurrentText("Lapis")
    page._on_fields_fetched(["Expression", "Sentence", "MainDefinition"])
    assert not page.guidance_label.isVisibleTo(page)


def test_notetype_page_empty_fieldlist_shows_unreachable_guidance(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    page.notetype_combo.setCurrentText("Ghost")
    page._on_fields_fetched([])
    assert page.guidance_label.isVisibleTo(page)


# ---------------------------------------------------------------------------
# DonePage
# ---------------------------------------------------------------------------


def test_done_page_summary_reflects_working_config(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    cfg = replace(wiz_config, anki_deck_name="Mining", anki_note_type="Lapis")
    wiz = SetupWizard(cfg)
    qtbot.addWidget(wiz)
    # Pretend AnkiConnect was reachable.
    wiz.ankiconnect_page._reachable = True
    page = wiz.done_page
    page.initializePage()
    text = page.summary_label.text()
    assert "Mining" in text
    assert "Lapis" in text


def test_done_page_is_final(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    assert wiz.done_page.isFinalPage() is True


# ---------------------------------------------------------------------------
# run_setup_wizard return contract
# ---------------------------------------------------------------------------


def test_run_setup_wizard_returns_working_config_on_skip(qtbot, wiz_config, monkeypatch):
    """A skip (reject) still returns the (possibly partial) working config."""
    from anki_miner.gui.widgets.dialogs.setup_wizard import run_setup_wizard  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import setup_wizard as sw_mod  # noqa: PLC0415

    # Don't actually spin a modal loop: stub exec() and mutate the working config first.
    def fake_exec(self):
        self.update_working_config(replace(self.working_config(), anki_deck_name="Touched"))
        return 0  # Rejected

    monkeypatch.setattr(sw_mod.SetupWizard, "exec", fake_exec)

    result = run_setup_wizard(None, wiz_config)
    assert isinstance(result, AnkiMinerConfig)
    assert result.anki_deck_name == "Touched"
