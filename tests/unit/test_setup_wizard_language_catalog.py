"""ResourcesPage iterates the active language's catalogue, not a JA constant."""

from __future__ import annotations

from dataclasses import replace

import pytest

from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services.resource_catalog import RECOMMENDED_DEFAULT_SET


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


def test_an_unregistered_language_degrades_instead_of_breaking_the_wizard(wizard_factory, test_config):
    """R7: ``ko`` is a LEGAL stored code with no registered profile until Stage 3.

    ``get_profile`` raises on it, and this page is built during wizard
    construction - so a settings import from a future ko build, or a hand-edited
    ``gui_config.json``, would make the whole wizard unconstructible on first run
    AND from Tools -> Setup Wizard. ``config_language`` degrades it to ja.
    """
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
