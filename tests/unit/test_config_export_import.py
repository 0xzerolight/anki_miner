"""Tests for portable settings export/import (GUIConfigManager)."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner import __version__
from anki_miner.config import AnkiMinerConfig, ChainEntry, FreqEntry
from anki_miner.gui.utils.config_manager import GUIConfigManager, ImportConfigResult


@pytest.fixture
def export_path(tmp_path: Path) -> Path:
    return tmp_path / "anki_miner_settings.json"


def _write_import_file(tmp_path: Path, payload) -> Path:
    path = tmp_path / "import_me.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestMachineSpecificFields:
    def test_includes_every_path_field(self):
        fields = GUIConfigManager.machine_specific_fields()
        assert GUIConfigManager._path_field_names() <= fields

    def test_includes_non_path_machine_state(self):
        fields = GUIConfigManager.machine_specific_fields()
        assert {
            "first_run_shortcut_done",
            "first_run_setup_done",
            "last_known_version",
            "skipped_update_version",
            "dictionary_chain",
            "expression_audio_chain",
            "frequency_chain",
            "youtube_cookies_from_browser",
            "asr_device",
            "config_version",
        } <= fields

    def test_keeps_portable_preferences(self):
        fields = GUIConfigManager.machine_specific_fields()
        assert "anki_deck_name" not in fields
        assert "max_frequency_rank" not in fields
        assert "theme" not in fields
        assert "max_parallel_workers" not in fields


class TestExport:
    def test_export_writes_envelope_with_version(self, export_path):
        GUIConfigManager.export_config(AnkiMinerConfig(), export_path)
        payload = json.loads(export_path.read_text(encoding="utf-8"))
        assert payload["anki_miner_settings"] == 1
        assert payload["app_version"] == __version__
        assert payload["config_schema_version"] == GUIConfigManager.CONFIG_SCHEMA_VERSION
        assert isinstance(payload["settings"], dict)

    def test_export_strips_machine_specific_fields(self, export_path):
        config = AnkiMinerConfig(
            anki_deck_name="Mining",
            dicts_root=Path("/mnt/ssd/dicts"),
            dictionary_chain=(ChainEntry(kind="indexed", dict_id="jitendex"),),
            frequency_chain=(FreqEntry(source_id="bccwj"),),
            first_run_setup_done=True,
            last_known_version="2.8.0",
            config_version=41,
        )
        GUIConfigManager.export_config(config, export_path)
        settings = json.loads(export_path.read_text(encoding="utf-8"))["settings"]
        for field_name in GUIConfigManager.machine_specific_fields():
            assert field_name not in settings
        assert settings["anki_deck_name"] == "Mining"

    def test_export_retains_portable_values(self, export_path):
        config = AnkiMinerConfig(max_frequency_rank=12000, anki_deck_name="日本語")
        GUIConfigManager.export_config(config, export_path)
        settings = json.loads(export_path.read_text(encoding="utf-8"))["settings"]
        assert settings["max_frequency_rank"] == 12000
        assert settings["anki_deck_name"] == "日本語"


class TestImportOverlay:
    def test_round_trip_applies_portable_and_keeps_machine_specific(self, tmp_path, export_path):
        source = AnkiMinerConfig(anki_deck_name="Exported Deck", max_sentence_chars=99)
        GUIConfigManager.export_config(source, export_path)

        current = AnkiMinerConfig(
            anki_deck_name="Local Deck",
            dicts_root=Path("/local/dicts"),
            dictionary_chain=(ChainEntry(kind="indexed", dict_id="local-dict"),),
        )
        result = GUIConfigManager.import_config(export_path, current).config

        assert result.anki_deck_name == "Exported Deck"
        assert result.max_sentence_chars == 99
        assert result.dicts_root == Path("/local/dicts")
        assert result.dictionary_chain == current.dictionary_chain

    def test_partial_import_keeps_missing_fields_current(self, tmp_path):
        path = _write_import_file(tmp_path, {"anki_deck_name": "FromFile"})
        current = AnkiMinerConfig(anki_deck_name="Old", max_frequency_rank=5000)
        result = GUIConfigManager.import_config(path, current).config
        assert result.anki_deck_name == "FromFile"
        assert result.max_frequency_rank == 5000

    def test_flat_full_dump_is_stripped_of_machine_specific(self, tmp_path):
        foreign = GUIConfigManager._paths_to_strings(
            GUIConfigManager._config_to_serializable_dict(
                AnkiMinerConfig(
                    anki_deck_name="Foreign",
                    dicts_root=Path("/foreign/dicts"),
                    first_run_setup_done=True,
                )
            )
        )
        path = _write_import_file(tmp_path, foreign)
        current = AnkiMinerConfig(dicts_root=Path("/local/dicts"))
        result = GUIConfigManager.import_config(path, current).config
        assert result.anki_deck_name == "Foreign"
        assert result.dicts_root == Path("/local/dicts")
        assert result.first_run_setup_done is False

    def test_unknown_keys_dropped_and_renames_migrated(self, tmp_path):
        path = _write_import_file(
            tmp_path,
            {
                "use_offline_dict": True,
                "some_future_field": "x",
                "anki_fields": {"pitch_accent": "MyPitch", "frequency_rank": "MyFreq"},
            },
        )
        current = AnkiMinerConfig()
        result = GUIConfigManager.import_config(path, current).config
        assert result.anki_fields["pitch_position"] == "MyPitch"
        assert result.anki_fields["frequency"] == "MyFreq"
        assert "pitch_accent" not in result.anki_fields

    def test_envelope_and_flat_forms_equivalent(self, tmp_path):
        flat = _write_import_file(tmp_path, {"anki_deck_name": "Same"})
        enveloped = tmp_path / "env.json"
        enveloped.write_text(
            json.dumps(
                {
                    "anki_miner_settings": 1,
                    "app_version": "0.0.0",
                    "settings": {"anki_deck_name": "Same"},
                }
            ),
            encoding="utf-8",
        )
        current = AnkiMinerConfig()
        assert (
            GUIConfigManager.import_config(flat, current).config.anki_deck_name
            == GUIConfigManager.import_config(enveloped, current).config.anki_deck_name
            == "Same"
        )

    def test_returns_result_carrier_with_empty_feedback_for_clean_flat_import(self, tmp_path):
        path = _write_import_file(tmp_path, {"anki_deck_name": "Clean"})

        result = GUIConfigManager.import_config(path, AnkiMinerConfig())

        assert isinstance(result, ImportConfigResult)
        assert result.config.anki_deck_name == "Clean"
        assert result.invalid_fields == []
        assert result.notices == []

    def test_flat_import_ignores_config_version(self, tmp_path):
        path = _write_import_file(tmp_path, {"anki_deck_name": "Imported", "config_version": 999})
        current = replace(AnkiMinerConfig(), config_version=17)

        result = GUIConfigManager.import_config(path, current)

        assert result.config.anki_deck_name == "Imported"
        assert result.config.config_version == 17


class TestImportProvenance:
    _OLDER_YTDLP_NOTICE = "Auto-update of yt-dlp was disabled (settings imported from an older version)."

    @pytest.mark.parametrize(
        ("source_schema", "expected_wordsets", "expected_ytdlp", "expects_notice"),
        [
            (1, AnkiMinerConfig().excluded_wordsets, False, True),
            (2, (), False, True),
            (3, (), True, False),
        ],
    )
    def test_envelope_schema_marker_gates_migration_shims(
        self,
        tmp_path,
        source_schema,
        expected_wordsets,
        expected_ytdlp,
        expects_notice,
    ):
        path = _write_import_file(
            tmp_path,
            {
                "anki_miner_settings": 1,
                "config_schema_version": source_schema,
                "settings": {
                    "excluded_wordsets": [],
                    "auto_update_ytdlp": True,
                },
            },
        )

        result = GUIConfigManager.import_config(path, AnkiMinerConfig())

        assert result.config.excluded_wordsets == expected_wordsets
        assert result.config.auto_update_ytdlp is expected_ytdlp
        assert (self._OLDER_YTDLP_NOTICE in result.notices) is expects_notice

    def test_malformed_legacy_seed_values_rejected_not_rewritten(self, tmp_path):
        """null wordsets / string ytdlp must land in invalid_fields, not be seeded."""
        path = _write_import_file(
            tmp_path,
            {
                "anki_miner_settings": 1,
                "config_schema_version": 1,
                "settings": {
                    "excluded_wordsets": None,
                    "auto_update_ytdlp": "yes",
                },
            },
        )
        current = replace(
            AnkiMinerConfig(),
            excluded_wordsets=("place-names",),
            auto_update_ytdlp=True,
        )

        result = GUIConfigManager.import_config(path, current)

        assert result.config.excluded_wordsets == ("place-names",)
        assert result.config.auto_update_ytdlp is True
        assert set(result.invalid_fields) >= {"excluded_wordsets", "auto_update_ytdlp"}
        assert self._OLDER_YTDLP_NOTICE not in result.notices

    def test_legacy_envelope_keeps_absent_seed_fields_current(self, tmp_path):
        path = _write_import_file(
            tmp_path,
            {
                "anki_miner_settings": 1,
                "config_schema_version": 1,
                "settings": {"anki_deck_name": "Imported"},
            },
        )
        current = replace(
            AnkiMinerConfig(),
            excluded_wordsets=("place-names",),
            auto_update_ytdlp=True,
        )

        result = GUIConfigManager.import_config(path, current)

        assert result.config.anki_deck_name == "Imported"
        assert result.config.excluded_wordsets == ("place-names",)
        assert result.config.auto_update_ytdlp is True
        assert result.notices == []

    @pytest.mark.parametrize(
        ("app_version", "expected_wordsets", "expected_ytdlp", "conservative"),
        [
            ("2.8.1", AnkiMinerConfig().excluded_wordsets, False, False),
            ("2.8.2", AnkiMinerConfig().excluded_wordsets, False, False),
            ("2.8.3", (), False, True),
            ("9.9.9", (), True, False),
            (None, (), True, False),
        ],
    )
    def test_legacy_envelope_app_version_mapping(
        self,
        tmp_path,
        app_version,
        expected_wordsets,
        expected_ytdlp,
        conservative,
    ):
        payload = {
            "anki_miner_settings": 1,
            "settings": {
                "excluded_wordsets": [],
                "auto_update_ytdlp": True,
            },
        }
        if app_version is not None:
            payload["app_version"] = app_version
        path = _write_import_file(tmp_path, payload)

        result = GUIConfigManager.import_config(path, AnkiMinerConfig())

        assert result.config.excluded_wordsets == expected_wordsets
        assert result.config.auto_update_ytdlp is expected_ytdlp
        assert (self._OLDER_YTDLP_NOTICE in result.notices) is (app_version in {"2.8.1", "2.8.2", "2.8.3"})
        assert any("2.8.3" in notice for notice in result.notices) is conservative

    def test_flat_import_has_no_schema_inference_or_seed_shims(self, tmp_path):
        path = _write_import_file(
            tmp_path,
            {
                "excluded_wordsets": [],
                "auto_update_ytdlp": True,
            },
        )

        result = GUIConfigManager.import_config(path, AnkiMinerConfig())

        assert result.config.excluded_wordsets == ()
        assert result.config.auto_update_ytdlp is True
        assert result.notices == []

    def test_envelope_schema_marker_takes_precedence_over_legacy_app_version(self, tmp_path):
        path = _write_import_file(
            tmp_path,
            {
                "anki_miner_settings": 1,
                "app_version": "2.8.1",
                "config_schema_version": GUIConfigManager.CONFIG_SCHEMA_VERSION,
                "settings": {
                    "excluded_wordsets": [],
                    "auto_update_ytdlp": True,
                },
            },
        )

        result = GUIConfigManager.import_config(path, AnkiMinerConfig())

        assert result.config.excluded_wordsets == ()
        assert result.config.auto_update_ytdlp is True
        assert result.notices == []


class TestNestedMappingOverlay:
    def test_partial_anki_fields_keeps_unlisted_current_subkeys(self, tmp_path):
        path = _write_import_file(tmp_path, {"anki_fields": {"word": "Vocab"}})
        current = replace(
            AnkiMinerConfig(),
            anki_fields={**AnkiMinerConfig().anki_fields, "source": "MySource"},
        )
        result = GUIConfigManager.import_config(path, current).config
        assert result.anki_fields["word"] == "Vocab"
        assert result.anki_fields["source"] == "MySource"

    def test_absent_anki_fields_keeps_current_wholesale(self, tmp_path):
        path = _write_import_file(tmp_path, {"anki_deck_name": "X"})
        current = replace(
            AnkiMinerConfig(),
            anki_fields={**AnkiMinerConfig().anki_fields, "source": "MySource"},
        )
        result = GUIConfigManager.import_config(path, current).config
        assert result.anki_fields["source"] == "MySource"

    def test_null_anki_fields_keeps_current(self, tmp_path):
        path = _write_import_file(tmp_path, {"anki_fields": None, "anki_deck_name": "X"})
        current = replace(
            AnkiMinerConfig(),
            anki_fields={**AnkiMinerConfig().anki_fields, "source": "MySource"},
        )
        result = GUIConfigManager.import_config(path, current).config
        assert result.anki_deck_name == "X"
        assert result.anki_fields["source"] == "MySource"

    def test_partial_card_type_marker_fields_merges_onto_current(self, tmp_path):
        path = _write_import_file(tmp_path, {"card_type_marker_fields": {"reading": "R2"}})
        current = replace(
            AnkiMinerConfig(),
            card_type_marker_fields={
                **AnkiMinerConfig().card_type_marker_fields,
                "expression": "E1",
            },
        )
        result = GUIConfigManager.import_config(path, current).config
        assert result.card_type_marker_fields["reading"] == "R2"
        assert result.card_type_marker_fields["expression"] == "E1"


class TestImportErrors:
    def test_malformed_json_raises(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            GUIConfigManager.import_config(path, AnkiMinerConfig())

    def test_non_dict_top_level_raises_value_error(self, tmp_path):
        for payload in ([1, 2, 3], "just a string", 5):
            path = _write_import_file(tmp_path, payload)
            with pytest.raises(ValueError):
                GUIConfigManager.import_config(path, AnkiMinerConfig())

    def test_non_dict_envelope_settings_raises_value_error(self, tmp_path):
        path = _write_import_file(tmp_path, {"anki_miner_settings": 1, "settings": [1]})
        with pytest.raises(ValueError):
            GUIConfigManager.import_config(path, AnkiMinerConfig())

    def test_invalid_typed_field_is_dropped_and_reported(self, tmp_path):
        path = _write_import_file(tmp_path, {"check_for_updates": {"not": "a bool"}})
        current = replace(AnkiMinerConfig(), check_for_updates=False)

        result = GUIConfigManager.import_config(path, current)

        assert result.config.check_for_updates is False
        assert result.invalid_fields == ["check_for_updates"]

    def test_missing_file_raises_oserror(self, tmp_path):
        with pytest.raises(OSError):
            GUIConfigManager.import_config(tmp_path / "nope.json", AnkiMinerConfig())
