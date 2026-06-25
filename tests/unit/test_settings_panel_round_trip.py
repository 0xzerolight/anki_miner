"""Round-trip equality test for the Save-path panel config-marshalling contract.

Spec (task-22a-brief.md §CRITICAL):
  Build a config with non-default values for every Save-path field, call
  ``load_from_config`` on all panels, then fold ``contribute`` over a base
  config, and assert the result EQUALS the original for all Save-path fields.

This is the regression net for OVH-019 / OVH-020: if any field is dropped,
added, mistyped, or re-encoded differently, this test fails.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.panels.anki_settings_panel import AnkiSettingsPanel
from anki_miner.gui.widgets.panels.filtering_settings_panel import FilteringSettingsPanel
from anki_miner.gui.widgets.panels.media_settings_panel import MediaSettingsPanel
from anki_miner.gui.widgets.panels.youtube_settings_panel import YouTubeSettingsPanel

# ---------------------------------------------------------------------------
# A config object with non-default values for every Save-path field.
# ---------------------------------------------------------------------------


def _non_default_save_config(tmp_path: Path) -> AnkiMinerConfig:
    """Return an AnkiMinerConfig where every Save-path field differs from default."""
    cookies_txt = tmp_path / "cookies.txt"
    cookies_txt.write_text("# Netscape HTTP Cookie File\n")
    bl = tmp_path / "blacklist.txt"
    bl.write_text("a\n")
    wl = tmp_path / "whitelist.txt"
    wl.write_text("b\n")

    return AnkiMinerConfig(
        # --- AnkiSettingsPanel ---
        anki_deck_name="TestDeck",
        anki_note_type="TestNoteType",
        ankiconnect_url="http://127.0.0.1:9999",
        anki_tags="tag1 tag2",
        anki_fields={
            "word": "Word",
            "sentence": "Sent",
            "definition": "Def",
            "glossary": "Gloss",
            "picture": "Pic",
            "audio": "Audio",
            "expression_audio": "ExprAudio",
            "expression_furigana": "ExprFuri",
            "expression_reading": "ExprRead",
            "sentence_furigana": "SentFuri",
            "sentence_reading": "SentRead",
            "pitch_position": "PitchPos",
            "pitch_category": "PitchCat",
            "frequency": "Freq",
            "source": "Source",
        },
        anki_word_field="Word",
        card_style_preset="minimal",
        custom_card_css=".custom { color: red; }",
        pitch_category_format="romaji",
        # --- MediaSettingsPanel ---
        audio_format="opus",
        audio_bitrate=96,
        audio_padding=0.5,
        screenshot_offset=2.0,
        max_parallel_workers=4,
        screenshot_animated=True,
        screenshot_animated_format="webp",
        screenshot_animated_clip_duration=3.0,
        screenshot_animated_match_audio=True,
        screenshot_animated_fps=15,
        screenshot_animated_height=480,
        screenshot_animated_quality=60,
        # --- FilteringSettingsPanel ---
        max_frequency_rank=5000,
        use_known_words_db=True,
        excluded_decks=("Deck A", "Deck B"),
        excluded_wordsets=("surnames", "given-names"),
        blacklist_path=bl,
        use_blacklist=True,
        whitelist_path=wl,
        use_whitelist=True,
        subtitle_regex_filter=r"\([^)]*\)",
        subtitle_regex_replacement="",
        use_subtitle_regex_filter=True,
        deduplicate_sentences=False,
        exclude_hiragana_only_words=True,
        exclude_katakana_only_words=True,
        use_i_plus_one_filter=True,
        use_sentence_length_filter=True,
        max_sentence_duration_seconds=8.0,
        max_sentence_chars=50,
        bold_target_in_sentence=True,
        # --- YouTubeSettingsPanel ---
        youtube_cookies_from_browser="firefox",
        youtube_cookies_file=cookies_txt,
        youtube_max_duration_s=3600,
        youtube_playlist_max=50,
    )


# The Save-path fields covered by the four panels.  Any field in this set that
# doesn't round-trip is a regression the test must catch.
_SAVE_PATH_FIELDS = frozenset(
    {
        # AnkiSettingsPanel
        "anki_deck_name",
        "anki_note_type",
        "ankiconnect_url",
        "anki_tags",
        "anki_fields",
        "anki_word_field",
        "card_style_preset",
        "custom_card_css",
        "pitch_category_format",
        # MediaSettingsPanel
        "audio_format",
        "audio_bitrate",
        "audio_padding",
        "screenshot_offset",
        "max_parallel_workers",
        "screenshot_animated",
        "screenshot_animated_format",
        "screenshot_animated_clip_duration",
        "screenshot_animated_match_audio",
        "screenshot_animated_fps",
        "screenshot_animated_height",
        "screenshot_animated_quality",
        # FilteringSettingsPanel
        "max_frequency_rank",
        "use_known_words_db",
        "excluded_decks",
        "excluded_wordsets",
        "blacklist_path",
        "use_blacklist",
        "whitelist_path",
        "use_whitelist",
        "subtitle_regex_filter",
        "subtitle_regex_replacement",
        "use_subtitle_regex_filter",
        "deduplicate_sentences",
        "exclude_hiragana_only_words",
        "exclude_katakana_only_words",
        "use_i_plus_one_filter",
        "use_sentence_length_filter",
        "max_sentence_duration_seconds",
        "max_sentence_chars",
        "bold_target_in_sentence",
        # YouTubeSettingsPanel
        "youtube_cookies_from_browser",
        "youtube_cookies_file",
        "youtube_max_duration_s",
        "youtube_playlist_max",
    }
)


class TestSavePathRoundTrip:
    """load_from_config → contribute must produce a byte-identical result."""

    def test_all_save_path_fields_round_trip(self, tmp_path, qtbot):
        """The canonical regression net for OVH-019/OVH-020.

        1. Build a config with non-default values for every Save-path field.
        2. Load it into all four save-path panels.
        3. Fold contribute() over a *default* base config.
        4. Assert every Save-path field on the result equals the original.
        """
        original = _non_default_save_config(tmp_path)

        # Construct panels (qtbot manages teardown).
        anki_panel = AnkiSettingsPanel()
        qtbot.addWidget(anki_panel)
        media_panel = MediaSettingsPanel()
        qtbot.addWidget(media_panel)
        filtering_panel = FilteringSettingsPanel()
        qtbot.addWidget(filtering_panel)
        youtube_panel = YouTubeSettingsPanel()
        qtbot.addWidget(youtube_panel)

        panels = [anki_panel, media_panel, filtering_panel, youtube_panel]

        # Step 2: load.
        for panel in panels:
            panel.load_from_config(original)

        # Step 3: fold contribute() over a default base config.
        # The base config must NOT already have non-default Save-path values so
        # we can confirm the fold writes them in (rather than them just being
        # leftover from the base).
        base = AnkiMinerConfig()
        result = base
        for panel in panels:
            result = panel.contribute(result)

        # Step 4: compare every Save-path field.
        failures = []
        for field_name in sorted(_SAVE_PATH_FIELDS):
            expected = getattr(original, field_name)
            got = getattr(result, field_name)
            if expected != got:
                failures.append(f"  {field_name}: expected {expected!r}, got {got!r}")

        if failures:
            raise AssertionError("Round-trip broke for the following Save-path fields:\n" + "\n".join(failures))

    def test_anki_panel_load_and_contribute(self, tmp_path, qtbot):
        """AnkiSettingsPanel round-trip in isolation."""
        original = _non_default_save_config(tmp_path)
        panel = AnkiSettingsPanel()
        qtbot.addWidget(panel)

        panel.load_from_config(original)
        result = panel.contribute(AnkiMinerConfig())

        for field_name in (
            "anki_deck_name",
            "anki_note_type",
            "ankiconnect_url",
            "anki_tags",
            "anki_fields",
            "anki_word_field",
            "card_style_preset",
            "custom_card_css",
            "pitch_category_format",
        ):
            assert getattr(result, field_name) == getattr(original, field_name), field_name

    def test_media_panel_load_and_contribute(self, tmp_path, qtbot):
        """MediaSettingsPanel round-trip in isolation."""
        original = _non_default_save_config(tmp_path)
        panel = MediaSettingsPanel()
        qtbot.addWidget(panel)

        panel.load_from_config(original)
        result = panel.contribute(AnkiMinerConfig())

        for field_name in (
            "audio_format",
            "audio_bitrate",
            "audio_padding",
            "screenshot_offset",
            "max_parallel_workers",
            "screenshot_animated",
            "screenshot_animated_format",
            "screenshot_animated_clip_duration",
            "screenshot_animated_match_audio",
            "screenshot_animated_fps",
            "screenshot_animated_height",
            "screenshot_animated_quality",
        ):
            assert (
                getattr(result, field_name) == pytest.approx(getattr(original, field_name))
                if isinstance(getattr(original, field_name), float)
                else getattr(result, field_name) == getattr(original, field_name)
            ), field_name

    def test_filtering_panel_load_and_contribute(self, tmp_path, qtbot):
        """FilteringSettingsPanel round-trip in isolation."""
        original = _non_default_save_config(tmp_path)
        panel = FilteringSettingsPanel()
        qtbot.addWidget(panel)

        panel.load_from_config(original)
        result = panel.contribute(AnkiMinerConfig())

        for field_name in (
            "max_frequency_rank",
            "use_known_words_db",
            "excluded_decks",
            "excluded_wordsets",
            "blacklist_path",
            "use_blacklist",
            "whitelist_path",
            "use_whitelist",
            "subtitle_regex_filter",
            "subtitle_regex_replacement",
            "use_subtitle_regex_filter",
            "deduplicate_sentences",
            "exclude_hiragana_only_words",
            "exclude_katakana_only_words",
            "use_i_plus_one_filter",
            "use_sentence_length_filter",
            "max_sentence_duration_seconds",
            "max_sentence_chars",
            "bold_target_in_sentence",
        ):
            assert getattr(result, field_name) == getattr(original, field_name), field_name

    def test_youtube_panel_load_and_contribute(self, tmp_path, qtbot):
        """YouTubeSettingsPanel round-trip in isolation."""
        original = _non_default_save_config(tmp_path)
        panel = YouTubeSettingsPanel()
        qtbot.addWidget(panel)

        panel.load_from_config(original)
        result = panel.contribute(AnkiMinerConfig())

        for field_name in (
            "youtube_cookies_from_browser",
            "youtube_cookies_file",
            "youtube_max_duration_s",
            "youtube_playlist_max",
        ):
            assert getattr(result, field_name) == getattr(original, field_name), field_name

    def test_none_blacklist_whitelist_round_trips_as_none(self, qtbot):
        """None word-list paths survive load → contribute as None (T-11 guard)."""
        cfg = AnkiMinerConfig(blacklist_path=None, whitelist_path=None)
        panel = FilteringSettingsPanel()
        qtbot.addWidget(panel)

        panel.load_from_config(cfg)
        result = panel.contribute(AnkiMinerConfig())

        assert result.blacklist_path is None
        assert result.whitelist_path is None

    def test_none_youtube_cookies_file_round_trips_as_none(self, qtbot):
        """None cookies_file survives load → contribute as None."""
        cfg = AnkiMinerConfig(youtube_cookies_file=None)
        panel = YouTubeSettingsPanel()
        qtbot.addWidget(panel)

        panel.load_from_config(cfg)
        result = panel.contribute(AnkiMinerConfig())

        assert result.youtube_cookies_file is None
