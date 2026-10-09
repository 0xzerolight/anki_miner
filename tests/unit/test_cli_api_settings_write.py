"""--api settings writes: settings-import into a new or a non-active profile, and the active profile's save."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner.cli.api import settings, settings_write
from anki_miner.cli.api.contract import ApiError
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.profile_store import ProfileStore


def _file(tmp_path: Path, **values: object) -> Path:
    """The envelope settings-export writes (GUIConfigManager.export_config)."""
    path = tmp_path / "surasura.json"
    envelope = {
        "anki_miner_settings": 1,
        "config_schema_version": GUIConfigManager.CONFIG_SCHEMA_VERSION,
        "settings": values,
    }
    path.write_text(json.dumps(envelope), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _no_seeded_marker(monkeypatch):
    """The API process never seeds ACTIVE_PROFILE_ID; tests must not leak one either."""
    monkeypatch.setattr(GUIConfigManager, "ACTIVE_PROFILE_ID", None)


@pytest.fixture
def live(test_config):
    """Set up once, before any profile exists."""
    GUIConfigManager.save_config(replace(test_config, anki_deck_name="Live deck"))
    return test_config


@pytest.fixture
def two_profiles(test_config, monkeypatch):
    """``default`` active, ``caller`` beside it."""
    monkeypatch.setattr(GUIConfigManager, "ACTIVE_PROFILE_ID", "default")
    GUIConfigManager.save_config(test_config)
    monkeypatch.setattr(GUIConfigManager, "ACTIVE_PROFILE_ID", None)
    ProfileStore.write_profile("default", test_config, name="Default")
    fields = {**test_config.anki_fields, "stale_key": "Old"}
    ProfileStore.write_profile(
        "caller", replace(test_config, anki_deck_name="Caller deck", anki_fields=fields), name="Caller"
    )
    return test_config


def _import(path, language="ja", *, name=None, profile_id=None):
    return settings_write.import_settings(path, language, name=name, profile_id=profile_id)


def test_name_adopts_default_then_adds_the_profile(live, tmp_path) -> None:
    result = _import(_file(tmp_path, anki_deck_name="Surasura", use_whitelist=False), name="Surasura")
    assert result == {"profile": "surasura", "created": True, "invalid_fields": []}
    assert settings.profiles() == [
        {"id": "default", "name": "Default", "active": True},
        {"id": "surasura", "name": "Surasura", "active": False},
    ]
    assert ProfileStore.read_profile("default").anki_deck_name == "Live deck"
    created = ProfileStore.read_profile("surasura")
    assert created.anki_deck_name == "Surasura" and created.use_whitelist is False
    assert created.anki_note_type == live.anki_note_type  # left out of the file: the active profile's
    # The window's next boot finds its marker, so it saves no "Recovered settings" profile.
    assert GUIConfigManager.read_active_profile_id() == "default"
    assert GUIConfigManager.load_config().anki_deck_name == "Live deck"
    assert GUIConfigManager.ACTIVE_PROFILE_ID is None  # restored after the save


def test_name_on_a_fresh_install_writes_the_marked_settings_file(tmp_path) -> None:
    assert not GUIConfigManager.CONFIG_FILE.exists()
    _import(_file(tmp_path), name="Caller")
    assert GUIConfigManager.read_active_profile_id() == "default"


def test_a_taken_name_is_refused_before_anything_is_written(live, tmp_path) -> None:
    with pytest.raises(ApiError) as err:
        _import(_file(tmp_path), name="default")
    # Default is the active profile before any profile exists: --profile default is refused too.
    assert err.value.code == "BAD_ARGUMENTS" and "active profile" in err.value.message
    assert "--profile" not in err.value.message
    assert not ProfileStore.profiles_dir().exists()


def test_a_taken_name_names_the_profile_to_update_unless_it_is_active(two_profiles, tmp_path) -> None:
    with pytest.raises(ApiError) as err:
        _import(_file(tmp_path), name="CALLER")
    assert err.value.code == "BAD_ARGUMENTS" and "use --profile caller to update it" in err.value.message
    with pytest.raises(ApiError) as err:
        _import(_file(tmp_path), name="Default")
    assert "active profile" in err.value.message and "--profile" not in err.value.message


def test_a_refused_file_with_name_writes_nothing(live, tmp_path) -> None:
    before = GUIConfigManager.CONFIG_FILE.read_bytes()
    with pytest.raises(ApiError) as err:
        _import(_file(tmp_path, language="ko"), name="Caller")
    assert err.value.code == "BAD_ARGUMENTS"
    assert not ProfileStore.profiles_dir().exists()
    assert GUIConfigManager.CONFIG_FILE.read_bytes() == before


def test_name_beside_existing_profiles_adopts_nothing(two_profiles, tmp_path) -> None:
    before = GUIConfigManager.CONFIG_FILE.read_bytes()
    default_before = (ProfileStore.profiles_dir() / "default.json").read_bytes()
    result = _import(_file(tmp_path, anki_deck_name="New deck"), name="New")
    assert result == {"profile": "new", "created": True, "invalid_fields": []}
    assert [p.name for p in ProfileStore.list_profiles()] == ["Caller", "Default", "New"]
    created = ProfileStore.read_profile("new")
    assert created.anki_deck_name == "New deck" and created.anki_note_type == two_profiles.anki_note_type
    assert GUIConfigManager.CONFIG_FILE.read_bytes() == before
    assert (ProfileStore.profiles_dir() / "default.json").read_bytes() == default_before


def test_the_active_profile_is_refused(two_profiles, tmp_path) -> None:
    with pytest.raises(ApiError) as err:
        _import(_file(tmp_path), profile_id="default")
    assert err.value.code == "BAD_ARGUMENTS" and "active profile" in err.value.message


def test_profile_updates_it_and_keeps_what_the_file_leaves_out(two_profiles, tmp_path) -> None:
    # As stored: read_profile rebases test_config's tmp-dir paths onto the isolated home.
    before = ProfileStore.read_profile("caller")
    path = _file(tmp_path, anki_deck_name="Imported", anki_fields={"word": "Front"}, dicts_root="/nowhere")
    assert _import(path, profile_id="caller") == {"profile": "caller", "created": False, "invalid_fields": []}
    caller = ProfileStore.read_profile("caller")
    assert caller.anki_deck_name == "Imported" and caller.anki_fields["word"] == "Front"
    assert caller.anki_fields["sentence"] == two_profiles.anki_fields["sentence"]
    assert caller.anki_fields["stale_key"] == "Old"  # the round trip the window's own switch makes keeps it
    assert caller.dicts_root == before.dicts_root != Path("/nowhere")  # paths never travel
    assert [p.name for p in ProfileStore.list_profiles()] == ["Caller", "Default"]
    assert GUIConfigManager.load_config().anki_deck_name == two_profiles.anki_deck_name


def test_language_switches_the_profile_and_the_file_applies_to_it(two_profiles, tmp_path) -> None:
    _import(_file(tmp_path, anki_deck_name="Korean"), "ko", profile_id="caller")
    caller = ProfileStore.read_profile("caller")
    assert caller.language == "ko" and caller.anki_deck_name == "Korean"
    assert caller.language_stash["ja"]["anki_deck_name"] == "Caller deck"


def test_a_file_for_another_language_is_refused(two_profiles, tmp_path) -> None:
    with pytest.raises(ApiError) as err:
        _import(_file(tmp_path, language="ko"), profile_id="caller")
    assert err.value.code == "BAD_ARGUMENTS" and "'ko'" in err.value.message
    assert ProfileStore.read_profile("caller").anki_deck_name == "Caller deck"


def test_refused_values_keep_the_profiles_and_are_listed(two_profiles, tmp_path) -> None:
    result = _import(_file(tmp_path, min_frequency_rank="5", subtitle_regex_filter="("), profile_id="caller")
    assert result["invalid_fields"] == ["min_frequency_rank", "subtitle_regex_filter"]
    caller = ProfileStore.read_profile("caller")
    assert caller.min_frequency_rank == two_profiles.min_frequency_rank
    assert caller.subtitle_regex_filter == two_profiles.subtitle_regex_filter


@pytest.mark.parametrize("content", ["[]", "not json"])
def test_a_file_that_is_not_settings_is_bad_arguments(two_profiles, tmp_path, content) -> None:
    path = tmp_path / "x.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ApiError) as err:
        _import(path, profile_id="caller")
    assert err.value.code == "BAD_ARGUMENTS"


def test_an_unknown_profile_is_unreadable(two_profiles, tmp_path) -> None:
    with pytest.raises(ApiError) as err:
        _import(_file(tmp_path), profile_id="nope")
    assert err.value.code == "PROFILE_UNREADABLE"


def test_save_profile_keeps_the_active_marker(two_profiles) -> None:
    assert settings_write.save_profile(None, replace(two_profiles, anki_deck_name="Saved")) == "default"
    assert GUIConfigManager.read_active_profile_id() == "default"
    assert GUIConfigManager.load_config().anki_deck_name == "Saved"
    assert settings_write.save_profile("caller", replace(two_profiles, anki_deck_name="C")) == "caller"
    assert ProfileStore.read_profile("caller").anki_deck_name == "C"
    assert [p.name for p in ProfileStore.list_profiles()] == ["Caller", "Default"]


def _from_a_newer_app() -> tuple[bytes, Path]:
    """gui_config.json as a newer Anki Miner left it, and where the window's load would archive it."""
    path = GUIConfigManager.CONFIG_FILE
    schema = GUIConfigManager.CONFIG_SCHEMA_VERSION + 1
    data = json.loads(path.read_text(encoding="utf-8"))
    data["config_schema_version"] = schema
    path.write_text(json.dumps(data), encoding="utf-8")
    return path.read_bytes(), path.with_name(f"gui_config.from-schema-{schema}.json")


def test_a_newer_apps_settings_file_is_archived_before_name_saves_over_it(live, tmp_path) -> None:
    original, archive = _from_a_newer_app()
    _import(_file(tmp_path), name="Caller")  # no profile yet: the live settings are saved as "default"
    assert archive.read_bytes() == original


def test_a_newer_apps_settings_file_is_archived_before_the_active_profile_is_saved(live) -> None:
    original, archive = _from_a_newer_app()
    settings_write.save_profile(None, live)  # setup's write to the active profile
    assert archive.read_bytes() == original


def test_a_current_settings_file_is_not_archived(live, tmp_path) -> None:
    _import(_file(tmp_path), name="Caller")
    assert list(GUIConfigManager.CONFIG_FILE.parent.glob("gui_config.from-schema-*")) == []
