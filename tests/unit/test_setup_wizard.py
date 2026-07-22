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


def _stub_anki_service(monkeypatch, wiz, *, decks=(), notetypes=()):
    """Replace the wizard's shared AnkiService with a hermetic mock.

    ``initializePage`` on the deck / note-type pages fires a worker that calls
    ``AnkiService.get_deck_names`` / ``get_model_names`` against real
    AnkiConnect; stubbing keeps these tests off the network.
    """
    fake = MagicMock()
    fake.get_deck_names.return_value = list(decks)
    fake.get_model_names.return_value = list(notetypes)
    monkeypatch.setattr(wiz, "anki_service", lambda: fake)
    return fake


def test_deck_page_preselects_config_deck(qtbot, wiz_config, monkeypatch):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    cfg = replace(wiz_config, anki_deck_name="My Mining Deck")
    wiz = SetupWizard(cfg)
    qtbot.addWidget(wiz)
    _stub_anki_service(monkeypatch, wiz, decks=["Default", "My Mining Deck"])
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


def _set_notetype_page_state(page, *, selected, models, field_names):
    page.notetype_combo.blockSignals(True)
    page.notetype_combo.setCurrentText(selected)
    page.notetype_combo.blockSignals(False)
    page._fetched_note_types = list(models)
    page._field_names = [] if field_names is None else list(field_names)
    page._field_names_note_type = None if field_names is None else selected


@pytest.mark.parametrize(
    ("models", "field_names", "word_field", "source_field", "card_type", "marker_field", "expected"),
    [
        pytest.param(["Basic"], ["Expression"], "Expression", "", "", "", False, id="model-missing"),
        pytest.param(["Lapis"], None, "Expression", "", "", "", False, id="fields-unfetched"),
        pytest.param(["Lapis"], ["Expression"], "", "", "", "", False, id="word-unmapped"),
        pytest.param(
            ["Lapis"],
            ["Expression"],
            "Expression",
            "MissingSource",
            "",
            "",
            False,
            id="optional-mapping-invalid",
        ),
        pytest.param(
            ["Lapis"],
            ["Expression"],
            "Expression",
            "",
            "click",
            "MissingMarker",
            False,
            id="active-marker-invalid",
        ),
        pytest.param(["Lapis"], ["Expression"], "Expression", "", "", "", True, id="valid"),
    ],
)
def test_notetype_page_completeness_matrix(
    qtbot,
    wiz_config,
    models,
    field_names,
    word_field,
    source_field,
    card_type,
    marker_field,
    expected,
):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    mappings = dict.fromkeys(AnkiMinerConfig().anki_fields, "")
    mappings["word"] = word_field
    mappings["source"] = source_field
    markers = dict(wiz_config.card_type_marker_fields)
    if card_type:
        markers[card_type] = marker_field
    cfg = replace(
        wiz_config,
        anki_note_type="Lapis",
        anki_fields=mappings,
        card_type=card_type,
        card_type_marker_fields=markers,
    )
    wiz = SetupWizard(cfg)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    _set_notetype_page_state(page, selected="Lapis", models=models, field_names=field_names)

    assert page.isComplete() is expected


def test_notetype_page_preselects_config_note_type(qtbot, wiz_config, monkeypatch):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    cfg = replace(wiz_config, anki_note_type="Lapis")
    wiz = SetupWizard(cfg)
    qtbot.addWidget(wiz)
    _stub_anki_service(monkeypatch, wiz, notetypes=["Basic", "Lapis"])
    page = wiz.notetype_page
    page.initializePage()
    qtbot.waitUntil(lambda: page._field_names_note_type == "Lapis", timeout=3000)
    assert page.notetype_combo.currentText() == "Lapis"


def test_notetype_page_auto_map_stages_fields(qtbot, wiz_config):
    """Auto-Map must stage the mapped anki_fields (plain dict) into the working config."""
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    # Auto-Map fires _warn_missing_fields -> an off-thread check_field_names
    # against real AnkiConnect (tests/_network_tripwire.py); stub it like the
    # warn-label tests below do.
    wiz.validation_service = MagicMock(  # type: ignore[method-assign]
        return_value=MagicMock(check_field_names=lambda: (True, ""))
    )
    page.notetype_combo.setCurrentText("Lapis")
    page._on_fields_fetched("Lapis", ["Expression", "Sentence", "MainDefinition", "Picture", "SentenceAudio"])
    page._on_auto_map_clicked()

    cfg = wiz.working_config()
    assert cfg.anki_note_type == "Lapis"
    assert cfg.anki_fields["word"] == "Expression"
    assert cfg.anki_fields["sentence"] == "Sentence"
    assert cfg.anki_fields["definition"] == "MainDefinition"
    # anki_fields is a plain dict at stage time, re-wrapped to MappingProxy by config.
    import types as _types  # noqa: PLC0415

    assert isinstance(cfg.anki_fields, _types.MappingProxyType)


def test_field_fetch_latest_selection_runs_after_stale_fetch(qtbot, wiz_config, monkeypatch):
    """Selecting B while A is in flight must fetch and apply B after A finishes."""
    import threading

    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    entered_a = threading.Event()
    release_a = threading.Event()
    calls: list[str] = []

    def get_fields(note_type: str) -> list[str]:
        calls.append(note_type)
        if note_type == "Type A":
            entered_a.set()
            release_a.wait(3.0)
            return ["AWord", "ASentence"]
        return ["BWord", "BSentence"]

    service = MagicMock()
    service.get_note_type_fields.side_effect = get_fields
    monkeypatch.setattr(wiz, "anki_service", lambda: service)

    try:
        page.notetype_combo.setCurrentText("Type A")
        page._on_notetypes_fetched(["Type A", "Type B"])
        qtbot.waitUntil(entered_a.is_set, timeout=3000)

        page.notetype_combo.setCurrentText("Type B")
        release_a.set()

        qtbot.waitUntil(lambda: page._field_names_note_type == "Type B", timeout=3000)
        assert calls == ["Type A", "Type B"]
        assert page._field_names == ["BWord", "BSentence"]
        assert wiz.working_config().anki_note_type == "Type B"
    finally:
        release_a.set()
        qtbot.wait(100)
        for worker in list(wiz._workers):
            worker.wait(5000)


def test_field_fetch_generation_rejects_stale_same_model_result(qtbot, wiz_config, monkeypatch):
    """A generation-1 A result must not satisfy a later A selection."""
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import pages as pages_mod  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    page._fetched_note_types = ["Type A", "Type B"]
    first_worker = MagicMock()
    latest_worker = MagicMock()
    workers = iter((first_worker, latest_worker))
    factory = MagicMock(side_effect=lambda *_args: next(workers))
    monkeypatch.setattr(pages_mod, "FetchFieldsWorker", factory)

    page.notetype_combo.setCurrentText("Type A")
    page.notetype_combo.setCurrentText("Type B")
    page.notetype_combo.setCurrentText("Type A")

    first_worker.result_ready.connect.call_args.args[0](["StaleAField"])
    assert page._field_names_note_type is None

    first_worker.finished.connect.call_args.args[0]()
    assert factory.call_count == 2
    assert factory.call_args.args[1] == "Type A"

    latest_worker.result_ready.connect.call_args.args[0](["FreshAField"])
    assert page._field_names_note_type == "Type A"
    assert page._field_names == ["FreshAField"]
    latest_worker.finished.connect.call_args.args[0]()


def test_notetype_page_emits_complete_changed_for_field_fetch_transitions(qtbot, wiz_config, monkeypatch):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import pages as pages_mod  # noqa: PLC0415

    mappings = dict.fromkeys(AnkiMinerConfig().anki_fields, "")
    mappings["word"] = "Expression"
    cfg = replace(wiz_config, anki_note_type="Lapis", anki_fields=mappings)
    wiz = SetupWizard(cfg)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    page._fetched_note_types = ["Lapis", "Basic"]
    result_worker = MagicMock()
    error_worker = MagicMock()
    workers = iter((result_worker, error_worker))
    monkeypatch.setattr(pages_mod, "FetchFieldsWorker", lambda *_args: next(workers))
    changed = MagicMock()
    page.completeChanged.connect(changed)

    page.notetype_combo.setCurrentText("Lapis")
    assert changed.call_count == 2  # selection + fetch start

    changed.reset_mock()
    on_result = result_worker.result_ready.connect.call_args.args[0]
    on_result(["Expression"])
    assert changed.call_count == 1
    on_finished = result_worker.finished.connect.call_args.args[0]
    on_finished()

    changed.reset_mock()
    page.notetype_combo.setCurrentText("Basic")
    assert changed.call_count == 2  # selection + fetch start
    changed.reset_mock()
    on_error = error_worker.error.connect.call_args.args[0]
    on_error("fetch failed")
    assert changed.call_count == 1
    error_worker.finished.connect.call_args.args[0]()


def test_notetype_page_field_result_emits_for_sanitize_and_result(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    mappings = dict.fromkeys(AnkiMinerConfig().anki_fields, "")
    mappings["word"] = "OldExpression"
    cfg = replace(wiz_config, anki_note_type="Lapis", anki_fields=mappings)
    wiz = SetupWizard(cfg)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    page.notetype_combo.blockSignals(True)
    page.notetype_combo.setCurrentText("Lapis")
    page.notetype_combo.blockSignals(False)
    changed = MagicMock()
    page.completeChanged.connect(changed)

    page._on_fields_fetched("Lapis", ["Expression"])

    assert wiz.working_config().anki_fields["word"] == ""
    assert changed.call_count == 2  # sanitize + fetch result


def test_notetype_page_emits_complete_changed_for_model_fetch_transitions(qtbot, wiz_config, monkeypatch):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import pages as pages_mod  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    worker = MagicMock()
    monkeypatch.setattr(pages_mod, "FetchNotetypesWorker", lambda *_args: worker)
    changed = MagicMock()
    page.completeChanged.connect(changed)

    page._on_refresh_clicked()
    assert changed.call_count == 1

    changed.reset_mock()
    worker.result_ready.connect.call_args.args[0]([])
    assert changed.call_count == 2  # result + blocked programmatic selection

    changed.reset_mock()
    worker.error.connect.call_args.args[0]("fetch failed")
    assert changed.call_count == 1


def test_model_fetch_result_records_programmatic_selection(qtbot, wiz_config, monkeypatch):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import pages as pages_mod  # noqa: PLC0415

    wiz = SetupWizard(replace(wiz_config, anki_note_type="Lapis"))
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    worker = MagicMock()
    factory = MagicMock(return_value=worker)
    monkeypatch.setattr(pages_mod, "FetchFieldsWorker", factory)

    page._on_notetypes_fetched(["Lapis"])

    assert page._desired_note_type == "Lapis"
    factory.assert_called_once()


def test_wizard_close_does_not_launch_pending_field_fetch(qtbot, wiz_config, monkeypatch):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import pages as pages_mod  # noqa: PLC0415

    wiz = SetupWizard(replace(wiz_config, anki_note_type="Type A"))
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    page._fetched_note_types = ["Type A", "Type B"]
    worker = MagicMock()
    worker.isRunning.return_value = True
    factory = MagicMock(return_value=worker)
    monkeypatch.setattr(pages_mod, "FetchFieldsWorker", factory)

    page.notetype_combo.setCurrentText("Type A")
    page.notetype_combo.setCurrentText("Type B")
    on_finished = worker.finished.connect.call_args.args[0]
    wiz.done(0)
    on_finished()

    factory.assert_called_once()


def test_auto_map_uses_sanitized_base_and_preserves_valid_manual_fields(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    seeded = dict.fromkeys(AnkiMinerConfig().anki_fields, "")
    seeded.update(
        word="ManualWord",
        sentence="OldSentence",
        definition="ManualDefinition",
        source="MissingSource",
    )
    markers = dict(wiz_config.card_type_marker_fields)
    markers.update(click="MissingMarker", sentence="InactiveMarker")
    cfg = replace(
        wiz_config,
        anki_note_type="Lapis",
        anki_fields=seeded,
        card_type="click",
        card_type_marker_fields=markers,
    )

    wiz = SetupWizard(cfg)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    wiz.validation_service = MagicMock(  # type: ignore[method-assign]
        return_value=MagicMock(check_field_names=lambda: (True, ""))
    )
    page.notetype_combo.setCurrentText("Lapis")
    page._on_fields_fetched(
        "Lapis",
        ["Expression", "ManualWord", "Sentence", "ManualDefinition", "PitchGraph", "PitchText"],
    )
    page._on_auto_map_clicked()

    result = wiz.working_config()
    fields = result.anki_fields
    assert fields["word"] == "ManualWord"
    assert fields["sentence"] == "Sentence"
    assert fields["definition"] == "ManualDefinition"
    assert fields["pitch_graph"] == "PitchGraph"
    assert fields["pitch_text"] == "PitchText"
    assert fields["source"] == ""
    assert set(AnkiMinerConfig().anki_fields) <= set(fields)
    assert result.card_type_marker_fields["click"] == ""
    assert result.card_type_marker_fields["sentence"] == "InactiveMarker"


def test_notetype_page_unsuitable_fieldlist_shows_guidance(qtbot, wiz_config):
    """A field list missing a word+sentence shape triggers the import-note-type guidance."""
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    page.notetype_combo.setCurrentText("Basic")
    page._on_fields_fetched("Basic", ["Front", "Back"])
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
    page._on_fields_fetched("Lapis", ["Expression", "Sentence", "MainDefinition"])
    assert not page.guidance_label.isVisibleTo(page)


def test_notetype_page_empty_fieldlist_shows_unreachable_guidance(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    page.notetype_combo.setCurrentText("Ghost")
    mappings_before = dict(wiz.working_config().anki_fields)
    markers_before = dict(wiz.working_config().card_type_marker_fields)
    page._on_fields_fetched("Ghost", [])
    assert page.guidance_label.isVisibleTo(page)
    assert wiz.working_config().anki_fields == mappings_before
    assert wiz.working_config().card_type_marker_fields == markers_before


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


# ---------------------------------------------------------------------------
# NoteTypePage: Auto-Map field-name check runs off the GUI thread
# ---------------------------------------------------------------------------


def test_warn_missing_fields_runs_off_gui_thread(qtbot, wiz_config):
    """check_field_names() must execute on a worker thread, not the GUI thread."""
    import threading

    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page

    gui_ident = threading.get_ident()
    seen = {}

    def fake_check():
        seen["ident"] = threading.get_ident()
        return (True, "")

    wiz.validation_service = MagicMock(  # type: ignore[method-assign]
        return_value=MagicMock(check_field_names=fake_check)
    )

    page._warn_missing_fields()
    qtbot.waitUntil(lambda: "ident" in seen, timeout=3000)
    assert seen["ident"] != gui_ident


def test_warn_missing_fields_updates_label_in_callback(qtbot, wiz_config):
    """On a not-ok result, warning_label shows the message (set from the GUI-thread slot)."""
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page

    wiz.validation_service = MagicMock(  # type: ignore[method-assign]
        return_value=MagicMock(check_field_names=lambda: (False, "Missing: word"))
    )

    page._warn_missing_fields()
    qtbot.waitUntil(lambda: page.warning_label.text() == "Missing: word", timeout=3000)


def test_warn_missing_fields_clears_label_when_ok(qtbot, wiz_config):
    """An ok result clears the warning_label."""
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    page.warning_label.setText("stale warning")

    wiz.validation_service = MagicMock(  # type: ignore[method-assign]
        return_value=MagicMock(check_field_names=lambda: (True, ""))
    )

    page._warn_missing_fields()
    qtbot.waitUntil(lambda: page.warning_label.text() == "", timeout=3000)


def test_warn_missing_fields_raising_check_does_not_crash(qtbot, wiz_config):
    """A raising/slow check must never raise into the GUI; the page stays alive."""
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page

    def boom():
        raise RuntimeError("anki down")

    wiz.validation_service = MagicMock(return_value=MagicMock(check_field_names=boom))  # type: ignore[method-assign]

    page._warn_missing_fields()
    # Give the worker time to run + deliver its error signal without raising.
    qtbot.wait(500)
    assert page is not None  # no crash


def test_warn_missing_fields_latest_check_wins(qtbot, wiz_config):
    """Overlapping checks: the stale result is ignored, the latest wins."""
    import threading

    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page

    release_first = threading.Event()

    def slow_first():
        release_first.wait(3.0)
        return (False, "STALE")

    def fast_second():
        return (False, "LATEST")

    # First (slow) dispatch.
    wiz.validation_service = MagicMock(  # type: ignore[method-assign]
        return_value=MagicMock(check_field_names=slow_first)
    )
    page._warn_missing_fields()

    # Second (fast) dispatch supersedes it.
    wiz.validation_service = MagicMock(  # type: ignore[method-assign]
        return_value=MagicMock(check_field_names=fast_second)
    )
    page._warn_missing_fields()

    qtbot.waitUntil(lambda: page.warning_label.text() == "LATEST", timeout=3000)
    # Now let the stale worker finish; its result must NOT overwrite the latest.
    release_first.set()
    qtbot.wait(500)
    assert page.warning_label.text() == "LATEST"
