"""Tests for config module."""

import types
from pathlib import Path

import pytest

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

    def test_alass_location_defaults_none(self):
        """alass_location defaults to None."""
        config = AnkiMinerConfig()
        assert config.alass_location is None

    def test_alass_location_coerced_from_string(self, temp_dir):
        """alass_location is coerced to Path when passed as str."""
        config = AnkiMinerConfig(alass_location=str(temp_dir / "alass"))
        assert isinstance(config.alass_location, Path)
        assert config.alass_location == temp_dir / "alass"

    def test_alass_location_empty_string_becomes_none(self):
        """An empty alass_location string normalizes to None."""
        config = AnkiMinerConfig(alass_location="")
        assert config.alass_location is None

    def test_alass_location_accepts_path(self, temp_dir):
        """alass_location accepts a Path object directly."""
        config = AnkiMinerConfig(alass_location=temp_dir / "alass")
        assert config.alass_location == temp_dir / "alass"

    def test_ytdlp_location_defaults_none(self):
        """ytdlp_location defaults to None."""
        config = AnkiMinerConfig()
        assert config.ytdlp_location is None

    def test_ytdlp_location_coerced_from_string(self, temp_dir):
        """ytdlp_location is coerced to Path when passed as str."""
        config = AnkiMinerConfig(ytdlp_location=str(temp_dir / "yt-dlp"))
        assert isinstance(config.ytdlp_location, Path)
        assert config.ytdlp_location == temp_dir / "yt-dlp"

    def test_ytdlp_location_empty_string_becomes_none(self):
        """An empty ytdlp_location string normalizes to None."""
        config = AnkiMinerConfig(ytdlp_location="")
        assert config.ytdlp_location is None

    def test_ytdlp_location_accepts_path(self, temp_dir):
        """ytdlp_location accepts a Path object directly."""
        config = AnkiMinerConfig(ytdlp_location=temp_dir / "yt-dlp")
        assert config.ytdlp_location == temp_dir / "yt-dlp"

    def test_auto_update_ytdlp_defaults_true(self):
        """auto_update_ytdlp defaults to True (background update on)."""
        config = AnkiMinerConfig()
        assert config.auto_update_ytdlp is True


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


def test_anki_fields_includes_conjugation_default():
    cfg = AnkiMinerConfig()
    # Deinflection-chain provenance field (3.2): opt-in, default "" = off.
    assert "conjugation" in cfg.anki_fields
    assert cfg.anki_fields["conjugation"] == ""


class TestExpressionAudioConfig:
    """Tests for expression audio config fields (Issue #73)."""

    def test_expression_audio_field_defaults_empty(self):
        """expression_audio Anki field defaults to "" — feature off by default
        (field-name presence is the on/off switch, no separate flag)."""
        cfg = AnkiMinerConfig()
        assert cfg.anki_fields.get("expression_audio") == ""

    def test_expression_audio_delay_defaults_0_2(self):
        """expression_audio_delay must default to 0.2 seconds."""
        cfg = AnkiMinerConfig()
        assert cfg.expression_audio_delay == 0.2

    def test_anki_fields_includes_expression_audio_default(self):
        """anki_fields must include 'expression_audio' key defaulting to empty string."""
        cfg = AnkiMinerConfig()
        assert "expression_audio" in cfg.anki_fields
        assert cfg.anki_fields["expression_audio"] == ""


def test_dictionary_chain_replace():
    from dataclasses import replace

    from anki_miner.config import AnkiMinerConfig, ChainEntry

    config = AnkiMinerConfig()
    new_chain = (ChainEntry(kind="jisho", dict_id=None, enabled=False),)
    updated = replace(config, dictionary_chain=new_chain)
    assert updated.dictionary_chain == new_chain
    # Original is unchanged
    assert len(config.dictionary_chain) == 2


def test_frequency_chain_default_empty():
    """frequency_chain defaults to an empty tuple (migration populates later)."""
    from anki_miner.config import AnkiMinerConfig

    config = AnkiMinerConfig()
    assert isinstance(config.frequency_chain, tuple)
    assert config.frequency_chain == ()


def test_freqs_root_default():
    """freqs_root defaults to ANKI_MINER_HOME / 'freqs'."""
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.config.paths import ANKI_MINER_HOME

    config = AnkiMinerConfig()
    assert config.freqs_root == ANKI_MINER_HOME / "freqs"
    assert isinstance(config.freqs_root, Path)


def test_freqs_root_str_coercion():
    """A str freqs_root is coerced to Path in __post_init__."""
    from anki_miner.config import AnkiMinerConfig

    config = AnkiMinerConfig(freqs_root="/tmp/some/freqs")  # type: ignore[arg-type]
    assert isinstance(config.freqs_root, Path)
    assert config.freqs_root == Path("/tmp/some/freqs")


def test_onnx_pack_root_str_coercion():
    """A str onnx_pack_root is coerced to Path in __post_init__ (like its siblings)."""
    from anki_miner.config import AnkiMinerConfig

    config = AnkiMinerConfig(onnx_pack_root="/tmp/some/onnx_pack")  # type: ignore[arg-type]
    assert isinstance(config.onnx_pack_root, Path)
    assert config.onnx_pack_root == Path("/tmp/some/onnx_pack")


def test_freq_entry_is_frozen():
    from dataclasses import FrozenInstanceError

    from anki_miner.config import FreqEntry

    entry = FreqEntry(source_id="jpdb")
    with pytest.raises(FrozenInstanceError):
        entry.source_id = "other"  # type: ignore[misc]


def test_freq_entry_enabled_defaults_true():
    from anki_miner.config import FreqEntry

    entry = FreqEntry(source_id="jpdb")
    assert entry.enabled is True


def test_frequency_chain_replace():
    from dataclasses import replace

    from anki_miner.config import AnkiMinerConfig, FreqEntry

    config = AnkiMinerConfig()
    new_chain = (FreqEntry(source_id="jpdb"), FreqEntry(source_id="bccwj", enabled=False))
    updated = replace(config, frequency_chain=new_chain)
    assert updated.frequency_chain == new_chain
    # Original is unchanged.
    assert config.frequency_chain == ()


def test_sentence_length_filter_defaults():
    """Sentence-length filter fields default to disabled / 0 (Issue #33)."""
    from anki_miner.config import AnkiMinerConfig

    cfg = AnkiMinerConfig()
    assert cfg.use_sentence_length_filter is False
    assert cfg.max_sentence_duration_seconds == 0.0
    assert cfg.max_sentence_chars == 0


class TestAudioSourceEntry:
    """Tests for the AudioSourceEntry frozen dataclass."""

    def test_frozen(self):
        """AudioSourceEntry must be immutable."""
        from dataclasses import FrozenInstanceError

        import pytest

        from anki_miner.config import AudioSourceEntry

        entry = AudioSourceEntry(kind="jpod101")
        with pytest.raises(FrozenInstanceError):
            entry.kind = "pack"  # type: ignore[misc]

    def test_defaults(self):
        """pack_id defaults to None; enabled defaults to True."""
        from anki_miner.config import AudioSourceEntry

        entry = AudioSourceEntry(kind="jpod101")
        assert entry.pack_id is None
        assert entry.enabled is True

    def test_pack_entry(self):
        """A pack entry stores pack_id and enabled properly."""
        from anki_miner.config import AudioSourceEntry

        entry = AudioSourceEntry(kind="pack", pack_id="nhk16", enabled=False)
        assert entry.kind == "pack"
        assert entry.pack_id == "nhk16"
        assert entry.enabled is False


class TestExpressionAudioChainConfig:
    """Tests for expression_audio_chain and audio_packs_root config fields."""

    def test_expression_audio_chain_default(self):
        """Default chain is jpod101 (enabled) then googletts (disabled)."""
        from anki_miner.config import AnkiMinerConfig, AudioSourceEntry

        cfg = AnkiMinerConfig()
        assert cfg.expression_audio_chain == (
            AudioSourceEntry(kind="jpod101"),
            AudioSourceEntry(kind="googletts", enabled=False),
        )

    def test_audio_packs_root_default_ends_in_audio_packs(self):
        """audio_packs_root must default to a Path ending in 'audio_packs'."""
        cfg = AnkiMinerConfig()
        assert isinstance(cfg.audio_packs_root, Path)
        assert cfg.audio_packs_root.name == "audio_packs"

    def test_audio_packs_root_coerced_from_string(self, tmp_path):
        """audio_packs_root must be coerced to Path when passed as str."""
        cfg = AnkiMinerConfig(audio_packs_root=str(tmp_path / "packs"))
        assert isinstance(cfg.audio_packs_root, Path)
        assert cfg.audio_packs_root == tmp_path / "packs"

    def test_expression_audio_chain_replace(self):
        """replace() must allow swapping the audio chain."""
        from dataclasses import replace

        from anki_miner.config import AnkiMinerConfig, AudioSourceEntry

        cfg = AnkiMinerConfig()
        new_chain = (
            AudioSourceEntry(kind="pack", pack_id="nhk16"),
            AudioSourceEntry(kind="jpod101"),
        )
        updated = replace(cfg, expression_audio_chain=new_chain)
        assert updated.expression_audio_chain == new_chain
        # Original unchanged (jpod101 + disabled googletts)
        assert len(cfg.expression_audio_chain) == 2


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


class TestUiZoom:
    """Tests for the ui_zoom config field (whole-UI zoom / QT_SCALE_FACTOR)."""

    def test_default_is_1_0(self):
        """ui_zoom must default to 1.0 (absent key in old configs → 1.0)."""
        cfg = AnkiMinerConfig()
        assert cfg.ui_zoom == 1.0

    def test_below_min_clamps_to_0_5(self):
        cfg = AnkiMinerConfig(ui_zoom=0.1)
        assert cfg.ui_zoom == 0.5

    def test_above_max_clamps_to_2_0(self):
        cfg = AnkiMinerConfig(ui_zoom=5.0)
        assert cfg.ui_zoom == 2.0

    def test_in_range_value_unchanged(self):
        cfg = AnkiMinerConfig(ui_zoom=1.5)
        assert cfg.ui_zoom == 1.5

    def test_boundaries_unchanged(self):
        assert AnkiMinerConfig(ui_zoom=0.5).ui_zoom == 0.5
        assert AnkiMinerConfig(ui_zoom=2.0).ui_zoom == 2.0


class TestFrozenConfigImmutability:
    """OVH-018: verify the three previously-mutable fields are now immutable."""

    def test_anki_fields_is_mapping_proxy(self):
        """anki_fields must be stored as a MappingProxyType, not a plain dict."""
        cfg = AnkiMinerConfig()
        assert isinstance(cfg.anki_fields, types.MappingProxyType)

    def test_anki_fields_mutation_raises(self):
        """Mutating anki_fields in place must raise TypeError."""
        cfg = AnkiMinerConfig()
        with pytest.raises(TypeError):
            cfg.anki_fields["word"] = "Hacked"  # type: ignore[index]

    def test_anki_fields_read_access_unchanged(self):
        """Read operations on anki_fields must still work as before."""
        cfg = AnkiMinerConfig()
        assert cfg.anki_fields["word"] == "Expression"
        assert cfg.anki_fields.get("sentence") == "Sentence"
        assert "word" in cfg.anki_fields
        assert list(cfg.anki_fields.values())  # iteration works

    def test_allowed_pos_is_tuple(self):
        """allowed_pos must be stored as a tuple."""
        cfg = AnkiMinerConfig()
        assert isinstance(cfg.allowed_pos, tuple)

    def test_excluded_subtypes_is_tuple(self):
        """excluded_subtypes must be stored as a tuple."""
        cfg = AnkiMinerConfig()
        assert isinstance(cfg.excluded_subtypes, tuple)

    def test_allowed_pos_json_list_coerced_to_tuple(self):
        """A list passed as allowed_pos (JSON round-trip) is coerced to tuple."""
        cfg = AnkiMinerConfig(allowed_pos=["名詞", "動詞"])
        assert isinstance(cfg.allowed_pos, tuple)
        assert cfg.allowed_pos == ("名詞", "動詞")

    def test_excluded_subtypes_json_list_coerced_to_tuple(self):
        """A list passed as excluded_subtypes (JSON round-trip) is coerced to tuple."""
        cfg = AnkiMinerConfig(excluded_subtypes=["非自立", "数詞"])
        assert isinstance(cfg.excluded_subtypes, tuple)
        assert cfg.excluded_subtypes == ("非自立", "数詞")

    def test_anki_fields_dict_input_wrapped_as_proxy(self):
        """A plain dict passed as anki_fields must be wrapped in MappingProxyType."""
        fields_dict = {"word": "VocabExpr", "sentence": "Sent"}
        cfg = AnkiMinerConfig(anki_fields=fields_dict)
        assert isinstance(cfg.anki_fields, types.MappingProxyType)
        assert cfg.anki_fields["word"] == "VocabExpr"


class TestFrequencySortField:
    """The frequency_sort optional anki_fields key (Multiple Additive Frequency Sources)."""

    def test_default_anki_fields_contains_frequency_sort(self):
        """The default anki_fields mapping carries an unmapped frequency_sort key."""
        cfg = AnkiMinerConfig()
        assert "frequency_sort" in cfg.anki_fields
        assert cfg.anki_fields["frequency_sort"] == ""

    def test_frequency_sort_not_required(self):
        """frequency_sort is optional — it must not be a required field key."""
        from anki_miner.services.anki_note_builder import REQUIRED_FIELD_KEYS

        assert "frequency_sort" not in REQUIRED_FIELD_KEYS
