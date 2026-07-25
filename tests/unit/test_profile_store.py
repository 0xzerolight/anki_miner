"""Tests for the named-settings-profile storage layer.

Two properties dominate this file:

* ``profiles_dir()`` must resolve through ``GUIConfigManager.CONFIG_FILE`` at
  call time, so the per-test home isolation redirects profile writes. A
  snapshotted path would write into the user's real ~/.anki_miner.
* ``read_profile`` must PROPAGATE read/decode failures. Falling back to factory
  defaults (as the gui_config.json load path does) would silently blank a named
  profile that has no .bak to recover from.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner.config import create_default_config
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.profile_store import MAX_PROFILES, Profile, ProfileStore


def _write_raw(profile_id: str, payload: object) -> Path:
    """Drop a raw profile JSON file straight into the profiles dir."""
    directory = ProfileStore.profiles_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{profile_id}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestProfilesDirIsolation:
    def test_profiles_dir_is_under_the_isolated_tmp_home(self, _isolate_anki_home: Path):
        assert ProfileStore.profiles_dir() == _isolate_anki_home / "profiles"

    def test_profiles_dir_is_not_under_the_real_home(self):
        real_home = Path(os.path.expanduser("~")) / ".anki_miner"
        assert real_home not in ProfileStore.profiles_dir().parents

    def test_profiles_dir_follows_config_file_at_call_time(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", tmp_path / "elsewhere" / "gui_config.json")

        assert ProfileStore.profiles_dir() == tmp_path / "elsewhere" / "profiles"


class TestListProfiles:
    def test_missing_directory_yields_empty_tuple(self):
        assert not ProfileStore.profiles_dir().exists()

        assert ProfileStore.list_profiles() == ()

    def test_empty_directory_yields_empty_tuple(self):
        ProfileStore.profiles_dir().mkdir(parents=True)

        assert ProfileStore.list_profiles() == ()

    def test_non_json_files_are_ignored(self):
        directory = ProfileStore.profiles_dir()
        directory.mkdir(parents=True)
        (directory / "notes.txt").write_text("ignored", encoding="utf-8")

        assert ProfileStore.list_profiles() == ()

    def test_sorted_by_display_name_case_insensitively(self):
        _write_raw("c", {"profile_name": "banana"})
        _write_raw("a", {"profile_name": "Apple"})
        _write_raw("b", {"profile_name": "Cherry"})

        assert [p.name for p in ProfileStore.list_profiles()] == ["Apple", "banana", "Cherry"]

    def test_unusable_member_files_fall_back_to_the_filename_stem(self):
        directory = ProfileStore.profiles_dir()
        directory.mkdir(parents=True)
        (directory / "truncated.json").write_text('{"profile_name": "Anim', encoding="utf-8")
        _write_raw("nondict", ["not", "a", "mapping"])
        _write_raw("nameless", {"anki_deck_name": "Deck"})
        _write_raw("badname", {"profile_name": 5})
        _write_raw("blankname", {"profile_name": "   "})
        # Oversized files are refused by the bounded reader, same as unreadable.
        (directory / "huge.json").write_text('{"profile_name": "' + "x" * 3_000_000 + '"}', encoding="utf-8")

        names = {p.id: p.name for p in ProfileStore.list_profiles()}

        assert names == {
            "truncated": "truncated",
            "nondict": "nondict",
            "nameless": "nameless",
            "badname": "badname",
            "blankname": "blankname",
            "huge": "huge",
        }

    def test_listing_is_truncated_at_the_cap(self):
        for index in range(MAX_PROFILES + 5):
            _write_raw(f"p{index:03d}", {"profile_name": f"P{index:03d}"})

        assert len(ProfileStore.list_profiles()) == MAX_PROFILES


class TestRoundTrip:
    def test_write_then_read_returns_an_equal_config(self):
        config = replace(create_default_config(), anki_deck_name="Anime Deck", max_frequency_rank=12345)

        ProfileStore.write_profile("anime", config, name="Anime")

        assert ProfileStore.read_profile("anime") == config

    def test_write_profile_reports_its_display_name_in_the_listing(self):
        ProfileStore.write_profile("anime", create_default_config(), name="Anime")

        assert ProfileStore.list_profiles() == (Profile(id="anime", name="Anime"),)

    def test_write_profile_never_stamps_active_profile_id(self, monkeypatch):
        monkeypatch.setattr(GUIConfigManager, "ACTIVE_PROFILE_ID", "novels")

        ProfileStore.write_profile("anime", create_default_config(), name="Anime")

        data = json.loads((ProfileStore.profiles_dir() / "anime.json").read_text(encoding="utf-8"))
        assert "active_profile_id" not in data
        assert data["profile_name"] == "Anime"
        assert data["config_schema_version"] == GUIConfigManager.CONFIG_SCHEMA_VERSION

    def test_write_profile_creates_the_directory(self):
        assert not ProfileStore.profiles_dir().exists()

        ProfileStore.write_profile("anime", create_default_config(), name="Anime")

        assert (ProfileStore.profiles_dir() / "anime.json").is_file()

    @pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
    def test_profile_file_is_owner_only(self):
        ProfileStore.write_profile("anime", create_default_config(), name="Anime")

        mode = (ProfileStore.profiles_dir() / "anime.json").stat().st_mode
        assert stat.S_IMODE(mode) == 0o600


class TestReadProfileErrorsPropagate:
    def test_corrupt_file_raises_and_leaves_the_directory_untouched(self):
        path = _write_raw("anime", {"anki_deck_name": "Anime"})
        path.write_text('{"anki_deck_name": "Ani', encoding="utf-8")
        before_bytes = path.read_bytes()
        before_entries = sorted(p.name for p in ProfileStore.profiles_dir().iterdir())

        with pytest.raises(ValueError):
            ProfileStore.read_profile("anime")

        assert path.read_bytes() == before_bytes
        assert sorted(p.name for p in ProfileStore.profiles_dir().iterdir()) == before_entries

    def test_non_object_root_raises(self):
        _write_raw("anime", ["not", "a", "mapping"])

        with pytest.raises(ValueError):
            ProfileStore.read_profile("anime")

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            ProfileStore.read_profile("nope")

    def test_missing_directory_raises(self):
        assert not ProfileStore.profiles_dir().exists()

        with pytest.raises(FileNotFoundError):
            ProfileStore.read_profile("anime")


class TestReadProfileMigration:
    def test_schema_zero_file_with_a_removed_key_migrates(self):
        _write_raw(
            "legacy",
            {
                "anki_deck_name": "Legacy Deck",
                "use_offline_dict": True,  # removed field from an old version
                "auto_update_ytdlp": True,
                "profile_name": "Legacy",
            },
        )

        loaded = ProfileStore.read_profile("legacy")

        assert loaded.anki_deck_name == "Legacy Deck"
        # The schema < 3 shim ran, proving the file went through _migrate_dict.
        assert loaded.auto_update_ytdlp is False
        assert loaded.excluded_wordsets == create_default_config().excluded_wordsets


class TestCreate:
    def test_ascii_name_gets_a_slug_id(self):
        profile = ProfileStore.create("My Anime", create_default_config())

        assert profile == Profile(id="my-anime", name="My Anime")
        assert (ProfileStore.profiles_dir() / "my-anime.json").is_file()

    def test_cjk_name_gets_a_non_empty_id(self):
        profile = ProfileStore.create("アニメ", create_default_config())

        assert profile.id
        assert profile.name == "アニメ"
        assert (ProfileStore.profiles_dir() / f"{profile.id}.json").is_file()
        assert ProfileStore.read_profile(profile.id) == create_default_config()

    def test_id_collision_gets_a_numeric_suffix(self):
        config = create_default_config()
        first = ProfileStore.create("Anime", config)
        second = ProfileStore.create("Anime!", config)
        third = ProfileStore.create("Anime?", config)

        assert (first.id, second.id, third.id) == ("anime", "anime-2", "anime-3")

    def test_name_is_stripped(self):
        profile = ProfileStore.create("  Anime  ", create_default_config())

        assert profile == Profile(id="anime", name="Anime")

    @pytest.mark.parametrize("name", ["", "   "])
    def test_blank_name_is_rejected(self, name: str):
        with pytest.raises(ValueError):
            ProfileStore.create(name, create_default_config())

        assert ProfileStore.list_profiles() == ()

    def test_case_insensitive_duplicate_name_is_rejected(self):
        config = create_default_config()
        ProfileStore.create("Anime", config)

        with pytest.raises(ValueError):
            ProfileStore.create("aNiMe", config)

        assert len(ProfileStore.list_profiles()) == 1

    def test_creation_at_the_cap_is_rejected(self):
        for index in range(MAX_PROFILES):
            _write_raw(f"p{index:03d}", {"profile_name": f"P{index:03d}"})

        with pytest.raises(ValueError):
            ProfileStore.create("One Too Many", create_default_config())

        assert not (ProfileStore.profiles_dir() / "one-too-many.json").exists()


class TestRename:
    def test_rename_changes_only_the_display_name(self):
        ProfileStore.write_profile("anime", create_default_config(), name="Anime")

        ProfileStore.rename("anime", "Slice Of Life")

        assert ProfileStore.list_profiles() == (Profile(id="anime", name="Slice Of Life"),)

    def test_rename_preserves_unknown_anki_fields_sub_keys(self):
        ProfileStore.write_profile("anime", create_default_config(), name="Anime")
        path = ProfileStore.profiles_dir() / "anime.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["anki_fields"]["cloze_prefix"] = "x"
        path.write_text(json.dumps(data), encoding="utf-8")

        ProfileStore.rename("anime", "Anime Deux")

        after = json.loads(path.read_text(encoding="utf-8"))
        assert after["anki_fields"]["cloze_prefix"] == "x"
        assert after["profile_name"] == "Anime Deux"

    def test_rename_is_stripped_and_rejects_blanks(self):
        ProfileStore.write_profile("anime", create_default_config(), name="Anime")

        with pytest.raises(ValueError):
            ProfileStore.rename("anime", "   ")

        ProfileStore.rename("anime", "  Anime Deux  ")
        assert ProfileStore.list_profiles()[0].name == "Anime Deux"

    def test_rename_to_another_profiles_name_is_rejected(self):
        config = create_default_config()
        ProfileStore.write_profile("anime", config, name="Anime")
        ProfileStore.write_profile("novels", config, name="Novels")

        with pytest.raises(ValueError):
            ProfileStore.rename("novels", "aNiMe")

    def test_rename_to_its_own_name_in_a_new_case_is_allowed(self):
        ProfileStore.write_profile("anime", create_default_config(), name="Anime")

        ProfileStore.rename("anime", "ANIME")

        assert ProfileStore.list_profiles() == (Profile(id="anime", name="ANIME"),)

    def test_rename_of_a_missing_profile_raises(self):
        with pytest.raises(FileNotFoundError):
            ProfileStore.rename("nope", "Anything")

    def test_rename_of_a_corrupt_profile_raises(self):
        path = _write_raw("anime", {"profile_name": "Anime"})
        path.write_text("{ broken", encoding="utf-8")

        with pytest.raises(ValueError):
            ProfileStore.rename("anime", "Anime Deux")


class TestDelete:
    def test_delete_removes_the_file(self):
        ProfileStore.write_profile("anime", create_default_config(), name="Anime")

        ProfileStore.delete("anime")

        assert ProfileStore.list_profiles() == ()

    def test_delete_of_a_missing_profile_raises(self):
        with pytest.raises(FileNotFoundError):
            ProfileStore.delete("nope")
