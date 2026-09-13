"""B.3: the setup wizard pre-ticks the resource that matches the config's regional variety."""

from __future__ import annotations

from dataclasses import replace

import pytest

from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard
from anki_miner.services.resource_catalog import RECOMMENDED_DEFAULT_SET, ResourceSpec
from tests.unit.languages.stub_registry import register_stub_profile

#: zh's own variant ids, so the test needs no profile that declares br/pt: the
#: mechanism is id-agnostic (pt's catalogue uses "br"/"pt").
CATALOG = (
    ResourceSpec(id="dict-xx", kind="dict", display_name="Dict", url="https://x/d.zip", license_note="n"),
    ResourceSpec(
        id="freq-a", kind="freq", display_name="A", url="https://x/a.txt", license_note="n", variant="simplified"
    ),
    ResourceSpec(
        id="freq-b", kind="freq", display_name="B", url="https://x/b.txt", license_note="n", variant="traditional"
    ),
)


class _FakeValidation:
    def check_resource_readiness(self):
        raise AssertionError("no live probe in this test")


@pytest.fixture
def resources_page(qtbot, monkeypatch, test_config):
    def build(**config_overrides):
        monkeypatch.setattr(SetupWizard, "validation_service", lambda self: _FakeValidation())
        wizard = SetupWizard(replace(test_config, **config_overrides))
        qtbot.addWidget(wizard)
        return wizard.resources_page

    return build


def test_a_spec_defaults_to_no_variant():
    assert all(spec.variant == "" for spec in RECOMMENDED_DEFAULT_SET)


@pytest.mark.parametrize(
    ("variant", "ticked"), [("simplified", {"dict-xx", "freq-a"}), ("traditional", {"dict-xx", "freq-b"})]
)
def test_only_the_matching_variety_starts_ticked(resources_page, monkeypatch, variant, ticked):
    register_stub_profile(monkeypatch, "zh", catalog=CATALOG)
    page = resources_page(language="zh", script_variant=variant)

    assert set(page.resource_checks) == {spec.id for spec in CATALOG}  # every row is still offered
    assert {spec.id for spec in page.selected_specs()} == ticked


def test_no_variety_ticks_only_the_shared_rows(resources_page, monkeypatch):
    register_stub_profile(monkeypatch, "zh", catalog=CATALOG)
    assert {spec.id for spec in resources_page(language="zh", script_variant="").selected_specs()} == {"dict-xx"}


def test_the_unticked_variety_can_still_be_chosen(resources_page, monkeypatch):
    register_stub_profile(monkeypatch, "zh", catalog=CATALOG)
    page = resources_page(language="zh", script_variant="simplified")
    page.resource_checks["freq-b"].setChecked(True)
    assert "freq-b" in {spec.id for spec in page.selected_specs()}
