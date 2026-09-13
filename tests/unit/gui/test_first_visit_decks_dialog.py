"""S15: every deck listed, all ticked but the target, fetched off the GUI thread."""

from __future__ import annotations

import logging
from dataclasses import replace

from PyQt6.QtCore import QObject, Qt

from anki_miner.gui.controllers import language_switch
from anki_miner.gui.widgets.dialogs.first_visit_decks_dialog import FirstVisitDecksDialog


def _dialog(qtbot, **overrides):
    kwargs = {
        "display_name": "English",
        "decks": ("Japanese", "English Vocab", "Anki Miner"),
        "ticked": ("Japanese", "English Vocab"),
        "offer_setup": True,
    }
    kwargs.update(overrides)
    dialog = FirstVisitDecksDialog(None, **kwargs)
    qtbot.addWidget(dialog)
    return dialog


def test_every_deck_is_listed_with_the_given_ticks(qtbot):
    dialog = _dialog(qtbot)
    states = {
        dialog.deck_list.item(i).text(): dialog.deck_list.item(i).checkState() for i in range(dialog.deck_list.count())
    }
    assert states == {
        "Japanese": Qt.CheckState.Checked,
        "English Vocab": Qt.CheckState.Checked,
        "Anki Miner": Qt.CheckState.Unchecked,
    }


def test_unticking_and_excluding_returns_what_is_left_ticked(qtbot):
    dialog = _dialog(qtbot)
    dialog.deck_list.item(1).setCheckState(Qt.CheckState.Unchecked)
    dialog.exclude_button.click()
    assert dialog.choice == "exclude"
    assert dialog.ticked_decks() == ("Japanese",)


def test_setup_and_close(qtbot):
    dialog = _dialog(qtbot)
    dialog.setup_button.click()
    assert dialog.choice == "setup"
    closed = _dialog(qtbot, offer_setup=False)
    assert closed.setup_button is None
    closed.reject()
    assert closed.choice == "none"


class _QtWindow(QObject):
    def __init__(self, config):
        super().__init__()
        self.config = config

    def get_config(self):
        return self.config

    def update_config(self, config) -> None:
        self.config = config


def test_a_qt_window_fetches_deck_names_off_thread_then_ticks_all_but_the_target(qtbot, monkeypatch, test_config):
    previous = replace(test_config, anki_deck_name="Japanese Mining")
    window = _QtWindow(replace(previous, language="zh", excluded_decks=(), anki_deck_name="Anki Miner"))
    fetched = ["Japanese Mining", "English Vocab", "Anki Miner", "Anki Miner::Sub"]
    works: list[object] = []

    def fake_run_off_thread(parent, work, on_done, on_error=None, **kwargs):
        works.append(work)
        on_done(fetched)

    class FakeService:
        def __init__(self, config):
            self.config = config

        def get_deck_names(self):
            return fetched

    seen: dict[str, object] = {}

    def fake_choice(parent, display_name, decks, ticked, offer_setup):
        seen.update(decks=decks, ticked=ticked)
        return language_switch.FIRST_VISIT_EXCLUDE, ticked

    monkeypatch.setattr(language_switch, "run_off_thread", fake_run_off_thread)
    monkeypatch.setattr(language_switch, "AnkiService", FakeService)
    monkeypatch.setattr(language_switch, "_first_visit_choice", fake_choice)

    language_switch.offer_first_visit_setup(window, previous)

    assert works and works[0]() == fetched
    assert seen["decks"] == ("Japanese Mining", "English Vocab", "Anki Miner", "Anki Miner::Sub")
    assert seen["ticked"] == ("Japanese Mining", "English Vocab")
    assert window.config.excluded_decks == ("Japanese Mining", "English Vocab")


def test_anki_unreachable_falls_back_to_the_config_decks(qtbot, monkeypatch, test_config):
    previous = replace(test_config, anki_deck_name="Japanese Mining")
    window = _QtWindow(replace(previous, language="zh", excluded_decks=(), anki_deck_name="Chinese Mining"))
    monkeypatch.setattr(
        language_switch, "run_off_thread", lambda parent, work, on_done, on_error=None, **kw: on_error("offline")
    )
    monkeypatch.setattr(
        language_switch,
        "_first_visit_choice",
        lambda *a, **k: (language_switch.FIRST_VISIT_EXCLUDE, ("Japanese Mining",)),
    )

    language_switch.offer_first_visit_setup(window, previous)

    assert window.config.excluded_decks == ("Japanese Mining",)


def test_a_switch_during_the_fetch_drops_the_result(qtbot, monkeypatch, test_config, caplog):
    caplog.set_level(logging.INFO, logger=language_switch.__name__)
    previous = replace(test_config, anki_deck_name="Japanese Mining")
    window = _QtWindow(replace(previous, language="zh", excluded_decks=(), anki_deck_name="Chinese Mining"))

    def switch_then_deliver(parent, work, on_done, on_error=None, **kwargs):
        window.config = replace(window.config, language="ko")  # the user moved on while deckNames ran
        on_done(["Japanese Mining"])

    monkeypatch.setattr(language_switch, "run_off_thread", switch_then_deliver)
    monkeypatch.setattr(
        language_switch, "_first_visit_choice", lambda *a, **k: (_ for _ in ()).throw(AssertionError("asked"))
    )

    language_switch.offer_first_visit_setup(window, previous)

    assert window.config.excluded_decks == ()
    assert "Dropped the first-visit deck list for zh" in caplog.text


def test_no_other_deck_still_offers_the_setup(monkeypatch, test_config):
    """I-1: a default config whose only other-language deck is this language's own still gets the wizard offer."""
    previous = replace(test_config, anki_deck_name="Anki Miner")
    config = replace(previous, language="zh", excluded_decks=(), anki_deck_name="Anki Miner", dictionary_chain=())
    seen: list[tuple] = []
    monkeypatch.setattr(
        language_switch, "_first_visit_choice", lambda *a: seen.append(a) or (language_switch.FIRST_VISIT_NONE, ())
    )

    class Plain:
        def get_config(self):
            return config

    language_switch.offer_first_visit_setup(Plain(), previous)

    assert len(seen) == 1 and seen[0][2] == ("Anki Miner",) and seen[0][3] == () and seen[0][4] is True
