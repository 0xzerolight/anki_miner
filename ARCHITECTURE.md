# Architecture

Anki Miner is a PyQt6 desktop application. It processes anime video/subtitle files through a 5-stage pipeline to create Japanese vocabulary flashcards in Anki.

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
│    fetch_video(url, workspace, sub_mode)            │
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

Cancellation is checked between each phase. Preview mode exits after stage 2 (shows words, creates no cards). An optional curation callback lets the GUI present a word selection dialog between stages 2 and 3.

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
- `show_word_preview(list[TokenizedWord])`: discovered word listing.

Implementations: `GUIPresenter` (Qt signals) and `NullPresenter` (tests). The protocol is preserved even without a CLI so that workers, orchestration, and services stay UI-agnostic and fully testable.

**ProgressCallback** (`interfaces/progress.py`): progress reporting with 4 methods.
- `on_start(total, description)`, `on_progress(current, item_description)`
- `on_complete()`, `on_error(item_description, error_message)`

**DictionaryProvider** (`interfaces/dictionary_provider.py`): pluggable dictionary backend.
- `name` property, `is_available()`, `load()`, `lookup(word) -> str | None`

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
| `SeriesStats` / `OverallStats` | `stats.py` | Aggregated analytics |
| `DifficultyEntry` | `stats.py` | Per-episode difficulty tracking |
| `HistoryEntry` | `history.py` | Mining history record with undo support (stores card IDs) |
| `VideoInfo` | `youtube.py` | YouTube probe result: id, title, duration, sub availability, is_live, is_age_restricted |
| `FetchedMedia` | `youtube.py` | yt-dlp fetch result: video path, subtitle path, `sub_source` ("manual" or "auto") |
| `SubMode` | `youtube.py` | `Literal["manual_only", "auto_only"]` — resolved in the GUI from the probe + user acceptance |

## Services

Stateless business logic classes in `services/`. Each receives the frozen `AnkiMinerConfig` in its constructor.

**Core services (always created):**

- **SubtitleParserService**: parses ASS/SRT/SSA files via `pysubs2`, tokenizes Japanese text with `fugashi` (MeCab wrapper), generates furigana annotations against `TokenizedWord.mined_form` (lemma for verbs/adjectives, surface for nouns), and deduplicates emitted words by lemma. Compound-merge passes (prefix, noun-suffix, verb-nominalizer) reconstruct synthetic lemmas from each component's `feature.lemma` so dictionary lookups can hit the headword.
- **WordFilterService**: multi-layer filtering via `filter_unknown` (lemma-only check against known vocabulary), `filter_by_frequency`, `filter_by_word_lists` (blacklist/whitelist keyed by lemma), `deduplicate_by_sentence` (NFKC-normalized, whitespace-collapsed dedup key), `filter_i_plus_one`, and `filter_by_episode_count`. The name-wordset filter (Issue #59) runs in `_phase2_filter` after the blacklist/whitelist step and is gated on `bypass_optional_filters` (skipped by the Deck Builder corpus preview); which wordsets are active is controlled by `config.excluded_wordsets`. Wordset files are bundled JMnedict-derived proper-noun lists in `anki_miner/gui/resources/wordsets/`.
- **MediaExtractorService**: extracts screenshots (`ffmpeg -frames:v 1`) and audio clips (`ffmpeg libmp3lame`) at subtitle timestamps. Runs in parallel via `ThreadPoolExecutor` with `max_parallel_workers` threads. Auto-detects the Japanese audio stream via `ffprobe` with thread-safe caching.
- **DefinitionService**: orchestrates the provider chain built by `DictionaryRegistry` from `config.dictionary_chain`. First-hit-wins across offline `IndexedDictProvider` instances, with `JishoProvider` as the online fallback. Returns HTML-formatted definition strings.
- **AnkiService**: AnkiConnect HTTP API wrapper (localhost:8765). Key operations: `get_existing_vocabulary`, `store_media_file`, `create_cards_batch` (batch size 50), `delete_notes`. Stores `last_created_note_ids` for undo support.
- **ValidationService**: checks AnkiConnect connectivity, ffmpeg presence, deck existence, and note type existence. Returns `ValidationResult` (never raises).
- **YouTubeFetcherService** (`services/youtube_fetcher.py`): wraps the `yt-dlp` subprocess. Two entry points: `probe_metadata(url) → VideoInfo` (fast, `--skip-download --dump-single-json`) and `fetch_video(url, workspace, sub_mode, progress_cb, cancel_event) → FetchedMedia`. Detects native vs translated auto-captions via `_has_native_auto_ja()`. Tracks the `Popen` handle so cancellation can kill the full process tree (yt-dlp → ffmpeg child) via `psutil`. Writes the (video, subtitle) pair into a caller-owned workspace directory.

**Optional services (created based on config flags):**

- **FrequencyService**: loads word frequency CSV, exposes `lookup(word) -> rank`.
- **PitchAccentService**: loads pitch accent CSV, exposes `lookup_batch`.
- **KnownWordDB**: SQLite-backed persistent known word cache. Supports differential sync with Anki vocabulary.
- **WordListService**: loads blacklist/whitelist text files for word filtering.
- **HistoryService**: SQLite-backed mining history (`mining_history` table). Records what was mined, supports undo via stored card IDs.
- **StatsService**: SQLite-backed analytics (`mining_sessions`, `difficulty_entries` tables). Provides aggregated stats and milestones.
- **UpdateChecker**: queries the GitHub Releases API for newer versions.
- **ExportService**: exports results to CSV, TSV, or vocabulary list formats.

**Dictionary providers** (`services/dictionary/providers/`):

- **IndexedDictProvider**: SQLite-backed offline provider used by every on-disk dictionary (JMdict and user-loaded Yomitan dicts). On first launch, JMdict XML is migrated to a SQLite index at `~/.anki_miner/dicts/jmdict-english/index.sqlite`; lookups run against that index. The read-only connection is opened with `check_same_thread=False` so a single instance is safe to share across worker threads.
- **DictionaryRegistry**: scans `config.dicts_root` (`ANKI_MINER_HOME/dicts/`) for installed dictionaries and builds the provider chain from `config.dictionary_chain`. Disk I/O happens in the explicit `load()` call, not in `__init__`. The chain is first-hit-wins, with `JishoProvider` appended as the online fallback.
- **JishoProvider**: REST client for the jisho.org API. Always available. Rate-limited with a configurable delay (`jisho_delay`).

## Orchestration

**EpisodeProcessor** (`orchestration/episode_processor.py`):
- Receives all services via constructor injection
- `process_episode()` runs the 5-stage pipeline
- `process_youtube_url()` calls `YouTubeFetcherService.fetch_video`, then delegates to the unchanged `process_episode` with `episode_name_override=f"YT:{video_id}"` and `series_name_override="YT:<channel_id>"` (or `"YouTube"` fallback). The workspace is allocated and cleaned by the worker, not the orchestrator.
- Cancellation checkpoints between each phase (`self._cancelled` flag); the YouTube flow additionally threads a `threading.Event` into the fetcher so an in-flight yt-dlp subprocess can be killed.
- Supports `preview_mode` (exits after filtering, no cards created)
- Supports `curation_callback` (GUI presents word selection dialog)
- Supports `cross_episode_counts` for batch frequency filtering
- Supports `episode_name_override` / `series_name_override` so YouTube-sourced sessions have stable, file-name-independent identity.
- Records to StatsService and KnownWordDB after successful processing
- Cleans up temp media files in `finally` block

**FolderProcessor** (`orchestration/folder_processor.py`):
- Wraps `EpisodeProcessor` for batch processing
- `find_video_subtitle_pairs()` matches files by stem name
- `collect_cross_episode_frequencies()` does a two-pass approach: first parses all subtitles, then counts word appearances across episodes
- `process_folder()` iterates matched pairs sequentially

**DeckBuilderWorker** (`gui/workers/deck_builder_worker.py`):
Whole-anime deck mining in two phases separated by a GUI confirm gate.

Phase 1 — aggregate + select: `SubtitleParserService.count_lemmas` is called on every subtitle in the request. The raw per-file counters are summed by `services.corpus_aggregator.aggregate` into a single corpus `Counter`. `select` then ranks lemmas by in-corpus frequency and picks a candidate set according to the mode (ALL, TOP_N, COVERAGE_PCT). Coverage is computed over in-corpus mineable-word token counts (the same POS-filter as mining applies), not `frequency.csv`. A `DeckBuildPreview` is emitted and the worker blocks on a `threading.Event` gate until the GUI calls `confirm()` or `reject()`.

Phase 2 — build: `AnkiService.ensure_deck` creates the target deck if it does not exist (idempotent). For each episode pair, a fresh `EpisodeProcessor` is created via `dataclasses.replace(config, anki_deck_name=deck_name, include_known_words=not collection_filter)` — no production code other than the config field changes. A `curation_callback` closure keeps a word only if its lemma is in the selected set and has not already been carded in a previous episode (`carded: set[str]` shared across the loop). This enforces the cross-episode "card each lemma once" invariant without touching `EpisodeProcessor` internals. `episode_name_override` and `series_name_override` are set to the video stem and deck name respectively so history rows are distinct from regular episode-mining sessions.

## Configuration

`AnkiMinerConfig` (`config/config.py`) is a frozen (immutable) dataclass with ~30 settings:

- **Anki:** deck name, note type, field mappings, AnkiConnect URL
- **Media:** audio padding, screenshot offset, temp folder, subtitle offset
- **Filtering:** min word length, allowed POS tags, excluded subtypes, deduplication
- **Dictionary:** `dictionary_chain` (the runtime-authoritative ordered list of providers — indexed dicts and Jisho, each toggleable), `dicts_root` (root for all installed `.sqlite` indexes; defaults to `ANKI_MINER_HOME/dicts/` via the `ANKI_MINER_HOME` constant in `config/paths.py`), Jisho URL/delay. Legacy `jmdict_path` + `use_offline_dict` are retained one release for first-launch migration only.
- **Optional data:** pitch accent, frequency, known words DB, blacklist/whitelist paths and toggles
- **History/analytics:** DB paths, enable flags
- **Performance:** max parallel workers (default 6)

The `__post_init__` method uses `object.__setattr__` to convert string paths to `Path` objects (required because the dataclass is frozen). New config instances are created with `dataclasses.replace()`.

**Config source:**
- GUI: `GUIConfigManager` (`gui/utils/config_manager.py`) persists to `~/.anki_miner/gui_config.json`. Defaults come from the `AnkiMinerConfig` dataclass field defaults.

## GUI Architecture

### Window Structure

`MainWindow` contains a `QTabWidget` with five tabs:
1. **SingleEpisodeTab**: file selectors (drag-and-drop), subtitle offset control, process/preview buttons, log widget, progress widget.
2. **BatchProcessingTab**: folder selection, `BatchQueue` management via queue panel, dual progress bars.
3. **YouTubeTab** (`gui/widgets/youtube_tab.py`): URL input + Add button, `QListWidget` queue of `YouTubeQueueItemWidget` rows (per-row status glyph, title, duration, sub source line, remove button), action buttons (Preview / Mine / Clear / Stop All), progress widget, log widget. Deck/note-type/tags widgets are global (see `AnkiSettingsPanel`).
4. **AnalyticsTab**: mining statistics dashboard (queries `StatsService`).
5. **SettingsTab**: config editing with sub-panels (Anki, media, dictionary, filtering, YouTube). Emits `config_changed` signal.

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
- `YouTubeQueueWorker` (`gui/workers/youtube_queue_worker.py`): `CancellableWorker` subclass that drives a list of `YouTubeQueueItem` through fetch + mine sequentially with retry-once on `YouTubeFetchError`. Per-attempt workspace allocation under `media_temp_folder/youtube/run-<uuid>/`.
- `YouTubeQueueItemWidget` (`gui/widgets/youtube_queue_item_widget.py`): pure renderer for one `YouTubeQueueItem` in the queue list.
- `PitchImportWorker` (`gui/workers/pitch_import_worker.py`): `CancellableWorker` that runs the Yomitan pitch-accent zip importer so the GUI stays responsive during a large import.
- `DeckBuilderWorker` (`gui/workers/deck_builder_worker.py`): see Orchestration section.

### Signal Architecture

`GUIPresenter` emits Qt signals from worker threads. Main window slots receive them on the GUI thread. Per-tab presenters avoid cross-tab signal pollution. `GUIProgressCallback` bridges the `ProgressCallback` protocol to Qt signals.

GUIPresenter does **not** explicitly inherit from `PresenterProtocol`. It satisfies the protocol via structural subtyping, which avoids a metaclass conflict between `QObject` and `Protocol`.

### Theme System

Theme singleton backed by JSON theme files in `gui/resources/styles/themes/`. 29 built-in themes across 10 families (Ayu, Catppuccin, Dracula, Everforest, GitHub, Gruvbox, Kanagawa, Rosé Pine, Solarized, Standalone). The `discover_themes()` function scans the themes directory at startup, validates each JSON file against a required color key schema (`REQUIRED_COLOR_KEYS`), and registers valid themes. A single `common.qss` stylesheet uses `${color-*}` variable substitution. The `Theme._substitute_variables()` method merges layout variables from `_variables.py` with color variables extracted from the active theme JSON. Custom themes can be added by dropping a valid JSON file into the themes directory. Theme preference is saved via `QSettings`.

### Dialogs

- `WordCurationDialog`: user selects which discovered words to mine (cross-thread via a `threading.Event` bridge). Embeds `SubtitlePlayerWidget` per row for in-place audio playback, and renders multi-dictionary lookup via `DefinitionService.lookup_all_offline`.
- `WordPreviewDialog`: preview discovered words.
- `PairPreviewDialog`: preview video/subtitle file pairing.
- `ResultsDialog`: summary of a mining session with undo option.
- `ExportDialog`: export results to file.
- `QueueManagerDialog`: manage the batch processing queue.

## External Integrations

### AnkiConnect

HTTP POST to `localhost:8765` (configurable). Protocol version 6. Key actions:
- `version`, `deckNames`, `modelNames`, `modelFieldNames`: validation.
- `findNotes`, `notesInfo`: vocabulary lookup.
- `storeMediaFile`: upload screenshots/audio.
- `addNote`, `addNotes`: card creation (batch size 50).
- `deleteNotes`: undo support.

### Jisho API

GET `https://jisho.org/api/v1/search/words?keyword=<word>`. Rate-limited with configurable delay (default 0.5s). Surfaced as `JishoProvider` inside the configurable provider chain — its position is user-controlled via `config.dictionary_chain`. In the default chain it sits after `IndexedDictProvider(jmdict-english)` so it acts as the online fallback when no installed dictionary returns a hit, but users may move it ahead of any indexed dictionary or disable it entirely.

### ffmpeg / ffprobe

- **ffmpeg:** `-ss` seek + `-i` input + `-frames:v 1` for screenshots, `libmp3lame` for audio extraction
- **ffprobe:** `-show_streams -select_streams a` for Japanese audio track detection
- Parallel execution via `ThreadPoolExecutor` (default 6 workers)

### yt-dlp

Subprocess invoked by `YouTubeFetcherService`. Probe uses `--skip-download --dump-single-json --no-playlist`; fetch uses `--write-sub` (or `--write-auto-sub` for auto-caption mode) + `--sub-lang ja --sub-format vtt/best --convert-subs srt` + a height-capped format string. Progress parsed from a custom `--progress-template`; post-download merge phases detected by scanning for `[Merger]`/`[SubtitleConvertor]` line signatures. Process tree killed via `psutil` on cancel (yt-dlp spawns ffmpeg as a child for merging; `Popen.terminate()` alone leaks it on Windows). Optional `--cookies-from-browser` flag enables bypass of bot-detection prompts and age-restricted content.

### PyInstaller hook for yt-dlp

yt-dlp lazy-loads ~1600 extractor modules plus optional deps (`websockets`, `mutagen`, `brotli`) that PyInstaller's static analysis misses. `PyInstaller-Hooks/hook-yt_dlp.py` calls `collect_all("yt_dlp")` and the release workflow passes `--additional-hooks-dir=PyInstaller-Hooks`. The release workflow's bundled smoke step (`ANKI_MINER_SMOKE=youtube` env var in `anki_miner/gui/app.py`) walks `yt_dlp.extractor.gen_extractors()` offline to verify the registry survived `collect_all`.

## Exception Hierarchy

```
AnkiMinerException (base)
├── ValidationError
├── SetupError
├── AnkiConnectionError
├── DeckNotFoundError
├── NoteTypeNotFoundError
├── CardCreationError
├── MediaExtractionError
├── SubtitleParseError
└── FFmpegError
```

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
| `frequency.csv` | CSV | Word frequency rankings |

Temporary media files are stored in the system temp directory under `anki_miner_temp/` and cleaned up after each processing run. YouTube downloads go one level deeper — `anki_miner_temp/youtube/run-<uuid>/` — owned by `YouTubeQueueWorker` (one workspace per attempt; cleaned up in `finally` on every exit path) and `rmtree`'d on every exit path (success, cancel, exception).
