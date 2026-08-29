"""First visit to a language offers to exclude the other languages' decks."""

from __future__ import annotations

from dataclasses import replace

from PyQt6.QtWidgets import QMessageBox

from anki_miner.gui.controllers import language_switch


class _Window:
    def __init__(self, config):
        self.config = config
        self.wizards = 0

    def get_config(self):
        return self.config

    def update_config(self, config) -> None:
        self.config = config

    def _run_setup_wizard_tool(self) -> None:
        self.wizards += 1


def test_other_language_decks_covers_live_and_stashed(test_config):
    config = replace(
        test_config,
        anki_deck_name="Japanese Mining",
        language_stash={"ko": {"anki_deck_name": "Korean Mining"}},
    )
    assert language_switch.other_language_decks(config) == ("Japanese Mining", "Korean Mining")


def test_accepting_adds_them_to_the_new_languages_exclusions(monkeypatch, test_config):
    monkeypatch.setattr(language_switch, "_first_visit_choice", lambda *a, **k: language_switch.FIRST_VISIT_EXCLUDE)
    previous = replace(test_config, anki_deck_name="Japanese Mining")
    window = _Window(replace(previous, language="zh", excluded_decks=(), anki_deck_name="Chinese Mining"))

    language_switch.offer_first_visit_setup(window, previous)

    assert window.config.excluded_decks == ("Japanese Mining",)


def test_declining_changes_nothing(monkeypatch, test_config):
    monkeypatch.setattr(language_switch, "_first_visit_choice", lambda *a, **k: language_switch.FIRST_VISIT_NONE)
    previous = replace(test_config, anki_deck_name="Japanese Mining")
    window = _Window(replace(previous, language="zh", excluded_decks=()))

    language_switch.offer_first_visit_setup(window, previous)

    assert window.config.excluded_decks == ()
    assert window.wizards == 0


def test_the_wizard_button_runs_the_wizard(monkeypatch, test_config):
    monkeypatch.setattr(language_switch, "_first_visit_choice", lambda *a, **k: language_switch.FIRST_VISIT_SETUP)
    previous = replace(test_config, anki_deck_name="Japanese Mining")
    window = _Window(replace(previous, language="zh", excluded_decks=()))

    language_switch.offer_first_visit_setup(window, previous)

    assert window.wizards == 1


def test_nothing_is_asked_when_the_decks_are_already_excluded(monkeypatch, test_config):
    monkeypatch.setattr(QMessageBox, "exec", lambda self: (_ for _ in ()).throw(AssertionError("asked")))
    previous = replace(test_config, anki_deck_name="Japanese Mining")
    window = _Window(replace(previous, language="zh", excluded_decks=("Japanese Mining",)))

    language_switch.offer_first_visit_setup(window, previous)

    assert window.config.excluded_decks == ("Japanese Mining",)


def test_a_non_widget_window_still_gets_the_prompt(qtbot, monkeypatch, test_config):
    """``QMessageBox`` rejects a non-QWidget parent with a TypeError.

    ``commit_language_change`` wraps this call in a try/except, so the raise
    would not crash - it would silently skip the prompt, which is worse. The
    parent is guarded instead, and the modal opens parentless.
    """
    seen: list[QMessageBox] = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: seen.append(self) or 0)
    previous = replace(test_config, anki_deck_name="Japanese Mining")
    window = _Window(replace(previous, language="zh", excluded_decks=(), anki_deck_name="Chinese Mining"))

    language_switch.offer_first_visit_setup(window, previous)

    assert len(seen) == 1
    assert seen[0].parent() is None


def test_an_unregistered_language_still_gets_the_prompt(qtbot, monkeypatch, test_config):
    """R7: ``ko`` is a legal stored code with no registered profile until Stage 3.

    ``get_profile`` raises on it, and ``commit_language_change`` swallows the
    raise - so the prompt would silently never appear. Degrade to ja instead.
    """
    seen: list[QMessageBox] = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: seen.append(self) or 0)
    previous = replace(test_config, anki_deck_name="Japanese Mining")
    window = _Window(replace(previous, language="ko", excluded_decks=(), anki_deck_name="Korean Mining"))

    language_switch.offer_first_visit_setup(window, previous)

    assert len(seen) == 1


def test_commit_only_offers_on_a_first_visit(monkeypatch, test_config):
    calls: list[str] = []
    monkeypatch.setattr(language_switch, "offer_first_visit_setup", lambda *a, **k: calls.append("asked"))
    window = _Window(replace(test_config, language="zh"))

    language_switch.commit_language_change(window, test_config, flush=False, first_visit=False)
    assert calls == []

    language_switch.commit_language_change(window, test_config, flush=False, first_visit=True)
    assert calls == ["asked"]
