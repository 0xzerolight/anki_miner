"""The wizard's mining-language step: the list, the switch, and what it leaves."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard
from anki_miner.gui.widgets.dialogs.setup_wizard import pages as wizard_pages
from anki_miner.languages.registry import config_language, get_profile
from anki_miner.languages.switching import switch_language


class _FakeValidation:
    """Answers every wizard probe instantly: no disk, no network."""

    def check_ankiconnect(self):
        return False, "AnkiConnect is not reachable"

    def check_resource_readiness(self):
        from anki_miner.services.validation_service import ResourceReadiness

        return ResourceReadiness(
            dictionary=(False, "no dictionary"),
            frequency=(None, ""),
            pitch=(None, ""),
        )


@pytest.fixture
def wizard_factory(qtbot, monkeypatch):
    """Build a wizard whose live checks never reach disk or Anki."""

    def build(config):
        monkeypatch.setattr(SetupWizard, "validation_service", lambda self: _FakeValidation())
        wiz = SetupWizard(config)
        qtbot.addWidget(wiz)
        return wiz

    return build


def _stub_anki_service(monkeypatch, wiz, *, decks=(), notetypes=()):
    fake = MagicMock()
    fake.get_deck_names.return_value = list(decks)
    fake.get_model_names.return_value = list(notetypes)
    monkeypatch.setattr(wiz, "anki_service", lambda: fake)
    return fake


def _pick(page, code):
    """Select ``code`` the way a user does: through the combo, signals live."""
    index = page.language_combo.findData(code)
    assert index >= 0, f"{code} is not offered"
    page.language_combo.setCurrentIndex(index)


# ---------------------------------------------------------------------------
# Where the page sits, and what it opens on
# ---------------------------------------------------------------------------


def test_the_language_page_is_the_second_step(wizard_factory, test_config):
    wiz = wizard_factory(test_config)

    ids = wiz.pageIds()
    assert len(ids) == 7
    assert wiz.page(ids[0]) is wiz.theme_page
    assert wiz.page(ids[1]) is wiz.language_page
    assert wiz.page(ids[2]) is wiz.ankiconnect_page


def test_the_page_never_blocks_next(wizard_factory, test_config):
    assert wizard_factory(test_config).language_page.isComplete() is True


def test_the_page_opens_on_the_working_config_language(wizard_factory, test_config):
    page = wizard_factory(switch_language(test_config, "zh")).language_page
    page.initializePage()

    assert page.language_combo.currentData() == "zh"


def test_next_without_a_pick_leaves_the_config_byte_identical(wizard_factory, test_config):
    """A Japanese learner clicks through, and everything downstream is unchanged."""
    wiz = wizard_factory(test_config)
    page = wiz.language_page
    page.initializePage()

    assert page.language_combo.currentData() == "ja"
    assert page.validatePage() is True
    assert wiz.working_config() == test_config


# ---------------------------------------------------------------------------
# The switch, and the pages downstream of it
# ---------------------------------------------------------------------------


def test_choosing_chinese_commits_the_scoped_defaults(wizard_factory, test_config):
    wiz = wizard_factory(test_config)
    page = wiz.language_page
    page.initializePage()

    _pick(page, "zh")
    assert page.validatePage() is True

    committed = wiz.working_config()
    zh_defaults = get_profile("zh").scoped_defaults
    assert config_language(committed) == "zh"
    assert committed.anki_deck_name == zh_defaults["anki_deck_name"]
    assert committed.anki_note_type == zh_defaults["anki_note_type"]
    assert committed.language_stash["ja"]["anki_deck_name"] == test_config.anki_deck_name


def test_next_is_what_commits_the_pick(qtbot, wizard_factory, test_config):
    """Driven through QWizard itself, not through the page's methods."""
    wiz = wizard_factory(test_config)
    wiz.restart()
    assert wiz.currentPage() is wiz.theme_page

    wiz.next()
    assert wiz.currentPage() is wiz.language_page
    _pick(wiz.language_page, "zh")
    assert wiz.working_config() == test_config  # still on the page: nothing committed

    wiz.next()
    assert wiz.currentPage() is wiz.ankiconnect_page
    assert config_language(wiz.working_config()) == "zh"
    # The page entered by that Next owns a worker thread; let it land.
    qtbot.waitUntil(lambda: bool(wiz.ankiconnect_page.result_label.text()), timeout=5000)


def test_the_resources_page_offers_the_chosen_language_catalogue(qtbot, wizard_factory, test_config):
    wiz = wizard_factory(test_config)
    assert "jmdict-english" in wiz.resources_page.resource_checks

    page = wiz.language_page
    page.initializePage()
    _pick(page, "zh")
    page.validatePage()

    wiz.resources_page.initializePage()
    assert set(wiz.resources_page.resource_checks) == {spec.id for spec in get_profile("zh").catalog}
    assert "cc-cedict" in wiz.resources_page.resource_checks
    # The page's own live probe owns a worker thread; let it land before teardown.
    qtbot.waitUntil(lambda: not wiz.resources_page.dictionary_label.text().startswith("Checking"), timeout=5000)


def test_the_deck_page_opens_on_the_chosen_language_deck(qtbot, monkeypatch, wizard_factory, test_config):
    wiz = wizard_factory(test_config)
    page = wiz.language_page
    page.initializePage()
    _pick(page, "zh")
    page.validatePage()

    zh_deck = get_profile("zh").scoped_defaults["anki_deck_name"]
    _stub_anki_service(monkeypatch, wiz, decks=[zh_deck, test_config.anki_deck_name])
    wiz.deck_page.initializePage()
    qtbot.waitUntil(lambda: bool(wiz.deck_page._fetched_decks), timeout=5000)

    assert wiz.deck_page.deck_combo.currentText() == zh_deck


# ---------------------------------------------------------------------------
# Re-entering the page
# ---------------------------------------------------------------------------


def test_re_choosing_leaves_what_one_switch_to_the_final_choice_leaves(wizard_factory, test_config):
    """A detour through Chinese must not spend Chinese's first visit."""
    wiz = wizard_factory(test_config)
    page = wiz.language_page

    page.initializePage()
    _pick(page, "zh")
    page.validatePage()

    page.initializePage()  # Back
    _pick(page, "ko")
    page.validatePage()

    assert wiz.working_config() == switch_language(test_config, "ko")
    assert "zh" not in wiz.working_config().language_stash


def test_re_choosing_the_starting_language_restores_the_starting_config(wizard_factory, test_config):
    wiz = wizard_factory(test_config)
    page = wiz.language_page

    page.initializePage()
    _pick(page, "zh")
    page.validatePage()

    page.initializePage()  # Back
    _pick(page, "ja")
    page.validatePage()

    assert wiz.working_config() == test_config


def test_an_edit_outside_the_scoped_fields_survives_a_re_choice(wizard_factory, test_config):
    """The AnkiConnect URL belongs to no language, so a re-choice may not drop it."""
    wiz = wizard_factory(test_config)
    page = wiz.language_page

    page.initializePage()
    _pick(page, "zh")
    page.validatePage()
    wiz.update_working_config(replace(wiz.working_config(), ankiconnect_url="http://127.0.0.1:9999"))

    page.initializePage()  # Back
    _pick(page, "ko")
    page.validatePage()

    assert wiz.working_config().ankiconnect_url == "http://127.0.0.1:9999"


# ---------------------------------------------------------------------------
# Walking away, and languages this build cannot mine
# ---------------------------------------------------------------------------


def test_an_unconfirmed_pick_is_not_staged_on_close(wizard_factory, test_config):
    """Skip Setup and Escape both stage the pages' edits; a language is not one."""
    wiz = wizard_factory(test_config)
    page = wiz.language_page
    page.initializePage()
    _pick(page, "zh")

    wiz._stage_current_edits()

    assert wiz.working_config() == test_config


def test_a_language_this_build_cannot_mine_is_not_offered(monkeypatch, wizard_factory, test_config):
    from tests.unit.languages.stub_registry import unregister_profile

    unregister_profile(monkeypatch, "ko")
    page = wizard_factory(test_config).language_page

    codes = [page.language_combo.itemData(i) for i in range(page.language_combo.count())]
    assert "ko" not in codes
    assert "ja" in codes


def test_a_config_naming_an_unofferable_language_is_left_alone(monkeypatch, wizard_factory, test_config):
    """The opening selection is a display default, never a switch nobody asked for."""
    monkeypatch.setattr(wizard_pages, "available_mining_languages", lambda: (("ja", "日本語"),))
    zh_config = switch_language(test_config, "zh")
    wiz = wizard_factory(zh_config)

    page = wiz.language_page
    page.initializePage()
    assert page.validatePage() is True

    assert wiz.working_config() == zh_config


def test_the_page_never_routes_through_the_first_visit_controller(monkeypatch, wizard_factory, test_config):
    """The deck-exclusion prompt belongs to the Settings switch, not to setup."""
    import anki_miner.gui.controllers.language_switch as controller

    def _boom(*args, **kwargs):
        raise AssertionError("the wizard must not run the guarded switch")

    monkeypatch.setattr(controller, "request_language_change", _boom)
    monkeypatch.setattr(controller, "offer_first_visit_setup", _boom)

    wiz = wizard_factory(test_config)
    page = wiz.language_page
    page.initializePage()
    _pick(page, "zh")
    page.validatePage()

    assert config_language(wiz.working_config()) == "zh"
