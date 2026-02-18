"""Configuration classes for Anki Miner."""

import tempfile
from dataclasses import dataclass, field
from pathlib import Path


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
            "picture": "Picture",
            "audio": "SentenceAudio",
            "expression_furigana": "ExpressionFurigana",
            "sentence_furigana": "SentenceFurigana",
            "pitch_accent": "",
            "frequency_rank": "",
        }
    )
    ankiconnect_url: str = "http://127.0.0.1:8765"

    # Media extraction settings
    audio_padding: float = 0.3  # Seconds to add before/after subtitle timing
    screenshot_offset: float = 1.0  # Seconds after subtitle start for screenshot
    media_temp_folder: Path = field(
        default_factory=lambda: Path(tempfile.gettempdir()) / "anki_miner_temp"
    )
    subtitle_offset: float = 0.0  # Seconds to shift subtitles (+ later, - earlier)

    # Word filtering settings
    min_word_length: int = 2
    allowed_pos: list[str] = field(
        default_factory=lambda: ["名詞", "動詞", "形容詞", "副詞", "形状詞"]
    )
    excluded_subtypes: list[str] = field(
        default_factory=lambda: [
            "非自立",
            "代名詞",
            "数詞",
            "接尾",
            "助動詞",
            "接頭",
            "固有名詞",
        ]
    )

    # Dictionary settings
    jmdict_path: Path = field(default_factory=lambda: Path.home() / ".anki_miner" / "JMdict_e")
    use_offline_dict: bool = True
    jisho_api_url: str = "https://jisho.org/api/v1/search/words"
    jisho_delay: float = 0.5  # Seconds between API calls

    # Pitch accent settings
    pitch_accent_path: Path = field(
        default_factory=lambda: Path.home() / ".anki_miner" / "pitch_accent.csv"
    )
    use_pitch_accent: bool = False

    # Frequency settings
    frequency_list_path: Path = field(
        default_factory=lambda: Path.home() / ".anki_miner" / "frequency.csv"
    )
    use_frequency_data: bool = False
    max_frequency_rank: int = 0  # 0 = no filtering; e.g. 10000 = only top 10k words

    # Known word database
    known_words_db_path: Path = field(
        default_factory=lambda: Path.home() / ".anki_miner" / "known_words.db"
    )
    use_known_words_db: bool = False

    # Word list settings
    blacklist_path: Path | None = None
    whitelist_path: Path | None = None
    use_blacklist: bool = False
    use_whitelist: bool = False

    # Deduplication settings
    deduplicate_sentences: bool = True

    # Cross-episode frequency settings
    use_cross_episode_priority: bool = False
    min_episode_appearances: int = 2  # Only mine words appearing in at least N episodes

    # Performance settings
    max_parallel_workers: int = 6  # Number of parallel ffmpeg processes

    # Analytics settings
    stats_db_path: Path = field(default_factory=lambda: Path.home() / ".anki_miner" / "stats.db")

    def __post_init__(self):
        """Convert string paths to Path objects if needed."""
        # Convert paths to Path objects (handles both str and Path inputs)
        if isinstance(self.media_temp_folder, str):
            object.__setattr__(self, "media_temp_folder", Path(self.media_temp_folder))
        if isinstance(self.jmdict_path, str):
            object.__setattr__(self, "jmdict_path", Path(self.jmdict_path))
        if isinstance(self.pitch_accent_path, str):
            object.__setattr__(self, "pitch_accent_path", Path(self.pitch_accent_path))
        if isinstance(self.frequency_list_path, str):
            object.__setattr__(self, "frequency_list_path", Path(self.frequency_list_path))
        if isinstance(self.known_words_db_path, str):
            object.__setattr__(self, "known_words_db_path", Path(self.known_words_db_path))
        if isinstance(self.blacklist_path, str):
            object.__setattr__(
                self, "blacklist_path", Path(self.blacklist_path) if self.blacklist_path else None
            )
        if isinstance(self.whitelist_path, str):
            object.__setattr__(
                self, "whitelist_path", Path(self.whitelist_path) if self.whitelist_path else None
            )
        if isinstance(self.stats_db_path, str):
            object.__setattr__(self, "stats_db_path", Path(self.stats_db_path))
