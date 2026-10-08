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
    """Build a wizard that offers the page and whose live checks stay offline."""

    def build(config, *, offer_mining_language=True):
        monkeypatch.setattr(SetupWizard, "validation_service", lambda self: _FakeValidation())
        wiz = SetupWizard(config, offer_mining_language=offer_mining_language)
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


def test_the_language_page_is_the_first_step(wizard_factory, test_config):
    wiz = wizard_factory(test_config)

    ids = wiz.pageIds()
    assert len(ids) == 4
    assert wiz.page(ids[0]) is wiz.language_page
    assert wiz.page(ids[1]) is wiz.resources_page
    assert wiz.page(ids[2]) is wiz.anki_page


def test_a_wizard_that_was_not_asked_has_no_language_step(wizard_factory, test_config):
    wiz = wizard_factory(test_config, offer_mining_language=False)

    ids = wiz.pageIds()
    assert len(ids) == 3
    assert wiz.language_page is None
    assert wiz.page(ids[0]) is wiz.resources_page


def test_a_wizard_without_the_page_has_nothing_to_revert_on_a_walk_away(wizard_factory, test_config):
    """The close funnel's revert is the language page's, so it cannot fire without one."""
    wiz = wizard_factory(test_config, offer_mining_language=False)

    wiz.reject()

    assert wiz.working_config() == test_config


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
    assert wiz.currentPage() is wiz.language_page
    _pick(wiz.language_page, "zh")
    assert wiz.working_config() == test_config  # still on the page: nothing committed

    wiz.next()
    assert wiz.currentPage() is wiz.resources_page
    assert config_language(wiz.working_config()) == "zh"
    # The page entered by that Next owns a worker thread; let it land.
    qtbot.waitUntil(lambda: not wiz.resources_page.dictionary_label.text().startswith("Checking"), timeout=5000)


def test_the_resources_page_offers_the_chosen_language_catalogue(qtbot, wizard_factory, test_config):
    wiz = wizard_factory(test_config)
    assert "jmdict-english" in {spec.id for spec in wiz.resources_page.selected_specs()}

    page = wiz.language_page
    page.initializePage()
    _pick(page, "zh")
    page.validatePage()

    wiz.resources_page.initializePage()
    assert {spec.id for spec in wiz.resources_page.selected_specs()} == {spec.id for spec in get_profile("zh").catalog}
    assert "cc-cedict" in {spec.id for spec in wiz.resources_page.selected_specs()}
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

    assert wiz.deck_page.current_deck() == zh_deck


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


@pytest.mark.parametrize("walk_away", ["skip", "reject"])
def test_a_committed_pick_does_not_survive_a_walk_away(wizard_factory, test_config, walk_away):
    """Next commits the switch for the steps after it; only Finish keeps it."""
    from PyQt6.QtWidgets import QWizard  # noqa: PLC0415

    wiz = wizard_factory(test_config)
    page = wiz.language_page
    page.initializePage()
    _pick(page, "zh")
    page.validatePage()
    assert config_language(wiz.working_config()) == "zh"

    if walk_away == "skip":
        wiz.customButtonClicked.emit(QWizard.WizardButton.CustomButton1.value)
    else:
        wiz.reject()

    assert wiz.working_config() == test_config


def test_finishing_is_what_keeps_the_pick(wizard_factory, test_config):
    wiz = wizard_factory(test_config)
    page = wiz.language_page
    page.initializePage()
    _pick(page, "zh")
    page.validatePage()

    wiz.accept()

    assert config_language(wiz.working_config()) == "zh"


def test_a_walk_away_reverts_the_languages_own_fields_and_nothing_else(wizard_factory, test_config):
    """The deck belongs to the language being taken back; the URL belongs to none."""
    wiz = wizard_factory(test_config)
    page = wiz.language_page
    page.initializePage()
    _pick(page, "zh")
    page.validatePage()
    wiz.ankiconnect_page.url_input.setText("http://127.0.0.1:9999")
    wiz.deck_page.select_deck("Chinese::Mining")

    wiz.reject()

    config = wiz.working_config()
    assert config.ankiconnect_url == "http://127.0.0.1:9999"
    assert config_language(config) == "ja"
    assert config.anki_deck_name == test_config.anki_deck_name


def test_a_cancelled_run_returns_the_language_it_was_given(qtbot, monkeypatch, test_config):
    """What the caller persists is the returned config, on every close path."""
    from PyQt6.QtWidgets import QDialog, QWizard  # noqa: PLC0415

    from anki_miner.gui.widgets.dialogs.setup_wizard import run_setup_wizard  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import setup_wizard as sw_mod  # noqa: PLC0415

    monkeypatch.setattr(sw_mod.SetupWizard, "validation_service", lambda self: _FakeValidation())

    def fake_exec(self):
        qtbot.addWidget(self)
        self.language_page.initializePage()
        _pick(self.language_page, "zh")
        self.language_page.validatePage()
        self.customButtonClicked.emit(QWizard.WizardButton.CustomButton1.value)
        return QDialog.DialogCode.Rejected.value

    monkeypatch.setattr(sw_mod.SetupWizard, "exec", fake_exec)

    outcome = run_setup_wizard(None, test_config, offer_mining_language=True)

    assert outcome.config == test_config


def test_a_language_this_build_cannot_mine_is_not_offered(monkeypatch, wizard_factory, test_config):
    from tests.unit.languages.stub_registry import unregister_profile

    unregister_profile(monkeypatch, "ko")
    page = wizard_factory(test_config).language_page

    codes = [page.language_combo.itemData(i) for i in range(page.language_combo.count())]
    assert "ko" not in codes
    assert "ja" in codes


def test_a_config_naming_an_unofferable_language_is_left_alone(monkeypatch, wizard_factory, test_config):
    """The opening selection is a display default, never a switch nobody asked for."""
    from anki_miner.gui.utils.language_choices import MiningLanguageChoice

    monkeypatch.setattr(
        wizard_pages, "mining_language_choices", lambda: (MiningLanguageChoice("ja", "日本語", "日本語"),)
    )
    zh_config = switch_language(test_config, "zh")
    wiz = wizard_factory(zh_config)

    page = wiz.language_page
    page.initializePage()
    assert page.validatePage() is True

    assert wiz.working_config() == zh_config


def test_the_page_never_routes_through_the_first_visit_controller(monkeypatch, wizard_factory, test_config):
    """The deck checklist comes after the wizard has closed, never from inside it."""
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


class _FakeInstallWorker:
    """Stands in for a running InstallWorker; nothing here ever runs."""

    def isRunning(self) -> bool:  # noqa: N802 - mirrors QThread
        return True


class _FakeBackgroundTasks:
    def __init__(self) -> None:
        self.language_pack_workers: dict[str, object] = {}
        self.started: list[str] = []
        self.on_status = None
        self.on_finished = None

    def start_language_pack_download(self, code, root, on_status, on_finished) -> None:
        self.started.append(code)
        self.on_status = on_status
        self.on_finished = on_finished
        self.language_pack_workers[code] = _FakeInstallWorker()


def _choices_with_a_download(monkeypatch):
    from anki_miner.gui.utils.language_choices import MiningLanguageChoice

    choices = (
        MiningLanguageChoice("ja", "日本語", "Japanese"),
        MiningLanguageChoice("de", "Deutsch", "German", True, 70),
    )
    monkeypatch.setattr(wizard_pages, "mining_language_choices", lambda: choices)


def _wizard_with_tasks(qtbot, monkeypatch, test_config):
    from PyQt6.QtWidgets import QWidget

    parent = QWidget()
    qtbot.addWidget(parent)
    tasks = _FakeBackgroundTasks()
    parent.background_tasks = tasks  # type: ignore[attr-defined]
    monkeypatch.setattr(SetupWizard, "validation_service", lambda self: _FakeValidation())
    wiz = SetupWizard(test_config, parent, offer_mining_language=True)
    return wiz, tasks


def test_a_language_needing_a_download_is_offered_with_a_suffix(qtbot, monkeypatch, test_config):
    _choices_with_a_download(monkeypatch)
    wiz, _tasks = _wizard_with_tasks(qtbot, monkeypatch, test_config)
    combo = wiz.language_page.language_combo

    assert combo.itemText(combo.findData("de")) == "Deutsch (download)"
    _pick(wiz.language_page, "de")
    # The name is wrapped in Unicode isolates so an Arabic name cannot turn
    # the English sentence right-to-left.
    assert wiz.language_page.download_note.text() == (
        "\u2068Deutsch\u2069 needs a one-time download of about 70 MB. It starts when you press Next and runs while "
        "you finish setup."
    )


def test_next_starts_the_pack_download_and_ready_waits_for_it(qtbot, monkeypatch, test_config):
    _choices_with_a_download(monkeypatch)
    monkeypatch.setattr(wizard_pages, "ensure_language_packs_on_syspath", lambda: None)
    wiz, tasks = _wizard_with_tasks(qtbot, monkeypatch, test_config)
    page = wiz.language_page
    _pick(page, "de")

    assert page.validatePage() is True

    assert tasks.started == ["de"]
    assert config_language(wiz.working_config()) == "de"
    assert page.pack_ready() is False
    wiz.done_page._results = dict.fromkeys(wizard_pages._FINAL_CHECKS, True)
    assert wiz.done_page.isComplete() is False
    assert "Deutsch" in wiz.done_page._summary_html()

    tasks.on_finished(True, "Deutsch pack installed.")

    assert page.pack_ready() is True
    assert wiz.done_page.isComplete() is True


def test_a_failed_pack_download_offers_retry(qtbot, monkeypatch, test_config):
    _choices_with_a_download(monkeypatch)
    wiz, tasks = _wizard_with_tasks(qtbot, monkeypatch, test_config)
    _pick(wiz.language_page, "de")
    wiz.language_page.validatePage()

    tasks.on_finished(False, "network down")

    assert wiz.language_page.pack_ready() is False
    line = wiz.language_page.ready_page_pack_line()
    assert 'href="pack"' in line
    tasks.language_pack_workers.clear()
    wiz.language_page.activate_link("pack")
    assert tasks.started == ["de", "de"]


def test_without_a_downloader_the_pick_is_refused_with_a_reason(qtbot, monkeypatch, test_config):
    _choices_with_a_download(monkeypatch)
    wiz = SetupWizard(test_config, offer_mining_language=True)
    qtbot.addWidget(wiz)
    _pick(wiz.language_page, "de")

    assert wiz.language_page.validatePage() is False
    assert "Settings → Mining Language" in wiz.language_page.download_note.text()
    assert config_language(wiz.working_config()) == "ja"
