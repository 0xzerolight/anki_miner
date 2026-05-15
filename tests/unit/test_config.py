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
        assert config.youtube_prefer_manual_subs is True
        assert config.youtube_warn_on_auto_captions is True
        assert config.youtube_max_duration_s == 7200
        assert config.youtube_max_height == 720
        assert config.youtube_cookies_from_browser is None
        assert config.youtube_ffmpeg_location is None

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
