"""ResourcesPage iterates the active language's catalogue, not a JA constant."""

from __future__ import annotations

from dataclasses import replace

import pytest

from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services.resource_catalog import RECOMMENDED_DEFAULT_SET
from tests.unit.languages.stub_registry import unregister_profile


class _FakeValidation:
    def check_resource_readiness(self):
        raise AssertionError("no live probe in this test")


@pytest.fixture
def wizard_factory(qtbot, monkeypatch):
    """Build a wizard whose live checks never reach disk or Anki.

    A local factory rather than test_setup_wizard.py's ``_wizard_with_validation``:
    that helper is private to a pre-existing file this plan may not edit.
    """
    built: list[SetupWizard] = []

    def build(config):
        monkeypatch.setattr(SetupWizard, "validation_service", lambda self: _FakeValidation())
        wiz = SetupWizard(config)
        qtbot.addWidget(wiz)
        built.append(wiz)
        return wiz

    return build


def test_ja_catalogue_is_the_recommended_default_set(wizard_factory, test_config):
    """The ja profile must keep offering exactly the shipped catalogue —
    test_setup_wizard.py's test_resources_page_offers_one_checkbox_per_catalog_entry_all_on
    asserts the page against RECOMMENDED_DEFAULT_SET and may not be edited."""
    assert get_profile("ja").catalog == RECOMMENDED_DEFAULT_SET
    page = wizard_factory(replace(test_config, language="ja")).resources_page
    assert page.selected_specs() == list(RECOMMENDED_DEFAULT_SET)
    assert not page.pitch_label.isHidden()


def test_an_unregistered_language_degrades_instead_of_breaking_the_wizard(wizard_factory, test_config, monkeypatch):
    """R7: a LEGAL stored code whose profile this build does not carry.

    ``get_profile`` raises on it, and this page is built during wizard
    construction - so a settings import from another build, or a hand-edited
    ``gui_config.json``, would make the whole wizard unconstructible on first run
    AND from Tools -> Setup Wizard. ``config_language`` degrades it to ja.
    ``ko`` registered in Stage 3, so the code is hidden rather than renamed.
    """
    unregister_profile(monkeypatch, "ko")

    page = wizard_factory(replace(test_config, language="ko")).resources_page

    assert page.selected_specs() == list(RECOMMENDED_DEFAULT_SET)
    assert not page.pitch_label.isHidden()


def test_zh_offers_its_own_catalogue_and_no_pitch_line(wizard_factory, test_config):
    zh_catalog = get_profile("zh").catalog
    page = wizard_factory(switch_language(test_config, "zh")).resources_page

    assert set(page.resource_checks) == {spec.id for spec in zh_catalog}
    assert all(box.isChecked() for box in page.resource_checks.values())
    assert "jmdict-english" not in page.resource_checks
    assert page.pitch_label.isHidden()


class TestAnEmptyCatalogue:
    """ko ships no downloadable resource, and the page has to survive that.

    With ``_specs == []`` the page used to offer an enabled Download button over
    a handler that returns silently, and gate Next on a dictionary probe with
    nothing to find - a first run that cannot be finished and cannot be
    explained. Nothing to download is nothing to block on.
    """

    @pytest.fixture
    def ko_page(self, wizard_factory, test_config):
        assert get_profile("ko").catalog == ()
        return wizard_factory(switch_language(test_config, "ko")).resources_page

    def test_nothing_is_offered_for_download(self, ko_page):
        assert ko_page.resource_checks == {}
        assert ko_page.selected_specs() == []

    def test_the_download_button_is_disabled(self, ko_page):
        assert not ko_page.download_button.isEnabled()

    def test_the_page_says_where_the_resources_come_from(self, ko_page):
        text = ko_page.status_label.text()

        assert text
        assert "Settings" in text

    def test_next_is_not_blocked(self, ko_page):
        assert ko_page.isComplete()


class TestAPopulatedCatalogueIsUnchanged:
    """ja and zh must behave exactly as they did before the empty-catalogue fix."""

    @pytest.mark.parametrize("code", ["ja", "zh"])
    def test_the_download_button_is_offered_and_next_still_waits_for_a_dictionary(
        self, wizard_factory, test_config, code
    ):
        assert get_profile(code).catalog  # non-empty, so the branch below is the live one
        page = wizard_factory(switch_language(test_config, code)).resources_page

        assert page.download_button.isEnabled()
        assert page.status_label.text() == ""
        assert not page.isComplete()
