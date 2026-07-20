"""Tests for dictionary_chain / expression_audio_chain persistence and legacy migration."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig, AudioSourceEntry, ChainEntry, FreqEntry, create_default_config
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.services.wordset_service import WORDSET_IDS


@pytest.fixture
def tmp_config(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "gui_config.json"
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", cfg_path)
    return cfg_path


def test_save_then_load_preserves_chain(tmp_config: Path):
    chain = (
        ChainEntry(kind="indexed", dict_id="custom-dict", enabled=True),
        ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=False),
        ChainEntry(kind="jisho", dict_id=None, enabled=True),
    )
    config = AnkiMinerConfig()
    config = replace(config, dictionary_chain=chain)
    GUIConfigManager.save_config(config)

    loaded = GUIConfigManager.load_config()
    assert loaded.dictionary_chain == chain


def test_old_config_backfills_expression_audio_in_anki_fields(tmp_config: Path):
    """A saved config whose anki_fields lacks 'expression_audio' must get the
    default value merged in on load — not crash or silently omit the key.
    """
    tmp_config.write_text(
        json.dumps(
            {
                "anki_fields": {
                    "word": "Expression",
                    "sentence": "Sentence",
                    "definition": "MainDefinition",
                    "glossary": "",
                    "picture": "Picture",
                    "audio": "SentenceAudio",
                    "expression_furigana": "ExpressionFurigana",
                    "expression_reading": "",
                    "sentence_furigana": "SentenceFurigana",
                    "sentence_reading": "",
                    "pitch_position": "",
                    "pitch_category": "",
                    "frequency": "Frequency",  # user-set non-empty value
                    "source": "",
                    # expression_audio intentionally absent (old config)
                },
            }
        )
    )

    loaded = GUIConfigManager.load_config()
    # New key must be present with the default empty string
    assert "expression_audio" in loaded.anki_fields
    assert loaded.anki_fields["expression_audio"] == ""
    # Existing user value must be preserved
    assert loaded.anki_fields["frequency"] == "Frequency"


def test_old_config_without_expression_audio_fields_gets_defaults(tmp_config: Path):
    """A saved config missing expression_audio_delay must fall through to
    dataclass defaults rather than crashing.
    """
    tmp_config.write_text(
        json.dumps(
            {
                "anki_deck_name": "MyDeck",
                # expression_audio_delay absent
            }
        )
    )

    loaded = GUIConfigManager.load_config()
    assert loaded.anki_deck_name == "MyDeck"
    assert loaded.expression_audio_delay == 0.2


def test_old_config_without_reading_min_occurrence_gets_default(tmp_config: Path):
    """A saved config predating reading_min_occurrence must backfill the default (1)."""
    tmp_config.write_text(
        json.dumps(
            {
                "anki_deck_name": "MyDeck",
                # reading_min_occurrence absent (old config)
            }
        )
    )

    loaded = GUIConfigManager.load_config()
    assert loaded.anki_deck_name == "MyDeck"
    assert loaded.reading_min_occurrence == 1


def test_reading_tts_defaults():
    """Feature ships inert: master flag OFF, both providers pre-selected."""
    config = AnkiMinerConfig()
    assert config.reading_tts_enabled is False
    assert config.reading_tts_google_enabled is True
    assert config.reading_tts_papago_enabled is True


def test_save_then_load_preserves_reading_tts_bools(tmp_config: Path):
    config = replace(
        AnkiMinerConfig(),
        reading_tts_enabled=True,
        reading_tts_google_enabled=False,
        reading_tts_papago_enabled=True,
    )
    GUIConfigManager.save_config(config)

    loaded = GUIConfigManager.load_config()
    assert loaded.reading_tts_enabled is True
    assert loaded.reading_tts_google_enabled is False
    assert loaded.reading_tts_papago_enabled is True


def test_old_config_without_reading_tts_keys_gets_defaults(tmp_config: Path):
    """A saved config predating sentence TTS must backfill the inert defaults."""
    tmp_config.write_text(json.dumps({"anki_deck_name": "MyDeck"}))

    loaded = GUIConfigManager.load_config()
    assert loaded.reading_tts_enabled is False
    assert loaded.reading_tts_google_enabled is True
    assert loaded.reading_tts_papago_enabled is True


def test_obsolete_expression_audio_enabled_key_is_dropped(tmp_config: Path):
    """A legacy config carrying the removed expression_audio_enabled flag must
    load cleanly (the unknown key is dropped, not fatal), and a non-empty
    expression_audio field name alone activates the feature.
    """
    tmp_config.write_text(
        json.dumps(
            {
                "anki_deck_name": "MyDeck",
                "expression_audio_enabled": True,  # removed field — must be ignored
                "anki_fields": {"expression_audio": "ExpressionAudio"},
            }
        )
    )

    loaded = GUIConfigManager.load_config()
    assert loaded.anki_deck_name == "MyDeck"
    assert not hasattr(loaded, "expression_audio_enabled")
    assert loaded.anki_fields["expression_audio"] == "ExpressionAudio"


def test_null_anki_fields_does_not_raise(tmp_config: Path):
    """Config JSON with anki_fields: null must not crash load_config; the
    corrupt value is replaced with full defaults while every OTHER saved
    setting survives (no whole-config reset).
    """
    tmp_config.write_text(json.dumps({"anki_fields": None, "anki_deck_name": "KeepMe", "expression_audio_delay": 1.5}))

    loaded = GUIConfigManager.load_config()
    assert "expression_audio" in loaded.anki_fields
    assert loaded.anki_deck_name == "KeepMe"
    assert loaded.expression_audio_delay == 1.5


def test_string_anki_fields_does_not_raise(tmp_config: Path):
    """Config JSON with anki_fields as a string must not crash load_config;
    same non-dict guard path as the null case.
    """
    tmp_config.write_text(json.dumps({"anki_fields": "legacy_string"}))

    loaded = GUIConfigManager.load_config()
    assert "expression_audio" in loaded.anki_fields


# ---------------------------------------------------------------------------
# expression_audio_chain migration tests
# ---------------------------------------------------------------------------


def test_save_then_load_preserves_audio_chain(tmp_config: Path):
    """A config with pack + jpod101 entries survives save/load unchanged.

    A persisted chain that already contains a googletts entry is not
    appended to again.
    """
    chain = (
        AudioSourceEntry(kind="pack", pack_id="nhk16", enabled=True),
        AudioSourceEntry(kind="pack", pack_id="forvo-jp", enabled=False),
        AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),
        AudioSourceEntry(kind="googletts", pack_id=None, enabled=False),
    )
    config = replace(AnkiMinerConfig(), expression_audio_chain=chain)
    GUIConfigManager.save_config(config)

    loaded = GUIConfigManager.load_config()
    assert loaded.expression_audio_chain == chain


def test_absent_audio_chain_yields_default(tmp_config: Path):
    """An old gui_config.json without expression_audio_chain uses the default."""
    tmp_config.write_text(json.dumps({"anki_deck_name": "MyDeck"}))

    loaded = GUIConfigManager.load_config()
    assert loaded.expression_audio_chain == (
        AudioSourceEntry(kind="jpod101"),
        AudioSourceEntry(kind="googletts", enabled=False),
    )


def test_migrate_expression_audio_chain_rebuilds_from_dicts():
    """_migrate_expression_audio_chain converts list[dict] to tuple[AudioSourceEntry]."""
    data = {
        "expression_audio_chain": [
            {"kind": "pack", "pack_id": "nhk16", "enabled": True},
            {"kind": "jpod101", "pack_id": None, "enabled": False},
            {"kind": "googletts", "pack_id": None, "enabled": True},
        ]
    }
    result = GUIConfigManager._migrate_expression_audio_chain(data)
    chain = result["expression_audio_chain"]
    assert isinstance(chain, tuple)
    assert chain == (
        AudioSourceEntry(kind="pack", pack_id="nhk16", enabled=True),
        AudioSourceEntry(kind="jpod101", pack_id=None, enabled=False),
        AudioSourceEntry(kind="googletts", pack_id=None, enabled=True),
    )


def test_migrate_expression_audio_chain_googletts_round_trip():
    """A googletts dict rebuilds into an AudioSourceEntry preserving enabled."""
    data = {
        "expression_audio_chain": [
            {"kind": "googletts", "pack_id": None, "enabled": True},
        ]
    }
    result = GUIConfigManager._migrate_expression_audio_chain(data)
    chain = result["expression_audio_chain"]
    assert chain[0] == AudioSourceEntry(kind="googletts", pack_id=None, enabled=True)


def test_migrate_expression_audio_chain_appends_missing_googletts():
    """A persisted jpod101-only chain gains a disabled googletts entry."""
    data = {
        "expression_audio_chain": [
            {"kind": "jpod101", "enabled": True},
        ]
    }
    result = GUIConfigManager._migrate_expression_audio_chain(data)
    chain = result["expression_audio_chain"]
    assert len(chain) == 2
    assert chain[-1] == AudioSourceEntry(kind="googletts", pack_id=None, enabled=False)


def test_migrate_expression_audio_chain_no_duplicate_googletts():
    """A chain already containing googletts is not appended to."""
    data = {
        "expression_audio_chain": [
            {"kind": "jpod101", "enabled": True},
            {"kind": "googletts", "enabled": True},
        ]
    }
    result = GUIConfigManager._migrate_expression_audio_chain(data)
    chain = result["expression_audio_chain"]
    assert len(chain) == 2
    assert sum(1 for e in chain if e.kind == "googletts") == 1
    # Existing enabled flag preserved (not overwritten with a disabled dup).
    assert chain[-1].enabled is True


def test_migrate_expression_audio_chain_enabled_flag_defaults_true():
    """Missing 'enabled' key in a JSON dict defaults to True."""
    data = {
        "expression_audio_chain": [
            {"kind": "pack", "pack_id": "nhk16"},
        ]
    }
    result = GUIConfigManager._migrate_expression_audio_chain(data)
    assert result["expression_audio_chain"][0].enabled is True


def test_migrate_expression_audio_chain_pack_id_none():
    """pack_id absent from JSON dict is stored as None."""
    data = {
        "expression_audio_chain": [
            {"kind": "jpod101"},
        ]
    }
    result = GUIConfigManager._migrate_expression_audio_chain(data)
    assert result["expression_audio_chain"][0].pack_id is None


def test_migrate_expression_audio_chain_skips_unknown_kinds():
    """Entries with unknown kind values are silently dropped.

    A disabled googletts entry is appended since the source chain had none.
    """
    data = {
        "expression_audio_chain": [
            {"kind": "unknown_source", "pack_id": "foo"},
            {"kind": "jpod101"},
        ]
    }
    result = GUIConfigManager._migrate_expression_audio_chain(data)
    chain = result["expression_audio_chain"]
    assert len(chain) == 2
    assert chain[0].kind == "jpod101"
    assert chain[1] == AudioSourceEntry(kind="googletts", enabled=False)


def test_migrate_expression_audio_chain_absent_key_is_noop():
    """When expression_audio_chain is absent the dict is returned unchanged."""
    data: dict = {"anki_deck_name": "Test"}
    result = GUIConfigManager._migrate_expression_audio_chain(data)
    assert "expression_audio_chain" not in result


def test_migrate_expression_audio_chain_rebuilds_custom_with_url():
    """A custom URL entry rebuilds into an AudioSourceEntry preserving url."""
    data = {
        "expression_audio_chain": [
            {"kind": "custom", "url": "http://localhost:5050/?term={term}&reading={reading}", "enabled": True},
            {"kind": "jpod101"},
        ]
    }
    result = GUIConfigManager._migrate_expression_audio_chain(data)
    chain = result["expression_audio_chain"]
    assert chain[0] == AudioSourceEntry(
        kind="custom",
        url="http://localhost:5050/?term={term}&reading={reading}",
        enabled=True,
    )


def test_migrate_expression_audio_chain_rebuilds_custom_json():
    """custom_json survives the JSON→dataclass rebuild; a removed scrape kind is dropped."""
    data = {
        "expression_audio_chain": [
            {"kind": "custom_json", "url": "http://localhost:5050/list?term={term}", "enabled": False},
            {"kind": "jpod101_scrape", "enabled": True},  # removed kind → silently dropped
            {"kind": "jpod101"},
        ]
    }
    result = GUIConfigManager._migrate_expression_audio_chain(data)
    kinds = [e.kind for e in result["expression_audio_chain"]]
    # Scrape entry dropped; a default googletts fallback is appended.
    assert kinds == ["custom_json", "jpod101", "googletts"]
    assert result["expression_audio_chain"][0].url == "http://localhost:5050/list?term={term}"


def test_save_then_load_preserves_custom_entries(tmp_config: Path):
    """A chain with custom entries survives a real save/load round-trip."""
    chain = (
        AudioSourceEntry(kind="custom", url="http://host/?t={term}&r={reading}", enabled=True),
        AudioSourceEntry(kind="jpod101", enabled=True),
        AudioSourceEntry(kind="googletts", enabled=False),
    )
    config = replace(AnkiMinerConfig(), expression_audio_chain=chain)
    GUIConfigManager.save_config(config)

    loaded = GUIConfigManager.load_config()
    assert loaded.expression_audio_chain == chain


# ---------------------------------------------------------------------------
# frequency_chain migration tests
# ---------------------------------------------------------------------------


def test_save_then_load_preserves_frequency_chain(tmp_config: Path):
    """A non-empty frequency_chain survives a save/load round-trip as FreqEntry."""
    chain = (
        FreqEntry(source_id="jpdb", enabled=True),
        FreqEntry(source_id="bccwj", enabled=False),
        FreqEntry(source_id="legacy-frequency", enabled=True),
    )
    config = replace(AnkiMinerConfig(), frequency_chain=chain)
    GUIConfigManager.save_config(config)

    loaded = GUIConfigManager.load_config()
    assert loaded.frequency_chain == chain
    assert all(isinstance(e, FreqEntry) for e in loaded.frequency_chain)


def test_migrate_frequency_chain_rebuilds_from_dicts():
    """_migrate_frequency_chain converts list[dict] to tuple[FreqEntry]."""
    data = {
        "frequency_chain": [
            {"source_id": "jpdb", "enabled": True},
            {"source_id": "bccwj", "enabled": False},
        ]
    }
    result = GUIConfigManager._migrate_frequency_chain(data)
    chain = result["frequency_chain"]
    assert chain == (
        FreqEntry(source_id="jpdb", enabled=True),
        FreqEntry(source_id="bccwj", enabled=False),
    )
    assert all(isinstance(e, FreqEntry) for e in chain)


def test_migrate_frequency_chain_defaults_enabled_true():
    """A dict lacking 'enabled' defaults to True."""
    data = {"frequency_chain": [{"source_id": "jpdb"}]}
    result = GUIConfigManager._migrate_frequency_chain(data)
    assert result["frequency_chain"] == (FreqEntry(source_id="jpdb", enabled=True),)


def test_migrate_frequency_chain_tolerates_existing_freqentry():
    """Items that are already FreqEntry instances pass through unchanged."""
    data = {"frequency_chain": [FreqEntry(source_id="jpdb", enabled=False)]}
    result = GUIConfigManager._migrate_frequency_chain(data)
    assert result["frequency_chain"] == (FreqEntry(source_id="jpdb", enabled=False),)


def test_migrate_frequency_chain_drops_malformed_entries():
    """Entries with missing/empty source_id (or wrong type) are dropped."""
    data = {
        "frequency_chain": [
            {"source_id": "jpdb"},
            {"source_id": ""},
            {"enabled": True},
            {"source_id": None},
            "not-a-dict",
            42,
        ]
    }
    result = GUIConfigManager._migrate_frequency_chain(data)
    assert result["frequency_chain"] == (FreqEntry(source_id="jpdb", enabled=True),)


def test_migrate_frequency_chain_absent_key_is_noop():
    """When frequency_chain is absent the dict is returned unchanged."""
    data: dict = {"anki_deck_name": "Test"}
    result = GUIConfigManager._migrate_frequency_chain(data)
    assert "frequency_chain" not in result


def test_old_config_without_frequency_chain_defaults_empty(tmp_config: Path):
    """A saved config predating frequency_chain loads as the empty-tuple default."""
    tmp_config.write_text(json.dumps({"anki_deck_name": "Test"}))
    loaded = GUIConfigManager.load_config()
    assert loaded.frequency_chain == ()


def test_handwritten_json_frequency_chain_loads_as_freqentries(tmp_config: Path):
    """A hand-written gui_config.json frequency_chain of dicts loads into FreqEntry."""
    tmp_config.write_text(
        json.dumps(
            {
                "frequency_chain": [
                    {"source_id": "jpdb", "enabled": True},
                    {"source_id": "bccwj", "enabled": False},
                ]
            }
        )
    )
    loaded = GUIConfigManager.load_config()
    assert loaded.frequency_chain == (
        FreqEntry(source_id="jpdb", enabled=True),
        FreqEntry(source_id="bccwj", enabled=False),
    )


# ---------------------------------------------------------------------------
# Name-wordset default-ON seed migration (schema v2, junk-reduction r3)
# ---------------------------------------------------------------------------

_ALL_WORDSETS = ("surnames", "given-names", "place-names", "org-product")


def _write_config(tmp_config: Path, body: dict) -> None:
    tmp_config.write_text(json.dumps(body), encoding="utf-8")


def test_schema_version_is_three():
    """The latest bump carries the updater-containment shim."""
    assert GUIConfigManager.CONFIG_SCHEMA_VERSION == 3


class TestWordsetSeedMigration:
    """Matrix: {no marker, marker=1} x {absent, [], partial, full} on LOAD."""

    def test_no_marker_absent_key_is_seeded(self, tmp_config: Path):
        _write_config(tmp_config, {"anki_deck_name": "D"})
        assert GUIConfigManager.load_config().excluded_wordsets == _ALL_WORDSETS

    def test_no_marker_empty_list_is_seeded(self, tmp_config: Path):
        _write_config(tmp_config, {"excluded_wordsets": []})
        assert GUIConfigManager.load_config().excluded_wordsets == _ALL_WORDSETS

    def test_no_marker_partial_list_untouched(self, tmp_config: Path):
        _write_config(tmp_config, {"excluded_wordsets": ["surnames"]})
        assert GUIConfigManager.load_config().excluded_wordsets == ("surnames",)

    def test_no_marker_full_list_untouched(self, tmp_config: Path):
        _write_config(tmp_config, {"excluded_wordsets": list(_ALL_WORDSETS)})
        assert GUIConfigManager.load_config().excluded_wordsets == _ALL_WORDSETS

    def test_marker_v1_absent_key_is_seeded(self, tmp_config: Path):
        _write_config(tmp_config, {"config_schema_version": 1, "anki_deck_name": "D"})
        assert GUIConfigManager.load_config().excluded_wordsets == _ALL_WORDSETS

    def test_marker_v1_empty_list_is_seeded(self, tmp_config: Path):
        _write_config(tmp_config, {"config_schema_version": 1, "excluded_wordsets": []})
        assert GUIConfigManager.load_config().excluded_wordsets == _ALL_WORDSETS

    def test_marker_v1_partial_list_untouched(self, tmp_config: Path):
        _write_config(
            tmp_config,
            {"config_schema_version": 1, "excluded_wordsets": ["place-names"]},
        )
        assert GUIConfigManager.load_config().excluded_wordsets == ("place-names",)

    def test_marker_v1_full_list_untouched(self, tmp_config: Path):
        _write_config(
            tmp_config,
            {"config_schema_version": 1, "excluded_wordsets": list(_ALL_WORDSETS)},
        )
        assert GUIConfigManager.load_config().excluded_wordsets == _ALL_WORDSETS

    def test_marker_v2_empty_list_not_reseeded(self, tmp_config: Path):
        """A v2 config with a deliberate all-off list is respected, not re-seeded."""
        _write_config(tmp_config, {"config_schema_version": 2, "excluded_wordsets": []})
        assert GUIConfigManager.load_config().excluded_wordsets == ()

    def test_seed_value_equals_wordset_ids(self, tmp_config: Path):
        """The seeded set is exactly the canonical WORDSET_IDS, in order."""
        _write_config(tmp_config, {"excluded_wordsets": []})
        assert GUIConfigManager.load_config().excluded_wordsets == WORDSET_IDS


class TestWordsetSeedImportPath:
    """Import must NOT seed — an imported all-off must survive."""

    def test_import_empty_list_not_seeded(self, tmp_path: Path):
        path = tmp_path / "import.json"
        path.write_text(json.dumps({"excluded_wordsets": []}), encoding="utf-8")
        # current has the default-ON set; the explicit imported empty wins and
        # is NOT force-re-seeded back to all-on.
        result = GUIConfigManager.import_config(path, create_default_config())
        assert result.excluded_wordsets == ()

    def test_import_partial_list_applied(self, tmp_path: Path):
        path = tmp_path / "import.json"
        path.write_text(json.dumps({"excluded_wordsets": ["surnames"]}), encoding="utf-8")
        result = GUIConfigManager.import_config(path, create_default_config())
        assert result.excluded_wordsets == ("surnames",)

    def test_import_absent_key_keeps_current(self, tmp_path: Path):
        path = tmp_path / "import.json"
        path.write_text(json.dumps({"anki_deck_name": "X"}), encoding="utf-8")
        current = replace(AnkiMinerConfig(), excluded_wordsets=("place-names",))
        result = GUIConfigManager.import_config(path, current)
        assert result.excluded_wordsets == ("place-names",)


class TestWordsetSeedIdempotence:
    """Once seeded and saved (v2), a reload is stable."""

    def test_default_save_load_round_trips_all_on(self, tmp_config: Path):
        GUIConfigManager.save_config(create_default_config())
        loaded = GUIConfigManager.load_config()
        assert loaded.excluded_wordsets == _ALL_WORDSETS

    def test_seed_then_resave_is_stable(self, tmp_config: Path):
        # First load seeds a pre-v2 markerless config to all-on.
        _write_config(tmp_config, {"excluded_wordsets": []})
        first = GUIConfigManager.load_config()
        assert first.excluded_wordsets == _ALL_WORDSETS
        # Re-save (stamps current schema) and reload: value stays stable.
        GUIConfigManager.save_config(first)
        raw = json.loads(tmp_config.read_text(encoding="utf-8"))
        assert raw["config_schema_version"] == GUIConfigManager.CONFIG_SCHEMA_VERSION
        second = GUIConfigManager.load_config()
        assert second.excluded_wordsets == _ALL_WORDSETS

    def test_deliberate_all_off_survives_save_load(self, tmp_config: Path):
        off = replace(create_default_config(), excluded_wordsets=())
        GUIConfigManager.save_config(off)
        loaded = GUIConfigManager.load_config()
        assert loaded.excluded_wordsets == ()
