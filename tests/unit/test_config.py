"""Tests for config module."""

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
