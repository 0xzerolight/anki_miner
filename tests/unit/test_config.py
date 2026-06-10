"""Tests for config module."""

from pathlib import Path

from anki_miner.config import AnkiMinerConfig


class TestAnkiWordFieldSync:
    """Tests for anki_word_field auto-sync from anki_fields['word']."""

    def test_syncs_word_field_from_anki_fields(self, temp_dir):
        """anki_word_field should auto-sync to anki_fields['word'] when they differ."""
        config = AnkiMinerConfig(
            anki_word_field="Expression",  # stale default
            anki_fields={
                "word": "Vocabulary",  # user changed this
                "sentence": "Sentence",
                "definition": "Definition",
                "picture": "Picture",
                "audio": "Audio",
                "expression_furigana": "EF",
                "sentence_furigana": "SF",
            },
            media_temp_folder=temp_dir / "temp",
            jmdict_path=temp_dir / "dict",
        )
        assert config.anki_word_field == "Vocabulary"

    def test_no_sync_when_fields_already_match(self, temp_dir):
        """anki_word_field should stay unchanged when it matches anki_fields['word']."""
        config = AnkiMinerConfig(
            anki_word_field="Expression",
            anki_fields={
                "word": "Expression",
                "sentence": "Sentence",
                "definition": "Definition",
                "picture": "Picture",
                "audio": "Audio",
                "expression_furigana": "EF",
                "sentence_furigana": "SF",
            },
            media_temp_folder=temp_dir / "temp",
            jmdict_path=temp_dir / "dict",
        )
        assert config.anki_word_field == "Expression"

    def test_no_sync_when_word_key_missing(self, temp_dir):
        """anki_word_field should keep its value if anki_fields has no 'word' key."""
        config = AnkiMinerConfig(
            anki_word_field="Expression",
            anki_fields={
                "sentence": "Sentence",
                "definition": "Definition",
                "picture": "Picture",
                "audio": "Audio",
                "expression_furigana": "EF",
                "sentence_furigana": "SF",
            },
            media_temp_folder=temp_dir / "temp",
            jmdict_path=temp_dir / "dict",
        )
        assert config.anki_word_field == "Expression"

    def test_no_sync_when_word_value_empty(self, temp_dir):
        """anki_word_field should keep its value if anki_fields['word'] is empty."""
        config = AnkiMinerConfig(
            anki_word_field="Expression",
            anki_fields={
                "word": "",
                "sentence": "Sentence",
                "definition": "Definition",
                "picture": "Picture",
                "audio": "Audio",
                "expression_furigana": "EF",
                "sentence_furigana": "SF",
            },
            media_temp_folder=temp_dir / "temp",
            jmdict_path=temp_dir / "dict",
        )
        assert config.anki_word_field == "Expression"


class TestIPlusOneFilter:
    """Tests for the i+1 sentence filter flag."""

    def test_use_i_plus_one_filter_defaults_false(self):
        """The i+1 filter must be off by default — zero overhead for the default path."""
        config = AnkiMinerConfig()
        assert config.use_i_plus_one_filter is False


class TestYouTubeConfig:
    """Tests for the YouTube-related config fields."""

    def test_defaults(self):
        """New YouTube fields should default to the documented values."""
        config = AnkiMinerConfig()
        assert config.youtube_max_duration_s == 7200
        assert config.youtube_max_height == 720
        assert config.youtube_cookies_from_browser is None
        assert config.youtube_cookies_file is None
        assert config.youtube_ffmpeg_location is None
        assert config.youtube_playlist_max == 100

    def test_cookies_file_coerced_from_string(self, temp_dir):
        """youtube_cookies_file should be coerced to Path when passed as str."""
        cookies_path = str(temp_dir / "cookies.txt")
        config = AnkiMinerConfig(youtube_cookies_file=cookies_path)
        assert isinstance(config.youtube_cookies_file, Path)
        assert config.youtube_cookies_file == Path(cookies_path)

    def test_cookies_file_empty_string_becomes_none(self):
        """An empty youtube_cookies_file string should normalize to None."""
        config = AnkiMinerConfig(youtube_cookies_file="")
        assert config.youtube_cookies_file is None

    def test_cookies_file_stays_none_when_unset(self):
        """youtube_cookies_file should remain None when not provided."""
        config = AnkiMinerConfig()
        assert config.youtube_cookies_file is None

    def test_cookies_file_accepts_path(self, temp_dir):
        """youtube_cookies_file should accept a Path object directly."""
        cookies_path = temp_dir / "cookies.txt"
        config = AnkiMinerConfig(youtube_cookies_file=cookies_path)
        assert isinstance(config.youtube_cookies_file, Path)
        assert config.youtube_cookies_file == cookies_path

    def test_ffmpeg_location_coerced_from_string(self, temp_dir):
        """youtube_ffmpeg_location should be coerced to Path when passed as str."""
        ffmpeg_path = str(temp_dir / "ffmpeg")
        config = AnkiMinerConfig(youtube_ffmpeg_location=ffmpeg_path)
        assert isinstance(config.youtube_ffmpeg_location, Path)
        assert config.youtube_ffmpeg_location == Path(ffmpeg_path)

    def test_ffmpeg_location_stays_none_when_unset(self):
        """youtube_ffmpeg_location should remain None when not provided."""
        config = AnkiMinerConfig()
        assert config.youtube_ffmpeg_location is None

    def test_ffmpeg_location_accepts_path(self, temp_dir):
        """youtube_ffmpeg_location should accept a Path object directly."""
        ffmpeg_path = temp_dir / "ffmpeg"
        config = AnkiMinerConfig(youtube_ffmpeg_location=ffmpeg_path)
        assert isinstance(config.youtube_ffmpeg_location, Path)
        assert config.youtube_ffmpeg_location == ffmpeg_path

    def test_bundled_tooling_locations_default_none(self):
        """ffmpeg_location/ffprobe_location default to None."""
        config = AnkiMinerConfig()
        assert config.ffmpeg_location is None
        assert config.ffprobe_location is None

    def test_ffmpeg_ffprobe_location_coerced_from_string(self, temp_dir):
        """ffmpeg_location/ffprobe_location are coerced to Path when passed as str."""
        config = AnkiMinerConfig(
            ffmpeg_location=str(temp_dir / "ffmpeg"),
            ffprobe_location=str(temp_dir / "ffprobe"),
        )
        assert isinstance(config.ffmpeg_location, Path)
        assert config.ffmpeg_location == temp_dir / "ffmpeg"
        assert isinstance(config.ffprobe_location, Path)
        assert config.ffprobe_location == temp_dir / "ffprobe"

    def test_ffmpeg_ffprobe_location_accept_path(self, temp_dir):
        """ffmpeg_location/ffprobe_location accept Path objects directly."""
        config = AnkiMinerConfig(
            ffmpeg_location=temp_dir / "ffmpeg",
            ffprobe_location=temp_dir / "ffprobe",
        )
        assert config.ffmpeg_location == temp_dir / "ffmpeg"
        assert config.ffprobe_location == temp_dir / "ffprobe"


def test_dictionary_chain_default():
    from anki_miner.config import AnkiMinerConfig, ChainEntry

    config = AnkiMinerConfig()
    chain = config.dictionary_chain
    assert isinstance(chain, tuple)
    assert chain == (
        ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=True),
        ChainEntry(kind="jisho", dict_id=None, enabled=False),
    )


def test_chain_entry_is_frozen():
    from dataclasses import FrozenInstanceError

    import pytest

    from anki_miner.config import ChainEntry

    entry = ChainEntry(kind="indexed", dict_id="jmdict-english")
    with pytest.raises(FrozenInstanceError):
        entry.dict_id = "other"  # type: ignore[misc]


def test_anki_fields_includes_glossary_default():
    cfg = AnkiMinerConfig()
    # Empty string default = "do not write Glossary field unless user maps it".
    assert "glossary" in cfg.anki_fields
    assert cfg.anki_fields["glossary"] == ""


def test_anki_fields_includes_source_default():
    cfg = AnkiMinerConfig()
    # Empty string default = opt-in: "source" is only written once the user
    # maps it to a real Anki field name (Issue #69).
    assert "source" in cfg.anki_fields
    assert cfg.anki_fields["source"] == ""


def test_dictionary_chain_replace():
    from dataclasses import replace

    from anki_miner.config import AnkiMinerConfig, ChainEntry

    config = AnkiMinerConfig()
    new_chain = (ChainEntry(kind="jisho", dict_id=None, enabled=False),)
    updated = replace(config, dictionary_chain=new_chain)
    assert updated.dictionary_chain == new_chain
    # Original is unchanged
    assert len(config.dictionary_chain) == 2


def test_sentence_length_filter_defaults():
    """Sentence-length filter fields default to disabled / 0 (Issue #33)."""
    from anki_miner.config import AnkiMinerConfig

    cfg = AnkiMinerConfig()
    assert cfg.use_sentence_length_filter is False
    assert cfg.max_sentence_duration_seconds == 0.0
    assert cfg.max_sentence_chars == 0


class TestUiFontScale:
    """Tests for the ui_font_scale config field (Issue #63)."""

    def test_default_is_1_0(self):
        """ui_font_scale must default to 1.0."""
        cfg = AnkiMinerConfig()
        assert cfg.ui_font_scale == 1.0

    def test_below_min_clamps_to_0_5(self):
        """Values below 0.5 must be clamped to 0.5."""
        cfg = AnkiMinerConfig(ui_font_scale=0.3)
        assert cfg.ui_font_scale == 0.5

    def test_above_max_clamps_to_2_0(self):
        """Values above 2.0 must be clamped to 2.0."""
        cfg = AnkiMinerConfig(ui_font_scale=3.0)
        assert cfg.ui_font_scale == 2.0

    def test_in_range_value_unchanged(self):
        """A value within [0.5, 2.0] must be stored as-is."""
        cfg = AnkiMinerConfig(ui_font_scale=1.5)
        assert cfg.ui_font_scale == 1.5

    def test_sub_one_value_unchanged(self):
        """A value between 0.5 and 1.0 (e.g. 0.75) must be stored as-is."""
        cfg = AnkiMinerConfig(ui_font_scale=0.75)
        assert cfg.ui_font_scale == 0.75

    def test_min_boundary_unchanged(self):
        """Exactly 0.5 must not be altered."""
        cfg = AnkiMinerConfig(ui_font_scale=0.5)
        assert cfg.ui_font_scale == 0.5

    def test_max_boundary_unchanged(self):
        """Exactly 2.0 must not be altered."""
        cfg = AnkiMinerConfig(ui_font_scale=2.0)
        assert cfg.ui_font_scale == 2.0
