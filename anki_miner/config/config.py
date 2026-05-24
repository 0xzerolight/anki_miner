"""Configuration classes for Anki Miner."""

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .paths import ANKI_MINER_HOME


@dataclass(frozen=True)
class ChainEntry:
    """One entry in the dictionary lookup chain.

    Indexed entries reference a folder under ~/.anki_miner/dicts/<dict_id>/.
    Jisho entries are the always-available online fallback; dict_id is None.
    """

    kind: Literal["indexed", "jisho"]
    dict_id: str | None = None
    enabled: bool = True


@dataclass(frozen=True)
class AnkiMinerConfig:
    """Immutable configuration for anki mining operations.

    All configuration is frozen (immutable) to ensure thread-safety
    and prevent accidental modifications during processing.
    """

    # Anki settings
    anki_deck_name: str = "Anki Miner"
    anki_note_type: str = "Lapis"
    anki_word_field: str = "Expression"
    anki_fields: dict[str, str] = field(
        default_factory=lambda: {
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
            "frequency": "",
        }
    )
    ankiconnect_url: str = "http://127.0.0.1:8765"
    anki_tags: str = "auto-mined"  # Whitespace-separated tags applied to every mined card; empty string means no tags

    # Media extraction settings
    audio_padding: float = 0.3  # Seconds to add before/after subtitle timing
    screenshot_offset: float = 1.0  # Seconds after subtitle start for screenshot
    media_temp_folder: Path = field(default_factory=lambda: Path(tempfile.gettempdir()) / "anki_miner_temp")
    # Audio extraction settings (Issue #18)
    audio_format: str = "mp3"  # "mp3" | "opus"
    audio_bitrate: int = 192  # kbps; applies to both mp3 and opus

    # Animated screenshot settings (opt-in; static JPEG remains default)
    screenshot_animated: bool = False
    screenshot_animated_format: str = "avif"  # "avif" | "webp"
    screenshot_animated_clip_duration: float = 2.0  # seconds; capped by word.duration
    screenshot_animated_match_audio: bool = (
        False  # If True, clip spans full audio range (subtitle + audio_padding on both sides), overriding clip_duration
    )
    screenshot_animated_fps: int = 20
    screenshot_animated_height: int = 720  # scale-to-height, aspect preserved
    screenshot_animated_quality: int = 30  # 0-100 user scale, mapped per codec
    subtitle_offset: float = 0.0  # Seconds to shift subtitles (+ later, - earlier)

    # Word filtering settings
    allowed_pos: list[str] = field(default_factory=lambda: ["名詞", "動詞", "形容詞", "副詞", "形状詞", "代名詞"])
    excluded_subtypes: list[str] = field(
        default_factory=lambda: [
            "非自立",
            "数詞",
            "接尾",
            "助動詞",
            "接頭",
            "固有名詞",
        ]
    )

    # Dictionary settings
    #
    # `dictionary_chain` is the runtime-authoritative list of providers in
    # priority order. `jmdict_path` is a still-live legacy field read by the
    # JMdict XML→SQLite setup flow (settings_tab.py and main_window.py) so
    # the UI knows where to find the user's XML and where to write the
    # indexed DB.
    dictionary_chain: tuple["ChainEntry", ...] = field(
        default_factory=lambda: (
            ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )
    jmdict_path: Path = field(default_factory=lambda: ANKI_MINER_HOME / "JMdict_e")
    dicts_root: Path = field(default_factory=lambda: ANKI_MINER_HOME / "dicts")
    jisho_api_url: str = "https://jisho.org/api/v1/search/words"
    jisho_delay: float = 0.5  # Seconds between API calls

    # Pitch accent settings
    pitch_accent_path: Path = field(default_factory=lambda: ANKI_MINER_HOME / "pitch_accent.csv")
    use_pitch_accent: bool = False
    # Output label format for the pitch_category Anki field.
    # "jp": 平板/頭高/中高/尾高/起伏 (legacy)
    # "romaji": heiban/atamadaka/nakadaka/odaka/kifuku (Yomitan/Lapis compatible)
    pitch_category_format: Literal["jp", "romaji"] = "jp"

    # Frequency settings
    frequency_list_path: Path = field(default_factory=lambda: ANKI_MINER_HOME / "frequency.csv")
    use_frequency_data: bool = False
    max_frequency_rank: int = 0  # 0 = no filtering; e.g. 10000 = only top 10k words

    # Known word database
    known_words_db_path: Path = field(default_factory=lambda: ANKI_MINER_HOME / "known_words.db")
    use_known_words_db: bool = False

    # Word list settings
    blacklist_path: Path | None = None
    whitelist_path: Path | None = None
    use_blacklist: bool = False
    use_whitelist: bool = False

    # Subtitle text filtering (Issue #8)
    # Python regex applied to each subtitle line after tag/HTML cleanup and
    # before tokenization. Matched text is replaced with subtitle_regex_replacement
    # (empty string = deletion). Both parse paths (raw entries for the viewer
    # and the mining path) honor the filter.
    subtitle_regex_filter: str = ""
    subtitle_regex_replacement: str = ""
    use_subtitle_regex_filter: bool = False

    # Card formatting
    # When True, wrap the mined target word in <b>...</b> inside the
    # Sentence and SentenceFurigana fields. Match is the exact MeCab span
    # of the mined surface (after compound-merge), not a string search,
    # so duplicated surfaces in the same sentence bold only the morpheme
    # that was actually mined. See Issue #20.
    bold_target_in_sentence: bool = False

    # Deduplication settings
    deduplicate_sentences: bool = True

    # i+1 sentence filtering. When True, only mine words that have at least
    # one example sentence containing exactly one unknown lemma.
    # Supersedes deduplicate_sentences when enabled.
    use_i_plus_one_filter: bool = False

    # Sentence length filter (Issue #33). Caps the example sentence by audio
    # duration and/or character count. ``use_sentence_length_filter`` is the
    # master toggle; each cap of ``0`` (or ``0.0``) means "no limit" for that
    # dimension when the toggle is on. Runs AFTER i+1 because filter_i_plus_one
    # swaps each word's sentence/duration to its chosen i+1 line — applying the
    # cap before that swap would be silently bypassed by the swap.
    use_sentence_length_filter: bool = False
    max_sentence_duration_seconds: float = 0.0  # 0 = no duration cap
    max_sentence_chars: int = 0  # 0 = no character cap

    # Cross-episode frequency settings
    use_cross_episode_priority: bool = False
    min_episode_appearances: int = 2  # Only mine words appearing in at least N episodes

    # History settings
    history_db_path: Path = field(default_factory=lambda: ANKI_MINER_HOME / "history.db")
    enable_history: bool = True

    # Update settings
    check_for_updates: bool = True
    skipped_update_version: str = ""
    last_known_version: str = ""

    # First-run flags (GUI-persisted; used to auto-create desktop shortcut once)
    first_run_shortcut_done: bool = False

    # Performance settings
    max_parallel_workers: int = 6  # Number of parallel ffmpeg processes

    # Analytics settings
    stats_db_path: Path = field(default_factory=lambda: ANKI_MINER_HOME / "stats.db")

    # --- YouTube ---
    youtube_max_duration_s: int = 7200
    youtube_max_height: int = 720
    youtube_cookies_from_browser: str | None = None
    youtube_ffmpeg_location: Path | None = None

    # Theme settings (UI state — persisted via gui_config.json).
    # `theme_favorites` is the curated list that drives the top-right combo;
    # the active `theme` does not need to be in favorites.
    theme: str = "light"
    theme_favorites: tuple[str, ...] = ("light", "dark")
    themes_root: Path = field(default_factory=lambda: ANKI_MINER_HOME / "themes")

    def __post_init__(self):
        """Convert string paths to Path objects if needed."""
        # Convert paths to Path objects (handles both str and Path inputs)
        if isinstance(self.media_temp_folder, str):
            object.__setattr__(self, "media_temp_folder", Path(self.media_temp_folder))
        if isinstance(self.jmdict_path, str):
            object.__setattr__(self, "jmdict_path", Path(self.jmdict_path))
        if isinstance(self.dicts_root, str):
            object.__setattr__(self, "dicts_root", Path(self.dicts_root))
        if isinstance(self.pitch_accent_path, str):
            object.__setattr__(self, "pitch_accent_path", Path(self.pitch_accent_path))
        if isinstance(self.frequency_list_path, str):
            object.__setattr__(self, "frequency_list_path", Path(self.frequency_list_path))
        if isinstance(self.known_words_db_path, str):
            object.__setattr__(self, "known_words_db_path", Path(self.known_words_db_path))
        if isinstance(self.blacklist_path, str):
            object.__setattr__(self, "blacklist_path", Path(self.blacklist_path) if self.blacklist_path else None)
        if isinstance(self.whitelist_path, str):
            object.__setattr__(self, "whitelist_path", Path(self.whitelist_path) if self.whitelist_path else None)
        if isinstance(self.stats_db_path, str):
            object.__setattr__(self, "stats_db_path", Path(self.stats_db_path))
        if isinstance(self.history_db_path, str):
            object.__setattr__(self, "history_db_path", Path(self.history_db_path))
        if isinstance(self.youtube_ffmpeg_location, str):
            object.__setattr__(
                self,
                "youtube_ffmpeg_location",
                Path(self.youtube_ffmpeg_location) if self.youtube_ffmpeg_location else None,
            )
        if isinstance(self.themes_root, str):
            object.__setattr__(self, "themes_root", Path(self.themes_root))
        # JSON round-trip yields a list for theme_favorites; coerce to tuple
        # so the frozen dataclass stays internally immutable.
        if isinstance(self.theme_favorites, list):
            object.__setattr__(self, "theme_favorites", tuple(self.theme_favorites))

        # Keep anki_word_field in sync with anki_fields["word"]
        word_field_from_mapping = self.anki_fields.get("word", "")
        if word_field_from_mapping and word_field_from_mapping != self.anki_word_field:
            object.__setattr__(self, "anki_word_field", word_field_from_mapping)
