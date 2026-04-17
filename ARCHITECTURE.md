# Architecture

Anki Miner is a Python desktop application with both CLI and PyQt6 GUI interfaces. It processes anime video/subtitle files through a 5-stage pipeline to create Japanese vocabulary flashcards in Anki.

## Processing Pipeline

The core data flow is a linear 5-stage pipeline orchestrated by `EpisodeProcessor`:

```
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
│    DefinitionService → DictionaryProviders          │
│    (JMdictProvider offline → JishoProvider fallback) │
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
cli/  ──────────┐
gui/  ──────────┤
                ▼
         orchestration/
                │
                ▼
           services/
           services/providers/
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

Leaf packages (`config`, `models`, `exceptions`, `utils`) have no internal dependencies. `interfaces` depends only on `models` for type signatures. `services` depends on `interfaces`, `models`, `config`, `exceptions`, and `utils`. `orchestration` composes services. `cli` and `gui` are top-level entry points.

## Core Abstractions

Three protocols in `interfaces/` define the system's extension points:

**PresenterProtocol** (`interfaces/presenter.py`): output abstraction with 7 methods.
- `show_info`, `show_success`, `show_warning`, `show_error`: message display.
- `show_validation_result(ValidationResult)`: system check results.
- `show_processing_result(ProcessingResult)`: episode processing summary.
- `show_word_preview(list[TokenizedWord])`: discovered word listing.

Implementations: `ConsolePresenter` (CLI), `GUIPresenter` (Qt signals), `NullPresenter` (tests).

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
| `TokenizedWord` | `word.py` | Parsed word with surface, lemma, reading, sentence, timing, furigana, frequency_rank |
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

## Services

Stateless business logic classes in `services/`. Each receives the frozen `AnkiMinerConfig` in its constructor.

**Core services (always created):**

- **SubtitleParserService**: parses ASS/SRT/SSA files via `pysubs2`, tokenizes Japanese text with `fugashi` (MeCab wrapper), generates furigana annotations, deduplicates by lemma+surface.
- **WordFilterService**: multi-layer filtering via `filter_unknown` (against known vocabulary), `filter_by_length`, `filter_by_frequency`, `filter_by_word_lists`, `deduplicate_by_sentence`, and `filter_by_episode_count`.
- **MediaExtractorService**: extracts screenshots (`ffmpeg -frames:v 1`) and audio clips (`ffmpeg libmp3lame`) at subtitle timestamps. Runs in parallel via `ThreadPoolExecutor` with `max_parallel_workers` threads. Auto-detects the Japanese audio stream via `ffprobe` with thread-safe caching.
- **DefinitionService**: orchestrates the provider chain. Default mode: JMdict first, Jisho only if JMdict is unavailable. Returns HTML-formatted definition strings.
- **AnkiService**: AnkiConnect HTTP API wrapper (localhost:8765). Key operations: `get_existing_vocabulary`, `store_media_file`, `create_cards_batch` (batch size 50), `delete_notes`. Stores `last_created_note_ids` for undo support.
- **ValidationService**: checks AnkiConnect connectivity, ffmpeg presence, deck existence, and note type existence. Returns `ValidationResult` (never raises).

**Optional services (created based on config flags):**

- **FrequencyService**: loads word frequency CSV, exposes `lookup(word) -> rank`.
- **PitchAccentService**: loads pitch accent CSV, exposes `lookup_batch`.
- **KnownWordDB**: SQLite-backed persistent known word cache. Supports differential sync with Anki vocabulary.
- **WordListService**: loads blacklist/whitelist text files for word filtering.
- **HistoryService**: SQLite-backed mining history (`mining_history` table). Records what was mined, supports undo via stored card IDs.
- **StatsService**: SQLite-backed analytics (`mining_sessions`, `difficulty_entries` tables). Provides aggregated stats and milestones.
- **UpdateChecker**: queries the GitHub Releases API for newer versions.
- **ExportService**: exports results to CSV, TSV, or vocabulary list formats.

**Dictionary providers** (`services/providers/`):

- **JMdictProvider**: parses JMdict XML (~60MB) into an in-memory dict on `load()`. Lookup returns HTML-formatted numbered definitions (max 5 per word).
- **JishoProvider**: REST client for the jisho.org API. Always available. Rate-limited with a configurable delay (`jisho_delay`).

## Orchestration

**EpisodeProcessor** (`orchestration/episode_processor.py`):
- Receives all services via constructor injection
- `process_episode()` runs the 5-stage pipeline
- Cancellation checkpoints between each phase (`self._cancelled` flag)
- Supports `preview_mode` (exits after filtering, no cards created)
- Supports `curation_callback` (GUI presents word selection dialog)
- Supports `cross_episode_counts` for batch frequency filtering
- Records to StatsService and KnownWordDB after successful processing
- Cleans up temp media files in `finally` block

**FolderProcessor** (`orchestration/folder_processor.py`):
- Wraps `EpisodeProcessor` for batch processing
- `find_video_subtitle_pairs()` matches files by stem name
- `collect_cross_episode_frequencies()` does a two-pass approach: first parses all subtitles, then counts word appearances across episodes
- `process_folder()` iterates matched pairs sequentially

## Configuration

`AnkiMinerConfig` (`config/config.py`) is a frozen (immutable) dataclass with ~30 settings:

- **Anki:** deck name, note type, field mappings, AnkiConnect URL
- **Media:** audio padding, screenshot offset, temp folder, subtitle offset
- **Filtering:** min word length, allowed POS tags, excluded subtypes, deduplication
- **Dictionary:** JMdict path, offline toggle, Jisho URL/delay
- **Optional data:** pitch accent, frequency, known words DB, blacklist/whitelist paths and toggles
- **History/analytics:** DB paths, enable flags
- **Performance:** max parallel workers (default 6)

The `__post_init__` method uses `object.__setattr__` to convert string paths to `Path` objects (required because the dataclass is frozen). New config instances are created with `dataclasses.replace()`.

**Config sources:**
- CLI: `create_default_config()` from `config/defaults.py` with optional overrides
- GUI: `GUIConfigManager` (`gui/utils/config_manager.py`) persists to `~/.anki_miner/gui_config.json`

## GUI Architecture

### Window Structure

`MainWindow` contains a `QTabWidget` with four tabs:
1. **SingleEpisodeTab**: file selectors (drag-and-drop), subtitle offset control, process/preview buttons, log widget, progress widget.
2. **BatchProcessingTab**: folder selection, `BatchQueue` management via queue panel, dual progress bars.
3. **AnalyticsTab**: mining statistics dashboard (queries `StatsService`).
4. **SettingsTab**: config editing with sub-panels (Anki, media, dictionary, filtering). Emits `config_changed` signal.

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

### Signal Architecture

`GUIPresenter` emits Qt signals from worker threads. Main window slots receive them on the GUI thread. Per-tab presenters avoid cross-tab signal pollution. `GUIProgressCallback` bridges the `ProgressCallback` protocol to Qt signals.

GUIPresenter does **not** explicitly inherit from `PresenterProtocol`. It satisfies the protocol via structural subtyping, which avoids a metaclass conflict between `QObject` and `Protocol`.

### Theme System

Theme singleton backed by JSON theme files in `gui/resources/styles/themes/`. Four built-in themes: Light, Dark, Sakura, and Tokyo Night. The `discover_themes()` function scans the themes directory at startup, validates each JSON file against a required color key schema (`REQUIRED_COLOR_KEYS`), and registers valid themes. A single `common.qss` stylesheet uses `${color-*}` variable substitution. The `Theme._substitute_variables()` method merges layout variables from `_variables.py` with color variables extracted from the active theme JSON. Custom themes can be added by dropping a valid JSON file into the themes directory. Theme preference is saved via `QSettings`.

### Dialogs

- `WordCurationDialog`: user selects which discovered words to mine (cross-thread via a `threading.Event` bridge).
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

GET `https://jisho.org/api/v1/search/words?keyword=<word>`. Rate-limited with configurable delay (default 0.5s). Used as fallback when JMdict is unavailable.

### ffmpeg / ffprobe

- **ffmpeg:** `-ss` seek + `-i` input + `-frames:v 1` for screenshots, `libmp3lame` for audio extraction
- **ffprobe:** `-show_streams -select_streams a` for Japanese audio track detection
- Parallel execution via `ThreadPoolExecutor` (default 6 workers)

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
| `JMdict_e` | XML | Offline Japanese-English dictionary (~60MB) |
| `known_words.db` | SQLite | Known word cache with Anki sync |
| `history.db` | SQLite | Mining history with undo support |
| `stats.db` | SQLite | Analytics (sessions, difficulty, milestones) |
| `pitch_accent.csv` | CSV | Pitch accent lookup data |
| `frequency.csv` | CSV | Word frequency rankings |

Temporary media files are stored in the system temp directory under `anki_miner_temp/` and cleaned up after each processing run.
