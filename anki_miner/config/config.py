"""Configuration classes for Anki Miner."""

import tempfile
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping

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
class AudioSourceEntry:
    """One entry in the expression audio source chain.

    Pack entries reference a local audio pack under
    ~/.anki_miner/audio_packs/<pack_id>/.
    JPod101 entries are the always-available online fallback; pack_id is None.
    GoogleTTS entries are a synthetic Google Translate TTS online fallback;
    pack_id is None (like jpod101).
    """

    kind: Literal["pack", "jpod101", "googletts"]
    pack_id: str | None = None
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
    anki_fields: Mapping[str, str] = field(
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
            "source": "",
            "expression_audio": "",
        }
    )
    ankiconnect_url: str = "http://127.0.0.1:8765"
    anki_tags: str = "auto-mined"  # Whitespace-separated tags applied to every mined card; empty string means no tags
    # Deck names excluded from known-words detection (Issue #38). Notes in these
    # decks (and their subdecks) are dropped from the findNotes query, so their
    # words are NOT treated as already-known. Empty tuple = scan the whole collection.
    excluded_decks: tuple[str, ...] = field(default_factory=tuple)

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
    allowed_pos: tuple[str, ...] = field(default_factory=lambda: ("名詞", "動詞", "形容詞", "副詞", "形状詞", "代名詞"))
    excluded_subtypes: tuple[str, ...] = field(
        default_factory=lambda: (
            "非自立",
            "数詞",
            "接尾",
            "助動詞",
            "接頭",
            "固有名詞",
        )
    )
    # Enabled name-wordset IDs (Issue #59). Each ID maps to a bundled
    # plain-text proper-noun list under resources/wordsets/<id>.txt.
    # Words on any enabled set are dropped from mining unless whitelisted.
    excluded_wordsets: tuple[str, ...] = field(default_factory=tuple)

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
            ChainEntry(kind="jisho", dict_id=None, enabled=False),
        )
    )
    jmdict_path: Path = field(default_factory=lambda: ANKI_MINER_HOME / "JMdict_e")
    dicts_root: Path = field(default_factory=lambda: ANKI_MINER_HOME / "dicts")
    jisho_api_url: str = "https://jisho.org/api/v1/search/words"
    jisho_delay: float = 0.5  # Seconds between API calls. Jisho rate-limits; do NOT remove or reduce.

    # Expression audio settings (Issue #73). Fetches word pronunciation audio
    # from an external endpoint and writes it to the expression_audio Anki field.
    # Activation mirrors other optional fields (frequency, pitch): the feature
    # is on iff anki_fields["expression_audio"] is non-empty. Off by default
    # because that field defaults to "". expression_audio_delay mirrors jisho_delay.
    expression_audio_delay: float = 0.2  # Seconds between audio fetch requests.
    # Ordered list of audio sources tried in priority order.
    # The disabled googletts entry is present-but-off so the Settings UI can
    # list it; disabled => skipped in the factory => byte-for-byte pre-feature
    # behaviour (jpod101-only) is preserved exactly.
    expression_audio_chain: tuple["AudioSourceEntry", ...] = field(
        default_factory=lambda: (
            AudioSourceEntry(kind="jpod101"),
            AudioSourceEntry(kind="googletts", enabled=False),
        )
    )
    audio_packs_root: Path = field(default_factory=lambda: ANKI_MINER_HOME / "audio_packs")

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
    # When True, the known-words subtraction in Phase 2 is skipped so ALL
    # mineable words are mined regardless of Anki collection state. Used by
    # the Deck Builder's "include everything" mode. Default False preserves
    # the standard filter-against-known-vocab behaviour.
    include_known_words: bool = False
    # Deck Builder "complete deck" mode. When True, the per-episode reduction
    # filters (frequency rank, word lists, sentence dedup, cross-episode,
    # i+1, sentence length) are skipped so the build matches the corpus
    # preview exactly. Known-words subtraction is unaffected (see
    # include_known_words). Default False preserves normal mining.
    bypass_optional_filters: bool = False
    # When True, notes are posted to AnkiConnect with
    # options={"allowDuplicate": True, "duplicateScope": "deck"} so words
    # already present elsewhere in the collection are still carded. Used by
    # the Deck Builder. Default False preserves the standard dedup behaviour.
    allow_duplicate_cards: bool = False

    # Script-type filters (Issue #57). When set, words whose card form
    # (mined_form) is written entirely in one kana script are dropped before
    # card creation. Useful for a kanji-focused deck / discarding katakana
    # loanwords. Both default False (no behaviour change). Gated by
    # bypass_optional_filters like the other optional reduction filters.
    exclude_hiragana_only_words: bool = False
    exclude_katakana_only_words: bool = False

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

    # Card styling (Issue #44). anki_miner emits definition HTML with its own
    # class scheme (`.yomitan-glossary`, `gloss-sc-*`, `data-sc-*`), but ships no
    # CSS — the look depends on the note type's card-template CSS. These fields
    # back the Settings → Card Styling section, which auto-syncs a managed CSS
    # block into the configured note type via AnkiConnect `updateModelStyling`
    # whenever Settings are saved (no separate Apply/Remove buttons). The dropdown
    # is the *desired* state; a status line reports what's actually live in Anki.
    # `card_style_preset` is a preset id (one of the `card_style_presets.PRESETS`
    # ids — off / default / yomitan-classic / minimal / none); `"off"` strips the
    # managed block, `"none"` writes a block with only `custom_card_css`. Default
    # is `"off"` so a fresh install never touches a note type without an explicit
    # choice. `custom_card_css` (Yomitan/Jitendex snippets work verbatim) is
    # appended after the preset. Distinct from the app-UI `theme` fields below.
    #
    # `card_style_migrated` gates the one-time, surprise-free reseed: on the first
    # AnkiConnect-reachable run it reads the note type's managed block and sets the
    # dropdown to match reality (block's preset, or Off when absent), discarding
    # any stale pre-auto-sync selection. False until that reseed completes.
    card_style_preset: str = "off"
    custom_card_css: str = ""
    card_style_migrated: bool = False

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
    # Set once the first-run recommended-resources setup has been offered (so the
    # Welcome dialog never re-fires). Persisted automatically; absent in old
    # configs defaults to False.
    first_run_setup_done: bool = False

    # Performance settings
    max_parallel_workers: int = 6  # Number of parallel ffmpeg processes

    # Analytics settings
    stats_db_path: Path = field(default_factory=lambda: ANKI_MINER_HOME / "stats.db")

    # Logging
    log_path: Path = field(default_factory=lambda: ANKI_MINER_HOME / "anki_miner.log")

    # --- YouTube ---
    youtube_max_duration_s: int = 7200
    youtube_max_height: int = 720
    youtube_playlist_max: int = 100
    youtube_cookies_from_browser: str | None = None
    youtube_cookies_file: Path | None = None
    youtube_ffmpeg_location: Path | None = None
    # Optional explicit override for the yt-dlp executable. When unset,
    # anki_miner.utils.ytdlp_resolver falls back to the app-managed downloaded
    # copy (~/.anki_miner/bin/), a bundled binary, or the bare literal on PATH.
    ytdlp_location: Path | None = None
    # When True, the GUI runs a throttled background yt-dlp self-update on
    # startup (auto-download to ~/.anki_miner/bin/, kept current). Independent of
    # check_for_updates (the app updater).
    auto_update_ytdlp: bool = True

    # --- Bundled media tooling ---
    # Optional explicit overrides for the ffmpeg/ffprobe executables. When unset,
    # anki_miner.utils.ffmpeg_resolver falls back to a bundled binary (frozen
    # distributable) or the bare literal on PATH.
    ffmpeg_location: Path | None = None
    ffprobe_location: Path | None = None
    # Optional explicit override for the alass executable. When unset,
    # subtitle retiming falls back to alass on PATH.
    alass_location: Path | None = None

    # ASR (Automatic Speech Recognition) settings. Used by the Local Subtitle
    # Creation feature (offline transcription via faster-whisper). Requires
    # the optional `[asr]` extra: pip install "anki-miner[asr]".
    # `asr_model` selects the faster-whisper model size. Unknown values are
    # silently reset to the default in __post_init__.
    # `asr_models_root` is the directory where downloaded model weights are
    # stored; derived from ANKI_MINER_HOME (never user-configurable directly).
    asr_model: str = "large-v3"
    asr_models_root: Path = field(default_factory=lambda: ANKI_MINER_HOME / "asr_models")

    # Theme settings (UI state — persisted via gui_config.json).
    # `theme_favorites` is the curated list that drives the top-right combo;
    # the active `theme` does not need to be in favorites.
    theme: str = "light"
    theme_favorites: tuple[str, ...] = ("light", "dark")
    themes_root: Path = field(default_factory=lambda: ANKI_MINER_HOME / "themes")
    # Global UI font scale factor. Applied to all QSS ${font-size-*} variables.
    # Clamped to [0.5, 2.0] in __post_init__; values outside the range are silently clamped.
    ui_font_scale: float = 1.0
    # UI language code (BCP-47-ish short code, e.g. "en", "fr", "ru"). "en" is
    # the source language: no translator is installed for it. Persisted via
    # gui_config.json; applied at startup (restart-to-apply). Discussion #76.
    ui_language: str = "en"

    def __post_init__(self):
        """Convert string paths to Path objects if needed.

        This is a frozen dataclass (frozen=True) for thread safety: config is
        shared across worker threads and must never be mutated in place (use
        ``dataclasses.replace()`` instead). Because the instance is frozen,
        normal attribute assignment raises, so coercion here goes through
        ``object.__setattr__``. That is intentional, not a workaround to be
        "cleaned up" — it is the supported way to normalise fields during
        __post_init__ on a frozen dataclass.
        """
        # Convert paths to Path objects (handles both str and Path inputs)
        if isinstance(self.media_temp_folder, str):
            object.__setattr__(self, "media_temp_folder", Path(self.media_temp_folder))
        if isinstance(self.jmdict_path, str):
            object.__setattr__(self, "jmdict_path", Path(self.jmdict_path))
        if isinstance(self.dicts_root, str):
            object.__setattr__(self, "dicts_root", Path(self.dicts_root))
        if isinstance(self.audio_packs_root, str):
            object.__setattr__(self, "audio_packs_root", Path(self.audio_packs_root))
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
        if isinstance(self.log_path, str):
            object.__setattr__(self, "log_path", Path(self.log_path))
        if isinstance(self.youtube_cookies_file, str):
            object.__setattr__(
                self,
                "youtube_cookies_file",
                Path(self.youtube_cookies_file) if self.youtube_cookies_file else None,
            )
        if isinstance(self.youtube_ffmpeg_location, str):
            object.__setattr__(
                self,
                "youtube_ffmpeg_location",
                Path(self.youtube_ffmpeg_location) if self.youtube_ffmpeg_location else None,
            )
        if isinstance(self.ffmpeg_location, str):
            object.__setattr__(
                self,
                "ffmpeg_location",
                Path(self.ffmpeg_location) if self.ffmpeg_location else None,
            )
        if isinstance(self.ffprobe_location, str):
            object.__setattr__(
                self,
                "ffprobe_location",
                Path(self.ffprobe_location) if self.ffprobe_location else None,
            )
        if isinstance(self.alass_location, str):
            object.__setattr__(
                self,
                "alass_location",
                Path(self.alass_location) if self.alass_location else None,
            )
        if isinstance(self.ytdlp_location, str):
            object.__setattr__(
                self,
                "ytdlp_location",
                Path(self.ytdlp_location) if self.ytdlp_location else None,
            )
        if isinstance(self.themes_root, str):
            object.__setattr__(self, "themes_root", Path(self.themes_root))
        if isinstance(self.asr_models_root, str):
            object.__setattr__(self, "asr_models_root", Path(self.asr_models_root))
        # JSON round-trip yields a list for theme_favorites; coerce to tuple
        # so the frozen dataclass stays internally immutable.
        if isinstance(self.theme_favorites, list):
            object.__setattr__(self, "theme_favorites", tuple(self.theme_favorites))
        # JSON round-trip yields a list for excluded_decks; coerce to tuple.
        if isinstance(self.excluded_decks, list):
            object.__setattr__(self, "excluded_decks", tuple(self.excluded_decks))
        # JSON round-trip yields a list for excluded_wordsets; coerce to tuple.
        if isinstance(self.excluded_wordsets, list):
            object.__setattr__(self, "excluded_wordsets", tuple(self.excluded_wordsets))
        # JSON round-trip yields a list for allowed_pos / excluded_subtypes;
        # coerce to tuple so the frozen instance stays internally immutable.
        if isinstance(self.allowed_pos, list):
            object.__setattr__(self, "allowed_pos", tuple(self.allowed_pos))
        if isinstance(self.excluded_subtypes, list):
            object.__setattr__(self, "excluded_subtypes", tuple(self.excluded_subtypes))
        # Wrap anki_fields in MappingProxyType so it cannot be mutated in place
        # on the shared frozen config instance (tuple coercion pattern already
        # applied to the other collection fields above).
        if not isinstance(self.anki_fields, types.MappingProxyType):
            object.__setattr__(self, "anki_fields", types.MappingProxyType(dict(self.anki_fields)))

        # Clamp ui_font_scale to [0.5, 2.0]
        object.__setattr__(self, "ui_font_scale", max(0.5, min(2.0, float(self.ui_font_scale))))

        # Normalize ui_language: lower-case, strip, empty → "en". Lenient (no
        # whitelist) so a contributor's freshly-added language code is accepted
        # before its catalog is fully wired; install_translators no-ops on a
        # code with no .qm.
        object.__setattr__(self, "ui_language", str(self.ui_language).strip().lower() or "en")

        # Validate asr_model: reset unknown values to the default so a stale or
        # hand-edited config never silently passes an unsupported model name to
        # faster-whisper. The authoritative set lives in services/asr/model_manager.py;
        # duplicated here to keep config self-contained and import-free.
        if self.asr_model not in {"large-v3", "small"}:
            object.__setattr__(self, "asr_model", "large-v3")

        # Keep anki_word_field in sync with anki_fields["word"]
        word_field_from_mapping = self.anki_fields.get("word", "")
        if word_field_from_mapping and word_field_from_mapping != self.anki_word_field:
            object.__setattr__(self, "anki_word_field", word_field_from_mapping)
