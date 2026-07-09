"""Tests for portable settings export/import (GUIConfigManager)."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner import __version__
from anki_miner.config import AnkiMinerConfig, ChainEntry, FreqEntry
from anki_miner.gui.utils.config_manager import GUIConfigManager


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
        assert isinstance(payload["settings"], dict)

    def test_export_strips_machine_specific_fields(self, export_path):
        config = AnkiMinerConfig(
            anki_deck_name="Mining",
            dicts_root=Path("/mnt/ssd/dicts"),
            dictionary_chain=(ChainEntry(kind="indexed", dict_id="jitendex"),),
            frequency_chain=(FreqEntry(source_id="bccwj"),),
            first_run_setup_done=True,
            last_known_version="2.8.0",
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
        result = GUIConfigManager.import_config(export_path, current)

        assert result.anki_deck_name == "Exported Deck"
        assert result.max_sentence_chars == 99
        assert result.dicts_root == Path("/local/dicts")
        assert result.dictionary_chain == current.dictionary_chain

    def test_partial_import_keeps_missing_fields_current(self, tmp_path):
        path = _write_import_file(tmp_path, {"anki_deck_name": "FromFile"})
        current = AnkiMinerConfig(anki_deck_name="Old", max_frequency_rank=5000)
        result = GUIConfigManager.import_config(path, current)
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
        result = GUIConfigManager.import_config(path, current)
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
        result = GUIConfigManager.import_config(path, current)
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
            GUIConfigManager.import_config(flat, current).anki_deck_name
            == GUIConfigManager.import_config(enveloped, current).anki_deck_name
            == "Same"
        )


class TestNestedMappingOverlay:
    def test_partial_anki_fields_keeps_unlisted_current_subkeys(self, tmp_path):
        path = _write_import_file(tmp_path, {"anki_fields": {"word": "Vocab"}})
        current = replace(
            AnkiMinerConfig(),
            anki_fields={**AnkiMinerConfig().anki_fields, "source": "MySource"},
        )
        result = GUIConfigManager.import_config(path, current)
        assert result.anki_fields["word"] == "Vocab"
        assert result.anki_fields["source"] == "MySource"

    def test_absent_anki_fields_keeps_current_wholesale(self, tmp_path):
        path = _write_import_file(tmp_path, {"anki_deck_name": "X"})
        current = replace(
            AnkiMinerConfig(),
            anki_fields={**AnkiMinerConfig().anki_fields, "source": "MySource"},
        )
        result = GUIConfigManager.import_config(path, current)
        assert result.anki_fields["source"] == "MySource"

    def test_null_anki_fields_keeps_current(self, tmp_path):
        path = _write_import_file(tmp_path, {"anki_fields": None, "anki_deck_name": "X"})
        current = replace(
            AnkiMinerConfig(),
            anki_fields={**AnkiMinerConfig().anki_fields, "source": "MySource"},
        )
        result = GUIConfigManager.import_config(path, current)
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
        result = GUIConfigManager.import_config(path, current)
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

    def test_type_poisoned_coerced_field_raises(self, tmp_path):
        path = _write_import_file(tmp_path, {"ui_font_scale": "abc"})
        with pytest.raises((TypeError, ValueError)):
            GUIConfigManager.import_config(path, AnkiMinerConfig())

    def test_missing_file_raises_oserror(self, tmp_path):
        with pytest.raises(OSError):
            GUIConfigManager.import_config(tmp_path / "nope.json", AnkiMinerConfig())
