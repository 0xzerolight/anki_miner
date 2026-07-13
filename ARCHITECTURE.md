# Architecture

Anki Miner is a PyQt6 desktop application. It processes video/subtitle files through a 5-stage pipeline to create Japanese vocabulary flashcards in Anki.

## Processing Pipeline

The core data flow is a linear 5-stage pipeline orchestrated by `EpisodeProcessor`. YouTube mining prepends a fetch pre-stage that produces the same `(video, subtitle)` pair the file-based flow starts from; everything downstream is unchanged.

```
YouTube URL (optional entry point)
  │
  ▼
┌─────────────────────────────────────────────────────┐
│ 0. Fetch (YouTube only)                             │
│    YouTubeFetcherService (yt-dlp subprocess)        │
│    probe_metadata(url) → VideoInfo                  │
│    fetch_video(url, video_id, workspace, sub_mode)  │
│    → FetchedMedia(video_file, subtitle_file, ...)   │
└─────────────────────────────────────────────────────┘
  │
  ▼
Subtitle file (ASS/SRT/SSA)
  │
  ▼
┌─────────────────────────────────────────────────────┐
│ 1. Parse Subtitles                                  │
│    SubtitleParserService (pysubs2 + fugashi/MeCab)   │
│    → list[TokenizedWord]                            │
├─────────────────────────────────────────────────────┤
│ 2. Filter Unknown Words                            │
│    WordFilterService + AnkiService                  │
│    + optional: FrequencyService, WordListService,   │
│      KnownWordDB, cross-episode counts              │
│    → list[TokenizedWord] (unknown only)             │
├─────────────────────────────────────────────────────┤
│ 3. Extract Media                                    │
│    MediaExtractorService (ffmpeg, parallel)          │
│    → list[(TokenizedWord, MediaData)]               │
├─────────────────────────────────────────────────────┤
│ 4. Fetch Definitions                                │
│    DefinitionService → DictionaryRegistry chain     │
│    (IndexedDictProvider offline dicts, first-hit-   │
│     wins; JishoProvider online fallback)            │
│    → list[str | None]                               │
├─────────────────────────────────────────────────────┤
│ 5. Create Anki Cards                                │
│    AnkiService (AnkiConnect HTTP API)               │
│    → cards_created count                            │
└─────────────────────────────────────────────────────┘
  │
  ▼
ProcessingResult
```

Before Phase 1, a pre-flight step validates the configured note type and field mapping against Anki and auto-creates the target deck. Cancellation is checked between each phase. An optional curation callback lets the GUI present a word selection dialog between stages 2 and 3.

The offline dictionary also participates in stage 1 when available: `service_factory` injects `DefinitionService.offline_terms_exist` into the parser, whose `CompoundDictionaryMatcher` (`services/compound_matcher.py`) merges adjacent MeCab tokens into a single word whenever the joined form — with the tail token deinflected via UniDic orthBase — is an exact dictionary headword (Yomitan's longest-match principle; fixes fragment mining like 走り出した→走り). With no offline dictionary or `compound_matching` off, stage 1 is unchanged.

## Package Dependencies

```
gui/
  │
  ▼
orchestration/
  │
  ▼
services/
services/dictionary/providers/
  │
┌───────┼───────┐
▼       ▼       ▼
interfaces/ models/ utils/
        │
        ▼
     models/

config/      ← used by all packages
exceptions/  ← used by all packages
```

Leaf packages (`config`, `models`, `exceptions`, `utils`) have no internal dependencies. `interfaces` depends only on `models` for type signatures. `services` depends on `interfaces`, `models`, `config`, `exceptions`, and `utils`. `orchestration` composes services. `gui` is the sole top-level entry point.

## Core Abstractions

Three protocols in `interfaces/` define the system's extension points:

**PresenterProtocol** (`interfaces/presenter.py`): output abstraction with 7 methods.
- `show_info`, `show_success`, `show_warning`, `show_error`: message display.
- `show_validation_result(ValidationResult)`: system check results.
- `show_processing_result(ProcessingResult)`: episode processing summary.

Implementations: `GUIPresenter` (Qt signals) and `NullPresenter` (tests). The protocol is preserved even without a CLI so that workers, orchestration, and services stay UI-agnostic and fully testable.

**ProgressCallback** (`interfaces/progress.py`): progress reporting with 4 methods.
- `on_start(total, description)`, `on_progress(current, item_description)`
- `on_complete()`, `on_error(item_description, error_message)`

**DictionaryProvider** (`interfaces/dictionary_provider.py`): pluggable dictionary backend.
- `name` property, `is_online` property, `is_available()`, `load()`, `lookup(word) -> str | None`

All use `typing.Protocol` for structural subtyping. Implementations satisfy the protocol via duck typing, without explicit inheritance.

## Models

Data classes in `models/`:

| Model | File | Purpose |
|-------|------|---------|
| `TokenizedWord` | `word.py` | Parsed word with surface, lemma, reading, sentence, timing, furigana, frequency_rank, pos. `mined_form` property selects lemma for verbs/adjectives, surface for nouns — this is the form that becomes the Anki Expression. |
| `WordData` | `word.py` | TokenizedWord + definition + media paths + pitch accent |
| `MediaData` | `media.py` | Screenshot/audio file paths and filenames |
| `ProcessingResult` | `processing.py` | Pipeline output: word counts, card count, errors, elapsed time, comprehension %, card IDs |
| `ValidationResult` | `processing.py` | System check results: connectivity, tool availability, issues list |
| `ValidationIssue` | `processing.py` | Component + severity (ERROR/WARNING) + message |
| `BatchQueue` / `QueueItem` | `batch_queue.py` | Batch processing queue with PENDING/PROCESSING/COMPLETED/ERROR states |
| `MiningSession` | `stats.py` | Analytics: series, episode, word counts, timing |
| `OverallStats` | `stats.py` | Aggregated analytics |
| `DifficultyEntry` | `stats.py` | Per-episode difficulty tracking |
| `Milestone` | `stats.py` | Milestone achievement record |
| `HistoryEntry` | `history.py` | Mining history record with undo support (stores card IDs) |
| `CardPayload` | `card_payload.py` | Assembled Anki note fields + media for one card |
| `YouTubeQueueItem` / `YouTubeItemStatus` / `YouTubeQueue` | `youtube_queue.py` | YouTube mining queue + per-item status |
| `AudiobookQueueItem` / `AudiobookItemStatus` / `AudiobookQueue` | `audiobook_queue.py` | Audiobook mining queue + per-item status |
| `ReadingQueueItem` / `ReadingItemStatus` | `reading_queue.py` | Reading (manga/novel) mining queue item + status |
| `ReadingDocument` / `ReadingSourceRef` / `ReadingUnit` / `ImageRef` | `reading.py` | Parsed reading source: units of text + page/cover image refs |
| `DeckBuildRequest` / `DeckBuildPreview` / `DeckSelectionMode` | `deck_build.py` | Deck Builder request, corpus preview, and selection mode (ALL/TOP_N/COVERAGE_PCT) |
| `VideoInfo` | `youtube.py` | YouTube probe result: id, title, duration, sub availability, is_live, is_age_restricted |
| `FetchedMedia` | `youtube.py` | yt-dlp fetch result: video path, subtitle path, `sub_source` ("manual" or "auto") |
| `SubMode` | `youtube.py` | `Literal["manual_only", "auto_only"]` — resolved in the GUI from the probe + user acceptance |
| `PlaylistEntry` | `youtube.py` | A single entry from a flat playlist probe: video_id, title, duration_s (optional), canonical URL |
| `PlaylistInfo` | `youtube.py` | Flat playlist probe result: playlist_id (optional), title, entries tuple, total_count (optional) |

## Services

Stateless business logic classes in `services/`. Each receives the frozen `AnkiMinerConfig` in its constructor.

**Core services (always created):**

- **SubtitleParserService**: parses ASS/SRT/SSA files via `pysubs2`, tokenizes Japanese text with `fugashi` (MeCab wrapper), generates furigana annotations against `TokenizedWord.mined_form` (lemma for verbs/adjectives, surface for nouns), and deduplicates emitted words by lemma. Compound-merge passes (prefix, noun-suffix, verb-nominalizer) reconstruct synthetic lemmas from each component's `feature.lemma` so dictionary lookups can hit the headword.
- **WordFilterService**: multi-layer filtering via `filter_unknown` (lemma-only check against known vocabulary), `filter_by_frequency`, `filter_by_word_lists` (blacklist/whitelist keyed by lemma), `deduplicate_by_sentence` (NFKC-normalized, whitespace-collapsed dedup key), `filter_i_plus_one`, and `filter_by_episode_count`. The name-wordset filter (Issue #59) runs in `_phase2_filter` after the blacklist/whitelist step and is gated on `bypass_optional_filters` (skipped by the Deck Builder corpus preview); which wordsets are active is controlled by `config.excluded_wordsets`. Wordset files are bundled JMnedict-derived proper-noun lists in `anki_miner/resources/wordsets/`.
- **MediaExtractorService**: extracts screenshots (`ffmpeg -frames:v 1`) and audio clips at subtitle timestamps. The audio encoder follows `config.audio_format`: `libmp3lame` for mp3 (the default), `libopus` for opus. Runs in parallel via `ThreadPoolExecutor` with `max_parallel_workers` threads. Auto-detects the Japanese audio stream via `ffprobe` with thread-safe caching. Optional animated screenshots use `libsvtav1` (AVIF) or `libwebp_anim` (WebP), each probed for availability before use.
- **DefinitionService**: orchestrates the provider chain built by `DictionaryRegistry` from `config.dictionary_chain`. First-hit-wins across offline `IndexedDictProvider` instances, with `JishoProvider` as the online fallback. Returns HTML-formatted definition strings.
- **AnkiService**: AnkiConnect HTTP API wrapper (localhost:8765). Key operations: `verify_card_target` (pre-flight: checks note type, validates fields, creates deck), `get_existing_vocabulary`, `store_media_file`, `create_cards_batch` (batch size 100; media uploads chunk separately at 50 files or 4 MB of base64, whichever comes first), `delete_notes`. Stores `last_created_note_ids` for undo support.
- **ValidationService**: checks AnkiConnect connectivity, ffmpeg presence, deck existence, and note type existence. Returns `ValidationResult` (never raises).
- **YouTubeFetcherService** (`services/youtube_fetcher.py`): wraps the `yt-dlp` subprocess. Three entry points: `probe_metadata(url) → VideoInfo` (fast, `--skip-download --dump-single-json --no-playlist`), `probe_playlist(url, limit) → PlaylistInfo` (`--skip-download --flat-playlist --dump-single-json --playlist-items 1:{limit+1}`, the extra entry lets callers detect over-cap playlists), and `fetch_video(url, video_id, workspace, sub_mode, progress_cb, cancel_event) → FetchedMedia`. Detects native vs translated auto-captions via `_has_native_auto_ja()`. Tracks the `Popen` handle so cancellation can kill the full process tree (yt-dlp → ffmpeg child) via `psutil`. Writes the (video, subtitle) pair into a caller-owned workspace directory.

**Optional services (created based on config flags):**

- **MultiFrequencyService** (`services/frequency/`): aggregates an ordered chain of SQLite-indexed frequency sources built by `FrequencySourceRegistry` from `config.frequency_chain`. Each source is an `IndexedFreqProvider` over `freqs_root/<source_id>/index.sqlite`. `lookup_min(word)` returns the best (lowest) rank across all enabled sources for filtering/sorting; `lookup_all(word)` returns the per-source breakdown for the card. A legacy single `frequency.csv` is folded into the chain once on first launch by `legacy_migration.migrate_legacy_frequency_csv` (the old single-CSV `FrequencyService` was removed). Registry I/O happens in `load()`, not `__init__`.
- **PitchAccentService**: loads pitch accent CSV, exposes `lookup_batch`.
- **ASR transcription** (`services/asr/`): offline speech-to-text via `faster-whisper`. `transcriber.py` runs the model, `model_manager.py` handles in-app model/acceleration-pack downloads, `_engine.py` probes availability (degrades gracefully when the `[asr]` extra is absent). Feeds the Tools → Generate tab.
- **Subtitle retiming** (`services/subtitle_retimer.py`): the module-level `retime_subtitle()` function (with the `_run_alass` helper) realigns an out-of-sync subtitle file to a video via the external `alass` binary, resolved by `utils/alass_resolver.py` and installed in-app by `services/alass_installer.py`. Feeds the Tools → Retime tab.
- **Audio condensing** (`services/audio_condenser.py`): `AudioCondenserService` builds dialogue-only condensed audio from a media file plus its subtitles — computing kept intervals (padding, gap-merge, offset, line filtering) with pure interval math, then extracting/concatenating them and re-encoding to `mp3`/`opus`/`flac` via ffmpeg, optionally emitting a re-timed `.srt`+`.lrc`. Reads external `.ass`/`.ssa`/`.srt`/`.vtt` or an embedded text track (`ffprobe`-listed, decoded with a `charset-normalizer` fallback). Feeds the Tools → Condense tab.
- **Reading sources** (`services/reading/`): parses mokuro-processed manga volumes and Japanese books into `ReadingDocument`s. `detector.py` classifies a path (`.mokuro`/`.cbz` → manga, `.epub` → book, `.txt` → Aozora/plain) and `load()` dispatches to `mokuro_source.py`, `epub_source.py`, or `aozora_source.py`; `sentence_splitter.py` segments text and `images.py` materializes page/cover images. Anki Miner consumes mokuro's existing OCR — it does no OCR itself. DRM-protected EPUBs are rejected up front (`epub_source._check_encryption`).
- **KnownWordDB**: SQLite-backed persistent known word cache. Supports differential sync with Anki vocabulary.
- **WordListService**: loads blacklist/whitelist text files for word filtering.
- **HistoryService**: SQLite-backed mining history (`mining_history` table). Records what was mined, supports undo via stored card IDs.
- **StatsService**: SQLite-backed analytics (`mining_sessions`, `series_difficulty` tables). Provides aggregated stats and milestones.
- **UpdateChecker**: queries the GitHub Releases API for newer versions.
- **ExportService**: exports results to CSV, TSV, or vocabulary list formats.

**Dictionary providers** (`services/dictionary/providers/`):

- **IndexedDictProvider**: SQLite-backed offline provider used by every on-disk dictionary (JMdict and user-loaded Yomitan dicts). On first launch, JMdict XML is migrated to a SQLite index at `~/.anki_miner/dicts/jmdict-english/index.sqlite`; lookups run against that index. The read-only connection is opened with `check_same_thread=False` so a single instance is safe to share across worker threads.
- **DictionaryRegistry**: scans `config.dicts_root` (`ANKI_MINER_HOME/dicts/`) for installed dictionaries and builds the provider chain from `config.dictionary_chain`. Disk I/O happens in the explicit `load()` call, not in `__init__`. The chain is first-hit-wins, with `JishoProvider` appended as the online fallback.
- **JishoProvider**: REST client for the jisho.org API. Always available. Rate-limited with a configurable delay (`jisho_delay`).

**Expression audio** (`services/audio_packs/`, `services/expression_audio_fetcher.py`):

Word-level audio (Issue #73) runs through a `ChainedExpressionAudioFetcher` that walks an ordered list of `ExpressionAudioFetcher` implementations (the protocol is in `interfaces/expression_audio.py`; fetchers never raise) and returns the first non-None path. The chain is assembled by `service_factory` from `config.expression_audio_chain` — a list of `AudioSourceEntry` objects, each tagged `kind: "pack"|"jpod101"|"googletts"|"custom"|"custom_json"` with an enabled flag (`custom`/`custom_json` are URL-template sources — a plain audio URL or a local-audio-yomichan-style JSON endpoint). The default chain contains the JPod101 entry plus a disabled Google Translate entry, preserving pre-feature behavior with zero extra I/O for users who have not imported any packs.

Local audio packs are imported from [local-audio-yomichan](https://github.com/themoeway/local-audio-yomichan)-compatible directories. Five physical pack formats are detected: `ozk5` and `ajt` (both `index.json`-driven — `ozk5` is checked first), `nhk16` (entries.json + audio/, NHK 2016 accent survey), `forvo` (speaker subdirectories), and `jpod_legacy` (flat `{reading} - {expression}` stems, covers the JapanesePod101 archive and its alternate variant). Format detection is in `services/audio_packs/formats.py`; importing is in `services/audio_packs/importer.py`.

Each imported pack gets a SQLite index at `config.audio_packs_root/<pack_id>/index.sqlite` (`~/.anki_miner/audio_packs/` by default) with an `entries(expression, reading, source, speaker, display, file)` table and a meta table. The audio files themselves are never moved — each entry's `file` is a posix-style path relative to the pack directory, and the meta table stores the absolute `pack_dir` they resolve against. Import is atomic (stage to a temp location, then rename into place). `AudioPackRegistry` (`services/audio_packs/registry.py`) has an I/O-free `__init__` and a `load()` call; it is only instantiated when expression audio is enabled and at least one enabled pack entry is present in the chain.

`LocalAudioPackFetcher` opens a read-only SQLite connection per call, queries by `(expression, reading)` — using `mined_form` for expression to match the card's Expression field — and copies the matched file into `~/.anki_miner/audio_cache/local_packs/` as `{pack_id}_{mined_form}_{reading}{ext}`. An empty or whitespace-only reading skips the fetch entirely (mirroring `JPod101AudioFetcher`): a reading-less lookup falls back to wildcard row selection, which could cache the wrong homograph pronunciation permanently. It never returns in-place pack paths (containment guard prevents path traversal), so the cached copy is what gets stored in Anki. In a chained configuration, pack fetchers are inserted above the JPod101 entry (packs take priority); JPod101 still acts as a fallback for words not covered by any installed pack.

`GoogleTranslateAudioFetcher` (`services/google_translate_audio_fetcher.py`) is an optional synthetic-TTS source backed by the `gtts` library (free, no API key). It is fed the kana **reading**, not the kanji, so pronunciation is correct and immune to homograph misreads; an empty or whitespace-only reading skips the fetch (mirroring the other fetchers). It sits as a chain fallback, typically after JPod101 — JPod101 is recorded native audio, Google is synthetic — and is disabled by default, so default behavior is unchanged. Hits cache to `~/.anki_miner/audio_cache/googletts/` as `googletts_{mined_form}_{reading}.mp3`; there are no negative (`.miss`) markers because synthetic failures are transient and retried next run.

The triple gate is unchanged: expression audio is written to a card only when `config.expression_audio_enabled` is set, a fetcher is injected, and `config.anki_fields["expression_audio"]` is non-empty.

**Sentence TTS for reading sources** (`services/sentence_tts_fetcher.py`, protocol in `interfaces/sentence_audio.py`): reading-mined cards (manga/novels) have no source audio, so `process_reading` phase 3' can synthesize the card sentence instead. A `ChainedSentenceAudioFetcher` walks Google Translate TTS (shared gtts synthesis leaf with the word fetcher) then Naver Papago (unofficial two-step endpoint: POST `makeID` → GET the audio; the fetcher's never-raises contract is load-bearing since the scraped endpoint may drift). Assembled by `service_factory._build_sentence_audio_fetcher` from three plain config bools — `reading_tts_enabled` (master, off by default) + one per provider, fixed Google-first order. Gated by `EpisodeProcessor._reading_tts_active`; the video/YouTube/audiobook paths never consult it. Hits cache to `~/.anki_miner/audio_cache/sentence_tts/` as `sentencetts_{provider}_{sha1(sentence)[:16]}.mp3` (content-hash keys; no `.miss` markers), and one run synthesizes each unique sentence once, sharing the file across cards.

The import flow is in `gui/controllers/audio_pack_import_flow.py`; the panel it drives is `gui/widgets/panels/audio_pack_settings_panel.py`. Settings → Audio shows a row per installed pack with format, entry count, and a missing-folder badge when the original audio directory has moved. JPod101 and Google Translate appear as built-in rows that can be disabled but not removed. Pack rows can be reordered, disabled, or removed (deletes the index only; audio files are untouched). A right-click context menu offers re-import. The Add Audio Pack button opens a directory picker; if the chosen directory contains multiple detectable packs (e.g. a parent folder), all are imported sequentially and inserted above the JPod101 chain entry in the canonical pack-id insertion priority (nhk16 > shinmeikai8 > forvo > jpod > jpod_alternate, `audio_pack_import_flow._PACK_PRIORITY`) — a pack-id ordering, distinct from the physical-format list above.

## Orchestration

**EpisodeProcessor** (`orchestration/episode_processor.py`):
- Receives all services via constructor injection
- `process_episode()` runs the 5-stage pipeline
- `process_youtube_url()` calls `YouTubeFetcherService.fetch_video`, then delegates to the unchanged `process_episode` with `episode_name_override=f"YT:{video_id}"` and `series_name_override="YouTube"`. The workspace is allocated and cleaned by the worker, not the orchestrator.
- `process_reading()` mines mokuro manga volumes and Japanese novels. It reuses `_phase2_filter`, `_phase4_lookup`, and `_phase5_create` but swaps the video media stage for `_phase3_reading_media`, which materializes each word's page/cover image and expression audio (no ffmpeg, no sentence audio). Between filtering and media it applies a `reading_min_occurrence` floor (`WordFilterService.filter_by_episode_count`) that drops words appearing fewer than the configured number of times in the volume (1 = off); force-included words bypass the floor.
- Cancellation checkpoints between each phase (`self._cancelled` flag); the YouTube flow additionally threads a `threading.Event` into the fetcher so an in-flight yt-dlp subprocess can be killed.
- Supports `curation_callback` (GUI presents word selection dialog)
- Supports `cross_episode_counts` for batch frequency filtering
- Supports `episode_name_override` / `series_name_override` so YouTube-sourced sessions have stable, file-name-independent identity.
- Records to StatsService and KnownWordDB after successful processing
- Cleans up temp media files in `finally` block

**Batch processing** (`gui/workers/batch_queue_worker.py`):
There is no separate folder-orchestrator class; batch mining is driven directly by `BatchQueueWorkerThread` (a `CancellableWorker`). For each `BatchQueue` item it pairs files via `FilePairMatcher.find_pairs_by_episode_number(video_folder, subtitle_folder)` (episode-number matching across two folders, not stem-name matching) and runs a fresh `EpisodeProcessor.process_episode` per pair sequentially. A per-item config copy with the item's `subtitle_offset` is made via `dataclasses.replace`. Per-pair failures are surfaced individually (the item is marked ERROR with a count) since `process_episode` returns failures as results rather than raising.

**DeckBuilderWorker** (`gui/workers/deck_builder_worker.py`):
Whole-series deck mining in two phases separated by a GUI confirm gate.

Phase 1 — aggregate + select: `SubtitleParserService.count_lemmas` is called on every subtitle in the request. The raw per-file counters are summed by `services.corpus_aggregator.aggregate` into a single corpus `Counter`. `select` then ranks lemmas by in-corpus frequency and picks a candidate set according to the mode (ALL, TOP_N, COVERAGE_PCT). Coverage is computed over in-corpus mineable-word token counts (the same POS-filter as mining applies), not `frequency.csv`. A `DeckBuildPreview` is emitted and the worker blocks on a `threading.Event` gate until the GUI calls `confirm()` or `reject()`.

Phase 2 — build: `AnkiService.ensure_deck` creates the target deck if it does not exist (idempotent). For each episode pair, a fresh `EpisodeProcessor` is created via `dataclasses.replace(config, anki_deck_name=deck_name, include_known_words=not collection_filter)` — no production code other than the config field changes. A `curation_callback` closure keeps a word only if its lemma is in the selected set and has not already been carded in a previous episode (`carded: set[str]` shared across the loop). This enforces the cross-episode "card each lemma once" invariant without touching `EpisodeProcessor` internals. `episode_name_override` and `series_name_override` are set to the video stem and deck name respectively so history rows are distinct from regular episode-mining sessions.

## Configuration

`AnkiMinerConfig` (`config/config.py`) is a frozen (immutable) dataclass with ~95 fields. Grouped by area (not every field is listed):

- **Anki:** deck name, note type, field mappings, AnkiConnect URL
- **Media:** audio padding, screenshot offset, temp folder, subtitle offset (range ±300s), `ffmpeg_location` / `ffprobe_location` (explicit binary paths consumed by the resolver — see [ffmpeg / ffprobe](#ffmpeg--ffprobe))
- **Filtering:** min word length, allowed POS tags, excluded subtypes, deduplication, `exclude_hiragana_only_words` / `exclude_katakana_only_words` (kana-only drops, default off), `excluded_wordsets` (active bundled JMnedict name wordsets), `reading_min_occurrence` (per-volume minimum word occurrence for the Reading tab; 1 = off)
- **Dictionary:** `dictionary_chain` (the runtime-authoritative ordered list of providers — indexed dicts and Jisho, each toggleable), `dicts_root` (root for all installed `.sqlite` indexes; defaults to `ANKI_MINER_HOME/dicts/` via the `ANKI_MINER_HOME` constant in `config/paths.py`), Jisho URL/delay. Legacy `jmdict_path` + `use_offline_dict` are retained one release for first-launch migration only.
- **Frequency:** `frequency_chain` (ordered tuple of `FreqEntry(source_id, enabled)` — the runtime-authoritative chain of frequency sources), `freqs_root` (root for the per-source `index.sqlite` files; defaults to `ANKI_MINER_HOME/freqs/`). The `frequency_sort` `anki_fields` entry writes the chosen sort value to its own card field. Legacy `frequency_list_path` is retained for one-time `frequency.csv` migration only.
- **Expression audio:** `expression_audio_chain` (ordered `AudioSourceEntry` list), `expression_audio_delay`, `audio_packs_root`
- **ASR (subtitle generation):** `asr_model`, `asr_device`, `asr_models_root`, `cuda_libs_root`, `onnx_pack_root`
- **YouTube:** `youtube_cookies_from_browser` (browser profile to pull cookies from) / `youtube_cookies_file` (explicit cookies file), max duration, subtitle mode
- **Appearance:** `theme`, `theme_favorites`, `themes_root`, `ui_font_scale` (whole-UI font scaling, clamped to [0.5, 2.0]), `ui_language`
- **Optional data:** pitch accent, frequency, known words DB, blacklist/whitelist paths and toggles
- **History/analytics:** DB paths, enable flags
- **Performance:** max parallel workers (default 6)

The `__post_init__` method uses `object.__setattr__` to convert string paths to `Path` objects (required because the dataclass is frozen). New config instances are created with `dataclasses.replace()`.

**Config source:**
- GUI: `GUIConfigManager` (`gui/utils/config_manager.py`) persists to `~/.anki_miner/gui_config.json`. Defaults come from the `AnkiMinerConfig` dataclass field defaults.

## GUI Architecture

### Window Structure

`MainWindow` contains a `QTabWidget` with seven tabs (registered in `gui/app.py` as Video, Deck Builder, Audio, Reading, Analytics, Tools, Settings):
1. **VideoTab** (`gui/widgets/video_tab.py`, "Video"): a container nesting three video-mining sub-tabs. **Single** (`SingleEpisodeTab`): file selectors (drag-and-drop), subtitle offset control, process button, log widget, progress widget. **Batch** (`BatchProcessingTab`): folder selection, `BatchQueue` management via queue panel, dual progress bars. **YouTube** (`YouTubeTab`, `gui/widgets/youtube_tab.py`): URL input + Add button, `QListWidget` queue of `YouTubeQueueItemWidget` rows (per-row status glyph, title, duration, sub source line, remove button), action buttons (Mine / Clear / Stop All), progress widget, log widget. Deck/note-type/tags widgets are global (see `AnkiSettingsPanel`). URL classification (plain video, playlist, video-in-playlist, Mix) is done without network access by `utils/youtube_url.py` (`classify_youtube_url`); playlist URLs dispatch to `YouTubePlaylistResolveWorker` then `YouTubePlaylistProbeWorker`; mixed watch+list URLs show a choice dialog; playlists over the `youtube_playlist_max` cap show an over-cap confirm. Each sub-tab keeps its own presenter and worker lifecycle; the container fans out config/shutdown and exposes the children's live workers via `iter_close_workers`.
2. **Deck Builder**: whole-series deck mining over a corpus of subtitles, driven by `DeckBuilderWorker` (see Orchestration). Two phases (aggregate/select, then build) separated by a GUI confirm gate.
3. **AudiobookTab** (`gui/widgets/audiobook_tab.py`, "Audio"): audio + subtitle file selectors (subtitle auto-filled from a same-stem `.srt`/`.vtt`/`.ass`/`.ssa` next to the audio file) + Add button, `QListWidget` queue of `AudiobookQueueItemWidget` rows, action buttons (Mine / Clear / Stop All), progress widget, log widget. No probe stage — local pairs enter the queue READY. Mining runs `process_episode` with `audio_only=True`: no per-word screenshots; embedded cover art is extracted once per book and shared as every card's Picture (blank if absent), and the keep/drop decision keys on audio clip success. Stats/history identity: `series_name_override="Audiobook"`, `episode_name_override=<audio file stem>`.
4. **ReadingTab** (`gui/widgets/reading_tab.py`): a container nesting two mining sub-tabs. **Manga** (`ReadingMangaTab`) mines mokuro-processed manga volumes (an image folder or `.cbz`/`.zip` with its sibling `.mokuro` file; the OCR is mokuro's, none is done here) with a quick-folder picker + queue that expands a series folder into per-volume items. **Novels** (`ReadingNovelsTab`) mines a single `.epub` or Aozora/plain `.txt`. Both subclass `_ReadingMiningTabBase` → `MiningTabBase`; mining runs `EpisodeProcessor.process_reading` (see Orchestration).
5. **AnalyticsTab**: mining statistics dashboard (queries `StatsService`).
6. **SubtitlesTab** (`gui/widgets/subtitles_tab.py`, "Tools"): a container with three inner tabs. **Generate** (`SubtitleCreationTab`) transcribes a video/audio file into an SRT with a local Whisper model (`services/asr/`), with in-app model + GPU/VAD pack downloads and a CPU-fallback device selector. **Retime** (`SubtitleRetimeTab`) realigns an out-of-sync subtitle file to a video via `alass` (`services/subtitle_retimer.py`). **Condense** (`CondenseTab`, `gui/widgets/condense_tab.py`) builds dialogue-only condensed audio from a media file + subtitles via `AudioCondenserService` (`services/audio_condenser.py`), with a single-file mode and a batch folder mode; the embedded-track picker is `SubtitleTracksDialog` (`gui/widgets/dialogs/subtitle_tracks_dialog.py`).
7. **SettingsTab**: config editing with sub-panels (Anki, Media, Dictionaries, Audio, Filtering, Frequency, Subtitles, YouTube, Themes). Emits `config_changed` signal.

### Worker Threads

`CancellableWorker` base class (QThread + `threading.Event`) provides:
- Thread-safe cancellation via `cancel()` / `is_cancelled()`
- Qt signals for results, errors, and progress

Worker implementations:
- `EpisodeWorkerThread`: runs `EpisodeProcessor.process_episode()` in the background.
- `BatchQueueWorkerThread`: processes batch queue items sequentially.
- `ManualPairWorkerThread`: processes manually paired files.
- `ValidationWorkerThread`: runs system validation checks.
- `UpdateWorkerThread`: checks for updates.
- `YouTubeProbeWorker` (`gui/workers/youtube_probe_worker.py`): short-lived QThread that calls `YouTubeFetcherService.probe_metadata` so the GUI stays responsive during network I/O.
- `YouTubePlaylistResolveWorker` (`gui/workers/youtube_playlist_probe_worker.py`): short-lived QThread that calls `YouTubeFetcherService.probe_playlist` once and emits a `PlaylistInfo` or error. Bounded by a subprocess timeout (default 120 s); no cancellation support.
- `YouTubePlaylistProbeWorker` (`gui/workers/youtube_playlist_probe_worker.py`): `CancellableWorker` that iterates a list of video URLs sequentially, calling `probe_metadata` for each and emitting `entry_probed` / `entry_failed` per entry. Cancellation is polled between entries; failures continue to the next URL.
- `YouTubeQueueWorker` (`gui/workers/youtube_queue_worker.py`): `CancellableWorker` subclass that drives a list of `YouTubeQueueItem` through fetch + mine sequentially with retry-once on `YouTubeFetchError`. Per-attempt workspace allocation under `media_temp_folder/youtube/run-<uuid>/`.
- `YouTubeQueueItemWidget` (`gui/widgets/youtube_queue_item_widget.py`): pure renderer for one `YouTubeQueueItem` in the queue list.
- `AudiobookQueueWorker` (`gui/workers/audiobook_queue_worker.py`): `CancellableWorker` subclass that drives a list of `AudiobookQueueItem` through `process_episode(audio_only=True)` sequentially. No fetch stage, no retry, no workspace allocation. Cancellation propagates into the processor via `cancel_event`, so a Stop mid-mine resolves at the next phase checkpoint without poisoning the shared processor.
- `AudiobookQueueItemWidget` (`gui/widgets/audiobook_queue_item_widget.py`): pure renderer for one `AudiobookQueueItem` in the queue list.
- `YomitanCsvImportWorker` (`gui/workers/yomitan_csv_import_worker.py`): `CancellableWorker` that runs an injected Yomitan CSV/zip importer (e.g. `import_yomitan_pitch_zip` for pitch accent) off the GUI thread so it stays responsive during a large import; the sibling `FrequencyImportWorker` (`gui/workers/frequency_import_worker.py`) does the same for frequency sources.
- `SubtitleGenWorker` (`gui/workers/subtitle_gen_worker.py`): `CancellableWorker` that runs the local Whisper (`faster-whisper`) transcription off the GUI thread for the Tools → Generate tab.
- `SubtitleRetimeWorker` (`gui/workers/subtitle_retime_worker.py`): `CancellableWorker` that runs the `alass` retiming subprocess for the Tools → Retime tab.
- `CondenseWorker` (`gui/workers/condense_worker.py`): `CancellableWorker` that drives a list of `CondenseItem` through `AudioCondenserService` sequentially (single file or batch folder) off the GUI thread for the Tools → Condense tab.
- `ReadingQueueWorker` (`gui/workers/reading_queue_worker.py`): `CancellableWorker` (owns its `EpisodeProcessor`) that drives a list of `ReadingQueueItem` through `process_reading` sequentially for the Reading tab.
- `DeckBuilderWorker` (`gui/workers/deck_builder_worker.py`): see Orchestration section.

### Signal Architecture

`GUIPresenter` emits Qt signals from worker threads. Main window slots receive them on the GUI thread. Per-tab presenters avoid cross-tab signal pollution. `GUIProgressCallback` bridges the `ProgressCallback` protocol to Qt signals.

GUIPresenter does **not** explicitly inherit from `PresenterProtocol`. It satisfies the protocol via structural subtyping, which avoids a metaclass conflict between `QObject` and `Protocol`.

### Theme System

Theme singleton backed by JSON theme files in `gui/resources/styles/themes/`. 29 built-in themes: 9 named families (Ayu, Catppuccin, Dracula, Everforest, GitHub, Gruvbox, Kanagawa, Rosé Pine, Solarized) plus 6 standalone themes (Dark, Light, Nord, One Dark, Sakura, Tokyo Night) that carry no `family` field. The `discover_themes()` function scans the themes directory at startup, validates each JSON file against a required color key schema (`REQUIRED_COLOR_KEYS`), and registers valid themes. A single `common.qss` stylesheet uses `${color-*}` variable substitution. The `Theme._substitute_variables()` method merges layout variables from `_variables.py` with color variables extracted from the active theme JSON. Custom themes can be added by dropping a valid JSON file into the themes directory. Theme preference is saved via `QSettings`.

### Dialogs

- `WordCurationDialog`: user selects which discovered words to mine (cross-thread via a `threading.Event` bridge). Embeds `SubtitlePlayerWidget` per row for in-place audio playback, and renders multi-dictionary lookup via `DefinitionService.lookup_all_offline`.
- `ResultsDialog`: summary of a mining session with undo option.
- `ExportDialog`: export results to file.

## External Integrations

### AnkiConnect

HTTP POST to `localhost:8765` (configurable). Protocol version 6. Key actions:
- `version`, `deckNames`, `modelNames`, `modelFieldNames`: validation.
- `findNotes`, `notesInfo`: vocabulary lookup.
- `storeMediaFile`: upload screenshots/audio.
- `addNote`, `addNotes`: card creation (batch size 100).
- `deleteNotes`: undo support.

### Jisho API

GET `https://jisho.org/api/v1/search/words?keyword=<word>`. Rate-limited with configurable delay (default 0.5s). Surfaced as `JishoProvider` inside the configurable provider chain — its position is user-controlled via `config.dictionary_chain`. In the default chain it sits after `IndexedDictProvider(jmdict-english)` so it acts as the online fallback when no installed dictionary returns a hit, but users may move it ahead of any indexed dictionary or disable it entirely.

### ffmpeg / ffprobe

- **ffmpeg:** `-ss` seek + `-i` input + `-frames:v 1` for screenshots, `libmp3lame` (mp3) or `libopus` (opus) for audio extraction per `config.audio_format`
- **ffprobe:** `-show_streams -select_streams a` for Japanese audio track detection
- Parallel execution via `ThreadPoolExecutor` (default 6 workers)

**Binary resolution.** All ffmpeg/ffprobe invocations (media extraction, subtitle-preview probe, YouTube fetch) go through a resolver instead of assuming a bare `ffmpeg` on PATH. Resolution order: explicit `config.ffmpeg_location` / `config.ffprobe_location` → bundled binaries shipped inside the frozen app → PATH. The standalone builds (Windows Inno `Setup.exe`, macOS `.tar.gz`, Linux AppImage) bundle GPL ffmpeg + ffprobe fetched per-OS by the release workflow, with encoder presence (`libmp3lame`, `libopus`, `libsvtav1`, `libwebp`, `libwebp_anim`) asserted in the release smoke step. The Linux `.deb` deliberately omits the bundled binaries (GPL distribution constraints) and resolves to the system ffmpeg; PyPI/`pipx` and source installs likewise rely on PATH. A startup health check validates the resolved ffmpeg/ffprobe and surfaces a clear error if neither bundled nor PATH binaries are usable.

### yt-dlp

Subprocess invoked by `YouTubeFetcherService`. Single-video probe uses `--skip-download --dump-single-json --no-playlist`; playlist probe uses `--flat-playlist --dump-single-json` with `--playlist-items 1:limit+1` (the extra entry lets callers detect over-cap playlists); fetch uses `--write-sub` (or `--write-auto-sub` for auto-caption mode) + `--sub-lang ja --sub-format vtt/best --convert-subs srt` + a height-capped format string. Progress parsed from a custom `--progress-template`; post-download merge phases detected by scanning for `[Merger]`/`[SubtitleConvertor]` line signatures. Process tree killed via `psutil` on cancel (yt-dlp spawns ffmpeg as a child for merging; `Popen.terminate()` alone leaks it on Windows). Optional `--cookies-from-browser` (from `config.youtube_cookies_from_browser`) or `--cookies` file (from `config.youtube_cookies_file`) enables bypass of bot-detection prompts and age-restricted content.

### PyInstaller hook for yt-dlp

yt-dlp lazy-loads ~1600 extractor modules plus optional deps (`websockets`, `mutagen`, `brotli`) that PyInstaller's static analysis misses. `PyInstaller-Hooks/hook-yt_dlp.py` calls `collect_all("yt_dlp")`; `anki_miner.spec` registers it via `hookspath=[".../PyInstaller-Hooks"]`, and the release workflow builds with `pyinstaller anki_miner.spec`. The release workflow's bundled smoke step (`ANKI_MINER_SMOKE=youtube` env var in `anki_miner/gui/app.py`) walks `yt_dlp.extractor.gen_extractors()` offline to verify the registry survived `collect_all`.

## Exception Hierarchy

```
AnkiMinerException (base)
├── SetupError
├── AnkiConnectionError
├── SubtitleParseError
├── SubtitleRetimeError
│   └── AlassNotFoundError
├── FfmpegNotFoundError
└── YouTubeFetchError
    ├── BotDetectionError
    ├── CookieDatabaseLockedError
    ├── VideoTooLongError
    └── YtdlpNotFoundError
```

Defined across `exceptions/` (`base.py`, `validation.py`, `anki.py`, `media.py`, `subtitle.py`, `youtube.py`). `FfmpegNotFoundError` is a direct subclass of `AnkiMinerException`, not of `YouTubeFetchError`, even though only the YouTube fetch path raises it today.

## Data Storage

All persistent user data under `~/.anki_miner/`:

| File | Format | Purpose |
|------|--------|---------|
| `gui_config.json` | JSON | GUI configuration persistence |
| `JMdict_e` | XML | Source JMdict XML (~60MB); migrated to SQLite on first launch |
| `dicts/<dict-id>/index.sqlite` | SQLite | Indexed offline dictionaries (e.g. `jmdict-english/`); queried by `IndexedDictProvider` |
| `known_words.db` | SQLite | Known word cache with Anki sync |
| `history.db` | SQLite | Mining history with undo support |
| `stats.db` | SQLite | Analytics (sessions, difficulty, milestones) |
| `pitch_accent.csv` | CSV | Pitch accent lookup data |
| `frequency.csv` | CSV | Legacy single frequency list; migrated once into the `freqs/` chain on first launch, then no longer consulted |
| `freqs/<source_id>/index.sqlite` | SQLite | Per-source frequency index queried by `IndexedFreqProvider`; the runtime-authoritative frequency chain |
| `audio_cache/jpod101/` | Files | JapanesePod101 expression audio cache: `jpod101_{mined_form}_{reading}.mp3` + zero-byte `.miss` negative markers |
| `audio_packs/<pack_id>/index.sqlite` | SQLite | Per-pack expression audio index; audio files stay in their original location |
| `audio_cache/local_packs/` | Files | Per-hit cache copies from installed packs: `{pack_id}_{mined_form}_{reading}{ext}` |
| `audio_cache/googletts/` | Files | Google Translate synthetic-TTS cache: `googletts_{mined_form}_{reading}.mp3` (no `.miss` markers — synthetic failures are transient) |
| `asr_models/` | Files | Downloaded local Whisper models (Tools → Generate) |
| `cuda_libs/` | Files | In-app CUDA acceleration pack for ASR |
| `onnx_pack/` | Files | In-app ONNX/VAD pack for ASR |
| `bin/` | Files | In-app-installed external binaries (e.g. `alass`) |
| `themes/` | JSON | User-added custom theme files |
| `anki_miner.log` | Log | Application log |

Temporary media files are stored in the system temp directory under `anki_miner_temp/` and cleaned up after each processing run. YouTube downloads go one level deeper — `anki_miner_temp/youtube/run-<uuid>/` — owned by `YouTubeQueueWorker` (one workspace per attempt; cleaned up in `finally` on every exit path) and `rmtree`'d on every exit path (success, cancel, exception). Reading (manga/novel) mining materializes each word's page or cover image into a temp workspace rather than running ffmpeg.
