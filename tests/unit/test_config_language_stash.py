"""language_stash: read-only wrap, JSON round-trip, export exclusion, plus the
committed pre-change gui_config.json proof (spec §13 item 3)."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

import anki_miner.config.config as config_module
from anki_miner.config import AnkiMinerConfig, ChainEntry, create_default_config
from anki_miner.gui.utils.config_manager import GUIConfigManager

#: Captured from the stage-0 base commit with ANKI_MINER_HOME=/fixture-home
#: (see the plan's Task 0.2 Step 1). Never regenerate it on a later commit —
#: its whole purpose is to be older than every multi-language change.
PRE_CHANGE_CONFIG = Path(__file__).resolve().parents[1] / "fixtures" / "config" / "gui_config_pre_multilanguage.json"

#: Fields present in the pre-change fixture that later releases removed from
#: AnkiMinerConfig. The fixture is a historical snapshot and stays as-is, so
#: the per-key equality check below skips these names; a load must migrate
#: them away rather than round-trip them, which the final set-difference
#: assertion in test_pre_change_config_loads_every_field_unchanged confirms.
REMOVED_FIELDS = frozenset(
    {"use_native_file_dialogs", "use_sentence_length_filter", "ui_font_scale", "jisho_api_url", "jisho_delay"}
)


@pytest.fixture
def isolated_config_file(tmp_path: Path, monkeypatch) -> Path:
    fake = tmp_path / "gui_config.json"
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", fake)
    return fake


def test_default_stash_is_empty():
    assert dict(AnkiMinerConfig().language_stash) == {}


def test_stash_is_read_only_outside_and_inside():
    cfg = AnkiMinerConfig(language_stash={"zh": {"anki_deck_name": "ZH"}})
    with pytest.raises(TypeError):
        cfg.language_stash["ko"] = {}
    with pytest.raises(TypeError):
        cfg.language_stash["zh"]["anki_deck_name"] = "other"


def test_stash_keys_are_normalized_like_the_language_field():
    """``language`` is strip+lower normalized, so a stash key that is not could
    never be matched against it."""
    cfg = AnkiMinerConfig(language_stash={" ZH ": {"anki_deck_name": "ZH"}})
    assert dict(cfg.language_stash) == {"zh": {"anki_deck_name": "ZH"}}


def test_stash_round_trips_chains_and_paths(isolated_config_file, tmp_path: Path):
    black = tmp_path / "black.txt"
    cfg = dataclasses.replace(
        create_default_config(),
        language="ja",
        language_stash={
            "zh": {
                "dictionary_chain": (ChainEntry(kind="indexed", dict_id="cc-cedict"),),
                "blacklist_path": black,
                "allowed_pos": ("n", "v"),
            }
        },
    )
    GUIConfigManager.save_config(cfg)
    loaded = GUIConfigManager.load_config()

    stashed = loaded.language_stash["zh"]
    assert stashed["dictionary_chain"] == (ChainEntry(kind="indexed", dict_id="cc-cedict"),)
    assert stashed["blacklist_path"] == black
    assert list(stashed["allowed_pos"]) == ["n", "v"]


def test_stash_is_machine_specific_and_stripped_from_exports(tmp_path: Path):
    assert "language_stash" in GUIConfigManager.machine_specific_fields()
    assert "language" not in GUIConfigManager.machine_specific_fields()
    export = tmp_path / "settings.json"
    GUIConfigManager.export_config(
        dataclasses.replace(create_default_config(), language_stash={"zh": {"anki_deck_name": "ZH"}}),
        export,
    )
    settings = json.loads(export.read_text(encoding="utf-8"))["settings"]
    assert "language_stash" not in settings
    assert settings["language"] == "ja"


def test_serialization_gains_exactly_two_keys(isolated_config_file):
    """A gui_config.json written WITHOUT the two new keys loads equal to the
    default config — the whole diff for existing users is these two keys."""
    payload = GUIConfigManager._paths_to_strings(GUIConfigManager._config_to_serializable_dict(create_default_config()))
    payload.pop("language")
    payload.pop("language_stash")
    payload["config_schema_version"] = GUIConfigManager.CONFIG_SCHEMA_VERSION
    isolated_config_file.write_text(json.dumps(payload), encoding="utf-8")

    assert GUIConfigManager.load_config() == create_default_config()


def test_pre_change_config_loads_every_field_unchanged(isolated_config_file):
    """Spec §13 item 3: a gui_config.json written by the pre-multi-language
    build loads with every pre-existing field equal to its recorded value, and
    the only added keys are Stage 0's `language` / `language_stash` plus task
    2A.11's two language-scoped fields, which load as their ja-inert defaults.
    The recorded `/fixture-home` default paths are rebased onto the current
    ANKI_MINER_HOME (BA-009), so they compare against that home.

    Fields added after that stage join the set as they land. `strict_card_order`
    is deliberately NOT in LANGUAGE_SCOPED_FIELDS: card creation order is the
    same decision in every language, so it stays global and survives a switch."""
    raw = PRE_CHANGE_CONFIG.read_text(encoding="utf-8")
    recorded = json.loads(raw)
    isolated_config_file.write_text(raw, encoding="utf-8")

    loaded = GUIConfigManager.load_config()
    reserialized = GUIConfigManager._paths_to_strings(GUIConfigManager._config_to_serializable_dict(loaded))

    for key, value in recorded.items():
        if key == "config_schema_version":  # envelope key, not a dataclass field
            continue
        if key in REMOVED_FIELDS:
            continue
        if key == "dictionary_chain":
            # The fixture's disabled Jisho row is the online dictionary since
            # removed; it migrates away and the rest keeps its order.
            assert reserialized[key] == [entry for entry in value if entry["kind"] != "jisho"]
            continue
        if key == "anki_fields":
            # Keys added to the default mapping since the fixture are backfilled
            # on load (_backfill_anki_fields); every recorded one must be unchanged.
            assert {k: reserialized[key][k] for k in value} == value
            assert set(reserialized[key]) - set(value) == {"sentence_translation", "language"}
            continue
        if isinstance(value, str) and value.startswith("/fixture-home/"):
            value = str(config_module.ANKI_MINER_HOME / value.removeprefix("/fixture-home/"))
        assert reserialized[key] == value, key
    assert not (REMOVED_FIELDS & set(reserialized)), "removed field survived reload"
    assert set(reserialized) - set(recorded) == {
        "language",
        "language_stash",
        "script_variant",
        "reading_tone_color",
        "strict_card_order",
        "condenser_merge_output",
        "secondary_subtitle_enabled",
        "review_words_before_mining",
        "youtube_align_captions",
        "youtube_subtitle_source",
        "deck_builder_mode",
        "deck_builder_top_n",
        "deck_builder_coverage_pct",
        "deck_builder_skip_known",
        "backfill_field_groups",
        "downloader_audio_lang",
        "mokuro_use_gpu",
        "mokuro_location",
        "uv_root",
        "merge_incomplete_cues",
        "hidden_utilities",
        "key_bindings",
        "auto_update_dictionaries",
        "auto_open_anki",
    }
    assert loaded.script_variant == "" and loaded.reading_tone_color is False


def test_amn_on_zh_leaves_ja_markers(test_config):
    """Card-type markers and the pitch format follow the mining language: applying
    Anki Miner Note on zh must not rewrite the Senren markers ja was set up with."""
    from anki_miner.languages.switching import switch_language
    from anki_miner.services.note_presets import ANKI_MINER_NOTE

    ja = dataclasses.replace(
        test_config,
        language="ja",
        card_type="sentence",
        card_type_marker_fields={
            "word_and_sentence": "",
            "click": "",
            "sentence": "sentenceCard",
            "audio": "audioCard",
        },
        pitch_category_format="jp",
    )
    zh = switch_language(ja, "zh")
    zh = dataclasses.replace(
        zh, card_type_marker_fields=dict(ANKI_MINER_NOTE.card_type_marker_fields), pitch_category_format="romaji"
    )
    back = switch_language(zh, "ja")
    assert back.card_type_marker_fields["sentence"] == "sentenceCard"
    assert back.pitch_category_format == "jp"
