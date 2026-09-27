"""--api settings: profile, language and the one-run overlay."""

from __future__ import annotations

from dataclasses import replace

import pytest

from anki_miner.cli.api import settings
from anki_miner.cli.api.contract import ApiError
from anki_miner.config import create_default_config
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.profile_store import ProfileStore


def test_no_profiles_lists_an_implicit_active_default() -> None:
    assert settings.profiles() == [{"id": "default", "name": "Default", "active": True}]


def test_live_config_is_read_without_writing(test_config) -> None:
    GUIConfigManager.save_config(replace(test_config, anki_deck_name="Live"))
    before = sorted(p.name for p in GUIConfigManager.CONFIG_FILE.parent.iterdir())
    assert settings.load_profile_config(None).anki_deck_name == "Live"
    assert settings.load_profile_config("default").anki_deck_name == "Live"
    assert sorted(p.name for p in GUIConfigManager.CONFIG_FILE.parent.iterdir()) == before


def test_missing_settings_read_as_defaults() -> None:
    assert not GUIConfigManager.CONFIG_FILE.exists()
    assert settings.load_profile_config(None).anki_deck_name == create_default_config().anki_deck_name


def test_unknown_profile_is_unreadable_never_defaults(test_config) -> None:
    GUIConfigManager.save_config(test_config)
    with pytest.raises(ApiError) as err:
        settings.load_profile_config("nope")
    assert err.value.code == "PROFILE_UNREADABLE"


def test_other_profile_reads_its_file(test_config) -> None:
    GUIConfigManager.save_config(test_config)
    ProfileStore.write_profile("caller", replace(test_config, anki_deck_name="Caller deck"), name="Caller")
    assert settings.load_profile_config("caller").anki_deck_name == "Caller deck"


def test_overlay_merges_fields_per_key(test_config) -> None:
    out = settings.apply_overlay(test_config, {"anki_fields": {"word": "Front"}, "min_frequency_rank": 5})
    assert out.anki_fields["word"] == "Front"
    assert out.anki_fields["sentence"] == test_config.anki_fields["sentence"]
    assert out.min_frequency_rank == 5


@pytest.mark.parametrize(
    "overlay",
    [
        {"theme": "dark"},
        {"min_frequency_rank": "5"},
        {"use_blacklist": 1},
        {"card_type": "bogus"},
        {"max_parallel_workers": 99},
    ],
)
def test_overlay_refusals_are_bad_run_file(test_config, overlay) -> None:
    with pytest.raises(ApiError) as err:
        settings.apply_overlay(test_config, overlay)
    assert err.value.code == "BAD_RUN_FILE"


def test_run_config_switches_language_then_overlays_and_forces_known_words(test_config) -> None:
    GUIConfigManager.save_config(test_config)
    config = settings.resolve_run_config(None, "ja", {"anki_deck_name": "Mining"})
    assert config.anki_deck_name == "Mining"
    assert config.include_known_words is True and config.use_known_words_db is False
    with pytest.raises(ApiError) as err:
        settings.resolve_run_config(None, "xx", {})
    assert err.value.code == "BAD_RUN_FILE"
