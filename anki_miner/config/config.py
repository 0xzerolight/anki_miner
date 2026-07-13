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
class FreqEntry:
    """One enabled/ordered frequency source in the additive chain.

    References a folder under ~/.anki_miner/freqs/<source_id>/ holding an
    index.sqlite built by the frequency source importer. Sources are layered
    additively at lookup time (min rank wins for filtering/sorting; all hits
    are shown in the card breakdown).
    """

    source_id: str
    enabled: bool = True


@dataclass(frozen=True)
class AudioSourceEntry:
    """One entry in the expression audio source chain.

    Pack entries reference a local audio pack under
    ~/.anki_miner/audio_packs/<pack_id>/.
    JPod101 entries are the always-available online fallback; pack_id is None.
    GoogleTTS entries are a synthetic Google Translate TTS online fallback;
    pack_id is None (like jpod101).

    ``custom`` / ``custom_json`` entries are user-configured URL-template sources
    (the local-audio-yomichan integration contract). ``url`` holds the template
    with ``{term}``/``{reading}``/``{language}`` placeholders; ``custom`` fetches
    the templated URL directly, ``custom_json`` fetches an ``audioSourceList``
    JSON document and downloads each listed URL in order. ``url`` is None for the
    non-custom kinds.
    """

    kind: Literal[
        "pack",
        "jpod101",
        "googletts",
        "custom",
        "custom_json",
    ]
    pack_id: str | None = None
    url: str | None = None
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
            # Inline pitch graph SVG / overline text (6.3). Both default ""
            # (feature off; wire byte-identical via the empty-name skip).
            "pitch_graph": "",
            "pitch_text": "",
            "frequency": "",
            "frequency_sort": "",
            "source": "",
            "expression_audio": "",
        }
    )
    # JP Mining Note-style card-type marker. When card_type is non-empty, an "x"
    # is written into the matching field from card_type_marker_fields so the note
    # type renders the card as that type. "" = feature off (default; identical to
    # pre-feature behaviour). Kept OUT of anki_fields on purpose: verify_card_target
    # validates every non-empty anki_fields value, so storing all four marker names
    # there would force-validate fields a non-JPMN note type (e.g. Lapis) lacks.
    # Only the active marker is validated/written.
    card_type: Literal["", "word_and_sentence", "click", "sentence", "audio"] = ""
    card_type_marker_fields: Mapping[str, str] = field(
        default_factory=lambda: {
            "word_and_sentence": "IsWordAndSentenceCard",
            "click": "IsClickCard",
            "sentence": "IsSentenceCard",
            "audio": "IsAudioCard",
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

    # Audio Condenser settings. Persisted defaults for the Condense tab's inline
    # run options; the tab seeds its widgets from these on load and writes them
    # back on edit via config_changed → MainWindow.update_config (see
    # CondenseTab._on_option_changed). All plain scalars (auto-persist; no
    # __post_init__ coercion).
    condenser_padding_ms: int = 500  # Silence kept on each side of every dialogue line
    condenser_offset_ms: int = 0  # Shift every subtitle cue before condensing
    condenser_output_format: str = "mp3"  # "mp3" | "opus" | "flac"
    condenser_bitrate_kbps: int = 96  # kbps; mp3/opus only (flac ignores)
    condenser_filtered_chars: str = "♪♫♬♩〜～"  # Cues consisting only of these are dropped
    condenser_write_subtitles: bool = False  # Also write condensed .srt + .lrc sidecars

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
    # NOTE: "非自立" here does NOT match unidic's "非自立可能" — and must not be
    # "fixed" to it. 非自立可能 is a LEXICAL tag attached to 出す/見る/いる in
    # every context (including as standalone main verbs), so excluding it would
    # drop legitimate independent verbs, not just compound fragments. Fragment
    # mining is solved by dictionary-attested compound matching instead
    # (services/compound_matcher.py).
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

    # Sentence TTS for reading sources (manga/novels), which have no source
    # audio. Reading-path only: video/YouTube/audiobook keep real ffmpeg clips.
    # The master flag is the sole opt-in switch — the target Anki field
    # (anki_fields["audio"]) is mapped by default, so field-presence gating
    # (the expression_audio pattern) cannot express consent here. Default OFF:
    # enabling sends full sentence text to Google/Naver and adds one network
    # round-trip per unique sentence, so it must be a deliberate choice.
    # Provider order is fixed (Google first, Papago fallback); the two bools
    # only select membership. Plain bools => no config migrator needed.
    reading_tts_enabled: bool = False
    reading_tts_google_enabled: bool = True
    reading_tts_papago_enabled: bool = True

    # Pitch accent settings. Activation is resource-driven (see the pitch_active
    # property): the lookup runs iff a pitch data file is present. There is no
    # separate on/off flag — importing/downloading the file is the switch.
    pitch_accent_path: Path = field(default_factory=lambda: ANKI_MINER_HOME / "pitch_accent.csv")
    # Output label format for the pitch_category Anki field.
    # "jp": 平板/頭高/中高/尾高/起伏 (legacy)
    # "romaji": heiban/atamadaka/nakadaka/odaka/kifuku (Yomitan/Lapis compatible)
    pitch_category_format: Literal["jp", "romaji"] = "jp"

    # Frequency settings
    # DEPRECATED: the UI no longer writes this (superseded by frequency_chain).
    # Kept only as the read source for the one-time legacy_migration that folds
    # an existing frequency.csv into the chain; it still round-trips so old
    # configs migrate cleanly. Do not wire new writers to it.
    frequency_list_path: Path = field(default_factory=lambda: ANKI_MINER_HOME / "frequency.csv")
    # Activation is resource-driven (see the frequency_active property): the
    # frequency service loads iff at least one enabled source is in the chain.
    # There is no separate on/off flag — adding a source is the switch.
    max_frequency_rank: int = 0  # 0 = no filtering; e.g. 10000 = only top 10k words

    # Additive multi-source frequency chain. Each enabled FreqEntry references a
    # per-source index under ~/.anki_miner/freqs/<source_id>/. Empty by default;
    # a later migration populates it from the legacy single-list config.
    frequency_chain: tuple["FreqEntry", ...] = field(default_factory=tuple)
    freqs_root: Path = field(default_factory=lambda: ANKI_MINER_HOME / "freqs")

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
    # class scheme (`.yomitan-glossary`, `gloss-sc-*`, `data-sc-*`) and ships one
    # universal glossary stylesheet (resources/glossary.css). Glossary styling is
    # self-contained per card (the Yomitan model): a `<style>` block — universal
    # sheet + every enabled dictionary's scoped styles.css — is embedded at the top
    # of each card's glossary field at card-creation time
    # (`EpisodeProcessor._phase5_create` → `build_card_style_block`). anki_miner
    # never writes to the note type's card styling.

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

    # Reading tab: minimum times a word must occur in a single book/volume to be
    # mined. 1 = no minimum (filter off). Consumed via
    # WordFilterService.filter_by_episode_count, which early-returns when the
    # threshold is <= 1. Migration-safe: absent in old configs → backfilled to 1
    # by GUIConfigManager.load_config (unknown-key filter + dataclass default).
    reading_min_occurrence: int = 1

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
    # `asr_device` selects the transcription backend: "auto" = GPU (CUDA) if
    # usable else CPU; "cuda"/"cpu" force GPU/CPU. Unknown values reset to "auto".
    asr_device: str = "auto"
    asr_models_root: Path = field(default_factory=lambda: ANKI_MINER_HOME / "asr_models")
    # Managed directory for downloaded cuDNN/cuBLAS shared libs (preloaded before
    # a CUDA build); derived from ANKI_MINER_HOME, never user-configurable directly.
    cuda_libs_root: Path = field(default_factory=lambda: ANKI_MINER_HOME / "cuda_libs")
    # Managed directory for the in-app-downloaded onnxruntime pack that enables
    # Silero VAD (silence removal) in the bundle, where onnxruntime is stripped to
    # stay slim. Extracted onnxruntime/ tree is added to sys.path on demand.
    # Derived from ANKI_MINER_HOME, never user-configurable directly.
    onnx_pack_root: Path = field(default_factory=lambda: ANKI_MINER_HOME / "onnx_pack")

    # Managed directory for in-app-downloaded executables (e.g. the alass
    # subtitle-alignment binary); derived from ANKI_MINER_HOME, never
    # user-configurable directly.
    bin_root: Path = field(default_factory=lambda: ANKI_MINER_HOME / "bin")

    # Theme settings (UI state — persisted via gui_config.json).
    # `theme_favorites` is the curated list that drives the top-right combo;
    # the active `theme` does not need to be in favorites.
    theme: str = "light"
    theme_favorites: tuple[str, ...] = ("light", "dark")
    themes_root: Path = field(default_factory=lambda: ANKI_MINER_HOME / "themes")
    # Global UI font scale factor. Applied to all QSS ${font-size-*} variables.
    # Clamped to [0.5, 2.0] in __post_init__; values outside the range are silently clamped.
    ui_font_scale: float = 1.0
    # Whole-UI zoom factor. Injected as QT_SCALE_FACTOR before QApplication is
    # constructed (gui/app.py), so it scales everything uniformly — fonts,
    # spacing, fixed-size widgets, pixmaps — unlike the font-only ui_font_scale.
    # Restart-to-apply (Qt reads QT_SCALE_FACTOR once at startup). Clamped to
    # [0.5, 2.0] in __post_init__.
    ui_zoom: float = 1.0
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
        if isinstance(self.freqs_root, str):
            object.__setattr__(self, "freqs_root", Path(self.freqs_root))
        if isinstance(self.known_words_db_path, str):
            object.__setattr__(self, "known_words_db_path", Path(self.known_words_db_path))
        if isinstance(self.blacklist_path, str):
            object.__setattr__(self, "blacklist_path", Path(self.blacklist_path) if self.blacklist_path else None)
        if isinstance(self.whitelist_path, str):
            object.__setattr__(self, "whitelist_path", Path(self.whitelist_path) if self.whitelist_path else None)
        if isinstance(self.stats_db_path, str):
            object.__setattr__(self, "stats_db_path", Path(self.stats_db_path))
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
        if isinstance(self.cuda_libs_root, str):
            object.__setattr__(self, "cuda_libs_root", Path(self.cuda_libs_root))
        if isinstance(self.onnx_pack_root, str):
            object.__setattr__(self, "onnx_pack_root", Path(self.onnx_pack_root))
        if isinstance(self.bin_root, str):
            object.__setattr__(self, "bin_root", Path(self.bin_root))
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
        # Same immutability wrap for the card-type marker name map.
        if not isinstance(self.card_type_marker_fields, types.MappingProxyType):
            object.__setattr__(
                self, "card_type_marker_fields", types.MappingProxyType(dict(self.card_type_marker_fields))
            )

        # Clamp ui_font_scale to [0.5, 2.0]
        object.__setattr__(self, "ui_font_scale", max(0.5, min(2.0, float(self.ui_font_scale))))

        # Clamp ui_zoom to [0.5, 2.0]
        object.__setattr__(self, "ui_zoom", max(0.5, min(2.0, float(self.ui_zoom))))

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

        # Validate asr_device the same way: a stale/hand-edited config must never
        # pass an unsupported backend name through to the transcriber.
        if self.asr_device not in {"auto", "cuda", "cpu", "vulkan"}:
            object.__setattr__(self, "asr_device", "auto")

    @property
    def frequency_active(self) -> bool:
        """Whether frequency data should load — iff an enabled source is configured.

        Replaces the removed ``use_frequency_data`` flag. This gates index load,
        the max-frequency-rank mining filter, the rank shown in the curation
        preview, and the CSV-export rank column — all of which happen *before*
        the card write, so a mapped ``frequency`` Anki field is the wrong signal
        (that only controls whether the rank is written onto the card). The
        default chain is empty, so default configs are inactive (byte-identical).
        Note: a truthy value means the service *should* load; it still yields no
        provider if the enabled source lacks a valid on-disk index.
        """
        return any(e.enabled for e in self.frequency_chain)

    @property
    def pitch_active(self) -> bool:
        """Whether pitch lookup should load — iff a pitch data file is present.

        Replaces the removed ``use_pitch_accent`` flag. The default path exists
        only after the user imports/downloads pitch data, so default configs are
        inactive. Like frequency, this drives pre-card-write surfaces (CSV
        export), so file-presence — not a mapped pitch field — is the switch.
        """
        return self.pitch_accent_path.is_file()
