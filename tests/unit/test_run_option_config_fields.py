"""The inline run options every workflow screen remembers between launches."""

from __future__ import annotations

from dataclasses import replace

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.config_manager import GUIConfigManager


def test_defaults_match_the_pre_change_hardcoded_widget_values():
    """Defaults reproduce byte-for-byte what the widgets used to hardcode."""
    config = AnkiMinerConfig()
    assert config.review_words_before_mining is False
    assert config.youtube_align_captions is False
    assert config.youtube_subtitle_source == "auto"
    assert config.deck_builder_mode == "all"
    assert config.deck_builder_top_n == 1000
    assert config.deck_builder_coverage_pct == 90.0
    assert config.deck_builder_skip_known is True
    assert config.backfill_field_groups == ()


def test_every_run_option_survives_a_save_load_round_trip():
    saved = replace(
        AnkiMinerConfig(),
        review_words_before_mining=True,
        youtube_align_captions=True,
        youtube_subtitle_source="captions",
        deck_builder_mode="coverage_pct",
        deck_builder_top_n=250,
        deck_builder_coverage_pct=98.5,
        deck_builder_skip_known=False,
        backfill_field_groups=("pitch", "frequency"),
    )
    GUIConfigManager.save_config(saved)
    loaded = GUIConfigManager.load_config()

    assert loaded.review_words_before_mining is True
    assert loaded.youtube_align_captions is True
    assert loaded.youtube_subtitle_source == "captions"
    assert loaded.deck_builder_mode == "coverage_pct"
    assert loaded.deck_builder_top_n == 250
    assert loaded.deck_builder_coverage_pct == 98.5
    assert loaded.deck_builder_skip_known is False
    assert loaded.backfill_field_groups == ("pitch", "frequency")


def test_a_json_list_of_field_groups_becomes_a_tuple():
    """JSON has no tuple; __post_init__ coerces, like excluded_wordsets."""
    config = AnkiMinerConfig(backfill_field_groups=["pitch", "glossary"])
    assert config.backfill_field_groups == ("pitch", "glossary")


def test_out_of_range_deck_builder_values_are_clamped():
    assert AnkiMinerConfig(deck_builder_top_n=0).deck_builder_top_n == 1
    assert AnkiMinerConfig(deck_builder_top_n=999_999).deck_builder_top_n == 100_000
    assert AnkiMinerConfig(deck_builder_coverage_pct=0.0).deck_builder_coverage_pct == 1.0
    assert AnkiMinerConfig(deck_builder_coverage_pct=500.0).deck_builder_coverage_pct == 100.0


def test_an_unknown_mode_or_subtitle_source_falls_back_to_the_default():
    assert AnkiMinerConfig(deck_builder_mode="nonsense").deck_builder_mode == "all"
    assert AnkiMinerConfig(youtube_subtitle_source="nonsense").youtube_subtitle_source == "auto"
